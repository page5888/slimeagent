"""Render-side helpers for applying a birth_signature to slime paint.

Pure logic (Python ints / floats / QColor) lives here so that the
two paint loops in slime_avatar.py and overlay.py share the same
math — if either drifts, the desktop overlay slime and the home tab
slime stop matching, which is exactly the kind of bug ADR
2026-05-01-slime-physical-individuation.md would call a manifesto
violation ("two views of the same slime should be the same slime").

Schema + ranges + generator are in sentinel/birth_signature.py
(no Qt). This file is the Qt half — depends on PySide6.

Three entry points:

  apply_signature_to_colors(colors, sig_dict)
    Returns a new colors dict with body / highlight / glow shifted
    by hue_offset and scaled by saturation_factor. Eye / mouth /
    accessory colours are untouched (they're not "the body").

  apply_signature_to_dimensions(body_w, body_h, sig_dict)
    Returns (body_w, body_h) scaled by width_factor / height_factor.
    The breath / bounce factors must already be applied — sig is
    layered on top of the live animation, not the base.

  draw_marking(painter, sig_dict, ...)
    Draws the optional marking (swirl / dot / line) on the body.
    No-op if sig has no marking. Call after body is painted, before
    eyes / mouth, so the marking sits on the body surface.

All three are defensive: passing an empty dict (`{}`) returns the
input unchanged. That keeps the existing render path correct for
edge cases where signature isn't loaded yet — first launch before
file write, or test environments mocking out evolution.
"""
from __future__ import annotations

from PySide6.QtGui import QPainter, QColor, QPen, QBrush
from PySide6.QtCore import Qt, QPoint, QRect
import math


def _shift_color(color: QColor, hue_offset: float,
                 saturation_factor: float) -> QColor:
    """Shift hue (degrees) + scale saturation, preserve value + alpha.

    HSV is the right space for both ops: hue rotation is a single
    angular add and saturation scaling stays perceptually linear.
    Achromatic input (hue == -1 from getHsv) gets a hue defaulted to
    0 before the offset, so a hue_offset on a near-grey starting
    colour produces a faint tint instead of staying grey.
    """
    h, s, v, a = color.getHsv()
    if h < 0:                       # achromatic → make tintable
        h = 0
    h = (h + int(round(hue_offset))) % 360
    s = max(0, min(255, int(round(s * saturation_factor))))
    new = QColor()
    new.setHsv(h, s, v, a)
    return new


def apply_signature_to_colors(colors: dict, sig: dict) -> dict:
    """Return a copy of `colors` with body/highlight/glow shifted by
    the signature. Caller can mutate the result freely; the original
    TIER_COLORS dict is never modified."""
    if not sig:
        return colors
    hue = float(sig.get("body_hue_offset", 0.0))
    sat = float(sig.get("body_saturation_factor", 1.0))
    if hue == 0.0 and sat == 1.0:
        return colors                 # nothing to shift

    out = dict(colors)
    for key in ("body", "highlight", "glow"):
        if key in out:
            out[key] = _shift_color(out[key], hue, sat)
    return out


def apply_signature_to_dimensions(body_w: int, body_h: int,
                                  sig: dict) -> tuple[int, int]:
    """Scale body_w / body_h by signature factors. Animation factors
    (breath / bounce) are expected to already be baked into the
    inputs — this layer is a static per-instance multiplier on top."""
    if not sig:
        return body_w, body_h
    w_factor = float(sig.get("body_width_factor", 1.0))
    h_factor = float(sig.get("body_height_factor", 1.0))
    return int(body_w * w_factor), int(body_h * h_factor)


def _marking_color(base_body_color: QColor, hue_delta: float,
                   lightness_delta: float) -> QColor:
    """Marking colour = body colour shifted in hue + lightness.

    Lightness shift uses HSL (where 'lightness' is the L axis), then
    we re-emit in RGB so the QPainter call doesn't have to care.
    Alpha is bumped slightly so the marking reads as a deliberate
    feature, not a translucent ghost.
    """
    h, s, l, a = base_body_color.getHsl()
    if h < 0:
        h = 0
    h = (h + int(round(hue_delta))) % 360
    l = max(0, min(255, int(round(l + lightness_delta * 255))))
    out = QColor()
    out.setHsl(h, s, l, min(255, int(a * 1.2)))
    return out


def draw_marking(painter: QPainter, sig: dict,
                 cx: int, cy_with_bounce: float,
                 body_w: int, body_h: int,
                 base_body_color: QColor) -> None:
    """Draw the optional marking on top of the body shape. No-op if
    `sig` is empty or carries `marking == None`.

    Coords: marking position_x/y are body-relative in [-1, 1]; we
    translate them onto absolute pixel coords using the current
    body_w / body_h. Y is anchored to cy_with_bounce so the marking
    rides along with the breath animation.

    Sizes are deliberately small (~10–15% of body width). Per ADR
    護欄 #5, markings should read as 'a small mark' not as a
    decoration overlay. If a marking type starts overpowering the
    silhouette, shrink the size constants here, not the position
    range in birth_signature.py.
    """
    if not sig:
        return
    marking = sig.get("marking")
    if not marking:
        return

    mtype = marking.get("type")
    if mtype not in ("swirl", "dot", "line"):
        return

    px = float(marking.get("position_x", 0.0))
    py = float(marking.get("position_y", 0.0))
    hue_delta = float(marking.get("hue_delta", 0.0))
    lightness_delta = float(marking.get("lightness_delta", 0.0))

    # Position in pixels. We compress the body-relative coord by ~0.6
    # so a value at the [-1, 1] edge stays inside the visible body
    # rather than landing on the body outline.
    mark_cx = cx + int(px * body_w * 0.55)
    mark_cy = int(cy_with_bounce + py * body_h * 0.55)

    color = _marking_color(base_body_color, hue_delta, lightness_delta)

    painter.save()
    try:
        if mtype == "dot":
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.NoPen)
            radius = max(2, body_w // 10)
            painter.drawEllipse(QPoint(mark_cx, mark_cy), radius, radius)

        elif mtype == "line":
            painter.setBrush(Qt.NoBrush)
            pen = QPen(color, max(1, body_w // 18))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            length = max(6, body_w // 4)
            # Slight angle so the line never reads as a horizon.
            dx = int(length * math.cos(0.4))
            dy = int(length * math.sin(0.4))
            painter.drawLine(mark_cx - dx, mark_cy - dy,
                             mark_cx + dx, mark_cy + dy)

        elif mtype == "swirl":
            painter.setBrush(Qt.NoBrush)
            pen = QPen(color, max(1, body_w // 22))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            r = max(4, body_w // 7)
            # Two short arcs offset 180° to suggest a swirl without
            # going full spiral (which would read as a logo, not a
            # body marking).
            rect = QRect(mark_cx - r, mark_cy - r, r * 2, r * 2)
            painter.drawArc(rect, 30 * 16, 240 * 16)
            inner_r = max(2, r // 2)
            inner_rect = QRect(mark_cx - inner_r, mark_cy - inner_r,
                               inner_r * 2, inner_r * 2)
            painter.drawArc(inner_rect, 210 * 16, 240 * 16)
    finally:
        painter.restore()
