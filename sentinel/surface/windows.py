"""Windows implementation of the Surface protocol.

Uses stdlib-only facilities where possible (ctypes for win32 APIs,
subprocess for shell integration, PIL for screenshots — already a
hard dependency for screen_watcher). No new third-party dependency.

Every primitive returns the `{"ok": bool, ...}` contract defined in
Surface so they can be registered as ACTION handlers unchanged.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import tempfile
import time
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from sentinel.surface.base import Surface, WindowInfo

log = logging.getLogger("sentinel.surface.windows")


# ── Win32 setup ────────────────────────────────────────────────────
# Resolve DLLs and function signatures once at module load. Keeps
# each primitive fast (no signature resolution per call) and makes
# typing explicit so wrong-type args raise immediately.

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_EnumWindows = _user32.EnumWindows
_EnumWindowsProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
)
_EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
_EnumWindows.restype = wintypes.BOOL

_GetWindowTextLengthW = _user32.GetWindowTextLengthW
_GetWindowTextLengthW.argtypes = [wintypes.HWND]
_GetWindowTextLengthW.restype = ctypes.c_int

_GetWindowTextW = _user32.GetWindowTextW
_GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_GetWindowTextW.restype = ctypes.c_int

_IsWindowVisible = _user32.IsWindowVisible
_IsWindowVisible.argtypes = [wintypes.HWND]
_IsWindowVisible.restype = wintypes.BOOL

_SetForegroundWindow = _user32.SetForegroundWindow
_SetForegroundWindow.argtypes = [wintypes.HWND]
_SetForegroundWindow.restype = wintypes.BOOL

_ShowWindow = _user32.ShowWindow
_ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_ShowWindow.restype = wintypes.BOOL
_SW_RESTORE = 9

_IsIconic = _user32.IsIconic   # True if window is minimized
_IsIconic.argtypes = [wintypes.HWND]
_IsIconic.restype = wintypes.BOOL

_GetWindowThreadProcessId = _user32.GetWindowThreadProcessId
_GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_GetWindowThreadProcessId.restype = wintypes.DWORD


def _process_name_for_pid(pid: int) -> str:
    """Best-effort lookup of the executable name for a PID.

    Uses psutil if already imported (it's in our deps), falls back to
    empty string on any error. We surface this in WindowInfo so
    callers can match on process rather than fragile title strings,
    but it's not critical — listing still works without it.
    """
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


class WindowsSurface(Surface):
    """Win32-backed Surface primitives."""

    platform = "windows"

    # ── list_windows ───────────────────────────────────────────────

    def list_windows(self) -> dict:
        results: list[WindowInfo] = []

        def _enum_proc(hwnd: int, _lparam: int) -> bool:
            if not _IsWindowVisible(hwnd):
                return True  # keep iterating
            length = _GetWindowTextLengthW(hwnd)
            if length == 0:
                # Windows with no title are almost always toolwindows /
                # hidden helpers that wouldn't be useful to return.
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            _GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

            pid = wintypes.DWORD()
            _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            results.append(WindowInfo(
                handle=int(hwnd),
                title=title,
                process_name=_process_name_for_pid(pid.value),
                is_visible=True,
            ))
            return True

        ok = bool(_EnumWindows(_EnumWindowsProc(_enum_proc), 0))
        return {
            "ok": ok,
            "windows": [asdict(w) for w in results],
            "count": len(results),
        }

    # ── focus_window ───────────────────────────────────────────────

    def focus_window(self, title_match: str) -> dict:
        """Substring match, case-insensitive. First match wins."""
        if not title_match:
            return {"ok": False, "error": "empty_title_match"}
        needle = title_match.lower()

        listed = self.list_windows().get("windows", [])
        match = next(
            (w for w in listed if needle in (w.get("title") or "").lower()),
            None,
        )
        if match is None:
            return {"ok": False, "error": "no_match", "title_match": title_match}

        hwnd = wintypes.HWND(match["handle"])
        # If minimized, SetForegroundWindow alone won't restore it.
        if _IsIconic(hwnd):
            _ShowWindow(hwnd, _SW_RESTORE)
        # Windows blocks SetForegroundWindow from non-foreground apps
        # unless certain conditions are met. Best-effort: try it, check
        # whether it actually took. In the worst case the window is
        # flashed in the taskbar which is still a user-visible signal.
        ok = bool(_SetForegroundWindow(hwnd))
        return {
            "ok": ok,
            "matched_title": match["title"],
            "handle": match["handle"],
        }

    # ── clipboard ──────────────────────────────────────────────────
    # Use Tk's built-in clipboard for portability without adding a
    # pywin32 dep. Tk ships with stdlib Python. We destroy the root
    # window immediately so no stray GUI element leaks.

    def _tk_clipboard(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        return root

    def get_clipboard(self) -> dict:
        try:
            root = self._tk_clipboard()
            try:
                text = root.clipboard_get()
            except Exception:
                # clipboard_get raises if clipboard is empty or non-text
                text = ""
            root.destroy()
            return {"ok": True, "text": text}
        except Exception as e:
            log.warning(f"get_clipboard failed: {e}")
            return {"ok": False, "error": str(e)}

    def set_clipboard(self, text: str) -> dict:
        try:
            root = self._tk_clipboard()
            root.clipboard_clear()
            root.clipboard_append(text)
            # Tk clipboard content is cleared when the root is destroyed
            # UNLESS we explicitly update first — this flushes to the
            # system clipboard so it survives root teardown.
            root.update()
            root.destroy()
            return {"ok": True, "length": len(text)}
        except Exception as e:
            log.warning(f"set_clipboard failed: {e}")
            return {"ok": False, "error": str(e)}

    # ── take_screenshot ────────────────────────────────────────────

    def take_screenshot(self, out_path: Optional[str] = None) -> dict:
        """Full-screen PNG via PIL.ImageGrab (already in deps)."""
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
        except Exception as e:
            return {"ok": False, "error": f"grab_failed: {e}"}

        if out_path is None:
            # Use a timestamped temp path. Callers can move/delete as
            # they wish. We don't auto-clean because we don't know the
            # caller's intent (might want to display it later).
            ts = int(time.time())
            out_path = str(Path(tempfile.gettempdir()) / f"aislime_screen_{ts}.png")

        try:
            img.save(out_path, "PNG")
        except Exception as e:
            return {"ok": False, "error": f"save_failed: {e}"}
        return {
            "ok": True,
            "path": out_path,
            "size": [img.width, img.height],
        }

    # ── open_path ──────────────────────────────────────────────────

    def open_path(self, path: str) -> dict:
        """Launch a file / folder / URL with the OS default handler.

        Uses os.startfile which is the Explorer-double-click equivalent
        on Windows. subprocess("start", path) would work too but
        requires shell=True and gets confused by URLs.
        """
        target = path or ""
        if not target:
            return {"ok": False, "error": "empty_path"}

        # Absolute path resolution for display purposes in the audit
        # log — os.startfile itself accepts relative and URL-shaped
        # inputs unchanged.
        resolved = target
        try:
            if os.path.exists(target):
                resolved = str(Path(target).resolve())
        except Exception:
            # Non-path things (mailto:, http://) don't have a
            # filesystem interpretation; that's fine.
            pass

        try:
            os.startfile(resolved)
            return {"ok": True, "path": resolved}
        except OSError as e:
            return {"ok": False, "error": str(e), "path": resolved}

    # ── open_url ───────────────────────────────────────────────────

    def open_url(self, url: str) -> dict:
        """Launch URL in the default browser via webbrowser module.

        webbrowser.open spawns a browser without blocking and handles
        the OS defaults correctly. We use it (rather than os.startfile
        or subprocess("start", ...)) because it's cross-platform and
        does the right thing on Windows — no shell=True, no URL
        quoting games.
        """
        if not url:
            return {"ok": False, "error": "empty_url"}
        try:
            import webbrowser
            ok = webbrowser.open(url, new=2)  # new=2 → new tab if possible
            return {"ok": bool(ok), "url": url}
        except Exception as e:
            return {"ok": False, "error": str(e), "url": url}
