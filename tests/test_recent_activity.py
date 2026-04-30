"""Tests for sentinel.recent_activity.

Hermetic — uses temp jsonl files, never reads ~/.hermes.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sentinel.recent_activity as ra


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestBuildBlockEmpty(unittest.TestCase):
    """The "no activity log yet" case must be quiet — empty string,
    not an error. chat.py splices the result raw into the prompt."""

    def test_missing_file_returns_empty(self):
        self.assertEqual(
            ra.build_block(now=time.time(),
                           path=Path("/nonexistent/activity.jsonl")),
            "",
        )

    def test_empty_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity.jsonl"
            path.touch()
            self.assertEqual(
                ra.build_block(now=time.time(), path=path),
                "",
            )

    def test_only_old_rows_returns_empty(self):
        # Rows older than the window — should be filtered out.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity.jsonl"
            now = time.time()
            old = now - 86400  # 1 day ago, way outside 30-min window
            _write_rows(path, [
                {"time": old, "process": "Code.exe",
                 "title": "ancient.py", "duration": 60},
            ])
            self.assertEqual(
                ra.build_block(now=now, window_minutes=30, path=path),
                "",
            )


class TestBuildBlockContent(unittest.TestCase):
    def test_aggregates_by_process(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity.jsonl"
            now = time.time()
            _write_rows(path, [
                {"time": now - 600, "process": "Code.exe",
                 "title": "chat.py", "duration": 300},
                {"time": now - 300, "process": "Code.exe",
                 "title": "test_chat.py", "duration": 200},
                {"time": now - 200, "process": "chrome.exe",
                 "title": "Stack Overflow", "duration": 60},
            ])
            block = ra.build_block(now=now, window_minutes=30, path=path)
        self.assertIn("Code.exe", block)
        self.assertIn("chrome.exe", block)
        # Code.exe should rank first (more time)
        code_pos = block.index("Code.exe")
        chrome_pos = block.index("chrome.exe")
        self.assertLess(code_pos, chrome_pos)
        # Window titles surface
        self.assertIn("chat.py", block)
        self.assertIn("test_chat.py", block)
        self.assertIn("Stack Overflow", block)

    def test_caps_titles_per_process(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity.jsonl"
            now = time.time()
            # 5 unique titles in one process — should only keep first
            # MAX_TITLES_PER_PROCESS in insertion order.
            rows = [
                {"time": now - 60 * (i + 1), "process": "Code.exe",
                 "title": f"file_{i}.py", "duration": 30}
                for i in range(5)
            ]
            _write_rows(path, rows)
            block = ra.build_block(now=now, window_minutes=30, path=path)
        # Only first MAX_TITLES_PER_PROCESS should appear.
        for i in range(ra.MAX_TITLES_PER_PROCESS):
            self.assertIn(f"file_{i}.py", block)
        for i in range(ra.MAX_TITLES_PER_PROCESS, 5):
            self.assertNotIn(f"file_{i}.py", block)

    def test_caps_processes_shown(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity.jsonl"
            now = time.time()
            # 7 different processes — should only keep top
            # MAX_PROCESSES_SHOWN by total duration.
            rows = [
                {"time": now - 60, "process": f"app_{i}.exe",
                 "title": f"t{i}", "duration": 1}
                for i in range(7)
            ]
            _write_rows(path, rows)
            block = ra.build_block(now=now, window_minutes=30, path=path)
        kept = sum(1 for i in range(7) if f"app_{i}.exe" in block)
        self.assertEqual(kept, ra.MAX_PROCESSES_SHOWN)

    def test_corrupt_lines_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity.jsonl"
            now = time.time()
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "time": now, "process": "Code.exe",
                    "title": "real.py", "duration": 60,
                }) + "\n")
                f.write("not json\n")
                f.write('{"missing_time": true}\n')
                f.write('{"time": "string-not-number"}\n')
            block = ra.build_block(now=now, window_minutes=30, path=path)
        self.assertIn("Code.exe", block)
        self.assertIn("real.py", block)

    def test_long_title_truncated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity.jsonl"
            now = time.time()
            long_title = "x" * 5000
            _write_rows(path, [
                {"time": now, "process": "Code.exe",
                 "title": long_title, "duration": 60},
            ])
            block = ra.build_block(now=now, window_minutes=30, path=path)
        # Block should contain a truncated version, not the full 5000.
        self.assertNotIn("x" * (ra.TITLE_MAX_CHARS + 50), block)

    def test_block_format_includes_window_size(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity.jsonl"
            now = time.time()
            _write_rows(path, [
                {"time": now, "process": "Code.exe",
                 "title": "chat.py", "duration": 60},
            ])
            block = ra.build_block(now=now, window_minutes=45, path=path)
        # The header should reflect the requested window so chat
        # context reads accurately.
        self.assertIn("最近 45 分鐘", block)


class TestZeroDurationRows(unittest.TestCase):
    """Some tracker entries lack duration. Should not crash; just
    contribute zero seconds (so the row's process won't rise to top
    rank but its title still appears if the process gets time
    elsewhere)."""

    def test_missing_duration_handled(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity.jsonl"
            now = time.time()
            _write_rows(path, [
                {"time": now, "process": "Code.exe", "title": "x.py"},
                {"time": now, "process": "Code.exe", "title": "y.py",
                 "duration": "not a number"},
                {"time": now, "process": "Code.exe", "title": "z.py",
                 "duration": 30},
            ])
            block = ra.build_block(now=now, window_minutes=30, path=path)
        self.assertIn("Code.exe", block)


if __name__ == "__main__":
    unittest.main()
