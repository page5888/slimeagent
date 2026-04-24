"""Surface — platform-abstracted primitives for action handlers.

Why this exists
---------------
Phase C1 gave us a generic approval queue that can dispatch any
registered action handler. Phase C2 (this package) gives those
handlers a **portable vocabulary of side-effects** to carry out on
the host machine: opening a file, focusing a window, reading the
clipboard, taking a screenshot, etc.

The split is deliberate:

  - `sentinel.growth.approval` handles consent (policy → user
    decision → audit trail).
  - `sentinel.surface` handles execution (what actually happens on
    Windows vs macOS vs Linux).

Neither knows about the other by default. They're wired together in
`sentinel/surface/handlers.py`, which registers each primitive as an
ACTION handler with an appropriate policy check. Phase D (computer-
use) will add the slime's *vocabulary* of actions on top of this
mechanical plumbing.

Inspired by moeru-ai/airi's `services/computer-use-mcp/src/executors`
pattern, but intentionally narrower for v1 — we ship with only the
primitives we can ship safely:

  open_path        — launch a file/app via the OS default handler
  focus_window     — bring a window with matching title to front
  list_windows     — enumerate visible windows (read-only)
  take_screenshot  — capture full screen to a PNG path
  get_clipboard    — read clipboard text
  set_clipboard    — write clipboard text

Deliberately NOT included in v1:
  type_text        — keyboard injection (Phase D, with per-window scope)
  click_at         — pixel-coordinate mouse click (too risky for first cut)
  kill_process     — stopping arbitrary processes
  write_file       — we already have direct file APIs for our own state

Usage
-----
    from sentinel.surface import get_surface
    surface = get_surface()
    if surface.supports("focus_window"):
        surface.focus_window("Visual Studio")

…but callers that want user consent route through the approval queue:

    from sentinel.growth import submit_action
    submit_action(
        "surface.focus_window",
        title="切到 VS Code",
        reason="使用者在聊天時請我幫忙",
        payload={"title_match": "Visual Studio"},
    )

The submit goes through policy, lands in pending/, and only runs via
the registered handler if the user approves.
"""
from __future__ import annotations

import sys
from typing import Optional

from sentinel.surface.base import Surface, DryRunSurface, WindowInfo

_surface: Optional[Surface] = None


def get_surface() -> Surface:
    """Return the appropriate Surface instance for this platform.

    Lazy singleton — we don't import heavy platform-specific modules
    unless something actually asks. DryRunSurface is the fallback
    for unsupported platforms (or when explicitly requested via
    AISLIME_DRY_RUN env var for testing).
    """
    global _surface
    if _surface is not None:
        return _surface

    import os
    if os.environ.get("AISLIME_DRY_RUN") == "1":
        _surface = DryRunSurface(reason="AISLIME_DRY_RUN=1 set")
        return _surface

    try:
        if sys.platform == "win32":
            from sentinel.surface.windows import WindowsSurface
            _surface = WindowsSurface()
        elif sys.platform == "darwin":
            from sentinel.surface.macos import MacSurface
            _surface = MacSurface()
        elif sys.platform.startswith("linux"):
            from sentinel.surface.linux import LinuxSurface
            _surface = LinuxSurface()
        else:
            _surface = DryRunSurface(reason=f"unsupported platform {sys.platform}")
    except Exception as e:
        # A partial / broken platform import shouldn't crash the app.
        # Fall back to dry-run and let the user see the warning.
        import logging
        logging.getLogger("sentinel.surface").warning(
            f"Surface init failed on {sys.platform}: {e}; using DryRunSurface"
        )
        _surface = DryRunSurface(reason=f"init failed: {e}")
    return _surface


__all__ = [
    "get_surface",
    "Surface",
    "DryRunSurface",
    "WindowInfo",
]
