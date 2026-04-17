"""Track keyboard input and mouse clicks for learning.

Captures what the user types (aggregated into chunks) and where they click.
All data stays local - only AI Slime reads it for distillation.
"""
import sys
import time
import logging
import threading
import json
from pathlib import Path
from collections import deque

log = logging.getLogger("sentinel.input")

INPUT_LOG = Path.home() / ".hermes" / "sentinel_input.jsonl"

# macOS: pynput keyboard listener calls TSMGetInputSourceProperty which
# must run on the main thread — starting it from a daemon thread causes
# dispatch_assert_queue abort. Disable tracking on macOS for now.
_MACOS = sys.platform == "darwin"

if not _MACOS:
    from pynput import keyboard, mouse
    _BREAK_KEYS = {keyboard.Key.enter, keyboard.Key.tab}
else:
    _BREAK_KEYS = set()


class InputTracker:
    def __init__(self):
        self._typing_buffer: list[str] = []
        self._chunks: deque[dict] = deque(maxlen=200)
        self._clicks: deque[dict] = deque(maxlen=200)
        self._last_key_time = 0.0
        self._chunk_start_time = 0.0
        self._kb_listener = None
        self._mouse_listener = None
        self._lock = threading.Lock()
        self._running = False
        # Flush timer - auto-flush if no typing for 5 seconds
        self._flush_timer = None

    def start(self):
        """Start listening for keyboard and mouse events."""
        if self._running:
            return
        self._running = True
        if _MACOS:
            log.info("Input tracking disabled on macOS (pynput main-thread restriction)")
            return
        self._kb_listener = keyboard.Listener(on_press=self._on_key)
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._kb_listener.start()
        self._mouse_listener.start()
        log.info("Input tracking started")

    def stop(self):
        """Stop listening."""
        self._running = False
        if self._kb_listener:
            self._kb_listener.stop()
        if self._mouse_listener:
            self._mouse_listener.stop()
        self._flush_typing()
        log.info("Input tracking stopped")

    def _on_key(self, key):
        now = time.time()

        # Auto-flush if pause > 5 seconds (new thought)
        if self._typing_buffer and (now - self._last_key_time) > 5:
            self._flush_typing()

        if not self._typing_buffer:
            self._chunk_start_time = now

        self._last_key_time = now

        # Convert key to character
        if key in _BREAK_KEYS:
            self._typing_buffer.append("\n")
            self._flush_typing()
            return

        if key == keyboard.Key.space:
            self._typing_buffer.append(" ")
        elif key == keyboard.Key.backspace:
            if self._typing_buffer:
                self._typing_buffer.pop()
        elif hasattr(key, 'char') and key.char:
            self._typing_buffer.append(key.char)

        # Schedule a flush check
        self._schedule_flush()

    def _schedule_flush(self):
        """Schedule auto-flush after 5s of no typing."""
        if self._flush_timer:
            self._flush_timer.cancel()
        self._flush_timer = threading.Timer(5.0, self._flush_typing)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _flush_typing(self):
        """Save accumulated typing as one chunk."""
        with self._lock:
            if not self._typing_buffer:
                return
            text = "".join(self._typing_buffer).strip()
            self._typing_buffer.clear()

        if len(text) < 3:
            return

        chunk = {
            "type": "typing",
            "time": self._chunk_start_time,
            "text": text,
            "duration": time.time() - self._chunk_start_time,
        }
        self._chunks.append(chunk)
        self._log_event(chunk)

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return

        # Get current window context
        try:
            from sentinel.activity_tracker import ActivityTracker
            title, proc = ActivityTracker._get_active_window(None)
        except Exception:
            title, proc = "", ""

        click = {
            "type": "click",
            "time": time.time(),
            "x": x,
            "y": y,
            "button": str(button),
            "window": title[:80],
            "process": proc,
        }
        self._clicks.append(click)
        # Don't log every click - too noisy. Only keep in memory.

    def _log_event(self, event: dict):
        """Append typing chunk to persistent log."""
        try:
            INPUT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(INPUT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def get_typing_summary(self, minutes=30) -> str:
        """Get recent typing activity for distillation."""
        cutoff = time.time() - (minutes * 60)
        recent = [c for c in self._chunks if c["time"] > cutoff]
        if not recent:
            return ""

        lines = ["=== 鍵盤輸入（最近 30 分鐘）==="]
        for chunk in recent[-30:]:  # Last 30 chunks
            text = chunk["text"]
            # Truncate very long chunks
            if len(text) > 200:
                text = text[:200] + "..."
            lines.append(f"  [{chunk.get('duration', 0):.0f}s] {text}")

        return "\n".join(lines)

    def get_click_summary(self, minutes=30) -> str:
        """Get recent click patterns for distillation."""
        cutoff = time.time() - (minutes * 60)
        recent = [c for c in self._clicks if c["time"] > cutoff]
        if not recent:
            return ""

        # Aggregate by process
        proc_clicks: dict[str, int] = {}
        for c in recent:
            proc = c.get("process", "unknown")
            proc_clicks[proc] = proc_clicks.get(proc, 0) + 1

        lines = ["=== 滑鼠點擊（最近 30 分鐘）==="]
        sorted_procs = sorted(proc_clicks.items(), key=lambda x: x[1], reverse=True)
        for proc, count in sorted_procs[:10]:
            lines.append(f"  {proc}: {count} 次")

        return "\n".join(lines)

    def get_full_summary(self, minutes=30) -> str:
        """Combined input summary for distillation."""
        parts = []
        typing = self.get_typing_summary(minutes)
        if typing:
            parts.append(typing)
        clicks = self.get_click_summary(minutes)
        if clicks:
            parts.append(clicks)
        return "\n\n".join(parts)
