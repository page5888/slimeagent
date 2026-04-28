"""Floating transparent slime overlay — always-on-top desktop pet.

A semi-transparent, draggable slime that floats on the desktop.
Shows the current evolution form, reacts to notifications,
and can be double-clicked to open the main window.
"""
import logging
import math
import time
from PySide6.QtWidgets import QWidget, QApplication, QMenu
from PySide6.QtGui import (
    QPainter, QColor, QRadialGradient, QPen, QBrush, QFont, QCursor,
    QAction, QPixmap, QTransform,
)
from PySide6.QtCore import Qt, QPoint, QRect, QTimer, Signal, QPropertyAnimation, QEasingCurve

from sentinel.slime_avatar import TIER_COLORS, TRAIT_ACCESSORIES
from sentinel import avatar as _avatar

log = logging.getLogger("sentinel.overlay")


class SlimeOverlay(QWidget):
    """Transparent floating slime widget — desktop pet style."""

    open_main_window = Signal()

    # Overlay size
    SIZE = 120

    def __init__(self, parent=None):
        super().__init__(parent)

        # Window flags: frameless, always-on-top, transparent background, tool window
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # Don't show in taskbar
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.SIZE, self.SIZE)

        # Start position: bottom-right corner
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - self.SIZE - 20, screen.height() - self.SIZE - 80)

        # State
        self._form = "Slime"
        self._traits = []
        self._title = "初生史萊姆"
        self._equipped_visuals = {}
        self._anim_phase = 0.0
        self._opacity = 0.75  # Base opacity (semi-transparent)
        self._hover = False
        self._particles = []

        # Notification bubble
        self._bubble_text = ""
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._clear_bubble)

        # Self-portrait avatar override (None = procedural slime). When
        # the user picks a portrait from the album, we load the cutout
        # PNG here and the paintEvent takes a different branch — same
        # breath/bounce math, applied to the pixmap instead of drawing
        # the procedural body.
        self._avatar_pixmap: QPixmap | None = None
        self._reload_avatar_override()

        # Drag support
        self._drag_pos = None

        # Animation
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)  # 20 FPS

        # Context menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def set_state(self, form: str, title: str, traits: list):
        """Update slime appearance."""
        self._form = form
        self._title = title
        self._traits = traits[:3]
        self._load_equipped_visuals()
        self.update()

    def _reload_avatar_override(self):
        """Read the persisted avatar override path and load it as a
        QPixmap, or clear it if absent."""
        path = _avatar.get_avatar_override()
        if path is None:
            self._avatar_pixmap = None
            return
        pix = QPixmap(str(path))
        self._avatar_pixmap = pix if not pix.isNull() else None
        if self._avatar_pixmap is None:
            log.warning(f"overlay: avatar override path {path} loaded as null pixmap")

    def set_avatar(self, path: str | None):
        """Public API: switch to / clear the self-portrait avatar at
        runtime. Called from the album dialog after the user picks a
        new portrait."""
        if path:
            pix = QPixmap(str(path))
            self._avatar_pixmap = pix if not pix.isNull() else None
        else:
            self._avatar_pixmap = None
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
                item = next((i for i in state.inventory if i["item_id"] == item_id), None)
                if not item:
                    continue
                template = next(
                    (t for t in EQUIPMENT_POOL if t["name"] == item["template_name"]), None)
                if template and template.get("visual"):
                    visuals[slot] = {
                        "visual": template["visual"],
                        "rarity": item["rarity"],
                        "name": item["template_name"],
                    }
            self._equipped_visuals = visuals
        except Exception:
            self._equipped_visuals = {}

    def show_bubble(self, text: str, duration_ms: int = 5000):
        """Show a notification bubble above the slime."""
        # Truncate long text
        if len(text) > 60:
            text = text[:57] + "..."
        self._bubble_text = text
        self._bubble_timer.stop()
        self._bubble_timer.start(duration_ms)
        self.update()

    def _clear_bubble(self):
        self._bubble_text = ""
        self.update()

    def _tick(self):
        self._anim_phase += 0.05
        if self._anim_phase > math.pi * 2:
            self._anim_phase -= math.pi * 2

        # Particles for higher tiers
        tier_index = list(TIER_COLORS.keys()).index(self._form) if self._form in TIER_COLORS else 0
        if tier_index >= 2 and len(self._particles) < 5:
            import random
            if random.random() < 0.08:
                self._particles.append({
                    "x": random.uniform(0.25, 0.75),
                    "y": random.uniform(0.5, 0.85),
                    "life": 1.0,
                    "speed": random.uniform(0.008, 0.02),
                    "size": random.uniform(1.5, 3.5),
                })

        alive = []
        for p in self._particles:
            p["y"] -= p["speed"]
            p["life"] -= 0.025
            if p["life"] > 0:
                alive.append(p)
        self._particles = alive

        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        w = self.width()
        h = self.height()
        cx = w // 2
        cy = int(h * 0.55)

        # Overall opacity
        opacity = 0.95 if self._hover else self._opacity
        p.setOpacity(opacity)

        # Self-portrait avatar mode — when the user picked a portrait
        # from the album, we skip the procedural body / equipment /
        # antennae and draw the cutout pixmap with the same breath +
        # bounce math that drives the procedural slime, so it still
        # feels alive (single static image, V-tuber-style idle anim).
        if self._avatar_pixmap is not None:
            self._paint_avatar(p, w, h, cx, cy)
            self._paint_bubble(p, w, cx)
            p.end()
            return

        colors = TIER_COLORS.get(self._form, TIER_COLORS["Slime"])
        tier_index = list(TIER_COLORS.keys()).index(self._form) if self._form in TIER_COLORS else 0

        # Skin override
        skin_info = self._equipped_visuals.get("skin")
        if skin_info:
            from sentinel.equipment_visuals import get_skin_override
            override = get_skin_override(skin_info.get("visual", ""))
            if override:
                colors = dict(colors)
                colors["body"] = override["body"]
                colors["highlight"] = override["highlight"]

        # Breathing
        breath = math.sin(self._anim_phase) * 0.03
        bounce = math.sin(self._anim_phase * 2) * 1.5

        SCALE = 0.6
        base_size = 30 + tier_index * 3
        body_w = int(base_size * (1.0 + breath))
        body_h = int(base_size * 0.8 * (1.0 - breath))

        # Equipment context
        equip_ctx = {
            "cx": cx, "cy": cy, "body_w": body_w, "body_h": body_h,
            "bounce": bounce, "phase": self._anim_phase,
            "w": w, "h": h, "tier_index": tier_index, "scale": SCALE,
        }

        # Background equipment
        if self._equipped_visuals.get("background"):
            from sentinel.equipment_visuals import VISUAL_REGISTRY
            bg_fn = VISUAL_REGISTRY.get(self._equipped_visuals["background"].get("visual", ""))
            if bg_fn:
                equip_ctx["p"] = p
                bg_fn(equip_ctx)

        # Glow aura
        if tier_index >= 2:
            glow = QRadialGradient(cx, cy + bounce, body_w * 1.6)
            glow.setColorAt(0, colors["glow"])
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(QBrush(glow))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPoint(cx, int(cy + bounce)), int(body_w * 1.6), int(body_w * 1.3))

        # Particles
        for pt in self._particles:
            px = int(pt["x"] * w)
            py = int(pt["y"] * h)
            alpha = int(pt["life"] * 120)
            pc = QColor(colors["glow"])
            pc.setAlpha(alpha)
            p.setBrush(QBrush(pc))
            p.setPen(Qt.NoPen)
            sz = int(pt["size"])
            p.drawEllipse(QPoint(px, py), sz, sz)

        # Shadow
        shadow_y = cy + body_h + 3 + bounce
        p.setBrush(QBrush(QColor(0, 0, 0, 25)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(cx, int(shadow_y)), body_w - 3, 5)

        # Body
        body_grad = QRadialGradient(cx - body_w * 0.2, cy - body_h * 0.3 + bounce, body_w * 1.2)
        body_grad.setColorAt(0, colors["highlight"])
        body_grad.setColorAt(0.6, colors["body"])
        darker = QColor(colors["body"].red() // 2, colors["body"].green() // 2,
                        colors["body"].blue() // 2, colors["body"].alpha())
        body_grad.setColorAt(1, darker)
        p.setBrush(QBrush(body_grad))
        outline = QColor(colors["body"])
        outline.setAlpha(180)
        p.setPen(QPen(outline, 1.5))
        body_rect = QRect(cx - body_w, int(cy - body_h + bounce), body_w * 2, body_h * 2)
        p.drawEllipse(body_rect)

        # Antenna (tier 1+)
        if tier_index >= 1:
            bh = 6 + tier_index * 2
            bw = 5 + tier_index
            bcx = cx + int(math.sin(self._anim_phase * 0.7) * 2)
            bcy = int(cy - body_h + bounce - bh * 0.3)
            bg = QRadialGradient(bcx, bcy - bh * 0.3, bw * 1.5)
            bg.setColorAt(0, colors["highlight"])
            bg.setColorAt(1, colors["body"])
            p.setBrush(QBrush(bg))
            p.drawEllipse(QPoint(bcx, bcy), bw, bh)

        # Eyes (skip if equipment overrides)
        if "eyes" not in self._equipped_visuals:
            eye_y = int(cy - body_h * 0.15 + bounce)
            eye_sp = int(body_w * 0.35)
            eye_sz = max(3, 2 + tier_index)

            p.setBrush(QBrush(QColor(255, 255, 255, 220)))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPoint(cx - eye_sp, eye_y), eye_sz + 1, eye_sz + 1)
            p.drawEllipse(QPoint(cx + eye_sp, eye_y), eye_sz + 1, eye_sz + 1)

            p.setBrush(QBrush(colors["eye"]))
            blink = abs(math.sin(self._anim_phase * 0.3))
            pupil_h = max(1, int(eye_sz * blink))
            p.drawEllipse(QPoint(cx - eye_sp, eye_y), eye_sz - 1, pupil_h)
            p.drawEllipse(QPoint(cx + eye_sp, eye_y), eye_sz - 1, pupil_h)

            p.setBrush(QBrush(QColor(255, 255, 255, 200)))
            p.drawEllipse(QPoint(cx - eye_sp - 1, eye_y - 1), 1, 1)
            p.drawEllipse(QPoint(cx + eye_sp - 1, eye_y - 1), 1, 1)

        # Mouth (skip if equipment overrides)
        if "mouth" not in self._equipped_visuals:
            mouth_y = int(cy + body_h * 0.15 + bounce)
            p.setPen(QPen(colors["mouth"], 1.5))
            p.setBrush(Qt.NoBrush)
            smile_w = 4 + tier_index
            if tier_index >= 4:
                p.drawArc(QRect(cx - smile_w, mouth_y - smile_w // 2, smile_w * 2, smile_w),
                          0, -180 * 16)
            else:
                p.drawArc(QRect(cx - smile_w, mouth_y - 2, smile_w * 2, 4),
                          0, -180 * 16)

        # Crown for Demon Lord+ (skip if helmet equipped)
        if tier_index >= 4 and "helmet" not in self._equipped_visuals:
            crown_y = int(cy - body_h - 8 + bounce)
            crown_color = QColor(255, 215, 0) if tier_index >= 5 else QColor(200, 100, 255)
            p.setPen(QPen(crown_color, 1.5))
            p.setBrush(QBrush(crown_color))
            from PySide6.QtGui import QPolygon
            pts = [
                QPoint(cx - 10, crown_y + 5),
                QPoint(cx - 8, crown_y),
                QPoint(cx - 3, crown_y + 3),
                QPoint(cx, crown_y - 3),
                QPoint(cx + 3, crown_y + 3),
                QPoint(cx + 8, crown_y),
                QPoint(cx + 10, crown_y + 5),
            ]
            p.drawPolygon(QPolygon(pts))

        # ─── Equipment visuals ───
        if self._equipped_visuals:
            from sentinel.equipment_visuals import render_equipment
            equip_to_draw = {k: v for k, v in self._equipped_visuals.items()
                            if k != "background"}
            equip_ctx["p"] = p
            render_equipment(p, equip_to_draw, equip_ctx)

        # Trait orbiting icons (smaller)
        for i, trait in enumerate(self._traits):
            acc = TRAIT_ACCESSORIES.get(trait)
            if not acc:
                continue
            angle = self._anim_phase + i * (math.pi * 2 / max(len(self._traits), 1))
            orbit_r = body_w + 15
            ax = cx + int(math.cos(angle) * orbit_r)
            ay = int(cy + math.sin(angle) * orbit_r * 0.5 + bounce)
            p.setPen(Qt.NoPen)
            tc = QColor(acc["color"].red(), acc["color"].green(), acc["color"].blue(), 80)
            p.setBrush(QBrush(tc))
            p.drawEllipse(QPoint(ax, ay), 8, 8)
            p.setPen(QPen(QColor(255, 255, 255, 180)))
            font = QFont("Segoe UI Emoji", 7)
            p.setFont(font)
            p.drawText(QRect(ax - 7, ay - 7, 14, 14), Qt.AlignCenter, acc["symbol"])

        # ── Notification bubble ──
        self._paint_bubble(p, w, cx)

        p.end()

    def _paint_bubble(self, p: QPainter, w: int, cx: int):
        """Draw the notification bubble at the top of the widget.
        Shared between the procedural and self-portrait paint paths
        so the bubble works regardless of which body is rendered."""
        if not self._bubble_text:
            return
        p.setOpacity(0.92)
        bubble_font = QFont("Microsoft JhengHei", 9)
        p.setFont(bubble_font)
        fm = p.fontMetrics()
        text_w = fm.horizontalAdvance(self._bubble_text) + 16
        text_h = fm.height() + 10
        bx = cx - text_w // 2
        by = 2

        if bx < 2:
            bx = 2
        if bx + text_w > w - 2:
            text_w = w - 4
            bx = 2

        p.setBrush(QBrush(QColor(20, 25, 40, 220)))
        p.setPen(QPen(QColor(0, 220, 255, 150), 1))
        p.drawRoundedRect(QRect(bx, by, text_w, text_h), 8, 8)

        p.setPen(QPen(QColor(230, 235, 245)))
        p.drawText(QRect(bx + 8, by, text_w - 16, text_h), Qt.AlignCenter, self._bubble_text)

        p.setBrush(QBrush(QColor(20, 25, 40, 220)))
        p.setPen(QPen(QColor(0, 220, 255, 150), 1))
        tri_cx = cx
        tri_y = by + text_h
        from PySide6.QtGui import QPolygon
        p.drawPolygon(QPolygon([
            QPoint(tri_cx - 5, tri_y),
            QPoint(tri_cx + 5, tri_y),
            QPoint(tri_cx, tri_y + 6),
        ]))

    def _paint_avatar(self, p: QPainter, w: int, h: int, cx: int, cy: int):
        """Draw the user-picked self-portrait with idle breath + bounce
        animation. Single static image but the subtle Y-bob, X/Y squish,
        and slight sway make it feel alive (same trick V-tuber PNGs use).
        """
        breath = math.sin(self._anim_phase) * 0.04
        bounce = math.sin(self._anim_phase * 2) * 2.0
        sway = math.sin(self._anim_phase * 0.7) * 1.2  # degrees

        pix = self._avatar_pixmap
        # Fit the pixmap to widget size with a little headroom for bounce.
        target = int(min(w, h) * 0.86)
        scaled = pix.scaled(
            target, target,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        # Soft shadow at the feet — anchors the pixmap to the world so
        # it doesn't read as floating clipart.
        p.setBrush(QBrush(QColor(0, 0, 0, 35)))
        p.setPen(Qt.NoPen)
        shadow_y = int(cy + scaled.height() * 0.45 + bounce * 0.5)
        p.drawEllipse(
            QPoint(cx, shadow_y),
            int(scaled.width() * 0.32),
            5,
        )

        # Per-frame transform: breath = subtle non-uniform scale
        # (X and Y move opposite directions so it looks like inhaling),
        # sway = small rotation, bounce = vertical offset.
        p.save()
        p.translate(cx, int(cy + bounce))
        p.rotate(sway)
        sx = 1.0 + breath
        sy = 1.0 - breath
        p.scale(sx, sy)
        p.drawPixmap(
            -scaled.width() // 2,
            -scaled.height() // 2,
            scaled,
        )
        p.restore()

    # ── Interaction ──

    def enterEvent(self, event):
        self._hover = True
        self.setCursor(Qt.OpenHandCursor)
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self.setCursor(Qt.OpenHandCursor if self._hover else Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, event):
        """Double-click to open main window."""
        self.open_main_window.emit()

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #1a2238; color: #e8eaf0; border: 1px solid rgba(255,255,255,0.1); }
            QMenu::item:selected { background: rgba(0, 220, 255, 0.2); }
        """)

        open_action = QAction("開啟主視窗", self)
        open_action.triggered.connect(self.open_main_window.emit)
        menu.addAction(open_action)

        opacity_menu = menu.addMenu("透明度")
        for label, val in [("100%", 1.0), ("75%", 0.75), ("50%", 0.5), ("25%", 0.25)]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked, v=val: self._set_opacity(v))
            opacity_menu.addAction(act)

        menu.addSeparator()

        hide_action = QAction("隱藏浮窗", self)
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)

        menu.exec(self.mapToGlobal(pos))

    def _set_opacity(self, val: float):
        self._opacity = val
        self.update()
