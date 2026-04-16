"""Equipment visual renderer — draws equipment on the slime avatar.

Each equipment `visual` key maps to a drawing function that paints
onto the slime using QPainter. All visuals are pure geometry + gradients,
no external image files needed.

Drawing functions receive a context dict with:
  - p: QPainter (already set up with antialiasing)
  - cx, cy: center of slime body
  - body_w, body_h: slime body radii
  - bounce: current vertical animation offset
  - phase: animation phase (0 to 2*pi, cycling)
  - w, h: widget total width/height
  - tier_index: 0-6 evolution tier
  - rarity: item rarity string
  - scale: size multiplier (1.0 for main avatar, ~0.6 for overlay)
"""
import math
from PySide6.QtGui import (
    QPainter, QColor, QRadialGradient, QLinearGradient,
    QPen, QBrush, QFont, QPolygon,
)
from PySide6.QtCore import Qt, QPoint, QRect


# ─── Rarity glow colors ─────────────────────────────────────────────────
RARITY_GLOW = {
    "common": QColor(170, 170, 170, 60),
    "uncommon": QColor(46, 213, 115, 80),
    "rare": QColor(30, 144, 255, 100),
    "epic": QColor(168, 85, 247, 120),
    "legendary": QColor(255, 165, 2, 140),
    "mythic": QColor(255, 71, 87, 150),
    "ultimate": QColor(255, 215, 0, 180),
}


def _s(val, scale):
    """Scale a pixel value."""
    return int(val * scale)


# ═══════════════════════════════════════════════════════════════════════════
#  HELMET
# ═══════════════════════════════════════════════════════════════════════════

def draw_hacker_goggles(ctx):
    p, cx, cy, body_w, body_h, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["scale"])
    y = int(cy - body_h * 0.15 + bounce)
    sp = int(body_w * 0.35)
    r = _s(7, scale)
    # Goggle frames
    p.setPen(QPen(QColor(60, 60, 60), _s(2, scale)))
    p.setBrush(QBrush(QColor(0, 255, 200, 100)))
    p.drawEllipse(QPoint(cx - sp, y), r, r)
    p.drawEllipse(QPoint(cx + sp, y), r, r)
    # Bridge
    p.drawLine(cx - sp + r, y, cx + sp - r, y)


def draw_cat_ears(ctx):
    p, cx, cy, body_w, body_h, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["scale"])
    top_y = int(cy - body_h + bounce)
    ear_h = _s(14, scale)
    ear_w = _s(8, scale)
    # Left ear
    p.setBrush(QBrush(QColor(255, 180, 200)))
    p.setPen(QPen(QColor(200, 100, 130), _s(1.5, scale)))
    pts_l = [QPoint(cx - _s(12, scale), top_y),
             QPoint(cx - _s(20, scale), top_y - ear_h),
             QPoint(cx - _s(4, scale), top_y - _s(3, scale))]
    p.drawPolygon(QPolygon(pts_l))
    # Right ear
    pts_r = [QPoint(cx + _s(12, scale), top_y),
             QPoint(cx + _s(20, scale), top_y - ear_h),
             QPoint(cx + _s(4, scale), top_y - _s(3, scale))]
    p.drawPolygon(QPolygon(pts_r))
    # Inner ear (pink)
    p.setBrush(QBrush(QColor(255, 130, 170)))
    p.setPen(Qt.NoPen)
    pts_li = [QPoint(cx - _s(12, scale), top_y),
              QPoint(cx - _s(17, scale), top_y - _s(8, scale)),
              QPoint(cx - _s(7, scale), top_y - _s(2, scale))]
    p.drawPolygon(QPolygon(pts_li))
    pts_ri = [QPoint(cx + _s(12, scale), top_y),
              QPoint(cx + _s(17, scale), top_y - _s(8, scale)),
              QPoint(cx + _s(7, scale), top_y - _s(2, scale))]
    p.drawPolygon(QPolygon(pts_ri))


def draw_sage_crown(ctx):
    p, cx, cy, body_w, body_h, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["scale"])
    top_y = int(cy - body_h - _s(5, scale) + bounce)
    # Golden crown base
    p.setBrush(QBrush(QColor(255, 215, 0)))
    p.setPen(QPen(QColor(200, 170, 0), _s(1.5, scale)))
    hw = _s(16, scale)
    pts = [
        QPoint(cx - hw, top_y + _s(8, scale)),
        QPoint(cx - _s(13, scale), top_y),
        QPoint(cx - _s(6, scale), top_y + _s(4, scale)),
        QPoint(cx, top_y - _s(4, scale)),
        QPoint(cx + _s(6, scale), top_y + _s(4, scale)),
        QPoint(cx + _s(13, scale), top_y),
        QPoint(cx + hw, top_y + _s(8, scale)),
    ]
    p.drawPolygon(QPolygon(pts))
    # Blue gem in center
    p.setBrush(QBrush(QColor(0, 150, 255)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx, top_y + _s(3, scale)), _s(3, scale), _s(3, scale))


def draw_demon_horns(ctx):
    p, cx, cy, body_w, body_h, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["scale"])
    top_y = int(cy - body_h + bounce)
    horn_h = _s(18, scale)
    # Left horn
    grad_l = QLinearGradient(cx - _s(15, scale), top_y, cx - _s(20, scale), top_y - horn_h)
    grad_l.setColorAt(0, QColor(80, 0, 120))
    grad_l.setColorAt(1, QColor(200, 50, 80))
    p.setBrush(QBrush(grad_l))
    p.setPen(QPen(QColor(60, 0, 80), _s(1, scale)))
    pts_l = [QPoint(cx - _s(8, scale), top_y),
             QPoint(cx - _s(22, scale), top_y - horn_h),
             QPoint(cx - _s(14, scale), top_y + _s(2, scale))]
    p.drawPolygon(QPolygon(pts_l))
    # Right horn
    grad_r = QLinearGradient(cx + _s(15, scale), top_y, cx + _s(20, scale), top_y - horn_h)
    grad_r.setColorAt(0, QColor(80, 0, 120))
    grad_r.setColorAt(1, QColor(200, 50, 80))
    p.setBrush(QBrush(grad_r))
    pts_r = [QPoint(cx + _s(8, scale), top_y),
             QPoint(cx + _s(22, scale), top_y - horn_h),
             QPoint(cx + _s(14, scale), top_y + _s(2, scale))]
    p.drawPolygon(QPolygon(pts_r))


def draw_dragon_skull_crown(ctx):
    p, cx, cy, body_w, body_h, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["scale"])
    top_y = int(cy - body_h - _s(3, scale) + bounce)
    # Bone-white crown with spikes
    p.setBrush(QBrush(QColor(230, 220, 200)))
    p.setPen(QPen(QColor(180, 160, 140), _s(1.5, scale)))
    hw = _s(18, scale)
    pts = [
        QPoint(cx - hw, top_y + _s(8, scale)),
        QPoint(cx - _s(15, scale), top_y - _s(8, scale)),
        QPoint(cx - _s(8, scale), top_y + _s(2, scale)),
        QPoint(cx, top_y - _s(12, scale)),
        QPoint(cx + _s(8, scale), top_y + _s(2, scale)),
        QPoint(cx + _s(15, scale), top_y - _s(8, scale)),
        QPoint(cx + hw, top_y + _s(8, scale)),
    ]
    p.drawPolygon(QPolygon(pts))
    # Red eye sockets
    p.setBrush(QBrush(QColor(255, 30, 30)))
    p.setPen(Qt.NoPen)
    ey = top_y + _s(4, scale)
    p.drawEllipse(QPoint(cx - _s(5, scale), ey), _s(2, scale), _s(2, scale))
    p.drawEllipse(QPoint(cx + _s(5, scale), ey), _s(2, scale), _s(2, scale))


def draw_veldora_crown(ctx):
    """Mythic — Veldora's dragon crown with lightning."""
    draw_dragon_skull_crown(ctx)
    p, cx, cy, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    # Lightning bolts from crown
    top_y = int(cy - body_h - _s(3, scale) + bounce)
    p.setPen(QPen(QColor(100, 200, 255, 180), _s(1.5, scale)))
    lx = cx - _s(18, scale) + int(math.sin(phase * 3) * _s(3, scale))
    p.drawLine(lx, top_y - _s(6, scale), lx - _s(4, scale), top_y - _s(14, scale))
    p.drawLine(lx - _s(4, scale), top_y - _s(14, scale), lx + _s(2, scale), top_y - _s(12, scale))
    p.drawLine(lx + _s(2, scale), top_y - _s(12, scale), lx - _s(2, scale), top_y - _s(20, scale))


def draw_void_diadem(ctx):
    """Ultimate — void energy diadem."""
    p, cx, cy, body_w, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    top_y = int(cy - body_h - _s(5, scale) + bounce)
    # Dark crown with purple energy
    grad = QLinearGradient(cx - _s(16, scale), top_y, cx + _s(16, scale), top_y)
    grad.setColorAt(0, QColor(40, 0, 60))
    grad.setColorAt(0.5, QColor(120, 0, 200))
    grad.setColorAt(1, QColor(40, 0, 60))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor(180, 100, 255, 200), _s(1.5, scale)))
    hw = _s(18, scale)
    pts = [
        QPoint(cx - hw, top_y + _s(8, scale)),
        QPoint(cx - _s(14, scale), top_y - _s(2, scale)),
        QPoint(cx - _s(6, scale), top_y + _s(4, scale)),
        QPoint(cx, top_y - _s(8, scale)),
        QPoint(cx + _s(6, scale), top_y + _s(4, scale)),
        QPoint(cx + _s(14, scale), top_y - _s(2, scale)),
        QPoint(cx + hw, top_y + _s(8, scale)),
    ]
    p.drawPolygon(QPolygon(pts))
    # Pulsing void gem
    gem_r = _s(4, scale) + int(math.sin(phase * 2) * _s(1, scale))
    gem_grad = QRadialGradient(cx, top_y + _s(2, scale), gem_r * 2)
    gem_grad.setColorAt(0, QColor(200, 100, 255, 220))
    gem_grad.setColorAt(1, QColor(80, 0, 150, 0))
    p.setBrush(QBrush(gem_grad))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx, top_y + _s(2, scale)), gem_r, gem_r)


# ═══════════════════════════════════════════════════════════════════════════
#  EYES
# ═══════════════════════════════════════════════════════════════════════════

def draw_cat_eyes(ctx):
    """Cat slit pupils."""
    p, cx, cy, body_w, body_h, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["scale"])
    y = int(cy - body_h * 0.15 + bounce)
    sp = int(body_w * 0.35)
    r = max(2, _s(4, scale))
    # Vertical slit pupils (green)
    p.setBrush(QBrush(QColor(50, 200, 50)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx - sp, y), max(1, r // 2), r)
    p.drawEllipse(QPoint(cx + sp, y), max(1, r // 2), r)
    # Slit
    p.setBrush(QBrush(QColor(10, 10, 10)))
    p.drawEllipse(QPoint(cx - sp, y), max(1, r // 4), r - 1)
    p.drawEllipse(QPoint(cx + sp, y), max(1, r // 4), r - 1)


def draw_starry_eyes(ctx):
    """Starfield in pupils."""
    p, cx, cy, body_w, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy - body_h * 0.15 + bounce)
    sp = int(body_w * 0.35)
    r = max(2, _s(5, scale))
    # Dark blue pupil
    for sx in [cx - sp, cx + sp]:
        grad = QRadialGradient(sx, y, r)
        grad.setColorAt(0, QColor(20, 20, 80))
        grad.setColorAt(1, QColor(0, 0, 30))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(sx, y), r, r)
        # Tiny stars
        p.setBrush(QBrush(QColor(255, 255, 200, 200)))
        for i in range(3):
            angle = phase + i * 2.1
            sx2 = sx + int(math.cos(angle) * r * 0.5)
            sy2 = y + int(math.sin(angle) * r * 0.5)
            p.drawEllipse(QPoint(sx2, sy2), 1, 1)


def draw_sage_eyes(ctx):
    """Sage's golden analytical eyes."""
    p, cx, cy, body_w, body_h, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["scale"])
    y = int(cy - body_h * 0.15 + bounce)
    sp = int(body_w * 0.35)
    r = max(2, _s(5, scale))
    for sx in [cx - sp, cx + sp]:
        grad = QRadialGradient(sx, y, r)
        grad.setColorAt(0, QColor(255, 230, 100))
        grad.setColorAt(0.7, QColor(200, 150, 0))
        grad.setColorAt(1, QColor(100, 70, 0))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(sx, y), r, r)
        # Cross-hair pattern
        p.setPen(QPen(QColor(255, 255, 200, 150), 1))
        p.drawLine(sx - r, y, sx + r, y)
        p.drawLine(sx, y - r, sx, y + r)


def draw_clairvoyance(ctx):
    """Epic clairvoyant eyes — rings of light."""
    draw_sage_eyes(ctx)
    p, cx, cy, body_w, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy - body_h * 0.15 + bounce)
    sp = int(body_w * 0.35)
    r = _s(7, scale)
    # Orbiting ring
    p.setPen(QPen(QColor(255, 220, 100, 120), 1))
    p.setBrush(Qt.NoBrush)
    ring_r = r + _s(2, scale) + int(math.sin(phase * 2) * _s(1, scale))
    for sx in [cx - sp, cx + sp]:
        p.drawEllipse(QPoint(sx, y), ring_r, ring_r)


def draw_demon_lord_eyes(ctx):
    """Legendary — menacing red eyes with glow."""
    p, cx, cy, body_w, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy - body_h * 0.15 + bounce)
    sp = int(body_w * 0.35)
    r = max(2, _s(5, scale))
    for sx in [cx - sp, cx + sp]:
        # Red glow
        glow = QRadialGradient(sx, y, r * 2.5)
        glow.setColorAt(0, QColor(255, 0, 0, 80))
        glow.setColorAt(1, QColor(255, 0, 0, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(sx, y), r * 2, r * 2)
        # Eye
        grad = QRadialGradient(sx, y, r)
        grad.setColorAt(0, QColor(255, 50, 50))
        grad.setColorAt(1, QColor(150, 0, 0))
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPoint(sx, y), r, r)
        # Slit
        p.setBrush(QBrush(QColor(20, 0, 0)))
        p.drawEllipse(QPoint(sx, y), max(1, r // 3), r - 1)


# ═══════════════════════════════════════════════════════════════════════════
#  MOUTH
# ═══════════════════════════════════════════════════════════════════════════

def draw_smile(ctx):
    pass  # Default smile, no override needed


def draw_cat_mouth(ctx):
    """Cat mouth :3"""
    p, cx, cy, body_h, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_h"],
        ctx["bounce"], ctx["scale"])
    y = int(cy + body_h * 0.15 + bounce)
    w = _s(5, scale)
    p.setPen(QPen(QColor(30, 30, 80, 200), _s(1.5, scale)))
    p.setBrush(Qt.NoBrush)
    # :3 — two bumps side by side
    p.drawArc(QRect(cx - w * 2, y - w // 2, w * 2, w), 0, -180 * 16)
    p.drawArc(QRect(cx, y - w // 2, w * 2, w), 0, -180 * 16)


def draw_sharp_teeth(ctx):
    """Sharp fangs."""
    p, cx, cy, body_h, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_h"],
        ctx["bounce"], ctx["scale"])
    y = int(cy + body_h * 0.12 + bounce)
    p.setBrush(QBrush(QColor(255, 255, 255)))
    p.setPen(QPen(QColor(200, 200, 200), 1))
    th = _s(5, scale)
    tw = _s(3, scale)
    # Left fang
    pts_l = [QPoint(cx - _s(6, scale), y),
             QPoint(cx - _s(4, scale), y + th),
             QPoint(cx - _s(2, scale), y)]
    p.drawPolygon(QPolygon(pts_l))
    # Right fang
    pts_r = [QPoint(cx + _s(2, scale), y),
             QPoint(cx + _s(4, scale), y + th),
             QPoint(cx + _s(6, scale), y)]
    p.drawPolygon(QPolygon(pts_r))


# ═══════════════════════════════════════════════════════════════════════════
#  SKIN (override body colors — handled specially in render_equipment)
# ═══════════════════════════════════════════════════════════════════════════

SKIN_COLORS = {
    "classic_blue": {
        "body": QColor(0, 180, 255, 200),
        "highlight": QColor(150, 230, 255, 180),
    },
    "veldora_purple": {
        "body": QColor(120, 80, 200, 210),
        "highlight": QColor(200, 160, 255, 190),
    },
    "flame_red": {
        "body": QColor(220, 60, 30, 220),
        "highlight": QColor(255, 150, 100, 200),
    },
    "golden_body": {
        "body": QColor(255, 200, 50, 230),
        "highlight": QColor(255, 240, 150, 210),
    },
    "void_body": {
        "body": QColor(30, 0, 50, 240),
        "highlight": QColor(120, 50, 180, 200),
    },
}


# ═══════════════════════════════════════════════════════════════════════════
#  CORE (體內晶核)
# ═══════════════════════════════════════════════════════════════════════════

def draw_basic_core(ctx):
    p, cx, cy, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy + bounce)
    r = _s(5, scale)
    grad = QRadialGradient(cx, y, r)
    grad.setColorAt(0, QColor(200, 230, 255, 150))
    grad.setColorAt(1, QColor(100, 180, 255, 50))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx, y), r, r)


def draw_enhanced_core(ctx):
    p, cx, cy, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy + bounce)
    r = _s(6, scale) + int(math.sin(phase * 2) * _s(1, scale))
    grad = QRadialGradient(cx, y, r)
    grad.setColorAt(0, QColor(150, 220, 255, 180))
    grad.setColorAt(0.6, QColor(50, 150, 255, 120))
    grad.setColorAt(1, QColor(0, 80, 200, 0))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx, y), r, r)


def draw_trade_core(ctx):
    p, cx, cy, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy + bounce)
    r = _s(6, scale)
    grad = QRadialGradient(cx, y, r)
    grad.setColorAt(0, QColor(255, 215, 100, 180))
    grad.setColorAt(1, QColor(200, 150, 0, 0))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx, y), r, r)
    # Coin symbol
    p.setPen(QPen(QColor(200, 170, 50, 200), _s(1, scale)))
    p.setFont(QFont("Arial", max(6, _s(8, scale)), QFont.Bold))
    p.drawText(QRect(cx - r, y - r, r * 2, r * 2), Qt.AlignCenter, "$")


def draw_demon_core(ctx):
    p, cx, cy, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy + bounce)
    r = _s(7, scale) + int(math.sin(phase * 2) * _s(1, scale))
    grad = QRadialGradient(cx, y, r)
    grad.setColorAt(0, QColor(200, 50, 255, 200))
    grad.setColorAt(0.5, QColor(100, 0, 180, 120))
    grad.setColorAt(1, QColor(50, 0, 100, 0))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx, y), r, r)


def draw_void_core(ctx):
    p, cx, cy, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy + bounce)
    r = _s(8, scale)
    # Outer void ring
    ring_r = r + _s(3, scale)
    p.setPen(QPen(QColor(150, 50, 200, int(100 + 50 * math.sin(phase * 3))), _s(1.5, scale)))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPoint(cx, y), ring_r, ring_r)
    # Core
    grad = QRadialGradient(cx, y, r)
    grad.setColorAt(0, QColor(180, 80, 255, 200))
    grad.setColorAt(1, QColor(40, 0, 80, 0))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx, y), r, r)


def draw_true_demon_core(ctx):
    draw_void_core(ctx)
    # Extra pulsing ring
    p, cx, cy, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy + bounce)
    r2 = _s(12, scale) + int(math.sin(phase) * _s(3, scale))
    p.setPen(QPen(QColor(255, 50, 100, int(60 + 40 * math.sin(phase * 2))), 1))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPoint(cx, y), r2, r2)


# ═══════════════════════════════════════════════════════════════════════════
#  HANDS (LEFT / RIGHT)
# ═══════════════════════════════════════════════════════════════════════════

def draw_code_book(ctx):
    p, cx, cy, body_w, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["bounce"], ctx["scale"])
    x = cx - body_w - _s(10, scale)
    y = int(cy - _s(5, scale) + bounce)
    bw, bh = _s(12, scale), _s(15, scale)
    # Book body
    p.setBrush(QBrush(QColor(80, 50, 30)))
    p.setPen(QPen(QColor(120, 80, 40), _s(1, scale)))
    p.drawRect(QRect(x, y, bw, bh))
    # Pages
    p.setBrush(QBrush(QColor(240, 230, 210)))
    p.drawRect(QRect(x + _s(2, scale), y + _s(2, scale), bw - _s(4, scale), bh - _s(4, scale)))
    # Code lines
    p.setPen(QPen(QColor(0, 200, 100, 180), 1))
    for i in range(3):
        ly = y + _s(4 + i * 3, scale)
        p.drawLine(x + _s(3, scale), ly, x + bw - _s(4, scale), ly)


def draw_scholar_scroll(ctx):
    p, cx, cy, body_w, bounce, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["bounce"], ctx["scale"])
    x = cx - body_w - _s(12, scale)
    y = int(cy - _s(8, scale) + bounce)
    sw, sh = _s(10, scale), _s(18, scale)
    # Scroll body
    p.setBrush(QBrush(QColor(240, 220, 180)))
    p.setPen(QPen(QColor(180, 150, 100), _s(1, scale)))
    p.drawRect(QRect(x, y, sw, sh))
    # Top/bottom rolls
    p.setBrush(QBrush(QColor(160, 120, 60)))
    p.drawEllipse(QPoint(x + sw // 2, y), sw // 2, _s(3, scale))
    p.drawEllipse(QPoint(x + sw // 2, y + sh), sw // 2, _s(3, scale))


def draw_pixel_sword(ctx):
    p, cx, cy, body_w, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["bounce"],
        ctx["phase"], ctx["scale"])
    x = cx + body_w + _s(5, scale)
    y = int(cy - _s(10, scale) + bounce)
    # Blade
    p.setBrush(QBrush(QColor(200, 210, 220)))
    p.setPen(QPen(QColor(150, 160, 170), _s(1, scale)))
    blade_w = _s(4, scale)
    blade_h = _s(20, scale)
    p.drawRect(QRect(x, y - blade_h, blade_w, blade_h))
    # Guard
    p.setBrush(QBrush(QColor(255, 215, 0)))
    p.drawRect(QRect(x - _s(3, scale), y, blade_w + _s(6, scale), _s(3, scale)))
    # Handle
    p.setBrush(QBrush(QColor(120, 60, 20)))
    p.drawRect(QRect(x, y + _s(3, scale), blade_w, _s(8, scale)))


def draw_storm_blade(ctx):
    draw_pixel_sword(ctx)
    p, cx, cy, body_w, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["bounce"],
        ctx["phase"], ctx["scale"])
    x = cx + body_w + _s(7, scale)
    y = int(cy - _s(15, scale) + bounce)
    # Lightning effect on blade
    p.setPen(QPen(QColor(100, 200, 255, int(150 + 50 * math.sin(phase * 4))), _s(1.5, scale)))
    p.drawLine(x, y - _s(5, scale), x + _s(3, scale), y - _s(10, scale))
    p.drawLine(x + _s(3, scale), y - _s(10, scale), x - _s(1, scale), y - _s(15, scale))


def draw_kusanagi(ctx):
    """Mythic sword — glowing divine blade."""
    p, cx, cy, body_w, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["bounce"],
        ctx["phase"], ctx["scale"])
    x = cx + body_w + _s(5, scale)
    y = int(cy - _s(8, scale) + bounce)
    blade_w = _s(5, scale)
    blade_h = _s(25, scale)
    # Glow
    glow = QRadialGradient(x + blade_w // 2, y - blade_h // 2, blade_h)
    glow.setColorAt(0, QColor(255, 215, 0, 60))
    glow.setColorAt(1, QColor(255, 215, 0, 0))
    p.setBrush(QBrush(glow))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(x + blade_w // 2, y - blade_h // 2), blade_h, blade_h)
    # Blade
    grad = QLinearGradient(x, y, x, y - blade_h)
    grad.setColorAt(0, QColor(220, 220, 240))
    grad.setColorAt(1, QColor(255, 240, 200))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor(255, 215, 0, 180), _s(1, scale)))
    p.drawRect(QRect(x, y - blade_h, blade_w, blade_h))
    # Guard (ornate)
    p.setBrush(QBrush(QColor(255, 200, 50)))
    p.drawEllipse(QPoint(x + blade_w // 2, y), _s(6, scale), _s(3, scale))
    # Handle
    p.setBrush(QBrush(QColor(100, 20, 20)))
    p.drawRect(QRect(x, y + _s(3, scale), blade_w, _s(8, scale)))


# ═══════════════════════════════════════════════════════════════════════════
#  BACKGROUND
# ═══════════════════════════════════════════════════════════════════════════

def draw_night_city(ctx):
    p, w, h, scale = ctx["p"], ctx["w"], ctx["h"], ctx["scale"]
    # City silhouette at bottom
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(20, 25, 40, 140)))
    base_y = int(h * 0.85)
    buildings = [(0.1, 0.15), (0.2, 0.25), (0.35, 0.12), (0.5, 0.2),
                 (0.65, 0.18), (0.8, 0.22), (0.9, 0.1)]
    for bx_pct, bh_pct in buildings:
        bx = int(bx_pct * w)
        bh = int(bh_pct * h)
        bw = _s(10, scale)
        p.drawRect(QRect(bx, base_y - bh, bw, bh))
    # Neon glow windows
    p.setBrush(QBrush(QColor(0, 255, 200, 130)))
    for bx_pct, bh_pct in buildings[:3]:
        bx = int(bx_pct * w) + _s(2, scale)
        by = base_y - int(bh_pct * h) + _s(3, scale)
        p.drawRect(QRect(bx, by, _s(3, scale), _s(2, scale)))


def draw_jura_forest(ctx):
    p, w, h, scale = ctx["p"], ctx["w"], ctx["h"], ctx["scale"]
    # Green trees silhouette
    base_y = int(h * 0.88)
    p.setPen(Qt.NoPen)
    trees = [(0.1, 20), (0.25, 25), (0.4, 18), (0.6, 22), (0.75, 28), (0.9, 16)]
    for tx_pct, th in trees:
        tx = int(tx_pct * w)
        th_s = _s(th, scale)
        # Triangle tree
        p.setBrush(QBrush(QColor(20, 80, 30, 130)))
        pts = [QPoint(tx, base_y),
               QPoint(tx - _s(8, scale), base_y),
               QPoint(tx - _s(4, scale), base_y - th_s)]
        p.drawPolygon(QPolygon(pts))


def draw_demon_castle(ctx):
    p, w, h, phase, scale = ctx["p"], ctx["w"], ctx["h"], ctx["phase"], ctx["scale"]
    base_y = int(h * 0.88)
    # Dark castle silhouette
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(30, 10, 40, 140)))
    cx = w // 2
    # Main tower
    tw = _s(16, scale)
    th = _s(30, scale)
    p.drawRect(QRect(cx - tw // 2, base_y - th, tw, th))
    # Side towers
    stw = _s(10, scale)
    sth = _s(20, scale)
    p.drawRect(QRect(cx - tw - stw, base_y - sth, stw, sth))
    p.drawRect(QRect(cx + tw, base_y - sth, stw, sth))
    # Glowing window
    p.setBrush(QBrush(QColor(255, 50, 50, int(160 + 60 * math.sin(phase * 2)))))
    p.drawEllipse(QPoint(cx, base_y - th + _s(8, scale)), _s(3, scale), _s(4, scale))


def draw_starry_abyss(ctx):
    p, w, h, phase, scale = ctx["p"], ctx["w"], ctx["h"], ctx["phase"], ctx["scale"]
    # Scattered stars around the edges
    p.setPen(Qt.NoPen)
    import random
    rng = random.Random(42)  # Deterministic star positions
    # More stars, brighter, slightly larger — makes the abyss actually
    # visible behind the slime body instead of a barely-there shimmer.
    for _ in range(28):
        sx = rng.randint(0, w)
        sy = rng.randint(0, h)
        brightness = int(180 + 70 * math.sin(phase + rng.random() * 6.28))
        brightness = max(0, min(255, brightness))
        p.setBrush(QBrush(QColor(220, 220, 255, brightness)))
        # Mix of tiny and slightly bigger stars for parallax feel
        star_size = 1 if rng.random() > 0.3 else 2
        p.drawEllipse(QPoint(sx, sy), star_size, star_size)


# ═══════════════════════════════════════════════════════════════════════════
#  MOUNT
# ═══════════════════════════════════════════════════════════════════════════

def draw_hoverboard(ctx):
    p, cx, cy, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy + body_h + _s(8, scale) + bounce)
    bw = _s(25, scale)
    bh = _s(5, scale)
    # Board
    grad = QLinearGradient(cx - bw, y, cx + bw, y)
    grad.setColorAt(0, QColor(50, 50, 60))
    grad.setColorAt(0.5, QColor(0, 180, 255, 200))
    grad.setColorAt(1, QColor(50, 50, 60))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor(0, 200, 255, 150), 1))
    p.drawRoundedRect(QRect(cx - bw, y, bw * 2, bh), 3, 3)
    # Hover glow
    glow = QRadialGradient(cx, y + bh, bw)
    glow.setColorAt(0, QColor(0, 200, 255, 40))
    glow.setColorAt(1, QColor(0, 200, 255, 0))
    p.setBrush(QBrush(glow))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(cx, y + bh + _s(3, scale)), bw, _s(6, scale))


def draw_veldora_mount(ctx):
    p, cx, cy, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy + body_h + _s(5, scale) + bounce)
    # Dragon wing hints
    wing_w = _s(30, scale)
    p.setPen(QPen(QColor(100, 80, 200, 120), _s(1.5, scale)))
    p.setBrush(QBrush(QColor(80, 60, 180, 40)))
    # Left wing
    pts_l = [QPoint(cx - _s(5, scale), y),
             QPoint(cx - wing_w, y - _s(15, scale)),
             QPoint(cx - wing_w + _s(5, scale), y + _s(5, scale))]
    p.drawPolygon(QPolygon(pts_l))
    # Right wing
    pts_r = [QPoint(cx + _s(5, scale), y),
             QPoint(cx + wing_w, y - _s(15, scale)),
             QPoint(cx + wing_w - _s(5, scale), y + _s(5, scale))]
    p.drawPolygon(QPolygon(pts_r))


def draw_void_ship(ctx):
    p, cx, cy, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy + body_h + _s(8, scale) + bounce)
    # Ship hull
    sw = _s(30, scale)
    sh = _s(8, scale)
    grad = QLinearGradient(cx - sw, y, cx + sw, y)
    grad.setColorAt(0, QColor(20, 0, 40))
    grad.setColorAt(0.5, QColor(80, 0, 150))
    grad.setColorAt(1, QColor(20, 0, 40))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor(150, 50, 255, 150), _s(1, scale)))
    pts = [QPoint(cx - sw, y + sh // 2),
           QPoint(cx - sw + _s(5, scale), y - sh // 2),
           QPoint(cx + sw - _s(5, scale), y - sh // 2),
           QPoint(cx + sw, y + sh // 2)]
    p.drawPolygon(QPolygon(pts))
    # Engine glow
    for dx in [-_s(10, scale), _s(10, scale)]:
        glow = QRadialGradient(cx + dx, y + sh, _s(4, scale))
        glow.setColorAt(0, QColor(200, 100, 255, 120))
        glow.setColorAt(1, QColor(200, 100, 255, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(cx + dx, y + sh), _s(4, scale), _s(6, scale))


# ═══════════════════════════════════════════════════════════════════════════
#  VFX (環繞特效)
# ═══════════════════════════════════════════════════════════════════════════

def draw_code_rain(ctx):
    p, w, h, phase, scale = ctx["p"], ctx["w"], ctx["h"], ctx["phase"], ctx["scale"]
    p.setFont(QFont("Consolas", max(6, _s(7, scale))))
    chars = "01{}()<>=/;"
    import random
    rng = random.Random(7)
    for _ in range(8):
        x = rng.randint(_s(5, scale), w - _s(5, scale))
        base_y = rng.randint(0, h)
        speed = rng.uniform(0.5, 1.5)
        y = int((base_y + phase * speed * 30) % h)
        alpha = int(60 + 40 * math.sin(phase + rng.random() * 6))
        p.setPen(QPen(QColor(0, 255, 100, alpha)))
        ch = chars[rng.randint(0, len(chars) - 1)]
        p.drawText(QPoint(x, y), ch)


def draw_lightning_sparks(ctx):
    p, cx, cy, body_w, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    p.setPen(QPen(QColor(100, 200, 255, 180), _s(1.5, scale)))
    for i in range(3):
        angle = phase * 2 + i * 2.1
        r = body_w + _s(15, scale)
        x1 = cx + int(math.cos(angle) * r)
        y1 = int(cy + math.sin(angle) * body_h + bounce)
        x2 = x1 + int(math.cos(angle + 0.5) * _s(8, scale))
        y2 = y1 + int(math.sin(angle + 0.5) * _s(8, scale))
        p.drawLine(x1, y1, x2, y2)
        x3 = x2 + int(math.cos(angle - 0.3) * _s(5, scale))
        y3 = y2 + int(math.sin(angle - 0.3) * _s(5, scale))
        p.drawLine(x2, y2, x3, y3)


def draw_sakura_fall(ctx):
    p, w, h, phase, scale = ctx["p"], ctx["w"], ctx["h"], ctx["phase"], ctx["scale"]
    import random
    rng = random.Random(88)
    for _ in range(6):
        base_x = rng.randint(0, w)
        base_y = rng.randint(0, h)
        speed = rng.uniform(0.3, 0.8)
        x = int((base_x + math.sin(phase * speed) * _s(15, scale)) % w)
        y = int((base_y + phase * speed * 15) % h)
        alpha = int(100 + 50 * math.sin(phase + rng.random() * 6))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(255, 180, 200, alpha)))
        # Petal shape (small ellipse, rotated feel)
        p.drawEllipse(QPoint(x, y), _s(3, scale), _s(2, scale))


def draw_reincarnation_light(ctx):
    p, cx, cy, body_w, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    # Rising light pillars
    for i in range(4):
        angle = phase + i * (math.pi / 2)
        r = body_w + _s(20, scale)
        x = cx + int(math.cos(angle) * r * 0.6)
        base_y = int(cy + body_h + bounce)
        pillar_h = _s(30, scale) + int(math.sin(phase * 2 + i) * _s(5, scale))
        alpha = int(40 + 30 * math.sin(phase * 2 + i))
        grad = QLinearGradient(x, base_y, x, base_y - pillar_h)
        grad.setColorAt(0, QColor(255, 255, 200, alpha))
        grad.setColorAt(1, QColor(255, 255, 200, 0))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRect(QRect(x - _s(2, scale), base_y - pillar_h, _s(4, scale), pillar_h))


def draw_void_rift(ctx):
    p, cx, cy, body_w, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["phase"], ctx["scale"])
    # Dimensional cracks
    for i in range(3):
        angle = phase * 0.5 + i * 2.1
        r = body_w + _s(20, scale)
        x = cx + int(math.cos(angle) * r)
        y = int(ctx["cy"] + math.sin(angle) * r * 0.6)
        length = _s(12, scale)
        # Crack line with purple glow
        glow = QRadialGradient(x, y, length)
        glow.setColorAt(0, QColor(150, 0, 255, 80))
        glow.setColorAt(1, QColor(150, 0, 255, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(x, y), length, _s(3, scale))
        # Bright crack center
        p.setPen(QPen(QColor(200, 150, 255, 180), _s(1, scale)))
        p.drawLine(x - length // 2, y, x + length // 2, y)


# ═══════════════════════════════════════════════════════════════════════════
#  DRONE (跟隨精靈)
# ═══════════════════════════════════════════════════════════════════════════

def draw_observer_sprite(ctx):
    p, cx, cy, body_w, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["bounce"],
        ctx["phase"], ctx["scale"])
    # Floating light orb
    ox = cx + body_w + _s(18, scale) + int(math.cos(phase) * _s(5, scale))
    oy = int(cy - _s(15, scale) + math.sin(phase * 1.5) * _s(8, scale) + bounce)
    r = _s(5, scale)
    # Glow
    glow = QRadialGradient(ox, oy, r * 3)
    glow.setColorAt(0, QColor(100, 200, 255, 80))
    glow.setColorAt(1, QColor(100, 200, 255, 0))
    p.setBrush(QBrush(glow))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(ox, oy), r * 3, r * 3)
    # Core
    p.setBrush(QBrush(QColor(150, 230, 255, 200)))
    p.drawEllipse(QPoint(ox, oy), r, r)


def draw_merchant_sprite(ctx):
    p, cx, cy, body_w, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["bounce"],
        ctx["phase"], ctx["scale"])
    ox = cx + body_w + _s(20, scale) + int(math.cos(phase * 0.8) * _s(6, scale))
    oy = int(cy - _s(10, scale) + math.sin(phase) * _s(6, scale) + bounce)
    r = _s(5, scale)
    # Gold orb
    p.setBrush(QBrush(QColor(255, 215, 100, 200)))
    p.setPen(QPen(QColor(200, 170, 0, 150), 1))
    p.drawEllipse(QPoint(ox, oy), r, r)
    # $ sign
    p.setPen(QPen(QColor(150, 100, 0), 1))
    p.setFont(QFont("Arial", max(5, _s(6, scale)), QFont.Bold))
    p.drawText(QRect(ox - r, oy - r, r * 2, r * 2), Qt.AlignCenter, "$")


def draw_social_drone(ctx):
    p, cx, cy, body_w, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["bounce"],
        ctx["phase"], ctx["scale"])
    ox = cx + body_w + _s(18, scale) + int(math.cos(phase * 0.7) * _s(5, scale))
    oy = int(cy - _s(12, scale) + math.sin(phase * 1.2) * _s(7, scale) + bounce)
    r = _s(6, scale)
    # Metallic drone body
    p.setBrush(QBrush(QColor(80, 90, 100)))
    p.setPen(QPen(QColor(120, 130, 140), 1))
    p.drawEllipse(QPoint(ox, oy), r, _s(4, scale))
    # Propeller spin
    prop_a = phase * 8
    pw = _s(8, scale)
    p.setPen(QPen(QColor(200, 200, 200, 150), 1))
    p.drawLine(
        ox + int(math.cos(prop_a) * pw), oy - _s(4, scale),
        ox - int(math.cos(prop_a) * pw), oy - _s(4, scale))
    # LED
    p.setBrush(QBrush(QColor(0, 255, 100, 180)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(ox, oy), _s(2, scale), _s(2, scale))


def draw_raphael_drone(ctx):
    """Ultimate — Great Sage Raphael as a hovering entity."""
    p, cx, cy, body_w, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_w"], ctx["bounce"],
        ctx["phase"], ctx["scale"])
    ox = cx + body_w + _s(22, scale) + int(math.cos(phase * 0.6) * _s(6, scale))
    oy = int(cy - _s(15, scale) + math.sin(phase) * _s(8, scale) + bounce)
    r = _s(8, scale)
    # Radiant glow
    glow = QRadialGradient(ox, oy, r * 3)
    glow.setColorAt(0, QColor(255, 215, 100, 100))
    glow.setColorAt(0.5, QColor(200, 150, 255, 40))
    glow.setColorAt(1, QColor(200, 150, 255, 0))
    p.setBrush(QBrush(glow))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPoint(ox, oy), r * 3, r * 3)
    # Core (golden)
    core_grad = QRadialGradient(ox, oy, r)
    core_grad.setColorAt(0, QColor(255, 240, 180, 220))
    core_grad.setColorAt(1, QColor(200, 150, 50, 150))
    p.setBrush(QBrush(core_grad))
    p.drawEllipse(QPoint(ox, oy), r, r)
    # Analytical eye
    p.setPen(QPen(QColor(255, 255, 200, 200), 1))
    p.drawLine(ox - r // 2, oy, ox + r // 2, oy)
    p.drawLine(ox, oy - r // 2, ox, oy + r // 2)
    # Orbiting ring
    ring_r = r + _s(3, scale)
    p.setPen(QPen(QColor(255, 215, 0, int(100 + 50 * math.sin(phase * 3))), 1))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPoint(ox, oy), ring_r, ring_r)


# ═══════════════════════════════════════════════════════════════════════════
#  TITLE (動態稱號) — drawn as floating text above slime
# ═══════════════════════════════════════════════════════════════════════════

def draw_title(ctx, title_name: str, rarity: str):
    """Draw title text floating above the slime."""
    p, cx, cy, body_h, bounce, phase, scale = (
        ctx["p"], ctx["cx"], ctx["cy"], ctx["body_h"],
        ctx["bounce"], ctx["phase"], ctx["scale"])
    y = int(cy - body_h - _s(20, scale) + bounce + math.sin(phase) * _s(2, scale))
    rarity_colors = {
        "common": QColor(170, 170, 170),
        "uncommon": QColor(46, 213, 115),
        "rare": QColor(30, 144, 255),
        "epic": QColor(168, 85, 247),
        "legendary": QColor(255, 165, 2),
        "mythic": QColor(255, 71, 87),
        "ultimate": QColor(255, 215, 0),
    }
    color = rarity_colors.get(rarity, QColor(200, 200, 200))
    # Text with glow
    font = QFont("Microsoft JhengHei", max(7, _s(9, scale)), QFont.Bold)
    p.setFont(font)
    fm = p.fontMetrics()
    tw = fm.horizontalAdvance(title_name)
    tx = cx - tw // 2
    # Glow background
    p.setPen(Qt.NoPen)
    gc = QColor(color)
    gc.setAlpha(30)
    p.setBrush(QBrush(gc))
    p.drawRoundedRect(QRect(tx - 4, y - fm.height(), tw + 8, fm.height() + 4), 4, 4)
    # Text
    p.setPen(QPen(color))
    p.drawText(QPoint(tx, y), title_name)


# ═══════════════════════════════════════════════════════════════════════════
#  VISUAL REGISTRY — maps visual key → draw function
# ═══════════════════════════════════════════════════════════════════════════

VISUAL_REGISTRY = {
    # Helmet
    "hacker_goggles": draw_hacker_goggles,
    "cat_ears": draw_cat_ears,
    "sage_crown": draw_sage_crown,
    "demon_horns": draw_demon_horns,
    "dragon_skull_crown": draw_dragon_skull_crown,
    "veldora_crown": draw_veldora_crown,
    "void_diadem": draw_void_diadem,
    # Eyes
    "cat_eyes": draw_cat_eyes,
    "starry_eyes": draw_starry_eyes,
    "sage_eyes": draw_sage_eyes,
    "clairvoyance": draw_clairvoyance,
    "demon_lord_eyes": draw_demon_lord_eyes,
    # Mouth
    "smile": draw_smile,
    "cat_mouth": draw_cat_mouth,
    "sharp_teeth": draw_sharp_teeth,
    # Core
    "basic_core": draw_basic_core,
    "enhanced_core": draw_enhanced_core,
    "trade_core": draw_trade_core,
    "demon_core": draw_demon_core,
    "void_core": draw_void_core,
    "true_demon_core": draw_true_demon_core,
    # Left hand
    "code_book": draw_code_book,
    "scholar_scroll": draw_scholar_scroll,
    # Right hand
    "pixel_sword": draw_pixel_sword,
    "storm_blade": draw_storm_blade,
    "kusanagi": draw_kusanagi,
    # Background
    "night_city": draw_night_city,
    "jura_forest": draw_jura_forest,
    "demon_castle": draw_demon_castle,
    "starry_abyss": draw_starry_abyss,
    # Mount
    "hoverboard": draw_hoverboard,
    "veldora_mount": draw_veldora_mount,
    "void_ship": draw_void_ship,
    # VFX
    "code_rain": draw_code_rain,
    "lightning_sparks": draw_lightning_sparks,
    "sakura_fall": draw_sakura_fall,
    "reincarnation_light": draw_reincarnation_light,
    "void_rift": draw_void_rift,
    # Drone
    "observer_sprite": draw_observer_sprite,
    "merchant_sprite": draw_merchant_sprite,
    "social_drone": draw_social_drone,
    "raphael_drone": draw_raphael_drone,
}

# Draw order: background first, then mount, body-layer (core, skin handled
# separately), then face (eyes, mouth), then helmet, hands, vfx, drone, title.
DRAW_ORDER = [
    "background", "mount", "core",
    "eyes", "mouth",
    "helmet", "left_hand", "right_hand",
    "vfx", "drone", "title",
]


def render_equipment(painter, equipped_visuals: dict, ctx: dict):
    """Render all equipped items onto the slime.

    Args:
        painter: QPainter instance
        equipped_visuals: dict of {slot: {"visual": str, "rarity": str, "name": str}}
        ctx: drawing context dict
    """
    ctx["p"] = painter

    for slot in DRAW_ORDER:
        info = equipped_visuals.get(slot)
        if not info:
            continue

        visual_key = info.get("visual")
        if not visual_key:
            continue

        # Title is special — needs the name and rarity
        if slot == "title":
            draw_title(ctx, info.get("name", ""), info.get("rarity", "common"))
            continue

        draw_fn = VISUAL_REGISTRY.get(visual_key)
        if draw_fn:
            ctx["rarity"] = info.get("rarity", "common")
            draw_fn(ctx)


def get_skin_override(visual_key: str) -> dict | None:
    """Return body color override for a skin visual, or None."""
    return SKIN_COLORS.get(visual_key)


# ═══════════════════════════════════════════════════════════════════════════
#  EQUIPMENT ICON WIDGET
# ═══════════════════════════════════════════════════════════════════════════

def render_equipment_icon(painter, size: int, visual_key: str,
                          rarity: str, slot: str):
    """Render a small preview of an equipment's visual onto `painter`.

    Used for inventory / market card thumbnails. Draws a neutral mini-slime
    body as backdrop and overlays the equipment.
    """
    painter.setRenderHint(QPainter.Antialiasing)

    cx = size // 2
    cy = int(size * 0.55)
    body_w = int(size * 0.32)
    body_h = int(size * 0.26)
    scale = size / 120.0  # Scale factor relative to main avatar (~200px)

    # Rarity-tinted background
    glow = RARITY_GLOW.get(rarity, RARITY_GLOW["common"])
    bg = QColor(glow)
    bg.setAlpha(40)
    painter.setBrush(QBrush(bg))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, size, size, 4, 4)

    # Slime body colors (can be overridden by skin)
    body_color = QColor(0, 180, 255, 200)
    highlight_color = QColor(150, 230, 255, 180)

    skin_override = get_skin_override(visual_key) if slot == "skin" else None
    if skin_override:
        body_color = QColor(skin_override["body"])
        highlight_color = QColor(skin_override["highlight"])

    ctx = {
        "p": painter, "cx": cx, "cy": cy,
        "body_w": body_w, "body_h": body_h,
        "bounce": 0, "phase": 0.0,
        "w": size, "h": size,
        "tier_index": 2, "scale": scale,
        "rarity": rarity,
    }

    # Draw background equipment first (so slime sits on top)
    if slot == "background":
        draw_fn = VISUAL_REGISTRY.get(visual_key)
        if draw_fn:
            draw_fn(ctx)

    # Mini slime body (so equipment has context to attach to)
    gradient = QRadialGradient(cx - body_w * 0.2, cy - body_h * 0.3, body_w * 1.2)
    gradient.setColorAt(0, highlight_color)
    gradient.setColorAt(1, body_color)
    painter.setBrush(QBrush(gradient))
    outline = QColor(body_color)
    outline.setAlpha(180)
    painter.setPen(QPen(outline, 1))
    painter.drawEllipse(QRect(cx - body_w, cy - body_h, body_w * 2, body_h * 2))

    # Draw equipment on top (unless it's background or skin, already handled)
    if slot == "title":
        # Title: just render the name in rarity color
        from PySide6.QtGui import QFont
        color = {
            "common": QColor(170, 170, 170), "uncommon": QColor(46, 213, 115),
            "rare": QColor(30, 144, 255), "epic": QColor(168, 85, 247),
            "legendary": QColor(255, 165, 2), "mythic": QColor(255, 71, 87),
            "ultimate": QColor(255, 215, 0),
        }.get(rarity, QColor(200, 200, 200))
        painter.setPen(QPen(color))
        f = QFont("Segoe UI Emoji", max(10, int(size * 0.22)), QFont.Bold)
        painter.setFont(f)
        painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, "【稱】")
    elif slot not in ("background", "skin"):
        draw_fn = VISUAL_REGISTRY.get(visual_key)
        if draw_fn:
            draw_fn(ctx)


# Lightweight QWidget wrapper for use in inventory/market cards.
try:
    from PySide6.QtWidgets import QWidget

    class EquipmentIcon(QWidget):
        """Small square widget showing a preview of one equipment visual."""

        def __init__(self, visual_key: str, rarity: str, slot: str,
                     size: int = 44, parent=None):
            super().__init__(parent)
            self._visual = visual_key or ""
            self._rarity = rarity or "common"
            self._slot = slot or ""
            self._size = size
            self.setFixedSize(size, size)

        def paintEvent(self, event):
            p = QPainter(self)
            try:
                render_equipment_icon(
                    p, self._size, self._visual, self._rarity, self._slot,
                )
            finally:
                p.end()
except ImportError:
    pass
