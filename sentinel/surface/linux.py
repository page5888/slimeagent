"""Linux stub for the Surface protocol.

X11 (xdotool/pygetwindow) vs Wayland changes the implementation path;
we don't pick a side until there's a real Linux user asking. For now
open_path + take_screenshot work via `xdg-open` and `scrot` / PIL.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from sentinel.surface.base import Surface

log = logging.getLogger("sentinel.surface.linux")


class LinuxSurface(Surface):
    platform = "linux"

    def open_path(self, path: str) -> dict:
        target = path or ""
        if not target:
            return {"ok": False, "error": "empty_path"}
        try:
            subprocess.run(["xdg-open", target], check=True, capture_output=True)
            return {"ok": True, "path": target}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "error": e.stderr.decode(errors="replace")}
        except FileNotFoundError:
            return {"ok": False, "error": "xdg-open not available"}

    def open_url(self, url: str) -> dict:
        if not url:
            return {"ok": False, "error": "empty_url"}
        try:
            import webbrowser
            ok = webbrowser.open(url, new=2)
            return {"ok": bool(ok), "url": url}
        except Exception as e:
            return {"ok": False, "error": str(e), "url": url}

    def take_screenshot(self, out_path: Optional[str] = None) -> dict:
        if out_path is None:
            ts = int(time.time())
            out_path = str(Path(tempfile.gettempdir()) / f"aislime_screen_{ts}.png")
        # Try PIL first (works on X11 without a subprocess), fall back
        # to scrot (common) or gnome-screenshot.
        try:
            from PIL import ImageGrab
            ImageGrab.grab().save(out_path, "PNG")
            return {"ok": True, "path": out_path, "method": "pil"}
        except Exception:
            pass
        for cmd in (["scrot", out_path], ["gnome-screenshot", "-f", out_path]):
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                return {"ok": True, "path": out_path, "method": cmd[0]}
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue
        return {"ok": False, "error": "no screenshot tool available"}
