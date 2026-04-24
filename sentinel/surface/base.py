"""Surface protocol — the abstract vocabulary of host-machine actions.

Every platform-specific Surface subclass (Windows/Mac/Linux) inherits
from this class and overrides only the primitives it can support.
Callers use `surface.supports(name)` before calling to handle graceful
degradation.

Primitive return shapes are intentionally uniform: every call returns
a dict with at minimum `{"ok": bool}` plus primitive-specific fields.
This matches the ACTION handler contract so surface methods can be
registered as handlers with zero adapter code.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("sentinel.surface")


@dataclass
class WindowInfo:
    """Platform-neutral description of a visible window.

    `handle` is a platform-opaque identifier — on Windows it's the
    HWND as int, on macOS it's the AX element ref, on Linux the
    XID. Callers shouldn't interpret it; they pass it back to the
    surface to act on a specific window.
    """
    handle: int
    title: str
    process_name: str = ""
    is_visible: bool = True


class Surface:
    """Abstract base. Subclasses override the primitives they implement.

    The default behavior of every primitive is "not supported" — returns
    {"ok": False, "error": "not_supported", "primitive": "..."} — so a
    caller that forgot to check `supports()` gets a structured no-op
    instead of a crash. This matches how ACTION handlers report errors
    through the approval audit log.

    Ordering in this class reflects risk level: read-only primitives
    first, state-changing last. The safety/policy layer can apply
    tighter rules to anything below `list_windows`.
    """

    # Name of the concrete implementation, used in logs / audit.
    platform: str = "abstract"

    # ── Capability probe ──────────────────────────────────────────

    def supports(self, primitive: str) -> bool:
        """Return True if this Surface actually implements the primitive.

        Default detection: the subclass has overridden the method (so
        its __func__ is not Surface's). Subclasses with conditional
        support (e.g. Linux with X11 vs Wayland) should override this
        to return per-runtime availability.
        """
        method = getattr(self, primitive, None)
        if method is None:
            return False
        base_method = getattr(Surface, primitive, None)
        if base_method is None:
            return False
        return getattr(method, "__func__", None) is not getattr(
            base_method, "__func__", None
        )

    # ── Read-only primitives (safe, low-policy) ───────────────────

    def list_windows(self) -> dict:
        """Enumerate visible windows.

        Returns {"ok": bool, "windows": list[WindowInfo-as-dict]}.
        Read-only: listing windows has no side effect. Pre-action
        reconnaissance for focus_window / typing targets lives here.
        """
        return _not_supported("list_windows")

    def get_clipboard(self) -> dict:
        """Read clipboard text.

        Returns {"ok": bool, "text": str}. Note that clipboard access
        may be restricted by the OS (e.g. macOS permission prompts);
        callers should handle ok=False gracefully.
        """
        return _not_supported("get_clipboard")

    def take_screenshot(self, out_path: Optional[str] = None) -> dict:
        """Capture full-screen PNG to disk.

        If out_path is None, writes to a temp file. Returns
        {"ok": bool, "path": str}. Already used by screen_watcher for
        the "千里眼" feature — exposed here so action handlers can
        reuse the same primitive.
        """
        return _not_supported("take_screenshot")

    # ── State-changing primitives (higher-policy, user-visible) ──

    def open_path(self, path: str) -> dict:
        """Open a file or app using the OS default handler.

        Returns {"ok": bool, "path": str}. The handler is the same as
        double-clicking — whatever the user has associated with the
        extension. Launching a .py opens the editor; launching a
        folder opens the file browser.

        Policy concerns (enforced in handlers.py):
          - Path must exist
          - Path must live under the user's home directory unless
            explicitly whitelisted — no opening arbitrary system files
        """
        return _not_supported("open_path")

    def focus_window(self, title_match: str) -> dict:
        """Bring the first window whose title substring-matches to front.

        Returns {"ok": bool, "matched_title": str, "handle": int}.
        Title matching is case-insensitive. If multiple windows match,
        the most-recently-active wins. If none match, ok=False.

        Policy concerns: none — focusing a window is inherently
        user-visible, the user sees what's happening and can ignore
        or switch back.
        """
        return _not_supported("focus_window")

    def set_clipboard(self, text: str) -> dict:
        """Write text to the clipboard.

        Returns {"ok": bool}. Overwrites existing clipboard content.
        Callers that need "append" semantics must read first.

        Policy concerns: the clipboard is shared with every app; writing
        to it is a small but real information leak if text contains
        sensitive content. Policies in handlers.py reject non-UTF-8
        content and cap at 100KB.
        """
        return _not_supported("set_clipboard")

    def open_url(self, url: str) -> dict:
        """Open a web URL in the user's default browser.

        Returns {"ok": bool, "url": str}. Distinct from open_path
        because URLs and filesystem paths have different safety
        concerns (phishing links vs. executable files), different
        schemes to validate (http/https vs. drive letters), and the
        LLM chose poorly when it had only open_path and tried to
        treat "YouTube" as a .app on disk.

        Policy concerns (enforced in handlers.py):
          - Scheme must be http or https
          - No javascript:, data:, file://, chrome://, etc.
          - URL length cap to avoid smuggling payloads in query strings
        """
        return _not_supported("open_url")


class DryRunSurface(Surface):
    """A Surface that logs intent but performs no real action.

    Used when:
      - AISLIME_DRY_RUN=1 env var is set (tests, debugging)
      - The platform-specific Surface failed to import
      - User explicitly opted out of action capabilities

    All primitives return ok=True so code paths downstream don't
    treat "dry run" as "failed". The intent is logged at INFO for
    traceability.
    """
    platform = "dry-run"

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        log.info(f"DryRunSurface active ({reason})")

    def _log_call(self, name: str, **kwargs) -> dict:
        log.info(f"[dry-run] {name}({kwargs})")
        return {"ok": True, "dry_run": True, "primitive": name, **kwargs}

    def list_windows(self) -> dict:
        return self._log_call("list_windows", windows=[])

    def get_clipboard(self) -> dict:
        return self._log_call("get_clipboard", text="")

    def take_screenshot(self, out_path: Optional[str] = None) -> dict:
        return self._log_call("take_screenshot", path=out_path or "")

    def open_path(self, path: str) -> dict:
        return self._log_call("open_path", path=path)

    def focus_window(self, title_match: str) -> dict:
        return self._log_call("focus_window", title_match=title_match)

    def set_clipboard(self, text: str) -> dict:
        return self._log_call("set_clipboard", text_len=len(text))

    def open_url(self, url: str) -> dict:
        return self._log_call("open_url", url=url)


def _not_supported(primitive: str) -> dict:
    """Uniform failure return for primitives the platform doesn't
    implement. Callers see a structured error instead of a crash."""
    return {"ok": False, "error": "not_supported", "primitive": primitive}
