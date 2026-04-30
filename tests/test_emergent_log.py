"""Tests for sentinel.emergent_log.

Mirrors the structure of test_llm_health.py: hermetic temp-file paths,
write/read round-trips, day-window filtering, summary correctness.
"""
from __future__ import annotations

import datetime
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sentinel.emergent_log as el


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestRecord(unittest.TestCase):
    def test_record_writes_jsonl_row(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            with mock.patch.object(el, "LOG_PATH", path):
                el.record_consultation(el.OUTCOME_MARK, "headline X")
            with open(path, "r", encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "mark")
        self.assertEqual(rows[0]["reason"], "headline X")
        self.assertIsInstance(rows[0]["ts"], float)

    def test_record_drops_invalid_outcome(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            with mock.patch.object(el, "LOG_PATH", path):
                el.record_consultation("not_a_real_outcome", "x")
            # File should not even be created since the call short-circuited.
            self.assertFalse(path.exists())

    def test_record_truncates_reason(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            with mock.patch.object(el, "LOG_PATH", path):
                el.record_consultation(el.OUTCOME_PARSE_FAIL, "x" * 5000)
            with open(path, "r", encoding="utf-8") as f:
                row = json.loads(f.readline())
        self.assertLessEqual(len(row["reason"]), 240)

    def test_record_swallows_oserror(self):
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            # Should NOT raise.
            el.record_consultation(el.OUTCOME_MARK, "y")


class TestReadWindow(unittest.TestCase):
    def test_empty_file_returns_no_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            self.assertEqual(el._read_recent_rows(30, time.time(), path=path), [])

    def test_missing_file_returns_no_rows(self):
        path = Path("/nonexistent/emergent_log.jsonl")
        self.assertEqual(el._read_recent_rows(30, time.time(), path=path), [])

    def test_zero_days_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            now = time.time()
            _write_rows(path, [
                {"ts": now, "outcome": "mark", "reason": ""},
            ])
            self.assertEqual(el._read_recent_rows(0, now, path=path), [])

    def test_corrupt_lines_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            now = time.time()
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"ts": ' + str(now) + ', "outcome": "mark", "reason": ""}\n')
                f.write("not json at all\n")
                f.write('{"missing_ts": true}\n')
                f.write('{"ts": "not a number", "outcome": "mark"}\n')
            rows = el._read_recent_rows(30, now, path=path)
        self.assertEqual(len(rows), 1)

    def test_filters_by_window(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            now = time.time()
            old = now - 31 * 86400   # 31 days ago — outside 30d window
            recent = now - 5 * 86400  # 5 days ago — inside
            _write_rows(path, [
                {"ts": old, "outcome": "mark", "reason": ""},
                {"ts": recent, "outcome": "mark", "reason": ""},
                {"ts": now, "outcome": "refuse", "reason": ""},
            ])
            rows = el._read_recent_rows(30, now, path=path)
        self.assertEqual(len(rows), 2)


class TestSummarize(unittest.TestCase):
    def test_empty_window_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            summary = el.summarize_recent(days=30, now=time.time(), path=path)
        self.assertEqual(summary["total_consultations"], 0)
        self.assertEqual(summary["mark_count"], 0)
        self.assertEqual(summary["rejection_rate"], 0.0)
        # All buckets present even when empty so callers can rely on shape.
        for outcome in el.VALID_OUTCOMES:
            self.assertIn(outcome, summary["by_outcome"])
            self.assertEqual(summary["by_outcome"][outcome], 0)

    def test_counts_outcomes_correctly(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            now = time.time()
            _write_rows(path, [
                {"ts": now, "outcome": "mark", "reason": ""},
                {"ts": now, "outcome": "refuse", "reason": ""},
                {"ts": now, "outcome": "refuse", "reason": ""},
                {"ts": now, "outcome": "parse_fail", "reason": ""},
                {"ts": now, "outcome": "unsafe", "reason": ""},
                {"ts": now, "outcome": "llm_none", "reason": ""},
                {"ts": now, "outcome": "empty_headline", "reason": ""},
            ])
            summary = el.summarize_recent(days=30, now=now, path=path)
        self.assertEqual(summary["total_consultations"], 7)
        self.assertEqual(summary["mark_count"], 1)
        # 1 mark out of 7 → rejection_rate = 6/7
        self.assertAlmostEqual(summary["rejection_rate"], 6 / 7, places=4)
        self.assertEqual(summary["by_outcome"]["refuse"], 2)
        self.assertEqual(summary["by_outcome"]["parse_fail"], 1)

    def test_rejection_rate_one_when_no_marks(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            now = time.time()
            _write_rows(path, [
                {"ts": now, "outcome": "refuse", "reason": ""},
                {"ts": now, "outcome": "refuse", "reason": ""},
                {"ts": now, "outcome": "refuse", "reason": ""},
            ])
            summary = el.summarize_recent(days=30, now=now, path=path)
        self.assertEqual(summary["rejection_rate"], 1.0)

    def test_rejection_rate_zero_when_all_marks(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            now = time.time()
            _write_rows(path, [
                {"ts": now, "outcome": "mark", "reason": ""},
                {"ts": now, "outcome": "mark", "reason": ""},
            ])
            summary = el.summarize_recent(days=30, now=now, path=path)
        self.assertEqual(summary["rejection_rate"], 0.0)
        self.assertEqual(summary["mark_count"], 2)

    def test_unknown_outcome_in_log_ignored(self):
        # If a future version adds a new outcome and we read its rows
        # with an older client, we should silently ignore the unknown
        # bucket rather than crash.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "emergent_log.jsonl"
            now = time.time()
            _write_rows(path, [
                {"ts": now, "outcome": "mark", "reason": ""},
                {"ts": now, "outcome": "future_unknown_outcome", "reason": ""},
            ])
            summary = el.summarize_recent(days=30, now=now, path=path)
        # Only the recognized "mark" row counts toward total.
        self.assertEqual(summary["total_consultations"], 1)
        self.assertEqual(summary["mark_count"], 1)


if __name__ == "__main__":
    unittest.main()
