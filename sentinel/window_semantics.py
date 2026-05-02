"""Window-title semantic understanding — rule layer only.

Phase 3a of v0.8 sensor refactor (2026-05-02). Takes a
`current_focus_snapshot()` dict (from `activity_tracker`, Phase 2)
and returns a richer semantic dict telling downstream code "what
the master is doing", not just "what window is open".

Example:

    in:  {"process_name": "chrome.exe",
          "window_title": "Reddit - r/programming - Bash 還是 Zsh?",
          ...}

    out: {"app_category": "browser",
          "content_type": "social_discussion",
          "topic_signal": "Reddit r/programming",
          "platform": "reddit",
          "confidence": "high"}

Rule layer covers ~80% of common apps. Phase 3b (next PR) adds an
LLM fallback for unknown titles + a persistent cache so the same
title doesn't get re-classified twice.

Why rule layer first
-------------------

The施工指示 asked for a hybrid approach. We ship rules as the
foundation because:

1. **No LLM token cost on every poll** — the daemon polls every 2s,
   un-cached LLM calls would burn tokens fast.
2. **Predictable** — same title always maps to the same semantic
   dict. Easy to test, easy for Phase 5 impulse engine to reason
   about.
3. **Coverage check** — if rule layer hits ~80% in 0xspeter's
   actual usage, the LLM fallback's surface is smaller than feared.
   We can measure this before committing to the LLM cost.

Design constraints
-----------------

- **No mutation**. Pure function: snapshot in → dict out. No file
  writes, no state. `interpret_window` can be called from any thread,
  any frequency, no side effects.
- **Forward-compatible output**. Returns `confidence: "high" | "low"
  | "unknown"` so Phase 3b's LLM layer knows when to override and
  when to keep the rule's answer.
- **Privacy by category**. Messaging apps return contact name only,
  never message content. Browser titles include the URL fragments
  the OS already exposes (page title) but no inferred private state.
"""
from __future__ import annotations

import re
from typing import Optional


# ─── Constants — string enums for downstream consumers ──────────────────


class AppCategory:
    BROWSER             = "browser"
    IDE                 = "ide"
    MESSAGING           = "messaging"
    VIDEO               = "video"
    AUDIO               = "audio"
    DOCUMENT            = "document"
    TERMINAL            = "terminal"
    FILE_BROWSER        = "file_browser"
    GAME                = "game"
    SELF_INTROSPECTION  = "self_introspection"   # slime looking at its own GUI
    UNKNOWN             = "unknown"

    ALL = (BROWSER, IDE, MESSAGING, VIDEO, AUDIO, DOCUMENT,
           TERMINAL, FILE_BROWSER, GAME, SELF_INTROSPECTION, UNKNOWN)


class ContentType:
    """High-level content the master is engaging with. Phase 5 impulse
    engine uses these to decide how (and whether) to react."""

    CODING             = "coding"
    SOCIAL_DISCUSSION  = "social_discussion"
    VIDEO_WATCHING     = "video_watching"
    MUSIC_LISTENING    = "music_listening"
    READING            = "reading"          # articles, docs, PDFs
    CONVERSATION       = "conversation"     # messaging
    SHELL              = "shell"            # terminal commands
    BROWSING           = "browsing"         # general web, no specific platform
    FILE_NAVIGATION    = "file_navigation"
    GAMING             = "gaming"
    SELF_INTROSPECTION = "self_introspection"  # master is looking at slime's own UI
    UNKNOWN            = "unknown"


class Confidence:
    """Where this interpretation came from."""
    HIGH    = "high"      # rule matched both process AND title pattern
    MEDIUM  = "medium"    # rule matched process only (category known, content fuzzy)
    LOW     = "low"       # process matched a generic family, no title insight
    UNKNOWN = "unknown"   # nothing matched; Phase 3b LLM can attempt


# ─── Process-name → category map ────────────────────────────────────────
#
# Keys are lowercase substring matches against the process_name. We
# substring-match rather than exact-match because Win32 process names
# vary by version: Chrome ships as `chrome.exe` (stable) but Code-OSS
# ships as `Code.exe` and Insiders as `Code - Insiders.exe`.
#
# Order matters within the dict only as a tie-breaker; in practice
# we walk the whole map and take the first match. If two entries
# would both match (rare), the more specific one should come first.

_PROCESS_TO_CATEGORY: dict[str, str] = {
    # Browsers
    "chrome":       AppCategory.BROWSER,
    "firefox":      AppCategory.BROWSER,
    "msedge":       AppCategory.BROWSER,
    "brave":        AppCategory.BROWSER,
    "vivaldi":      AppCategory.BROWSER,
    "opera":        AppCategory.BROWSER,
    "arc":          AppCategory.BROWSER,
    "safari":       AppCategory.BROWSER,
    "iexplore":     AppCategory.BROWSER,

    # IDEs / editors
    "code.exe":         AppCategory.IDE,
    "code - insiders":  AppCategory.IDE,
    "cursor":           AppCategory.IDE,
    "claude.exe":       AppCategory.IDE,    # Claude Code (Anthropic desktop)
    "claude code":      AppCategory.IDE,
    "sublime_text":     AppCategory.IDE,
    "pycharm":          AppCategory.IDE,
    "idea":             AppCategory.IDE,
    "webstorm":         AppCategory.IDE,
    "rider":            AppCategory.IDE,
    "clion":            AppCategory.IDE,
    "phpstorm":         AppCategory.IDE,
    "rubymine":         AppCategory.IDE,
    "atom":             AppCategory.IDE,
    "notepad++":        AppCategory.IDE,
    "neovim":           AppCategory.IDE,
    "nvim":             AppCategory.IDE,
    "vim":              AppCategory.IDE,
    "emacs":            AppCategory.IDE,
    "zed":              AppCategory.IDE,
    "fleet":            AppCategory.IDE,
    "devenv":           AppCategory.IDE,    # Visual Studio
    "xcode":            AppCategory.IDE,
    "android studio":   AppCategory.IDE,

    # Messaging — note we deliberately key on app, not content
    "discord":      AppCategory.MESSAGING,
    "slack":        AppCategory.MESSAGING,
    "telegram":     AppCategory.MESSAGING,
    "whatsapp":     AppCategory.MESSAGING,
    "messenger":    AppCategory.MESSAGING,
    "wechat":       AppCategory.MESSAGING,
    "weixin":       AppCategory.MESSAGING,
    "line":         AppCategory.MESSAGING,
    "skype":        AppCategory.MESSAGING,
    "teams":        AppCategory.MESSAGING,
    "zoom":         AppCategory.MESSAGING,
    "signal":       AppCategory.MESSAGING,
    "element":      AppCategory.MESSAGING,
    "kakaotalk":    AppCategory.MESSAGING,

    # Video / audio
    "vlc":              AppCategory.VIDEO,
    "mpv":              AppCategory.VIDEO,
    "potplayer":        AppCategory.VIDEO,
    "wmplayer":         AppCategory.VIDEO,
    "windowsmediaplayer": AppCategory.VIDEO,
    "spotify":          AppCategory.AUDIO,
    "music":            AppCategory.AUDIO,
    "applemusic":       AppCategory.AUDIO,
    "foobar2000":       AppCategory.AUDIO,

    # Documents
    "winword":      AppCategory.DOCUMENT,
    "excel":        AppCategory.DOCUMENT,
    "powerpnt":     AppCategory.DOCUMENT,
    "onenote":      AppCategory.DOCUMENT,
    "acrobat":      AppCategory.DOCUMENT,
    "acrord":       AppCategory.DOCUMENT,
    "sumatrapdf":   AppCategory.DOCUMENT,
    "preview":      AppCategory.DOCUMENT,
    "obsidian":     AppCategory.DOCUMENT,
    "notion":       AppCategory.DOCUMENT,
    "logseq":       AppCategory.DOCUMENT,
    "typora":       AppCategory.DOCUMENT,

    # Terminals
    "cmd.exe":              AppCategory.TERMINAL,
    "powershell":           AppCategory.TERMINAL,
    "pwsh":                 AppCategory.TERMINAL,
    "windowsterminal":      AppCategory.TERMINAL,
    "wsl":                  AppCategory.TERMINAL,
    "wezterm":              AppCategory.TERMINAL,
    "alacritty":            AppCategory.TERMINAL,
    "kitty":                AppCategory.TERMINAL,
    "iterm":                AppCategory.TERMINAL,
    "conhost":              AppCategory.TERMINAL,
    "tabby":                AppCategory.TERMINAL,

    # File browsers
    "explorer.exe": AppCategory.FILE_BROWSER,
    "finder":       AppCategory.FILE_BROWSER,
    "totalcmd":     AppCategory.FILE_BROWSER,
    "files":        AppCategory.FILE_BROWSER,

    # Games — there's no clean process pattern; we leave a few
    # well-known launchers and let the LLM fallback handle the rest.
    "steam":        AppCategory.GAME,
    "epicgames":    AppCategory.GAME,
}


# ─── Title patterns → platform / content (browser) ──────────────────────
#
# Each entry: (regex, platform_label, content_type_override). The
# content override only fires for some platforms (YouTube → video,
# Spotify-as-web-player → music). For most browser visits content
# stays at SOCIAL_DISCUSSION or BROWSING based on URL family.

_BROWSER_PLATFORM_RULES: list[tuple[re.Pattern, str, Optional[str]]] = [
    (re.compile(r"\breddit\.com|r/[a-zA-Z0-9_]+|reddit\b", re.IGNORECASE),
     "reddit", ContentType.SOCIAL_DISCUSSION),
    (re.compile(r"\byoutube\.com|YouTube\b", re.IGNORECASE),
     "youtube", ContentType.VIDEO_WATCHING),
    (re.compile(r"\bgithub\.com|\bGitHub\b", re.IGNORECASE),
     "github", ContentType.READING),
    (re.compile(r"\bstackoverflow\.com|Stack Overflow", re.IGNORECASE),
     "stackoverflow", ContentType.READING),
    (re.compile(r"\b(twitter\.com|x\.com)\b|\bTwitter\b", re.IGNORECASE),
     "twitter", ContentType.SOCIAL_DISCUSSION),
    (re.compile(r"\bfacebook\.com|\bFacebook\b", re.IGNORECASE),
     "facebook", ContentType.SOCIAL_DISCUSSION),
    (re.compile(r"\bnews\.ycombinator\.com|Hacker News", re.IGNORECASE),
     "hackernews", ContentType.SOCIAL_DISCUSSION),
    (re.compile(r"\bmedium\.com|\bMedium\b", re.IGNORECASE),
     "medium", ContentType.READING),
    (re.compile(r"\bnotion\.so\b", re.IGNORECASE),
     "notion", ContentType.READING),
    (re.compile(r"\binstagram\.com|\bInstagram\b", re.IGNORECASE),
     "instagram", ContentType.SOCIAL_DISCUSSION),
    (re.compile(r"\bbilibili\.com|嗶哩嗶哩|Bilibili", re.IGNORECASE),
     "bilibili", ContentType.VIDEO_WATCHING),
    (re.compile(r"\btwitch\.tv|Twitch", re.IGNORECASE),
     "twitch", ContentType.VIDEO_WATCHING),
    (re.compile(r"\bnetflix\.com|Netflix", re.IGNORECASE),
     "netflix", ContentType.VIDEO_WATCHING),
    (re.compile(r"\bspotify\.com\b", re.IGNORECASE),
     "spotify", ContentType.MUSIC_LISTENING),
    (re.compile(r"\b(claude\.ai|chatgpt\.com|chat\.openai\.com|gemini\.google\.com)\b",
                re.IGNORECASE),
     "ai_chat", ContentType.CONVERSATION),
]


# Default content type when in a browser but no platform rule fired.
_BROWSER_DEFAULT_CONTENT = ContentType.BROWSING


# ─── Category → default content_type ────────────────────────────────────
#
# Used when a more specific rule didn't fire — e.g. IDE without a
# recognized file extension still maps to CODING because that's what
# IDEs do.

_CATEGORY_DEFAULT_CONTENT: dict[str, str] = {
    AppCategory.BROWSER:      ContentType.BROWSING,
    AppCategory.IDE:          ContentType.CODING,
    AppCategory.MESSAGING:    ContentType.CONVERSATION,
    AppCategory.VIDEO:        ContentType.VIDEO_WATCHING,
    AppCategory.AUDIO:        ContentType.MUSIC_LISTENING,
    AppCategory.DOCUMENT:     ContentType.READING,
    AppCategory.TERMINAL:     ContentType.SHELL,
    AppCategory.FILE_BROWSER: ContentType.FILE_NAVIGATION,
    AppCategory.GAME:         ContentType.GAMING,
}


# ─── IDE title parser ───────────────────────────────────────────────────


# IDE windows commonly format titles as one of:
#   "main.py - Visual Studio Code"
#   "main.py — slimeagent — Visual Studio Code"
#   "main.py (slimeagent) - PyCharm 2024.1"
#   "● main.py - Visual Studio Code"     (modified marker)
# We try a few tolerant patterns and return (file, project) or None.

_IDE_TITLE_PATTERNS = [
    # "[modified marker] file - PROJECT - IDE"  (VS Code style with em-dashes)
    re.compile(r"^[●\*●]?\s*([^—\-]+?)\s+[—\-]\s+([^—\-]+?)\s+[—\-]\s+(.+)$"),
    # "file - IDE"  (no project segment)
    re.compile(r"^[●\*●]?\s*([^—\-]+?)\s+[—\-]\s+(.+)$"),
    # "file (PROJECT) - IDE"  (JetBrains style)
    re.compile(r"^[●\*●]?\s*([^()]+?)\s+\(([^()]+)\)\s+[—\-]\s+(.+)$"),
]


def _parse_ide_title(title: str) -> tuple[Optional[str], Optional[str]]:
    """Extract (filename, project) from an IDE title. Either may be None.

    Heuristics-based: IDE title formats are not standardized. When in
    doubt, return Nones rather than guess wrong — the LLM fallback in
    Phase 3b can clean up.
    """
    if not title:
        return None, None
    s = title.strip()

    # JetBrains-style "file (project) - IDE"
    m = re.match(r"^[●\*●]?\s*([^\s][^()]*?)\s+\(([^()]+)\)\s+[—\-]\s+(.+)$",
                 s)
    if m:
        return m.group(1).strip() or None, m.group(2).strip() or None

    # 3-segment "file — project — IDE"
    parts = re.split(r"\s+[—\-]\s+", s)
    parts = [p.strip("●*● ").strip() for p in parts if p.strip()]
    if len(parts) >= 3:
        return parts[0] or None, parts[1] or None
    if len(parts) == 2:
        return parts[0] or None, None

    return None, None


# ─── Browser title parser ───────────────────────────────────────────────


def _detect_browser_platform(
    title: str,
) -> tuple[Optional[str], Optional[str]]:
    """Walk browser-platform rules in declaration order. Returns
    (platform, content_type_override) or (None, None)."""
    if not title:
        return None, None
    for pat, platform, content_override in _BROWSER_PLATFORM_RULES:
        if pat.search(title):
            return platform, content_override
    return None, None


# ─── Messaging title parser ─────────────────────────────────────────────


# Messaging apps tend to format the title as either:
#   "AppName - ConversationName"        (Telegram, Slack)
#   "AppName"                           (Discord — single window for all)
#   "ConversationName · Slack"          (newer Slack)
#   "ConversationName | WhatsApp"       (some web clients)
#
# For privacy, we extract only the *contact / channel name*, never any
# preview content. If the title's been hijacked to show a notification
# preview, the heuristic will likely degrade gracefully because we're
# matching standard separators only.

def _parse_messaging_title(title: str) -> Optional[str]:
    """Best-effort contact/channel extraction. Returns the contact
    name string, or None if the title doesn't follow a recognized
    format."""
    if not title:
        return None

    # "Contact - AppName" — try common separators
    for sep in (" - ", " — ", " · ", " | "):
        if sep in title:
            head, _, tail = title.partition(sep)
            # Heuristic: app name is one of these well-known keywords;
            # if so, the OTHER side is the contact.
            head_l = head.strip().lower()
            tail_l = tail.strip().lower()
            for app in ("telegram", "slack", "whatsapp", "discord",
                        "messenger", "wechat", "line", "signal",
                        "teams", "skype", "zoom", "weixin"):
                if app == head_l or app in head_l.split():
                    return tail.strip() or None
                if app == tail_l or app in tail_l.split():
                    return head.strip() or None

    return None


# ─── Main entry point ───────────────────────────────────────────────────


def _category_from_process(process_name: str) -> tuple[str, str]:
    """Walk the process map. Returns (category, confidence)."""
    if not process_name:
        return AppCategory.UNKNOWN, Confidence.UNKNOWN
    proc_l = process_name.lower()
    for key, category in _PROCESS_TO_CATEGORY.items():
        if key in proc_l:
            return category, Confidence.MEDIUM
    return AppCategory.UNKNOWN, Confidence.UNKNOWN


def interpret_window(focus_snapshot: dict) -> dict:
    """Take a snapshot from `activity_tracker.current_focus_snapshot`
    and return semantic interpretation.

    Output schema (always present, falsy when not detected):

        app_category    — AppCategory.*
        content_type    — ContentType.*
        topic_signal    — short human-readable hint, e.g. "Reddit r/programming"
        platform        — browser-only: "reddit" / "youtube" / etc; "" otherwise
        file            — IDE-only: detected filename; "" otherwise
        project         — IDE-only: detected project; "" otherwise
        contact         — messaging-only: contact/channel; "" otherwise
        confidence      — Confidence.*
        is_idle         — passed through from snapshot

    Pure function. Same input → same output. Safe to call every poll.
    """
    process_name = (focus_snapshot.get("process_name") or "").strip()
    title        = (focus_snapshot.get("window_title") or "").strip()

    out = {
        "app_category": AppCategory.UNKNOWN,
        "content_type": ContentType.UNKNOWN,
        "topic_signal": "",
        "platform":     "",
        "file":         "",
        "project":      "",
        "contact":      "",
        "confidence":   Confidence.UNKNOWN,
        "is_idle":      bool(focus_snapshot.get("is_idle", False)),
    }

    if not process_name and not title:
        return out

    # 0. Self-introspection check — when the master is looking at slime's
    # own GUI (MainWindow, dialogs, tabs), we want this classified as
    # SELF_INTROSPECTION rather than "unknown".
    #
    # Two signals must BOTH match:
    #   (a) process is one of the python launchers slime runs under
    #       (python.exe / pythonw.exe). Restricting by process avoids
    #       the false positive where a browser visits a page titled
    #       "AI Slime" (e.g. the github repo readme).
    #   (b) title contains "AI Slime" — slime's windows all carry
    #       this prefix and that string is vanishingly unlikely in
    #       another python-based app.
    #
    # Why classify rather than filter: Phase 5's impulse engine should
    # KNOW the master is looking at slime — it's a real interaction
    # (peeking at the box, reviewing approvals, etc.) — but it's also
    # not "看主人在做什麼" in the sensor-refactor sense, so it deserves
    # its own bucket rather than being mixed in with browser/IDE.
    proc_l = (process_name or "").lower()
    is_python_launcher = proc_l in (
        "python.exe", "pythonw.exe", "py.exe",
        "aislime.exe", "ai_slime.exe",  # if pyinstaller build is ever shipped
    )
    if is_python_launcher and "AI Slime" in (title or ""):
        out["app_category"] = AppCategory.SELF_INTROSPECTION
        out["content_type"] = ContentType.SELF_INTROSPECTION
        out["topic_signal"] = _truncate_for_signal(title) or "slime UI"
        out["confidence"] = Confidence.HIGH
        return out

    # 1. Category from process. Falls back to UNKNOWN if no rule hit.
    category, confidence = _category_from_process(process_name)
    out["app_category"] = category
    out["confidence"] = confidence

    # 2. Default content type for that category
    if category in _CATEGORY_DEFAULT_CONTENT:
        out["content_type"] = _CATEGORY_DEFAULT_CONTENT[category]

    # 3. Category-specific title parsing
    if category == AppCategory.BROWSER:
        platform, content_override = _detect_browser_platform(title)
        if platform:
            out["platform"] = platform
            out["confidence"] = Confidence.HIGH
            if content_override:
                out["content_type"] = content_override
            # Best topic signal we can build from rules alone:
            # "Platform - first segment of title before the platform name"
            out["topic_signal"] = _browser_topic_signal(title, platform)
        else:
            # In a browser but no platform matched. Title itself is
            # the best we can do.
            out["topic_signal"] = _truncate_for_signal(title)

    elif category == AppCategory.IDE:
        file_, project = _parse_ide_title(title)
        if file_:
            out["file"] = file_
        if project:
            out["project"] = project
        if file_ or project:
            out["confidence"] = Confidence.HIGH
        # Topic signal: "coding: file in project" if both, else either.
        if file_ and project:
            out["topic_signal"] = f"coding: {file_} ({project})"
        elif file_:
            out["topic_signal"] = f"coding: {file_}"
        elif project:
            out["topic_signal"] = f"coding: {project}"
        else:
            out["topic_signal"] = "coding"

    elif category == AppCategory.MESSAGING:
        contact = _parse_messaging_title(title)
        if contact:
            out["contact"] = contact
            out["confidence"] = Confidence.HIGH
            out["topic_signal"] = f"chatting: {contact}"
        else:
            out["topic_signal"] = "chatting"

    elif category == AppCategory.TERMINAL:
        # Terminal title often = current command or path.
        out["topic_signal"] = _truncate_for_signal(title) or "shell"

    else:
        # Generic fallback — title is the best signal.
        if title:
            out["topic_signal"] = _truncate_for_signal(title)

    return out


def _truncate_for_signal(text: str, max_len: int = 80) -> str:
    """Cap topic_signal so log lines and downstream prompts stay
    bounded. Strips trailing whitespace before measuring length."""
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _browser_topic_signal(title: str, platform: str) -> str:
    """Build a short topic_signal from a browser title once a platform
    is detected. Falls back to the title itself if the platform name
    doesn't appear in the title (rare, but happens with custom
    extensions / page titles).
    """
    base = f"{platform.capitalize()}"
    s = (title or "").strip()
    if not s:
        return base
    # Try to extract the "interesting" part — page title, subreddit,
    # video title — by stripping the platform name from the title.
    cleaned = re.sub(re.escape(platform), "", s, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*[-—|·]\s*", " - ", cleaned).strip(" -—|·")
    if cleaned and cleaned != s:
        return _truncate_for_signal(f"{base}: {cleaned}")
    return _truncate_for_signal(f"{base}: {s}") if s else base
