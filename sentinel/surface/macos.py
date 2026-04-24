"""macOS stub for the Surface protocol.

Real implementations require either pyobjc (heavy, Accessibility API)
or osascript calls (lighter but limited). For now we ship a stub so
Mac launches succeed; the methods return the uniform "not_supported"
shape so callers degrade gracefully.

Implementing this lands in a later phase — open_path and
take_screenshot are the cheapest to port (os.system open / PIL
ImageGrab already works on Mac) if a Mac user wants to contribute.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from sentinel.surface.base import Surface

log = logging.getLogger("sentinel.surface.macos")


class MacSurface(Surface):
    """macOS Surface — minimal useful subset using `open` command.

    Deliberately does not implement focus_window, list_windows, or
    clipboard — those need Accessibility API + TCC permission dance
    we haven't tackled yet. open_path + take_screenshot work with
    just stdlib + PIL which is what we have.
    """
    platform = "macos"

    def open_path(self, path: str) -> dict:
        target = path or ""
        if not target:
            return {"ok": False, "error": "empty_path"}
        try:
            # `open` is the Mac "double-click equivalent": files open
            # in their registered app, URLs launch the default browser.
            subprocess.run(["open", target], check=True, capture_output=True)
            return {"ok": True, "path": target}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "error": e.stderr.decode(errors="replace")}
        except FileNotFoundError:
            return {"ok": False, "error": "open command not found"}

    def take_screenshot(self, out_path: Optional[str] = None) -> dict:
        """Use PIL if available (needs Screen Recording permission) or
        the stdlib `screencapture` binary (also needs the permission
        but the system prompt is clearer)."""
        if out_path is None:
            ts = int(time.time())
            out_path = str(Path(tempfile.gettempdir()) / f"aislime_screen_{ts}.png")
        try:
            # -x = no sound, -t png = format. Quiet if permission granted.
            subprocess.run(
                ["screencapture", "-x", "-t", "png", out_path],
                check=True, capture_output=True,
            )
            return {"ok": True, "path": out_path}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "error": e.stderr.decode(errors="replace")}
        except FileNotFoundError:
            return {"ok": False, "error": "screencapture not found"}
