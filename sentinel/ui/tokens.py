"""Design tokens — colors, spacing, typography, helpers.

Constraints we picked for the visual system:

  - DARK first. The slime's whole vibe is "守護靈 in your machine"
    which feels right at home in dark mode. Light mode is a future
    concern.
  - Three accents max. Cyan for system / federation, amber for
    your-slime-specific (own proposals, action cards), red only for
    danger. Avoid the rainbow look the early prototype had with green
    (vote), purple (memory), pink (chat), orange (warning).
  - Typography scale of 5 sizes. 11/12/13/14/15. Anything outside is
    a special case (tab bar, page title) and should be argued for in
    review.
  - Spacing on a 4-px grid (4/8/12/16/24/32). Values like "padding:
    7px" / "margin: 10px" mean someone eyeballed it; tokens stop the
    drift.
  - Radius: 4 (chip), 8 (card), 14 (pill button). Three values — easy
    to grok and applies broadly.

What this module DOESN'T do (yet): theme-switching, CSS variables in
QSS, system-font-stack detection. PySide6 stylesheets are limited
enough that a string-formatter approach is the right tool.
"""
from __future__ import annotations

from typing import Final


# ── Palette ───────────────────────────────────────────────────────

PALETTE: Final[dict[str, str]] = {
    # Surfaces — bottom of the layer cake.
    "bg":            "#0e1014",
    "bg_elev":       "#161922",   # cards / elevated surfaces
    "bg_sunken":     "#0a0c10",   # sub-areas in cards
    "border":        "#272a35",
    "border_subtle": "#1d2028",

    # Text.
    "text":          "#e6e7eb",   # body
    "text_dim":      "#9097a3",   # secondary
    "text_muted":    "#5e6470",   # meta / hints
    "text_inverse":  "#1a1a1a",   # text on bright button bg

    # Accents.
    "cyan":          "#5fd7e8",   # primary — federation / system
    "cyan_dim":      "#3a8b96",
    "amber":         "#f0c674",   # your-slime / action propose
    "amber_dim":     "#b48f4d",
    "danger":        "#cc6b63",   # destructive / errors
    "ok":            "#7eb88f",   # green for success markers

    # Backgrounds for bubbles / pills (transparent over base).
    "bubble_user":   "rgba(95,215,232,0.10)",   # cyan tint
    "bubble_slime":  "rgba(240,198,116,0.08)",  # amber tint
    "bubble_system": "rgba(255,255,255,0.03)",  # very subtle
    "bubble_note":   "rgba(126,184,143,0.10)",  # green tint
}


# ── Spacing scale (4px grid) ─────────────────────────────────────

SPACE: Final[dict[str, int]] = {
    "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32,
}


# ── Border radius ─────────────────────────────────────────────────

RADIUS: Final[dict[str, int]] = {
    "chip": 4,
    "card": 8,
    "pill": 14,
}


# ── Type scale ────────────────────────────────────────────────────

FONT_SIZE: Final[dict[str, int]] = {
    "meta": 11,    # tiny captions, timestamps
    "body": 13,    # default body text
    "section": 12, # bold section headers (smaller than body, weight makes them present)
    "title": 15,   # page-level titles
    "lg":   18,    # rare — splash / hero areas
}


# ── Reusable stylesheet helpers ──────────────────────────────────
#
# Each function returns a Qt stylesheet string. Inline `.format()` /
# f-strings keep them short. They're simple snippets, not a real CSS
# system — Qt's stylesheet engine is constrained, so we stick to
# what works reliably (background, border, padding, color, border-
# radius, font-size).


# Buttons ----------------------------------------------------------


def btn_primary() -> str:
    """Filled accent button — the main call-to-action on a screen."""
    return (
        f"QPushButton {{"
        f" background:{PALETTE['cyan']};"
        f" color:{PALETTE['text_inverse']};"
        f" font-weight:600;"
        f" padding:6px 16px;"
        f" border:none;"
        f" border-radius:{RADIUS['pill']}px;"
        f" font-size:{FONT_SIZE['body']}px; }}"
        f"QPushButton:hover {{"
        f" background:#7be0ed; }}"
        f"QPushButton:disabled {{"
        f" background:{PALETTE['cyan_dim']};"
        f" color:#666; }}"
    )


def btn_secondary() -> str:
    """Outlined button — for secondary or warm-toned actions."""
    return (
        f"QPushButton {{"
        f" background:transparent;"
        f" color:{PALETTE['amber']};"
        f" padding:6px 14px;"
        f" border:1px solid {PALETTE['amber_dim']};"
        f" border-radius:{RADIUS['pill']}px;"
        f" font-size:{FONT_SIZE['body']}px; }}"
        f"QPushButton:hover {{"
        f" border-color:{PALETTE['amber']};"
        f" background:rgba(240,198,116,0.08); }}"
    )


def btn_ghost() -> str:
    """Minimal button — for tertiary / dismiss actions."""
    return (
        f"QPushButton {{"
        f" background:transparent;"
        f" color:{PALETTE['text_dim']};"
        f" padding:5px 12px;"
        f" border:1px solid {PALETTE['border']};"
        f" border-radius:{RADIUS['pill']}px;"
        f" font-size:{FONT_SIZE['meta']}px; }}"
        f"QPushButton:hover {{"
        f" color:{PALETTE['text']};"
        f" border-color:{PALETTE['text_dim']}; }}"
    )


# Text -------------------------------------------------------------


def text_title() -> str:
    return (
        f"color:{PALETTE['cyan']};"
        f" font-size:{FONT_SIZE['title']}px;"
        f" font-weight:600;"
    )


def text_section() -> str:
    return (
        f"color:{PALETTE['amber']};"
        f" font-size:{FONT_SIZE['section']}px;"
        f" font-weight:600;"
        f" letter-spacing:0.3px;"
    )


def text_body() -> str:
    return f"color:{PALETTE['text']}; font-size:{FONT_SIZE['body']}px;"


def text_meta() -> str:
    return f"color:{PALETTE['text_muted']}; font-size:{FONT_SIZE['meta']}px;"


# Cards ------------------------------------------------------------


def card() -> str:
    """Flat card, no fill, just a subtle border. Used for content blocks
    that need separation but not visual weight."""
    return (
        f"QFrame {{"
        f" background:transparent;"
        f" border:1px solid {PALETTE['border_subtle']};"
        f" border-radius:{RADIUS['card']}px; }}"
    )


def card_with_accent(accent_color: str) -> str:
    """Card with a 3-px left accent stripe, no other border. Used in
    federation / approval lists where one color encodes status."""
    return (
        f"QFrame {{"
        f" background:transparent;"
        f" border:none;"
        f" border-left:3px solid {accent_color}; }}"
    )


# Chat bubbles -----------------------------------------------------
# These are HTML wrapper functions (rather than Qt stylesheets)
# because the chat tab uses QTextEdit with rich-text HTML for
# message rendering. The output is a string that wraps the message
# body so the caller doesn't have to care about layout.


_USER_TINT = "rgba(95,215,232,0.14)"   # cyan
_SLIME_TINT = "rgba(240,198,116,0.12)" # amber


def bubble_user(html_body: str) -> str:
    """Right-aligned cyan-tinted bubble for the user's messages.

    Qt's QTextEdit rich-text engine doesn't honor `display:inline-block`
    or `max-width:%`, so we use HTML 4 patterns that Qt actually
    renders: a right-aligned `<table>` with a single cell. cellpadding
    + background-color on the cell give us the bubble; the table's
    align attribute handles right-alignment without needing CSS flex
    or inline-block. width="60%" caps the bubble width — Qt honors
    table-width percentages.
    """
    return (
        f'<table align="right" width="65%" cellpadding="10" '
        f'cellspacing="0" style="margin:6px 0;">'
        f'<tr><td style="background-color:{_USER_TINT};'
        f' color:{PALETTE["text"]};'
        f' border-left:3px solid {PALETTE["cyan"]};'
        f' font-size:{FONT_SIZE["body"]}px;">'
        f'{html_body}'
        f'</td></tr></table>'
        # Trailing line break clears the float so the next message
        # doesn't sit alongside this one (Qt's table flow inherits
        # HTML 4 quirks).
        f'<br clear="all">'
    )


def bubble_slime(html_body: str) -> str:
    """Left-aligned amber-tinted bubble for the slime's messages."""
    return (
        f'<table align="left" width="65%" cellpadding="10" '
        f'cellspacing="0" style="margin:6px 0;">'
        f'<tr><td style="background-color:{_SLIME_TINT};'
        f' color:{PALETTE["text"]};'
        f' border-left:3px solid {PALETTE["amber"]};'
        f' font-size:{FONT_SIZE["body"]}px;">'
        f'<b style="color:{PALETTE["amber"]};">史萊姆</b>　'
        f'{html_body}'
        f'</td></tr></table>'
        f'<br clear="all">'
    )


def bubble_system(html_body: str) -> str:
    """Centered subtle line for system / status events
    (login, action queued, etc.). `<p align="center">` is the
    Qt-supported way to center; CSS text-align on a div doesn't
    propagate reliably through Qt's renderer.
    """
    return (
        f'<p align="center" style="margin:6px 0;">'
        f'<span style="color:{PALETTE["text_muted"]};'
        f' font-size:{FONT_SIZE["meta"]}px;'
        f' font-style:italic;">'
        f'{html_body}'
        f'</span></p>'
    )


def bubble_note(html_body: str) -> str:
    """Green-tinted action-result line. Indented but not in a bubble —
    these are *outputs* of approved actions, not separate messages."""
    return (
        f'<p style="margin:2px 0 2px 24px;">'
        f'<span style="color:{PALETTE["ok"]};'
        f' font-size:{FONT_SIZE["meta"]}px;'
        f' font-style:italic;">'
        f'↳ {html_body}'
        f'</span></p>'
    )
