"""Release version bump utility — one command, three files synced + preflight gate.

Bumps the three places that must stay in sync on every release:
  - sentinel/_version.py  (__version__ constant; runtime version)
  - README.md             (shields.io badge in header)
  - CHANGELOG.md          ([Unreleased] → [X.Y.Z] — YYYY-MM-DD)

Plus, by default, **runs scripts/preflight.py first and refuses to
bump if anything FAILs**. Today's sprint shipped 8 patch releases
where 3+ of them were on top of code paths that had never actually
executed in production — the daemon needed restart to apply the
fixes, but we cut the release before verifying. The whole class of
"shipped a release whose claimed feature doesn't actually run" bugs
is exactly what preflight catches and exactly what release.py would
have stopped if this gate had existed.

Usage:

    python scripts/release.py 0.7.11           # gate on, dies if preflight FAIL
    python scripts/release.py 0.7.11 --strict  # also dies on WARN
    python scripts/release.py 0.7.11 --force   # bypass preflight (have a reason)
    python scripts/release.py 0.7.11 --dry-run # show what would change

Atomic: validates all three files first; writes only if all three
checks pass AND preflight passes (or --force given). A failure in
any one leaves the working tree untouched.

Does NOT do git operations — review the diff first, then commit /
push / tag manually using the standard release flow in CLAUDE.md.

Exit codes:
  0 — bumped successfully (or dry-run completed)
  1 — bad input, file not in expected shape, or preflight FAIL
  2 — preflight ran but unexpectedly broke
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass
class FileBump:
    """A planned text replacement in one file. Validated before any writes."""
    path: Path
    new_text: str  # full file contents after the change
    summary: str   # one-line description for human display


def _plan_version_py(new_version: str) -> FileBump:
    path = REPO_ROOT / "sentinel" / "_version.py"
    text = path.read_text(encoding="utf-8")
    pattern = r'__version__ = "([^"]+)"'
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f"FAIL: could not find __version__ assignment in {path}")
    old = m.group(1)
    new_text = re.sub(pattern, f'__version__ = "{new_version}"', text, count=1)
    return FileBump(path, new_text, f"sentinel/_version.py: {old} → {new_version}")


def _plan_readme(new_version: str) -> FileBump:
    path = REPO_ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    pattern = r"(badge/version-)([0-9]+\.[0-9]+\.[0-9]+)(-)"
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f"FAIL: could not find version badge in {path}")
    old = m.group(2)
    new_text = re.sub(pattern, rf"\g<1>{new_version}\g<3>", text, count=1)
    return FileBump(path, new_text, f"README.md badge:      {old} → {new_version}")


def _plan_changelog(new_version: str, date: str) -> FileBump:
    """Rename top-of-file [Unreleased] → versioned header.

    Critical sanity check: [Unreleased] MUST appear before any other
    version header. If a stray [Unreleased] exists in the file's
    middle (e.g. orphaned from a botched previous cut), the .replace()
    call would silently rename it and bury today's real release content
    under a stale section title. Refuse if that's the case — the file
    needs manual triage first.
    """
    path = REPO_ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    marker = "## [Unreleased]"

    if marker not in text:
        raise SystemExit(
            f"FAIL: no '{marker}' section in {path}.\n"
            "Either add an [Unreleased] section with this release's notes "
            "first, or fix the CHANGELOG manually if it's already bumped."
        )

    unreleased_pos = text.index(marker)
    version_header = re.search(r"^## \[\d+\.\d+\.\d+\]", text, flags=re.MULTILINE)
    if version_header and version_header.start() < unreleased_pos:
        line_no = text[:unreleased_pos].count("\n") + 1
        raise SystemExit(
            f"FAIL: '{marker}' appears AFTER a versioned header in {path} "
            f"(line {line_no}).\n"
            "An orphaned [Unreleased] section is buried in the file's "
            "middle. Triage manually — fold its content into the appropriate "
            "released version, delete it, or move it to the top. Don't bump "
            "until exactly one [Unreleased] exists at the top of the file."
        )

    new_header = f"## [{new_version}] — {date}"
    new_text = text.replace(marker, new_header, 1)
    return FileBump(
        path, new_text,
        f"CHANGELOG.md:         [Unreleased] → [{new_version}] — {date}",
    )


def _run_preflight_gate() -> tuple[int, int, int, list[str]]:
    """Run preflight as subprocess, parse stdout for PASS/WARN/FAIL counts.

    Subprocess (vs in-process import) is the simpler path: preflight does
    sys.path manipulation + stream reconfiguration at module-load time
    that doesn't compose cleanly with being imported from another script.
    Running it as its own process gives us isolation; we just count
    lines and look at exit code.

    Returns (n_pass, n_warn, n_fail, formatted_lines).
    """
    import subprocess
    import sys

    try:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "preflight.py")],
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT), encoding="utf-8",
        )
    except Exception as e:
        raise SystemExit(f"FAIL: could not invoke scripts/preflight.py: {e}")

    output = proc.stdout or ""
    pass_n = warn_n = fail_n = 0
    lines: list[str] = []
    for line in output.splitlines():
        s = line.strip()
        if s.startswith("[PASS]"):
            pass_n += 1
            lines.append("    " + line)
        elif s.startswith("[WARN]"):
            warn_n += 1
            # Truncate very long WARN messages (e.g. 21-term drift list)
            # so the gate output stays readable.
            if len(line) > 200:
                line = line[:200] + " …(截斷)"
            lines.append("    " + line)
        elif s.startswith("[FAIL]"):
            fail_n += 1
            lines.append("    " + line)
        elif s.startswith("[SKIP]"):
            lines.append("    " + line)
    return pass_n, warn_n, fail_n, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("version", help="new version (X.Y.Z)")
    parser.add_argument("--date", default=None,
                        help="release date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would change without writing")
    parser.add_argument("--strict", action="store_true",
                        help="treat preflight WARN as a release blocker")
    parser.add_argument("--force", action="store_true",
                        help="bypass preflight gate entirely (use sparingly)")
    args = parser.parse_args(argv)

    if not VERSION_RE.match(args.version):
        parser.error(f"bad version format: {args.version} (expected X.Y.Z)")
    date = args.date or dt.date.today().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        parser.error(f"bad date format: {date} (expected YYYY-MM-DD)")

    mode = "(dry run) " if args.dry_run else ""
    print(f"=== {mode}release bump → {args.version} ({date}) ===")

    # Phase 0: preflight gate. Skipped on --force (escape hatch for
    # genuine emergencies — but the whole point of this gate is that
    # 'I forgot to verify' is not an emergency, it's the recurring
    # bug class we're trying to stop).
    if not args.force:
        print("--- preflight gate ---")
        try:
            p, w, f, lines = _run_preflight_gate()
        except SystemExit:
            raise
        except Exception as e:
            print(f"  preflight gate crashed: {e}")
            print("  use --force to bypass if you've already verified manually")
            return 2
        for line in lines:
            print(line)
        print(f"  → {p} PASS · {w} WARN · {f} FAIL")
        if f > 0:
            print(f"\nFAIL: preflight has {f} failure(s). Don't cut a release on top of a broken signal.")
            print("Triage what's failing, restart the daemon if needed, re-verify, then re-run.")
            print("If you're absolutely sure, use --force to bypass (you'll regret it).")
            return 1
        if w > 0 and args.strict:
            print(f"\nFAIL: preflight has {w} warning(s) and --strict is on.")
            return 1
        if w > 0:
            print(f"  ({w} warning(s) — proceeding because --strict not set)")
    else:
        print("--- preflight gate SKIPPED (--force) ---")

    # Phase 1: plan + validate all three. Any failure raises SystemExit
    # before any file is written.
    plans = [
        _plan_version_py(args.version),
        _plan_readme(args.version),
        _plan_changelog(args.version, date),
    ]
    print("--- file bump plan ---")
    for p in plans:
        print(f"  {p.summary}")

    if args.dry_run:
        print("\n(dry run — no files modified)")
        return 0

    # Phase 2: apply. By this point all validations have passed.
    for p in plans:
        p.path.write_text(p.new_text, encoding="utf-8")

    print(f"\nDone. Suggested next steps:")
    print(f"  git diff   # review the bump")
    print(f"  git checkout -b release/{args.version}")
    print(f"  git add CHANGELOG.md README.md sentinel/_version.py")
    print(f'  git commit -m "docs(release): cut [{args.version}]"')
    print(f"  git push -u origin release/{args.version}")
    print(f"  # ... open PR, wait CI, merge, then:")
    print(f"  git checkout main && git pull")
    print(f'  git tag -a v{args.version} -m "v{args.version}"')
    print(f"  git push origin v{args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
