"""Track active window, idle time, and work patterns.

This is pure local observation - no API needed.
Like eyes that are always open, silently recording.
"""
import time
import logging
import ctypes
import ctypes.wintypes
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import json

log = logging.getLogger("sentinel.activity")

ACTIVITY_LOG = Path.home() / ".hermes" / "sentinel_activity.jsonl"


@dataclass
class WindowEvent:
    timestamp: float
    title: str
    process_name: str
    duration: float = 0  # How long user stayed on this window


class ActivityTracker:
    def __init__(self, max_events=500):
        self.events = deque(maxlen=max_events)
        self._last_window = ""
        self._last_switch_time = time.time()
        self._idle_start = None
        self._daily_stats: dict[str, float] = {}  # app_name -> total seconds

    def poll(self):
        """Call this periodically to track active window changes."""
        try:
            title, proc = self._get_active_window()
        except Exception:
            return

        now = time.time()

        # Track idle (no window change + no input for a while)
        if title == self._last_window:
            return

        # Window changed - record the previous one's duration
        duration = now - self._last_switch_time
        if self._last_window and duration > 1:
            event = WindowEvent(
                timestamp=self._last_switch_time,
                title=self._last_window,
                process_name=self._last_proc if hasattr(self, '_last_proc') else "",
                duration=duration,
            )
            self.events.append(event)
            self._log_event(event)

            # Accumulate daily stats
            app = event.process_name or event.title.split(" - ")[-1][:30]
            self._daily_stats[app] = self._daily_stats.get(app, 0) + duration

        self._last_window = title
        self._last_proc = proc
        self._last_switch_time = now

    def _get_active_window(self) -> tuple[str, str]:
        """Get the currently active window title and process name (Windows)."""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", ""

        # Get window title
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        # Get process name
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = ""
        try:
            import psutil
            proc = psutil.Process(pid.value)
            proc_name = proc.name()
        except Exception:
            pass

        return title, proc_name

    def _log_event(self, event: WindowEvent):
        """Append to persistent activity log."""
        try:
            with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "time": event.timestamp,
                    "title": event.title[:100],
                    "process": event.process_name,
                    "duration": round(event.duration, 1),
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def get_recent_activity(self, minutes=30) -> list[WindowEvent]:
        """Get activity from the last N minutes."""
        cutoff = time.time() - (minutes * 60)
        return [e for e in self.events if e.timestamp > cutoff]

    def get_activity_summary(self) -> str:
        """Human-readable summary of recent activity."""
        recent = self.get_recent_activity(30)
        if not recent:
            return ""

        lines = ["=== 使用者活動（最近 30 分鐘）==="]

        # Top apps by time
        app_time: dict[str, float] = {}
        for e in recent:
            app = e.process_name or "unknown"
            app_time[app] = app_time.get(app, 0) + e.duration

        sorted_apps = sorted(app_time.items(), key=lambda x: x[1], reverse=True)
        for app, secs in sorted_apps[:8]:
            mins = secs / 60
            lines.append(f"  {app}: {mins:.1f} 分鐘")

        # Recent window switches (last 10)
        lines.append("\n最近切換：")
        for e in recent[-10:]:
            title_short = e.title[:50]
            lines.append(f"  [{e.process_name}] {title_short} ({e.duration:.0f}s)")

        return "\n".join(lines)

    def get_daily_stats(self) -> dict[str, float]:
        """Get accumulated daily usage stats per app."""
        return dict(self._daily_stats)

    def get_idle_duration(self) -> float:
        """Seconds since last window switch."""
        return time.time() - self._last_switch_time

    def current_app_name(self) -> str:
        """目前使用中的 app 名稱。"""
        return getattr(self, '_last_proc', '') or ''

    def current_app_duration(self) -> float:
        """目前 app 已使用的秒數。"""
        return time.time() - self._last_switch_time

    def get_switch_count(self, minutes: int = 10) -> int:
        """最近 N 分鐘內的視窗切換次數。"""
        cutoff = time.time() - (minutes * 60)
        return sum(1 for e in self.events if e.timestamp > cutoff)
