"""UI design system — shared tokens + helpers.

Central place for colors, spacing, typography, and reusable
stylesheet snippets. The goal is visual cohesion across tabs:
when a user touches the federation tab, then settings, then
chat — they should feel like one product, not three.

Pre-Phase L the UI accumulated stylesheets per-component, with
each author picking their own color values. Result was random
shades of grey, inconsistent border-radius, and no rhythm to
spacing. This module is the single source of truth — change a
token here and every consumer updates.

Usage:
    from sentinel.ui import tokens

    button.setStyleSheet(tokens.btn_primary())
    label.setStyleSheet(tokens.text_meta())
    card.setStyleSheet(tokens.card("amber"))
"""
from sentinel.ui.tokens import (
    PALETTE,
    SPACE,
    RADIUS,
    FONT_SIZE,
    btn_primary, btn_secondary, btn_ghost,
    text_title, text_section, text_body, text_meta,
    card, card_with_accent,
    bubble_user, bubble_slime, bubble_system, bubble_note,
)

__all__ = [
    "PALETTE", "SPACE", "RADIUS", "FONT_SIZE",
    "btn_primary", "btn_secondary", "btn_ghost",
    "text_title", "text_section", "text_body", "text_meta",
    "card", "card_with_accent",
    "bubble_user", "bubble_slime", "bubble_system", "bubble_note",
]
