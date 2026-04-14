"""Generate placeholder sprite PNGs for all equipment/body slots.

Run once to create colored placeholder images with text labels.
These will be replaced by real pixel art from community contributors.

Usage: python -m sentinel.generate_placeholders
"""
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QPen, QLinearGradient, QBrush, QRadialGradient
from PySide6.QtCore import Qt, QRect, QPoint
import sys

SPRITE_DIR = Path(__file__).parent / "assets" / "sprites"
SIZE = 256

# Rarity color schemes
RARITY_COLORS = {
    1: {"bg": QColor(120, 120, 120, 60), "border": QColor(180, 180, 180), "label": "★"},
    2: {"bg": QColor(60, 160, 60, 60), "border": QColor(100, 220, 100), "label": "★★"},
    3: {"bg": QColor(50, 120, 220, 60), "border": QColor(80, 160, 255), "label": "★★★"},
    4: {"bg": QColor(140, 60, 200, 60), "border": QColor(180, 100, 255), "label": "★★★★"},
    5: {"bg": QColor(220, 180, 0, 60), "border": QColor(255, 215, 0), "label": "★★★★★"},
    6: {"bg": QColor(200, 50, 50, 60), "border": QColor(255, 80, 80), "label": "★★★★★★"},
    7: {"bg": QColor(30, 30, 30, 80), "border": QColor(255, 215, 0), "label": "★★★★★★★"},
}

# All sprites to generate (slot, filename, display_name, rarity)
SPRITES = [
    # Body (7)
    ("body", "body_slime", "初生史萊姆", 1),
    ("body", "body_slime_plus", "覺醒史萊姆", 2),
    ("body", "body_named", "被命名的", 3),
    ("body", "body_majin", "魔人", 4),
    ("body", "body_demon_seed", "魔王種", 5),
    ("body", "body_true_demon", "真・魔王", 6),
    ("body", "body_ultimate", "究極", 7),
    # Helmet (7)
    ("helmet", "helmet_hacker_goggles", "駭客護目鏡", 1),
    ("helmet", "helmet_cat_ears", "貓耳帽", 2),
    ("helmet", "helmet_sage_crown", "大賢者之冠", 3),
    ("helmet", "helmet_demon_horns", "魔王的角", 4),
    ("helmet", "helmet_dragon_skull_crown", "龍骨頭冠", 5),
    ("helmet", "helmet_veldora_crown", "暴風龍之冠", 6),
    ("helmet", "helmet_void_diadem", "虛數之冕", 7),
    # Eyes (5)
    ("eyes", "eyes_cat_eyes", "貓瞳", 1),
    ("eyes", "eyes_starry_eyes", "星空瞳", 2),
    ("eyes", "eyes_sage_eyes", "大賢者之眼", 3),
    ("eyes", "eyes_clairvoyance", "千里眼", 4),
    ("eyes", "eyes_demon_lord_eyes", "魔王之瞳", 5),
    # Mouth (3)
    ("mouth", "mouth_smile", "微笑", 1),
    ("mouth", "mouth_cat_mouth", "貓嘴", 2),
    ("mouth", "mouth_sharp_teeth", "銳齒", 3),
    # Skin (5)
    ("skin", "skin_classic_blue", "經典藍", 1),
    ("skin", "skin_veldora_purple", "暴風龍紫", 2),
    ("skin", "skin_flame_red", "炎魔紅", 3),
    ("skin", "skin_golden_body", "黃金之體", 5),
    ("skin", "skin_void_body", "虛空之體", 7),
    # Core (6)
    ("core", "core_basic_core", "基本晶核", 1),
    ("core", "core_enhanced_core", "強化晶核", 2),
    ("core", "core_trade_core", "交易晶核", 3),
    ("core", "core_demon_core", "魔王晶核", 4),
    ("core", "core_void_core", "虛無之王晶核", 5),
    ("core", "core_true_demon_core", "真魔王晶核", 6),
    # Left hand (2)
    ("left_hand", "left_hand_code_book", "程式之書", 2),
    ("left_hand", "left_hand_scholar_scroll", "學者之卷", 3),
    # Right hand (3)
    ("right_hand", "right_hand_pixel_sword", "像素魔劍", 1),
    ("right_hand", "right_hand_storm_blade", "魔劍・暴風", 3),
    ("right_hand", "right_hand_kusanagi", "天叢雲", 6),
    # Background (4)
    ("background", "bg_night_city", "夜晚都市", 1),
    ("background", "bg_jura_forest", "朱拉大森林", 2),
    ("background", "bg_demon_castle", "魔王城", 4),
    ("background", "bg_starry_abyss", "星空深淵", 5),
    # Mount (3)
    ("mount", "mount_hoverboard", "懸浮滑板", 2),
    ("mount", "mount_veldora_mount", "暴風龍座騎", 4),
    ("mount", "mount_void_ship", "虛空戰艦", 6),
    # VFX (5)
    ("vfx", "vfx_code_rain", "駭客代碼流", 2),
    ("vfx", "vfx_lightning_sparks", "閃電火花", 3),
    ("vfx", "vfx_sakura_fall", "櫻花飄落", 4),
    ("vfx", "vfx_reincarnation_light", "轉生之光", 6),
    ("vfx", "vfx_void_rift", "虛空裂縫", 7),
    # Drone (4)
    ("drone", "drone_observer_sprite", "觀測小精靈", 2),
    ("drone", "drone_merchant_sprite", "商人精靈", 3),
    ("drone", "drone_social_drone", "社群無人機", 4),
    ("drone", "drone_raphael_drone", "拉斐爾", 7),
    # Frame (7)
    ("frame", "frame_common", "普通", 1),
    ("frame", "frame_uncommon", "優良", 2),
    ("frame", "frame_rare", "稀有", 3),
    ("frame", "frame_epic", "史詩", 4),
    ("frame", "frame_legendary", "傳說", 5),
    ("frame", "frame_mythic", "神話", 6),
    ("frame", "frame_ultimate", "究極", 7),
]

# Slot-specific icon shapes
SLOT_SHAPES = {
    "body": "circle",
    "helmet": "triangle",
    "eyes": "diamond",
    "mouth": "arc",
    "skin": "circle",
    "core": "diamond",
    "left_hand": "rect",
    "right_hand": "rect",
    "background": "fill",
    "mount": "rect",
    "vfx": "star",
    "drone": "circle_small",
    "frame": "border",
}


def _draw_placeholder(p: QPainter, slot: str, name: str, rarity: int):
    """Draw a styled placeholder image."""
    colors = RARITY_COLORS[rarity]
    w = h = SIZE

    # Background varies by slot type
    if slot == "background":
        # Full background fill with gradient
        grad = QLinearGradient(0, 0, 0, h)
        c = colors["border"]
        grad.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 40))
        grad.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 80))
        p.fillRect(0, 0, w, h, QBrush(grad))
    elif slot == "frame":
        # Frame: border only, transparent center
        pen = QPen(colors["border"], 6)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(8, 8, w - 16, h - 16)
        if rarity >= 4:
            pen2 = QPen(QColor(colors["border"].red(), colors["border"].green(),
                               colors["border"].blue(), 100), 3)
            p.setPen(pen2)
            p.drawRect(14, 14, w - 28, h - 28)
    else:
        # Equipment: draw a shape in the center
        cx, cy = w // 2, h // 2
        shape = SLOT_SHAPES.get(slot, "circle")

        # Glow for high rarity
        if rarity >= 4:
            glow = QRadialGradient(cx, cy, 80)
            gc = colors["border"]
            glow.setColorAt(0, QColor(gc.red(), gc.green(), gc.blue(), 60))
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(QPoint(cx, cy), 80, 80)

        p.setPen(QPen(colors["border"], 2))
        p.setBrush(QBrush(colors["bg"]))

        if shape == "circle":
            p.drawEllipse(QPoint(cx, cy), 50, 50)
        elif shape == "circle_small":
            p.drawEllipse(QPoint(cx - 30, cy - 30), 25, 25)
        elif shape == "diamond":
            pts = [QPoint(cx, cy - 40), QPoint(cx + 30, cy),
                   QPoint(cx, cy + 40), QPoint(cx - 30, cy)]
            from PySide6.QtGui import QPolygon
            p.drawPolygon(QPolygon(pts))
        elif shape == "triangle":
            pts = [QPoint(cx, cy - 45), QPoint(cx + 40, cy + 25),
                   QPoint(cx - 40, cy + 25)]
            from PySide6.QtGui import QPolygon
            p.drawPolygon(QPolygon(pts))
        elif shape == "rect":
            p.drawRect(cx - 35, cy - 25, 70, 50)
        elif shape == "arc":
            p.drawArc(QRect(cx - 20, cy - 10, 40, 20), 0, -180 * 16)
        elif shape == "star":
            import math
            pts = []
            for i in range(10):
                angle = math.pi / 2 + i * math.pi / 5
                r = 45 if i % 2 == 0 else 20
                pts.append(QPoint(int(cx + r * math.cos(angle)),
                                  int(cy - r * math.sin(angle))))
            from PySide6.QtGui import QPolygon
            p.drawPolygon(QPolygon(pts))

    # Slot label (top)
    p.setPen(QPen(QColor(255, 255, 255, 150)))
    font_small = QFont("Microsoft JhengHei", 10)
    p.setFont(font_small)
    p.drawText(QRect(0, 10, w, 25), Qt.AlignCenter, slot.upper())

    # Name (center-bottom)
    p.setPen(QPen(colors["border"]))
    font_name = QFont("Microsoft JhengHei", 14, QFont.Bold)
    p.setFont(font_name)
    p.drawText(QRect(0, h - 70, w, 30), Qt.AlignCenter, name)

    # Rarity stars (bottom)
    p.setPen(QPen(QColor(255, 215, 0, 200)))
    font_star = QFont("Segoe UI", 12)
    p.setFont(font_star)
    p.drawText(QRect(0, h - 40, w, 25), Qt.AlignCenter, colors["label"])

    # "PLACEHOLDER" watermark
    p.setPen(QPen(QColor(255, 255, 255, 40)))
    font_wm = QFont("Consolas", 9)
    p.setFont(font_wm)
    p.drawText(QRect(0, h - 20, w, 18), Qt.AlignCenter, "PLACEHOLDER")


def generate_all():
    """Generate all placeholder sprites."""
    app = QApplication.instance() or QApplication(sys.argv)

    generated = 0
    skipped = 0

    for slot, filename, name, rarity in SPRITES:
        out_dir = SPRITE_DIR / slot
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{filename}.png"

        # Don't overwrite real sprites
        if out_path.exists():
            # Check if it's a real sprite (>5KB) vs placeholder (<5KB)
            if out_path.stat().st_size > 5000:
                skipped += 1
                continue

        img = QImage(SIZE, SIZE, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))  # Transparent

        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        _draw_placeholder(p, slot, name, rarity)
        p.end()

        img.save(str(out_path), "PNG")
        generated += 1

    print(f"Generated {generated} placeholder sprites, skipped {skipped} existing real sprites.")
    print(f"Output: {SPRITE_DIR}")


if __name__ == "__main__":
    generate_all()
