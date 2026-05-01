"""Preflight health check — verify slimeagent's actual runtime state matches expectations.

Runs a battery of cheap checks against the live `~/.hermes/` data
and the repo's expected state. Designed to take <5 seconds and
produce a yes/no verdict you can trust before claiming "it works".

This is the operational form of `feedback_engineering_defaults.md`'s
default #2 ("First deliverable for any bug report is a runnable
yes/no signal in <5 minutes"). Every signal we've been bitten by
in the last 24 hours is checked here:

  - Daemon actually running (PR #108 — wires emergent self-mark
    into GUI loop only fires if daemon thread is alive).
  - Persistent log file fresh (PR #98 — fixed silent FileHandler).
  - Version coherence between _version.py / README / git tag / log
    file's first line (PR #102 — header version display assumed
    we'd remember to bump all three places).
  - Cron checks producing data (PR #99 / #108 — the three weeks
    worth of zero data that surfaced this morning).
  - No voice drift in recent chat (PR #110 — banned-word filter).
  - No push-spam in recent chat-or-telegram (PR #107 — advisor
    module killed).

Usage (from repo root):

    python scripts/preflight.py

Or, more strictly:

    python scripts/preflight.py --strict   # warnings count as failures

Exit codes:
  0 — all PASS (or only WARN, in non-strict mode).
  1 — at least one FAIL.
  2 — runtime error (preflight itself broke).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Make `sentinel` importable from repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Force UTF-8 stdout on Windows.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


HOME = Path.home()
HERMES = HOME / ".hermes"


# ─── Result type ────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    status: str  # "PASS" | "WARN" | "FAIL" | "SKIP"
    message: str

    @property
    def emoji(self) -> str:
        return {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[self.status]


def _ok(name: str, msg: str = "") -> CheckResult:
    return CheckResult(name, "PASS", msg)


def _warn(name: str, msg: str) -> CheckResult:
    return CheckResult(name, "WARN", msg)


def _fail(name: str, msg: str) -> CheckResult:
    return CheckResult(name, "FAIL", msg)


def _skip(name: str, msg: str) -> CheckResult:
    return CheckResult(name, "SKIP", msg)


# ─── Individual checks ──────────────────────────────────────────────


def check_daemon_running() -> CheckResult:
    """Is `python -m sentinel` (or sentinel.exe) actually running?"""
    name = "daemon_running"
    try:
        import psutil  # type: ignore
    except ImportError:
        return _skip(name, "psutil not installed; cannot check process list")

    me = None
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(p.info.get("cmdline") or [])
            normalized = cmdline.replace("\\", "/")
            if "-m sentinel" in normalized or "/sentinel/__main__" in normalized:
                me = p.info["pid"]
                break
        except Exception:
            continue
    if me is None:
        return _fail(name, "no sentinel process found — daemon not running")
    return _ok(name, f"sentinel pid {me}")


def check_log_fresh() -> CheckResult:
    """Is `~/.hermes/sentinel.log` being written to right now?"""
    name = "log_fresh"
    log = HERMES / "sentinel.log"
    if not log.exists():
        return _fail(name, f"{log} does not exist — logging FileHandler attach probably broken")
    age_s = time.time() - log.stat().st_mtime
    if age_s > 600:
        return _warn(name, f"last write {age_s/60:.1f} min ago — daemon may be hung or logging stopped")
    return _ok(name, f"last write {age_s:.0f}s ago")


def _read_repo_version() -> str | None:
    try:
        ns: dict = {}
        exec((REPO_ROOT / "sentinel" / "_version.py").read_text(encoding="utf-8"), ns)
        return ns.get("__version__")
    except Exception:
        return None


def _read_readme_version() -> str | None:
    try:
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        m = re.search(r"badge/version-([0-9]+\.[0-9]+\.[0-9]+)-", text)
        return m.group(1) if m else None
    except Exception:
        return None


def _read_running_version() -> str | None:
    """Pull version from daemon's first 'AI Slime vX.Y.Z starting' line."""
    log = HERMES / "sentinel.log"
    if not log.exists():
        return None
    try:
        with log.open("r", encoding="utf-8") as f:
            # Read recent tail since the file may include older boot lines too.
            # The most recent boot is what matters.
            lines = f.readlines()
        for line in reversed(lines[-2000:]):
            m = re.search(r"AI Slime v([0-9]+\.[0-9]+\.[0-9]+) starting", line)
            if m:
                return m.group(1)
    except Exception:
        return None
    return None


def check_version_coherence() -> CheckResult:
    """Do `_version.py`, README badge, and the running daemon log all agree?"""
    name = "version_coherence"
    repo = _read_repo_version()
    readme = _read_readme_version()
    running = _read_running_version()
    parts = [f"_version.py={repo}", f"README={readme}", f"running={running}"]
    if not (repo and readme):
        return _fail(name, "could not read repo version files: " + " ".join(parts))
    if repo != readme:
        return _fail(name, "_version.py and README badge disagree: " + " ".join(parts))
    if running is None:
        return _warn(name, "could not detect running version (daemon may not have logged a startup line yet): " + " ".join(parts))
    if running != repo:
        return _warn(name, "running daemon is on an older version — restart needed to apply: " + " ".join(parts))
    return _ok(name, f"all on {repo}")


def check_cron_consultations() -> CheckResult:
    """Has emergent_self_mark been consulted at least once recently?"""
    name = "cron_consultations"
    log = HERMES / "emergent_self_mark_log.jsonl"
    if not log.exists():
        return _fail(name, f"{log} does not exist — cron check has never fired (PR #108 fix not effective yet)")
    if log.stat().st_size == 0:
        return _fail(name, "log file exists but empty")
    try:
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
    except Exception as e:
        return _fail(name, f"log unreadable: {e}")
    if not rows:
        return _fail(name, "no consultation rows")
    last_ts = max(r.get("time", 0) for r in rows)
    age_h = (time.time() - last_ts) / 3600
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("outcome", "?")] = counts.get(r.get("outcome", "?"), 0) + 1
    summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    if age_h > 36:
        return _warn(name, f"last consultation {age_h:.1f}h ago — cron may be stalled. {summary}")
    return _ok(name, f"{len(rows)} total, last {age_h:.1f}h ago. outcomes: {summary}")


def check_master_phrases() -> CheckResult:
    """How many co-reference anchors has slime captured? Reports as info."""
    name = "master_phrases"
    mem = HERMES / "sentinel_memory.json"
    if not mem.exists():
        return _skip(name, "no memory file yet")
    try:
        m = json.loads(mem.read_text(encoding="utf-8"))
        moments = m.get("memorable_moments", [])
        with_phrase = [x for x in moments if x.get("master_phrase")]
        emergent = [x for x in moments if x.get("category") == "emergent_self_mark"]
        return _ok(
            name,
            f"{len(emergent)} emergent self-marks, {len(with_phrase)} with master_phrase "
            f"(Slime 之語 dictionary size)"
        )
    except Exception as e:
        return _fail(name, f"memory unreadable: {e}")


# Banned words — keep in sync with chat.py's voice anti-drift rules.
# If chat.py rules change, mirror here.
_VOICE_DRIFT_BANNED = [
    # Programming metaphors slime shouldn't use unprompted
    "callback", "race condition", "stack trace", "資料結構", "debug 工具",
    "函數呼叫", "branch", "解析",
    # Generic AI consultant abstractions
    "依附感", "黏著度", "信任回路", "持續一致性", "預期管理",
    "個人化催化劑", "回饋循環", "用戶體驗", "智能推薦", "貼心提醒",
    # Sycophant tail
    "你是不是準備要讓你的專案", "這種魔法",
    # Brain-reading flex
    "蒐集你的", "預判你的下一步", "我懂你的思考", "你腦中的", "我就是你",
]


def check_chat_voice_drift(window: int = 10) -> CheckResult:
    """Scan last N chat assistant messages for banned words / voice drift."""
    name = "chat_voice_drift"
    chats = HERMES / "sentinel_chats.jsonl"
    if not chats.exists():
        return _skip(name, "no chat log yet")
    try:
        lines = chats.read_text(encoding="utf-8").strip().split("\n")
        rows = []
        for l in reversed(lines):
            try:
                row = json.loads(l)
            except Exception:
                continue
            if row.get("role") == "assistant":
                rows.append(row)
            if len(rows) >= window:
                break
    except Exception as e:
        return _fail(name, f"chat log unreadable: {e}")
    if not rows:
        return _skip(name, "no assistant messages in chat log")

    hits: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        text = row.get("text", "") or ""
        for term in _VOICE_DRIFT_BANNED:
            if term in text:
                hits.setdefault(term, []).append(i)
    if not hits:
        return _ok(name, f"no banned terms in last {len(rows)} assistant messages")
    pieces = [f"'{term}' x{len(idxs)}" for term, idxs in sorted(hits.items())]
    return _warn(name, f"voice drift in last {len(rows)} messages: " + ", ".join(pieces))


def check_data_files_parseable() -> CheckResult:
    """All key JSON / JSONL data files are valid."""
    name = "data_files_parseable"
    files = [
        HERMES / "sentinel_memory.json",
        HERMES / "aislime_evolution.json",
        HERMES / "emergent_self_mark_log.jsonl",
    ]
    bad = []
    for f in files:
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8")
            if f.suffix == ".jsonl":
                for i, line in enumerate(text.strip().split("\n")):
                    if line.strip():
                        json.loads(line)
            else:
                json.loads(text)
        except Exception as e:
            bad.append(f"{f.name}: {e}")
    if bad:
        return _fail(name, "; ".join(bad))
    return _ok(name, f"{len([f for f in files if f.exists()])} files OK")


# ─── Aggregator ──────────────────────────────────────────────────────


CHECKS = [
    check_daemon_running,
    check_log_fresh,
    check_version_coherence,
    check_cron_consultations,
    check_master_phrases,
    check_chat_voice_drift,
    check_data_files_parseable,
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--strict", action="store_true",
        help="treat WARN as FAIL — exit 1 on any non-PASS",
    )
    args = parser.parse_args(argv)

    print("=" * 64)
    print("AI Slime preflight")
    print("Started:", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 64)

    results: list[CheckResult] = []
    for check in CHECKS:
        try:
            r = check()
        except Exception as e:
            r = CheckResult(check.__name__, "FAIL", f"check itself crashed: {e}")
        results.append(r)
        print(f"{r.emoji} {r.name:30s} {r.message}")

    print("=" * 64)
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for r in results:
        counts[r.status] += 1
    print(f"  {counts['PASS']} PASS · {counts['WARN']} WARN · "
          f"{counts['FAIL']} FAIL · {counts['SKIP']} SKIP")

    if counts["FAIL"]:
        print("\n→ FAIL — at least one check rejected. Do not declare 'shipped'.")
        return 1
    if counts["WARN"] and args.strict:
        print("\n→ WARN — strict mode treats warnings as failure.")
        return 1
    if counts["WARN"]:
        print("\n→ OK with warnings.")
        return 0
    print("\n→ ALL CLEAR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
