"""Tests for sentinel/activity_tracker.py — Phase 2 of v0.8 sensor refactor.

Focuses on the new APIs added in Phase 2:
  - seconds_since_last_input() — wraps Win32 GetLastInputInfo
  - is_user_idle(threshold)  — boolean idle check
  - current_focus_snapshot() — the spec-shape dict callers consume

Existing pre-Phase-2 behaviour (poll, _get_active_window, daily stats,
get_recent_activity) doesn't have explicit tests yet and isn't in
this PR's scope — but the new tests don't break the existing API.

All tests are hermetic: Win32 calls are mocked, the active-window
fetch is mocked, and the activity log path is redirected to a temp
file so we never touch ~/.hermes.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sentinel.activity_tracker import (
    ActivityTracker, WindowEvent, DEFAULT_IDLE_THRESHOLD_SECS,
)


class TestSecondsSinceLastInput(unittest.TestCase):
    """seconds_since_last_input wraps Win32 GetLastInputInfo. The Win32
    plumbing itself can't be unit-tested headlessly, so we mock the
    ctypes.windll lookup and assert the function's contract:

      - non-Windows → 0.0 (degrade gracefully, treat as 'just had input')
      - Windows + API success → returns elapsed seconds correctly
      - Windows + API failure → 0.0 (don't crash the polling loop)
    """

    def test_non_windows_returns_zero(self):
        with mock.patch.object(sys, "platform", "linux"):
            tracker = ActivityTracker()
            self.assertEqual(tracker.seconds_since_last_input(), 0.0)

    def test_non_windows_means_never_idle(self):
        with mock.patch.object(sys, "platform", "darwin"):
            tracker = ActivityTracker()
            self.assertFalse(tracker.is_user_idle(threshold_secs=60))

    def test_windows_api_failure_returns_zero(self):
        # If the Win32 call raises (driver glitch, restricted token,
        # etc.), we should swallow it and return 0 rather than
        # killing the loop. We mock the GetLastInputInfo function
        # specifically so the exception path actually fires.
        bad_user32 = mock.MagicMock()
        bad_user32.GetLastInputInfo.side_effect = OSError("access denied")
        windll_mock = mock.MagicMock()
        windll_mock.user32 = bad_user32
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("ctypes.windll", windll_mock):
            tracker = ActivityTracker()
            self.assertEqual(tracker.seconds_since_last_input(), 0.0)


class TestIsUserIdle(unittest.TestCase):
    """is_user_idle is a thin wrapper over seconds_since_last_input —
    we just check the threshold logic."""

    def test_below_threshold_not_idle(self):
        tracker = ActivityTracker()
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=10.0):
            self.assertFalse(tracker.is_user_idle(threshold_secs=60))

    def test_at_threshold_is_idle(self):
        # Boundary: >= threshold counts as idle (per ADR-aligned
        # contract — "they've been gone at least N seconds")
        tracker = ActivityTracker()
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=60.0):
            self.assertTrue(tracker.is_user_idle(threshold_secs=60))

    def test_above_threshold_is_idle(self):
        tracker = ActivityTracker()
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=120.0):
            self.assertTrue(tracker.is_user_idle(threshold_secs=60))

    def test_default_threshold_is_one_minute(self):
        # The constant is the ADR-mentioned "subtle but visible"
        # window — if it changes, downstream impulse engine logic
        # may need to retune. Pin it to fail-loud on accidental
        # changes.
        self.assertEqual(DEFAULT_IDLE_THRESHOLD_SECS, 60)


class TestCurrentFocusSnapshot(unittest.TestCase):
    """current_focus_snapshot is the spec-shape dict returned to
    downstream callers (Phase 3 will consume this)."""

    def _tracker_with_window(self, title: str, proc: str) -> ActivityTracker:
        tracker = ActivityTracker()
        # Patch the Win32 active-window fetch instead of calling
        # poll() so we don't have to set up ctypes mocks.
        tracker._get_active_window = mock.Mock(return_value=(title, proc))
        return tracker

    def test_returns_all_spec_fields(self):
        tracker = self._tracker_with_window("Reddit - r/programming", "chrome.exe")
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=5.0):
            snap = tracker.current_focus_snapshot()
        # The施工指示 spec asks for these specific keys.
        for key in ("timestamp", "epoch", "app_name", "process_name",
                    "window_title", "duration_in_focus",
                    "idle_seconds", "is_idle"):
            self.assertIn(key, snap, f"missing key: {key}")

    def test_timestamp_is_iso_8601_z(self):
        tracker = self._tracker_with_window("x", "x.exe")
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=0.0):
            snap = tracker.current_focus_snapshot()
        # YYYY-MM-DDTHH:MM:SS(.ffffff)?Z
        self.assertRegex(snap["timestamp"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

    def test_window_title_and_process_pass_through(self):
        tracker = self._tracker_with_window("YouTube - 標題", "msedge.exe")
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=0.0):
            snap = tracker.current_focus_snapshot()
        self.assertEqual(snap["window_title"], "YouTube - 標題")
        self.assertEqual(snap["process_name"], "msedge.exe")
        # app_name is a Phase-3 placeholder for friendly display name;
        # for now it equals process_name (Phase 3 will swap in a
        # rule + LLM hybrid mapping).
        self.assertEqual(snap["app_name"], "msedge.exe")

    def test_duration_zero_when_window_just_changed(self):
        # Active window differs from the cached _last_window — the
        # tracker hasn't called poll() yet to commit the switch, so
        # duration on the new window is 0.
        tracker = self._tracker_with_window("New window", "new.exe")
        tracker._last_window = "Old window"
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=0.0):
            snap = tracker.current_focus_snapshot()
        self.assertEqual(snap["duration_in_focus"], 0.0)

    def test_duration_grows_when_window_unchanged(self):
        # Same window since 5 seconds ago → duration ~5.
        tracker = self._tracker_with_window("Editor", "code.exe")
        tracker._last_window = "Editor"
        tracker._last_switch_time = time.time() - 5
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=0.0):
            snap = tracker.current_focus_snapshot()
        self.assertAlmostEqual(snap["duration_in_focus"], 5.0, delta=0.5)

    def test_is_idle_flag_reflects_threshold(self):
        tracker = self._tracker_with_window("editor", "code.exe")
        # Below default threshold — active.
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=10.0):
            self.assertFalse(tracker.current_focus_snapshot()["is_idle"])
        # Above default threshold — idle.
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=300.0):
            self.assertTrue(tracker.current_focus_snapshot()["is_idle"])

    def test_active_window_failure_returns_empty_strings(self):
        # If GetForegroundWindow blows up, snapshot still returns
        # something usable — empty strings for the title fields, the
        # rest of the spec keys still present. Polling loop must not
        # crash on a transient Win32 error.
        tracker = ActivityTracker()
        tracker._get_active_window = mock.Mock(side_effect=OSError("flaky"))
        with mock.patch.object(tracker, "seconds_since_last_input",
                               return_value=0.0):
            snap = tracker.current_focus_snapshot()
        self.assertEqual(snap["window_title"], "")
        self.assertEqual(snap["process_name"], "")
        self.assertIn("timestamp", snap)


class TestEventLogIncludesIsIdle(unittest.TestCase):
    """Phase 2 added is_idle to the persisted JSONL row. Phase 4 will
    read it back for activity-history reconstruction."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self.tmp.name) / "sentinel_activity.jsonl"
        self.patcher = mock.patch(
            "sentinel.activity_tracker.ACTIVITY_LOG", self.log_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_log_event_writes_is_idle_field(self):
        tracker = ActivityTracker()
        event = WindowEvent(
            timestamp=time.time(),
            title="some title",
            process_name="proc.exe",
            duration=42.0,
            is_idle=True,
        )
        tracker._log_event(event)

        rows = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)
        data = json.loads(rows[0])
        self.assertIn("is_idle", data)
        self.assertTrue(data["is_idle"])

    def test_poll_records_idle_state_at_switch(self):
        # Force a window change; the previous window's recorded event
        # should carry whatever is_user_idle() returned at switch time.
        tracker = ActivityTracker()
        # Seed a previous window so the next poll() detects a change.
        tracker._last_window = "Editor"
        tracker._last_proc = "code.exe"
        tracker._last_switch_time = time.time() - 10  # >1s, so recorded
        # New window:
        tracker._get_active_window = mock.Mock(return_value=("Browser", "chrome.exe"))
        with mock.patch.object(tracker, "is_user_idle", return_value=True):
            tracker.poll()

        # We just emitted one event for the previous window with
        # is_idle=True.
        self.assertEqual(len(tracker.events), 1)
        self.assertTrue(tracker.events[0].is_idle)
        self.assertEqual(tracker.events[0].title, "Editor")
        # And it was persisted with is_idle in the JSONL row.
        rows = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        data = json.loads(rows[0])
        self.assertTrue(data["is_idle"])


if __name__ == "__main__":
    unittest.main()
