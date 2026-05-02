"""Track active window, idle time, and work patterns.

This is pure local observation - no API needed.
Like eyes that are always open, silently recording.

Phase 2 of v0.8 sensor refactor (2026-05-02): the spec asks for a
periodic snapshot of "what is the master doing right now" with
shape:

    {
      timestamp, app_name, process_name, window_title,
      duration_in_focus, is_idle,
    }

The pre-Phase-2 tracker already covered window title + process name +
focus duration, polled every ~2s by the GUI daemon loop. Phase 2
adds: (a) input-idle detection via Win32 GetLastInputInfo, (b) a
single `current_focus_snapshot()` method that returns the spec dict,
(c) `is_idle` recorded into the JSONL event log so Phase 4 can
filter "what was the master doing while not idle".

Linux / macOS path: GetLastInputInfo is Windows-only, so
seconds_since_last_input() falls back to 0 on other platforms (i.e.
"never idle"). Slimeagent is Windows-by-design today (per gui.py's
restart and start.bat), but we keep the fallback so unit tests can
run cross-platform under offscreen Qt.
"""
import sys
import time
import logging
import ctypes
import ctypes.wintypes
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
import json
from datetime import datetime, timezone

log = logging.getLogger("sentinel.activity")

ACTIVITY_LOG = Path.home() / ".hermes" / "sentinel_activity.jsonl"

# How long without keyboard / mouse input before we call the master
# "idle". 60s is conservative — the master can pause to read or
# think for almost a minute without us flagging them as away. Phase
# 5 may tune this when it redesigns the impulse engine.
DEFAULT_IDLE_THRESHOLD_SECS = 60


@dataclass
class WindowEvent:
    timestamp: float
    title: str
    process_name: str
    duration: float = 0  # How long user stayed on this window
    is_idle: bool = False  # Was the master idle when this event fired?


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
            # is_idle reflects state at the moment of *the switch*, not
            # of the prior window's whole lifetime. A window the master
            # walked away from for 30 minutes is still recorded as that
            # window for those 30 minutes — but the event's is_idle
            # tells the reader that the master was already away by the
            # time they came back and switched. Phase 5's impulse
            # engine cares about this distinction.
            event = WindowEvent(
                timestamp=self._last_switch_time,
                title=self._last_window,
                process_name=self._last_proc if hasattr(self, '_last_proc') else "",
                duration=duration,
                is_idle=self.is_user_idle(),
            )
            self.events.append(event)
            self._log_event(event)

            # Accumulate daily stats
            app = event.process_name or event.title.split(" - ")[-1][:30]
            self._daily_stats[app] = self._daily_stats.get(app, 0) + duration

        # INFO log on switch — Phase 2 acceptance criterion: 0xspeter
        # can see "Slime 知道我在看 X 網頁了" in the main log without
        # tailing the JSONL. Truncate the title so a giant Reddit URL
        # doesn't dominate one line.
        title_short = (title or "(untitled)")[:80]
        log.info("active window: [%s] %s", proc or "?", title_short)

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

    def seconds_since_last_input(self) -> float:
        """Seconds since the last keyboard / mouse / touch input.

        Win32 GetLastInputInfo is the canonical idle-detection API on
        Windows — it returns the tick count of the last input event
        from any source, including programmatic input. Returns 0 on
        non-Windows ("never idle") so cross-platform code paths and
        tests degrade gracefully rather than crashing on
        ctypes.windll access.

        Wrapped in try/except: a Win32 API call failing should not
        kill the polling loop; we just return 0 (treat as 'just had
        input') so the master is presumed active when we can't tell.
        """
        if sys.platform != "win32":
            return 0.0
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.wintypes.UINT),
                    ("dwTime", ctypes.wintypes.DWORD),
                ]

            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if not user32.GetLastInputInfo(ctypes.byref(info)):
                return 0.0

            # GetTickCount wraps every ~49.7 days (32-bit ms counter).
            # The (now - last) subtraction handles wrap correctly as
            # long as both values are within the same wrap cycle, which
            # they always are because last is by definition recent.
            now_ticks = kernel32.GetTickCount()
            elapsed_ms = (now_ticks - info.dwTime) & 0xFFFFFFFF
            return elapsed_ms / 1000.0
        except Exception:
            return 0.0

    def is_user_idle(self, threshold_secs: float = DEFAULT_IDLE_THRESHOLD_SECS) -> bool:
        """True if the master hasn't given input for `threshold_secs`."""
        return self.seconds_since_last_input() >= threshold_secs

    def current_focus_snapshot(
        self, idle_threshold_secs: float = DEFAULT_IDLE_THRESHOLD_SECS,
    ) -> dict:
        """Return the spec-shape snapshot of where the master is right now.

        Phase 2 of v0.8 sensor refactor — the input feed for downstream
        semantic understanding (Phase 3) and activity-track storage
        (Phase 4). Cheap to call: one Win32 query + one local read of
        the cached _last_window. Safe to call every poll tick.

        timestamp is ISO-8601 in UTC (Z-suffixed) so log lines are
        sortable and timezone-unambiguous when read back later.
        """
        try:
            cur_title, cur_proc = self._get_active_window()
        except Exception:
            cur_title, cur_proc = "", ""

        now = time.time()
        # If the active window matches what we last observed, focus
        # has been continuous since _last_switch_time. If they differ
        # but poll() hasn't been called yet to commit the switch,
        # _last_switch_time still points at the previous window's
        # entry — duration is the upcoming new window's age, which is
        # ~0. Both readings are coherent for callers that just want
        # "how long has the master been on whatever they're on".
        if cur_title and cur_title == self._last_window:
            duration = now - self._last_switch_time
        else:
            duration = 0.0

        idle_secs = self.seconds_since_last_input()

        return {
            "timestamp": datetime.fromtimestamp(now, tz=timezone.utc)
                            .isoformat().replace("+00:00", "Z"),
            "epoch": now,
            "app_name": cur_proc,           # Phase 3 will map .exe → friendly name
            "process_name": cur_proc,
            "window_title": cur_title,
            "duration_in_focus": round(duration, 1),
            "idle_seconds": round(idle_secs, 1),
            "is_idle": idle_secs >= idle_threshold_secs,
        }

    def _log_event(self, event: WindowEvent):
        """Append to persistent activity log.

        Schema gained `is_idle` in Phase 2 — Phase 4 will filter on it
        when reconstructing master-activity history. Pre-Phase-2 rows
        without the field are read with default `False` by recent-
        activity readers (their .get() calls supply the default).
        """
        try:
            with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "time": event.timestamp,
                    "title": event.title[:100],
                    "process": event.process_name,
                    "duration": round(event.duration, 1),
                    "is_idle": event.is_idle,
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
