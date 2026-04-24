"""Surface ↔ approval queue wiring.

Registers every Surface primitive as an ACTION handler in
`sentinel.growth.approval`, each with:
  - a `surface.<primitive>` action_type (stable, greppable)
  - a **policy check** that runs at submit-time
  - an **executor** that runs at approve-time

The policy functions are the only place user-facing safety rules live
for these primitives. Keeping them here (rather than spreading them
across callers) means when we later tighten a rule — e.g. "open_path
must stay inside the workspace" — there's one file to change.

Call `register_all()` once at process startup. Idempotent: re-calling
it just re-registers, which is useful during live development.

Action types (after register_all):
  surface.list_windows       — no policy (read-only)
  surface.focus_window       — required: title_match string
  surface.get_clipboard      — no policy (read-only)
  surface.set_clipboard      — max 100KB, UTF-8 only
  surface.take_screenshot    — no policy (already allowed by existing
                               千里眼 feature; reuses same primitive)
  surface.open_path          — path must exist AND live under a
                               whitelisted root (user home + explicit
                               WORKSPACE_ROOTS from config)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sentinel.growth import approval as _approval
from sentinel.surface import get_surface

log = logging.getLogger("sentinel.surface.handlers")

# Policy-allowed roots for open_path. Starts with the user's home; can
# be extended at runtime (e.g. config.WORKSPACE_ROOTS) by
# `extend_open_path_roots()`. Conservative default: opening anything
# outside ~ is rejected at submit time, so the user doesn't see a
# proposal offering to launch arbitrary system files.
_OPEN_PATH_ROOTS: list[Path] = [Path.home().resolve()]


def extend_open_path_roots(paths) -> None:
    """Add additional directories to the open_path whitelist.

    Callers can expand the policy at runtime — e.g. from a settings
    tab or a per-project config. Non-existent directories are ignored
    silently (we check at validation time, not here).
    """
    for p in paths:
        try:
            resolved = Path(p).resolve()
        except Exception:
            continue
        if resolved not in _OPEN_PATH_ROOTS:
            _OPEN_PATH_ROOTS.append(resolved)


# ── Policy functions ──────────────────────────────────────────────
# Signature contract: (payload: dict) -> tuple[bool, list[dict]].
# Each finding: {"level": "warn"|"error"|"info", "msg": str}.


def _policy_focus_window(payload: dict) -> tuple[bool, list[dict]]:
    title = (payload or {}).get("title_match") or ""
    if not isinstance(title, str) or not title.strip():
        return False, [{
            "level": "error",
            "msg": "title_match must be a non-empty string",
        }]
    if len(title) > 200:
        return False, [{
            "level": "error",
            "msg": "title_match too long (max 200 chars)",
        }]
    return True, []


def _policy_set_clipboard(payload: dict) -> tuple[bool, list[dict]]:
    text = (payload or {}).get("text")
    if not isinstance(text, str):
        return False, [{
            "level": "error",
            "msg": "text must be a string",
        }]
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        return False, [{
            "level": "error",
            "msg": "text must be UTF-8 encodable",
        }]
    if len(encoded) > 100 * 1024:
        return False, [{
            "level": "error",
            "msg": f"text too large ({len(encoded)} bytes; max 100KB)",
        }]
    return True, []


# URL schemes we'll allow the slime to open on the user's browser.
# Explicit allowlist — everything else (javascript:, data:, file://,
# chrome://, about:, view-source:, …) is refused. User-facing
# behaviors like phishing mitigations are the browser's job; ours is
# to keep the slime from being tricked into executing local
# javascript schemes or exfiltrating local files via file://.
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# Enough room for an OAuth callback URL with query params but not
# for smuggling a multi-KB payload.
_MAX_URL_LEN = 2048


def _policy_open_url(payload: dict) -> tuple[bool, list[dict]]:
    """Validate URL shape + scheme. Adds a warn-level finding for
    every URL so the user sees the exact target on the approval
    card before clicking — protects against the LLM paraphrasing
    'YouTube' as a URL the user didn't expect.
    """
    url = (payload or {}).get("url")
    if not isinstance(url, str) or not url.strip():
        return False, [{"level": "error",
                        "msg": "url must be a non-empty string"}]
    url = url.strip()
    if len(url) > _MAX_URL_LEN:
        return False, [{
            "level": "error",
            "msg": f"url too long ({len(url)} chars; max {_MAX_URL_LEN})",
        }]
    # Parse scheme loosely — full urllib.parse.urlparse accepts a lot
    # of input shapes; for a policy gate we just want the "word before
    # ://" part.
    import re as _re
    m = _re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):", url)
    scheme = (m.group(1).lower() if m else "").strip()
    if scheme not in _ALLOWED_URL_SCHEMES:
        return False, [{
            "level": "error",
            "msg": (
                f"scheme {scheme!r} not allowed; only "
                f"{sorted(_ALLOWED_URL_SCHEMES)} are permitted"
            ),
        }]
    return True, [{
        "level": "warn",
        "msg": f"會在預設瀏覽器開啟：{url}",
    }]


def _policy_open_path(payload: dict) -> tuple[bool, list[dict]]:
    """Enforce: path exists, path is under a whitelisted root.

    URLs (http://, mailto:, etc.) are rejected at this level — we
    could allow them later with their own policy, but this first cut
    is about opening local files safely. A separate open_url action
    type can handle web-shaped intents if/when needed.
    """
    raw = (payload or {}).get("path")
    if not isinstance(raw, str) or not raw.strip():
        return False, [{
            "level": "error",
            "msg": "path must be a non-empty string",
        }]
    # Reject URL-shapes early with a clear message rather than letting
    # them fall through resolve() and look like mysterious path
    # failures.
    url_prefixes = ("http://", "https://", "ftp://", "mailto:",
                    "file://", "javascript:", "data:")
    if any(raw.lower().startswith(p) for p in url_prefixes):
        return False, [{
            "level": "error",
            "msg": "URLs not allowed by open_path policy; use open_url action",
        }]
    try:
        target = Path(raw).expanduser().resolve()
    except Exception as e:
        return False, [{
            "level": "error",
            "msg": f"could not resolve path: {e}",
        }]
    if not target.exists():
        return False, [{
            "level": "error",
            "msg": f"path does not exist: {target}",
        }]
    # Must live under at least one whitelisted root.
    in_allowed = False
    for root in _OPEN_PATH_ROOTS:
        try:
            target.relative_to(root)
            in_allowed = True
            break
        except ValueError:
            continue
    if not in_allowed:
        return False, [{
            "level": "error",
            "msg": (
                f"path is outside allowed roots "
                f"({', '.join(str(r) for r in _OPEN_PATH_ROOTS)})"
            ),
        }]
    # Surface warnings without blocking: opening executables is
    # risky enough to flag even inside the whitelist.
    findings: list[dict] = []
    suffix = target.suffix.lower()
    if suffix in (".exe", ".bat", ".cmd", ".ps1", ".sh", ".msi"):
        findings.append({
            "level": "warn",
            "msg": f"target is an executable ({suffix}); review before approving",
        })
    return True, findings


# ── Executor functions ────────────────────────────────────────────
# Each just delegates to the current Surface. Written as closures
# around `get_surface()` so hot-reloading the module picks up a new
# surface (e.g. switching into DryRun for tests) without re-registering.


def _exec_list_windows(payload: dict) -> dict:
    return get_surface().list_windows()


def _exec_focus_window(payload: dict) -> dict:
    return get_surface().focus_window(payload.get("title_match", ""))


def _exec_get_clipboard(payload: dict) -> dict:
    return get_surface().get_clipboard()


def _exec_set_clipboard(payload: dict) -> dict:
    return get_surface().set_clipboard(payload.get("text", ""))


def _exec_take_screenshot(payload: dict) -> dict:
    return get_surface().take_screenshot(payload.get("out_path"))


def _exec_open_path(payload: dict) -> dict:
    return get_surface().open_path(payload.get("path", ""))


def _exec_open_url(payload: dict) -> dict:
    return get_surface().open_url(payload.get("url", ""))


# ── Vision (Phase D3) ──────────────────────────────────────────────
# Lives here instead of a separate vision handlers module because it
# shares the same "screenshot-as-side-effect" audit path: the user
# approves each vision call individually, the result goes into both
# the audit log and the Context Bus, and it's one handler to reason
# about.


def _policy_interpret_screen(payload: dict) -> tuple[bool, list[dict]]:
    """Require a non-empty prompt ≤ 500 chars so the LLM call has
    concrete direction and we don't accidentally proxy a huge
    user-supplied string through as the VLM prompt.

    Adds a warn-level finding (not blocking) to flag that this action
    will send a live screen capture to a cloud provider — user should
    see this in the approval card before clicking approve.
    """
    prompt = (payload or {}).get("prompt") or ""
    if not isinstance(prompt, str) or not prompt.strip():
        return False, [{
            "level": "error",
            "msg": "prompt must be a non-empty string",
        }]
    if len(prompt) > 500:
        return False, [{
            "level": "error",
            "msg": f"prompt too long ({len(prompt)} chars; max 500)",
        }]
    return True, [{
        "level": "warn",
        "msg": "會截取目前螢幕並傳送到雲端 VLM 分析 — 確認畫面上沒有敏感資訊",
    }]


def _exec_interpret_screen(payload: dict) -> dict:
    """Run the vision pipeline + publish result to the Context Bus.

    Publishing to the bus (SOURCE_SCREEN bucket) means the slime's
    next chat turn automatically has the analysis available without
    the user having to ask a follow-up. The handler also returns the
    analysis so the approval audit log captures what was observed.
    """
    from sentinel.vision import interpret_current_screen
    from sentinel.context_bus import get_bus

    prompt = (payload or {}).get("prompt") or None
    result = interpret_current_screen(prompt=prompt, cleanup=True)
    if result.get("ok"):
        analysis = result.get("analysis") or ""
        if analysis:
            # Publish so the next LLM call sees the fresh screen read
            # without requiring the chat caller to marshal it manually.
            get_bus().publish(
                "screen",
                f"[由 {result.get('provider', '?')} 分析]\n{analysis}",
            )
    return result


# ── Registration ──────────────────────────────────────────────────


_REGISTERED: list[tuple[str, Any]] = [
    # (action_type, policy_fn_or_None, executor)
    ("surface.list_windows",    None,                     _exec_list_windows),
    ("surface.focus_window",    _policy_focus_window,     _exec_focus_window),
    ("surface.get_clipboard",   None,                     _exec_get_clipboard),
    ("surface.set_clipboard",   _policy_set_clipboard,    _exec_set_clipboard),
    ("surface.take_screenshot", None,                     _exec_take_screenshot),
    ("surface.open_path",       _policy_open_path,        _exec_open_path),
    ("surface.open_url",        _policy_open_url,         _exec_open_url),
    # Phase D3 — vision pipeline as an action. Policy surfaces a
    # warning (screenshot → cloud VLM) so the approval card shows
    # what the user is consenting to.
    ("vision.interpret_screen", _policy_interpret_screen, _exec_interpret_screen),
]


def register_all() -> None:
    """Register every Surface primitive as an ACTION handler.

    Called once at daemon startup; safe to call multiple times (each
    re-registration just overwrites the previous binding).
    """
    for action_type, policy, executor in _REGISTERED:
        _approval.register_action_handler(
            action_type=action_type,
            handler=executor,
            policy=policy,
        )
    # Phase D4: chain.run sits alongside surface primitives in the
    # action registry. Registered here (rather than a separate
    # bootstrap step) so daemon startup order keeps simple — one call
    # to register_all brings up every action the LLM can propose.
    # chain.run's own policy_check recursively uses the surface.*
    # handlers registered above, so order matters: primitives first,
    # chain.run after.
    try:
        from sentinel.actions.chain import register as _register_chain
        _register_chain()
    except Exception as e:
        log.warning(f"chain.run registration failed: {e}")
    log.info(
        "Action handlers registered (%d surface primitives + chain.run on %s)",
        len(_REGISTERED),
        get_surface().platform,
    )
