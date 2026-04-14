"""Sprite-based avatar renderer.

Composites multiple PNG layers (body + equipment) into a single image.
Render order follows SPRITE_SPEC.md (bottom to top):
  background → mount → body → skin → core → left_hand → right_hand
  → mouth → eyes → helmet → vfx → drone → frame → title (text)
"""
from pathlib import Path
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QPen, QPixmap
from PySide6.QtCore import Qt, QRect, QPoint, QSize, QTimer
import logging

log = logging.getLogger("sentinel.sprite_renderer")

SPRITE_DIR = Path(__file__).parent / "assets" / "sprites"
SPRITE_SIZE = 256

# Evolution form → body sprite filename (without .png)
FORM_TO_BODY = {
    "Slime":           "body_slime",
    "Slime+":          "body_slime_plus",
    "Named Slime":     "body_named",
    "Majin":           "body_majin",
    "Demon Lord Seed": "body_demon_seed",
    "True Demon Lord": "body_true_demon",
    "Ultimate Slime":  "body_ultimate",
}

# Equipment visual → sprite filename mapping
# slot + visual → {slot}/{slot}_{visual}.png
# Special cases: background uses "bg_" prefix
SLOT_PREFIX = {
    "background": "bg",
}

# Render order (bottom to top) — frame rendered separately after rarity is known
RENDER_ORDER = [
    "background",
    "mount",
    "body",       # special: determined by evolution form, not equipment
    "skin",
    "core",
    "left_hand",
    "right_hand",
    "mouth",
    "eyes",
    "helmet",
    "vfx",
    "drone",
    # "frame" handled after loop (needs highest rarity)
    # "title" is text, rendered in widget paintEvent
]

# Rarity → frame sprite
RARITY_TO_FRAME = {
    "common": "frame_common",
    "uncommon": "frame_uncommon",
    "rare": "frame_rare",
    "epic": "frame_epic",
    "legendary": "frame_legendary",
    "mythic": "frame_mythic",
    "ultimate": "frame_ultimate",
}

# Sprite cache to avoid reloading from disk
_sprite_cache: dict[str, QPixmap | None] = {}


def _load_sprite(slot: str, filename: str) -> QPixmap | None:
    """Load a sprite from disk (cached)."""
    cache_key = f"{slot}/{filename}"
    if cache_key in _sprite_cache:
        return _sprite_cache[cache_key]

    path = SPRITE_DIR / slot / f"{filename}.png"
    if path.exists():
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            _sprite_cache[cache_key] = pixmap
            return pixmap

    _sprite_cache[cache_key] = None
    return None


def clear_sprite_cache():
    """Clear the sprite cache (call after sprites change on disk)."""
    _sprite_cache.clear()


def _get_sprite_filename(slot: str, visual: str) -> str:
    """Convert slot + visual key to sprite filename."""
    prefix = SLOT_PREFIX.get(slot, slot)
    return f"{prefix}_{visual}"


def render_avatar(form: str, equipped: dict, inventory: list,
                  size: int = SPRITE_SIZE) -> QImage:
    """Render a composited avatar image.

    Args:
        form: Evolution form name (e.g. "Slime", "Majin")
        equipped: dict of slot → item_id
        inventory: list of item dicts (to look up template_name from item_id)
        size: Output image size (square)

    Returns:
        QImage with transparent background, all layers composited.
    """
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))

    p = QPainter(img)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    # Build lookup: item_id → item dict
    item_lookup = {i["item_id"]: i for i in inventory if "item_id" in i}

    # Find highest rarity among equipped items (for frame)
    highest_rarity = None
    rarity_order = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "ultimate"]

    def _draw(pixmap):
        if pixmap:
            if pixmap.width() != size or pixmap.height() != size:
                pixmap = pixmap.scaled(size, size, Qt.KeepAspectRatio,
                                       Qt.SmoothTransformation)
            x = (size - pixmap.width()) // 2
            y = (size - pixmap.height()) // 2
            p.drawPixmap(x, y, pixmap)

    for layer in RENDER_ORDER:
        if layer == "body":
            body_name = FORM_TO_BODY.get(form, "body_slime")
            _draw(_load_sprite("body", body_name))
        else:
            item_id = equipped.get(layer)
            if not item_id:
                continue
            item = item_lookup.get(item_id)
            if not item:
                continue
            template = _find_template(item.get("template_name", ""))
            if template and template.get("visual"):
                filename = _get_sprite_filename(layer, template["visual"])
                _draw(_load_sprite(layer, filename))
            # Track highest rarity for frame
            item_rarity = item.get("rarity", "common")
            if item_rarity in rarity_order:
                idx = rarity_order.index(item_rarity)
                if highest_rarity is None or idx > rarity_order.index(highest_rarity):
                    highest_rarity = item_rarity

    # Frame: rendered last, based on highest equipped rarity
    if highest_rarity:
        frame_name = RARITY_TO_FRAME.get(highest_rarity)
        if frame_name:
            _draw(_load_sprite("frame", frame_name))

    p.end()
    return img


def _find_template(template_name: str) -> dict | None:
    """Find an equipment template by name."""
    try:
        from sentinel.wallet.equipment import EQUIPMENT_POOL
        return next((t for t in EQUIPMENT_POOL if t["name"] == template_name), None)
    except ImportError:
        return None


# ── Widget ──────────────────────────────────────────────────────────────

class SpriteAvatarWidget(QWidget):
    """Widget that displays the composited sprite avatar.

    Drop-in replacement for SlimeWidget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self._form = "Slime"
        self._title = "初生史萊姆"
        self._equipped: dict = {}
        self._inventory: list = []
        self._cached_image: QImage | None = None
        self._title_text = ""

    def set_state(self, form: str, title: str, traits: list = None,
                  skill_count: int = 0):
        """Update evolution state (compatible with SlimeWidget API)."""
        changed = (form != self._form or title != self._title)
        self._form = form
        self._title = title
        self._title_text = title
        if changed:
            self._cached_image = None
            self.update()

    def set_equipment(self, equipped: dict, inventory: list):
        """Update equipped items."""
        if equipped != self._equipped or len(inventory) != len(self._inventory):
            self._equipped = dict(equipped)
            self._inventory = list(inventory)
            self._cached_image = None
            self.update()

    def paintEvent(self, event):
        if self._cached_image is None:
            self._cached_image = render_avatar(
                self._form, self._equipped, self._inventory,
                size=SPRITE_SIZE,
            )

        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        w = self.width()
        h = self.height()

        # Calculate display size (fit in widget, leave room for title)
        title_h = 30
        avail_h = h - title_h
        display_size = min(w, avail_h, SPRITE_SIZE)

        # Center horizontally, align top
        x = (w - display_size) // 2
        y = max(0, (avail_h - display_size) // 2)

        # Draw the composited avatar
        pixmap = QPixmap.fromImage(self._cached_image)
        if display_size != SPRITE_SIZE:
            pixmap = pixmap.scaled(display_size, display_size,
                                   Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
        p.drawPixmap(x, y, pixmap)

        # Draw title text below avatar
        if self._title_text:
            p.setPen(QPen(QColor(255, 215, 0, 220)))
            font = QFont("Microsoft JhengHei", 11, QFont.Bold)
            p.setFont(font)
            title_y = y + display_size + 2
            p.drawText(QRect(0, title_y, w, title_h),
                       Qt.AlignHCenter | Qt.AlignTop, self._title_text)

        p.end()
