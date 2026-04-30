"""Check whether ADR 2026-04-30 (b) impulse-mechanism open-conditions are met.

The ADR specified three conditions that must hold simultaneously
before starting any (b) implementation work:

  1. Real emergent_self_mark sample count over the last 30 days >= 5
  2. Rejection rate over the last 30 days >= 80%
  3. Master has actively asked "is the slime going to talk to me?"
     (this can't be detected programmatically; surfaced for manual
     confirmation only)

This script encodes #1 and #2 against the actual local data so the
question stops being a vibe check. #3 is printed as a manual checkbox
the master answers themselves.

Usage (from repo root):

    python scripts/check_b_preconditions.py

Exit codes:
  0 — conditions #1 and #2 both met (no opinion on #3).
  1 — at least one of #1 / #2 not yet met.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `sentinel` importable from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force UTF-8 stdout on Windows.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


# Thresholds from ADR 2026-04-30.
WINDOW_DAYS = 30
MIN_MARK_COUNT = 5
MIN_REJECTION_RATE = 0.80


def main() -> int:
    from sentinel.emergent_log import LOG_PATH, summarize_recent

    summary = summarize_recent(days=WINDOW_DAYS)

    print("=" * 64)
    print("ADR (b) impulse-mechanism — precondition check")
    print("=" * 64)
    print(f"Window: last {summary['window_days']} days")
    print(f"Log:    {LOG_PATH}")
    print()

    total = summary["total_consultations"]
    marks = summary["mark_count"]
    rate = summary["rejection_rate"]

    print(f"Consultations:     {total}")
    print(f"Marks recorded:    {marks}")
    print(f"Rejection rate:    {rate:.1%}")
    print()
    if total > 0:
        print("By outcome:")
        for outcome, count in sorted(summary["by_outcome"].items()):
            if count > 0:
                print(f"  - {outcome}: {count}")
        print()

    # Condition 1: mark count
    cond1_met = marks >= MIN_MARK_COUNT
    cond1_label = "PASS" if cond1_met else "FAIL"
    print(f"[{cond1_label}] Condition 1 — mark_count >= {MIN_MARK_COUNT}")
    print(f"        observed: {marks}")
    if not cond1_met:
        print(f"        ground truth for 「史萊姆覺得什麼值得說」 still too thin.")

    # Condition 2: rejection rate
    # Only meaningful if there are at least *some* consultations.
    if total == 0:
        cond2_met = False
        cond2_label = "WAIT"
        print(f"[{cond2_label}] Condition 2 — rejection_rate >= {MIN_REJECTION_RATE:.0%}")
        print(f"        no consultations yet; rate undefined")
    else:
        cond2_met = rate >= MIN_REJECTION_RATE
        cond2_label = "PASS" if cond2_met else "FAIL"
        print(f"[{cond2_label}] Condition 2 — rejection_rate >= {MIN_REJECTION_RATE:.0%}")
        print(f"        observed: {rate:.1%}")
        if not cond2_met:
            print(f"        too eager; calibrate the (c) prompt before scaling.")

    # Condition 3: manual — script can never answer this.
    print(f"[ ?  ] Condition 3 — master has actively asked")
    print(f"        「史萊姆會主動講話嗎」 / variants thereof?")
    print(f"        (You answer this — script can't detect it.)")

    print()
    if cond1_met and cond2_met:
        print("Conditions 1 + 2 met. If condition 3 is also true, the ADR's")
        print("first (b) PR (the letter_to_master schema field) is unblocked.")
        print("If condition 3 is NOT yet true, keep waiting.")
        return 0
    else:
        print("Not ready. Keep doing other things, come back when both #1 and #2")
        print("are PASS. Per ADR: 「不要急。順序錯了，後面修不回來。」")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
