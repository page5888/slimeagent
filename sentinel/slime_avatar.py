"""Pixel-art Slime Avatar Generator.

Generates cute pixel-art slime images that evolve based on AI Slime's current form.
Each tier has a different look, and dominant traits add visual accessories.
Uses QPainter for pure programmatic rendering - no external assets needed.
"""
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QRadialGradient, QPen, QBrush, QFont
from PySide6.QtCore import Qt, QRect, QPoint, QTimer
import math
import time


# Color palettes per evolution tier
TIER_COLORS = {
    "Slime": {
        "body": QColor(0, 180, 255, 200),      # 淺藍透明
        "highlight": QColor(150, 230, 255, 180),
        "eye": QColor(30, 30, 30),
        "mouth": QColor(30, 30, 30),
        "glow": QColor(0, 180, 255, 40),
    },
    "Slime+": {
        "body": QColor(0, 200, 255, 210),       # 更亮的藍
        "highlight": QColor(180, 240, 255, 190),
        "eye": QColor(20, 20, 20),
        "mouth": QColor(20, 20, 20),
        "glow": QColor(0, 220, 255, 60),
    },
    "Named Slime": {
        "body": QColor(0, 220, 255, 220),       # 被命名後更有存在感
        "highlight": QColor(200, 250, 255, 200),
        "eye": QColor(10, 10, 10),
        "mouth": QColor(10, 10, 10),
        "glow": QColor(0, 220, 255, 80),
    },
    "Majin": {
        "body": QColor(80, 120, 255, 230),      # 偏紫藍 - 魔人形態
        "highlight": QColor(180, 200, 255, 200),
        "eye": QColor(255, 200, 0),              # 金色眼睛
        "mouth": QColor(40, 40, 60),
        "glow": QColor(100, 100, 255, 80),
    },
    "Demon Lord Seed": {
        "body": QColor(120, 50, 200, 230),       # 紫色 - 魔王種
        "highlight": QColor(200, 150, 255, 200),
        "eye": QColor(255, 50, 50),               # 紅色眼睛
        "mouth": QColor(60, 20, 60),
        "glow": QColor(150, 50, 200, 100),
    },
    "True Demon Lord": {
        "body": QColor(200, 30, 80, 240),        # 深紅 - 真魔王
        "highlight": QColor(255, 150, 180, 200),
        "eye": QColor(255, 215, 0),               # 金色
        "mouth": QColor(80, 10, 30),
        "glow": QColor(200, 30, 80, 120),
    },
    "Ultimate Slime": {
        "body": QColor(255, 215, 0, 240),         # 金色 - 究極
        "highlight": QColor(255, 250, 200, 220),
        "eye": QColor(100, 0, 150),
        "mouth": QColor(100, 50, 0),
        "glow": QColor(255, 215, 0, 150),
    },
}

# Trait visual accessories
TRAIT_ACCESSORIES = {
    "coding": {"symbol": "</>", "color": QColor(0, 255, 120)},
    "communication": {"symbol": "💬", "color": QColor(0, 200, 255)},
    "research": {"symbol": "🔍", "color": QColor(255, 200, 0)},
    "creative": {"symbol": "✨", "color": QColor(255, 100, 200)},
    "multitasking": {"symbol": "⚡", "color": QColor(255, 165, 0)},
    "deep_focus": {"symbol": "🎯", "color": QColor(0, 255, 200)},
    "late_night": {"symbol": "🌙", "color": QColor(150, 150, 255)},
}


class SlimeWidget(QWidget):
    """Animated pixel-art slime avatar widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self._form = "Slime"
        self._traits = []
        self._skill_count = 0
        self._title = "初生史萊姆"
        self._equipped_visuals = {}  # {slot: {"visual", "rarity", "name"}}

        # Animation
        self._anim_phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)  # 20 FPS

        # Particle system for glow
        self._particles = []

    def set_state(self, form: str, title: str, traits: list, skill_count: int):
        """Update the slime's appearance based on evolution state."""
        self._form = form
        self._title = title
        self._traits = traits[:3]  # Max 3 visible traits
        self._skill_count = skill_count
        self._load_equipped_visuals()
        self.update()

    def _load_equipped_visuals(self):
        """Load equipped item visuals from equipment state."""
        try:
            from sentinel.wallet.equipment import load_equipment, EQUIPMENT_POOL
            state = load_equipment()
            visuals = {}
            for slot, item_id in state.equipped.items():
                if not item_id:
                    continue
                # Find the item in inventory
                item = next((i for i in state.inventory if i["item_id"] == item_id), None)
                if not item:
                    continue
                # Find the template
                template = next(
                    (t for t in EQUIPMENT_POOL if t["name"] == item["template_name"]),
                    None,
                )
                if template and template.get("visual"):
                    visuals[slot] = {
                        "visual": template["visual"],
                        "rarity": item["rarity"],
                        "name": item["template_name"],
                    }
            self._equipped_visuals = visuals
        except Exception:
            self._equipped_visuals = {}

    def _tick(self):
        self._anim_phase += 0.05
        if self._anim_phase > math.pi * 2:
            self._anim_phase -= math.pi * 2

        # Random particles
        if len(self._particles) < 8 and self._form != "Slime":
            import random
            if random.random() < 0.1:
                self._particles.append({
                    "x": random.uniform(0.3, 0.7),
                    "y": random.uniform(0.6, 0.9),
                    "life": 1.0,
                    "speed": random.uniform(0.005, 0.015),
                    "size": random.uniform(2, 5),
                })

        # Update particles
        alive = []
        for p in self._particles:
            p["y"] -= p["speed"]
            p["life"] -= 0.02
            if p["life"] > 0:
                alive.append(p)
        self._particles = alive

        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        cx = w // 2
        cy = h // 2

        colors = TIER_COLORS.get(self._form, TIER_COLORS["Slime"])

        # Skin override from equipment
        skin_info = self._equipped_visuals.get("skin")
        if skin_info:
            from sentinel.equipment_visuals import get_skin_override
            override = get_skin_override(skin_info.get("visual", ""))
            if override:
                colors = dict(colors)  # copy
                colors["body"] = override["body"]
                colors["highlight"] = override["highlight"]

        # Breathing animation
        breath = math.sin(self._anim_phase) * 0.03
        bounce = math.sin(self._anim_phase * 2) * 2

        # Body size (grows with tier)
        tier_index = list(TIER_COLORS.keys()).index(self._form) if self._form in TIER_COLORS else 0
        base_size = 50 + tier_index * 5
        body_w = int(base_size * (1.0 + breath))
        body_h = int(base_size * 0.8 * (1.0 - breath))

        # Equipment drawing context
        equip_ctx = {
            "cx": cx, "cy": cy, "body_w": body_w, "body_h": body_h,
            "bounce": bounce, "phase": self._anim_phase,
            "w": w, "h": h, "tier_index": tier_index, "scale": 1.0,
        }

        # ─── Background equipment (drawn first) ───
        if self._equipped_visuals.get("background"):
            from sentinel.equipment_visuals import VISUAL_REGISTRY
            bg_fn = VISUAL_REGISTRY.get(self._equipped_visuals["background"].get("visual", ""))
            if bg_fn:
                equip_ctx["p"] = p
                bg_fn(equip_ctx)

        # ─── Glow aura ───
        glow_color = colors["glow"]
        if tier_index >= 2:
            gradient = QRadialGradient(cx, cy + bounce, body_w * 1.8)
            gradient.setColorAt(0, glow_color)
            gradient.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(QBrush(gradient))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPoint(cx, int(cy + bounce)), int(body_w * 1.8), int(body_w * 1.5))

        # ─── Particles ───
        for pt in self._particles:
            px = int(pt["x"] * w)
            py = int(pt["y"] * h)
            alpha = int(pt["life"] * 150)
            pc = QColor(colors["glow"])
            pc.setAlpha(alpha)
            p.setBrush(QBrush(pc))
            p.setPen(Qt.NoPen)
            size = int(pt["size"])
            p.drawEllipse(QPoint(px, py), size, size)

        # ─── Shadow ───
        shadow_y = cy + body_h + 5 + bounce
        p.setBrush(QBrush(QColor(0, 0, 0, 30)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(cx, int(shadow_y)), body_w - 5, 8)

        # ─── Body (main slime shape) ───
        body_gradient = QRadialGradient(cx - body_w * 0.2, cy - body_h * 0.3 + bounce, body_w * 1.2)
        body_gradient.setColorAt(0, colors["highlight"])
        body_gradient.setColorAt(0.6, colors["body"])
        body_gradient.setColorAt(1, QColor(colors["body"].red() // 2, colors["body"].green() // 2, colors["body"].blue() // 2, colors["body"].alpha()))
        p.setBrush(QBrush(body_gradient))

        # Outline
        outline_color = QColor(colors["body"])
        outline_color.setAlpha(180)
        p.setPen(QPen(outline_color, 2))

        # Draw body as smooth ellipse (like a droplet)
        body_rect = QRect(cx - body_w, int(cy - body_h + bounce), body_w * 2, body_h * 2)
        p.drawEllipse(body_rect)

        # ─── Small top bump (slime antenna) ───
        if tier_index >= 1:
            bump_h = 10 + tier_index * 3
            bump_w = 8 + tier_index * 2
            bump_cx = cx + int(math.sin(self._anim_phase * 0.7) * 3)
            bump_cy = int(cy - body_h + bounce - bump_h * 0.3)
            bump_gradient = QRadialGradient(bump_cx, bump_cy - bump_h * 0.3, bump_w * 1.5)
            bump_gradient.setColorAt(0, colors["highlight"])
            bump_gradient.setColorAt(1, colors["body"])
            p.setBrush(QBrush(bump_gradient))
            p.drawEllipse(QPoint(bump_cx, bump_cy), bump_w, bump_h)

        # ─── Eyes ───
        has_eye_equip = "eyes" in self._equipped_visuals
        if not has_eye_equip:
            eye_y = int(cy - body_h * 0.15 + bounce)
            eye_spacing = int(body_w * 0.35)
            eye_size = max(4, 3 + tier_index)

            # Eye whites
            p.setBrush(QBrush(QColor(255, 255, 255, 220)))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPoint(cx - eye_spacing, eye_y), eye_size + 2, eye_size + 2)
            p.drawEllipse(QPoint(cx + eye_spacing, eye_y), eye_size + 2, eye_size + 2)

            # Pupils
            p.setBrush(QBrush(colors["eye"]))
            blink = abs(math.sin(self._anim_phase * 0.3))
            pupil_h = max(1, int(eye_size * blink))
            p.drawEllipse(QPoint(cx - eye_spacing, eye_y), eye_size - 1, pupil_h)
            p.drawEllipse(QPoint(cx + eye_spacing, eye_y), eye_size - 1, pupil_h)

            # Eye shine
            p.setBrush(QBrush(QColor(255, 255, 255, 200)))
            p.drawEllipse(QPoint(cx - eye_spacing - 1, eye_y - 2), 2, 2)
            p.drawEllipse(QPoint(cx + eye_spacing - 1, eye_y - 2), 2, 2)

        # ─── Mouth ───
        has_mouth_equip = "mouth" in self._equipped_visuals
        if not has_mouth_equip:
            mouth_y = int(cy + body_h * 0.15 + bounce)
            p.setPen(QPen(colors["mouth"], 2))
            p.setBrush(Qt.NoBrush)

            # Smile width grows with tier
            smile_w = 6 + tier_index * 2
            if tier_index >= 4:
                # Big grin for Demon Lord+
                p.drawArc(QRect(cx - smile_w, mouth_y - smile_w // 2, smile_w * 2, smile_w),
                           0, -180 * 16)
            else:
                # Cute small smile
                p.drawArc(QRect(cx - smile_w, mouth_y - 3, smile_w * 2, 6),
                           0, -180 * 16)

        # ─── Trait accessories (floating around slime) ───
        for i, trait in enumerate(self._traits):
            acc = TRAIT_ACCESSORIES.get(trait)
            if not acc:
                continue
            angle = self._anim_phase + i * (math.pi * 2 / max(len(self._traits), 1))
            orbit_r = body_w + 25
            ax = cx + int(math.cos(angle) * orbit_r)
            ay = int(cy + math.sin(angle) * orbit_r * 0.5 + bounce)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(acc["color"].red(), acc["color"].green(), acc["color"].blue(), 100)))
            p.drawEllipse(QPoint(ax, ay), 12, 12)
            p.setPen(QPen(QColor(255, 255, 255, 200)))
            font = QFont("Segoe UI Emoji", 10)
            p.setFont(font)
            p.drawText(QRect(ax - 10, ay - 10, 20, 20), Qt.AlignCenter, acc["symbol"])

        # ─── Crown for Demon Lord+ (skip if helmet equipped) ───
        has_helmet = "helmet" in self._equipped_visuals
        if tier_index >= 4 and not has_helmet:
            crown_y = int(cy - body_h - 15 + bounce)
            crown_color = QColor(255, 215, 0) if tier_index >= 5 else QColor(200, 100, 255)
            p.setPen(QPen(crown_color, 2))
            p.setBrush(QBrush(crown_color))
            pts = [
                QPoint(cx - 15, crown_y + 8),
                QPoint(cx - 12, crown_y),
                QPoint(cx - 5, crown_y + 5),
                QPoint(cx, crown_y - 5),
                QPoint(cx + 5, crown_y + 5),
                QPoint(cx + 12, crown_y),
                QPoint(cx + 15, crown_y + 8),
            ]
            from PySide6.QtGui import QPolygon
            p.drawPolygon(QPolygon(pts))

        # ─── Equipment visuals ───
        if self._equipped_visuals:
            from sentinel.equipment_visuals import render_equipment
            # Don't re-draw background (already drawn above)
            equip_to_draw = {k: v for k, v in self._equipped_visuals.items()
                            if k != "background"}
            equip_ctx["p"] = p
            render_equipment(p, equip_to_draw, equip_ctx)

        p.end()
