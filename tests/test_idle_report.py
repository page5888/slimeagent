"""Tests for sentinel.idle_report.compose_message.

The whole point of this module is that the daemon should NOT send
heartbeat-style Telegram messages. The most important test is the
"empty inputs return None" case — that's the assertion that closes
the bug from the user report ("46 messages/day even when nothing
is wrong").
"""
from __future__ import annotations

import unittest

from sentinel.idle_report import compose_message


class TestComposeMessage(unittest.TestCase):
    def test_no_news_returns_none(self):
        # The bug fix: empty warnings + no llm_warning → silence.
        self.assertIsNone(compose_message(
            warnings=[],
            snapshot_summary="CPU 12% / RAM 40% / Disk 60%",
            llm_warning=None,
        ))

    def test_llm_warning_only(self):
        msg = compose_message(
            warnings=[],
            snapshot_summary="CPU 12% / RAM 40% / Disk 60%",
            llm_warning="⚠️ LLM: Gemini 今天 5 個 model 全踩過 rate error",
        )
        self.assertIsNotNone(msg)
        # The LLM warning is shown on its own — snapshot stats don't
        # need to be wrapped around it because nothing's wrong with
        # CPU/RAM/disk.
        self.assertIn("⚠️ LLM", msg)
        self.assertNotIn("CPU", msg)

    def test_snapshot_warnings_only(self):
        msg = compose_message(
            warnings=["disk space low"],
            snapshot_summary="CPU 12% / RAM 40% / Disk 95%",
            llm_warning=None,
        )
        self.assertIsNotNone(msg)
        # Snapshot info is wrapped with the *AI Slime* header so it
        # reads consistently with other alert-path messages from
        # daemon.py line 154.
        self.assertIn("*AI Slime*", msg)
        self.assertIn("Disk 95%", msg)

    def test_both_signals_combined(self):
        msg = compose_message(
            warnings=["high cpu burst"],
            snapshot_summary="CPU 99% / RAM 60% / Disk 60%",
            llm_warning="⚠️ LLM: Gemini blocked",
        )
        self.assertIsNotNone(msg)
        # Both pieces should be there.
        self.assertIn("CPU 99%", msg)
        self.assertIn("⚠️ LLM", msg)
        # And separated by blank line (so they read as two signals).
        self.assertIn("\n\n", msg)

    def test_empty_string_llm_warning_treated_as_none(self):
        # Edge: compose_idle_warning shouldn't return "" but if it ever
        # did (some future bug), we shouldn't render an empty section.
        # `if llm_warning` is falsy for "" so the function should treat
        # it the same as None.
        self.assertIsNone(compose_message(
            warnings=[],
            snapshot_summary="CPU 12%",
            llm_warning="",
        ))

    def test_empty_warnings_list_is_quiet(self):
        # Ensure `warnings=[]` (the common case from snapshot) doesn't
        # accidentally evaluate truthy via the snapshot_summary content.
        # (Would only fail if we used `len(snapshot_summary)` instead
        # of `if warnings`.)
        self.assertIsNone(compose_message(
            warnings=[],
            snapshot_summary="CPU 99%",  # high but no warnings flagged
            llm_warning=None,
        ))


if __name__ == "__main__":
    unittest.main()
