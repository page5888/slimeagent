"""Print today's LLM rate-error summary.

Usage (from repo root):

    python scripts/llm_health_today.py

Reads `~/.hermes/llm_health.jsonl` (the structured log written by
sentinel.llm's per-provider call sites whenever a 429 / quota /
overloaded error fires) and prints a one-screen breakdown of which
providers / models hit limits today.

The point of this tool: by the time the slime falls back from
Gemini → OpenAI → Anthropic, no user-visible signal said anything was
wrong. Tailing sentinel.log catches it but the noise ratio is bad.
This CLI is the smallest "is my primary provider currently silent-
failing?" check.

Exit codes:
  0 — read OK; primary provider is fine OR no calls happened today.
  2 — read OK but primary provider is fully blocked (every model in
      config.LLM_PROVIDERS[0]['models'] hit a rate error today).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `sentinel` importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force UTF-8 stdout on Windows; provider error strings often include
# non-ASCII glyphs depending on locale.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def main() -> int:
    from sentinel.llm_health import LOG_PATH, get_today_summary

    summary = get_today_summary()

    print("=" * 60)
    print("LLM RATE-ERROR SUMMARY — TODAY")
    print("=" * 60)
    print(f"Log: {LOG_PATH}")
    print()

    total = summary["total_rate_errors"]
    if total == 0:
        print("No rate errors recorded today. ✨")
        return 0

    print(f"Total rate-class errors today: {total}")
    print()

    for prov, bucket in sorted(
        summary["by_provider"].items(),
        key=lambda kv: kv[1]["count"],
        reverse=True,
    ):
        print(f"  {prov}: {bucket['count']} error(s)")
        for model, n in sorted(bucket["models"].items(),
                               key=lambda kv: kv[1], reverse=True):
            print(f"    - {model}: {n}")

    print()
    if summary["primary_blocked"]:
        print(
            f"!! Primary provider ({summary['primary_provider']}) is "
            "FULLY BLOCKED today — every configured model hit a rate "
            "error. The daemon is silently falling back. Worth checking "
            "fallback provider quotas before they go too."
        )
        return 2

    print(f"Primary provider ({summary['primary_provider']}) still has "
          "headroom (not every model rate-errored today).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
