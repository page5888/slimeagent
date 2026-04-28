"""Custom avatar (self-portrait → desktop overlay) — selection + bg removal.

The desktop overlay (sentinel/overlay.py) is procedurally drawn by default.
When the user picks a self-portrait from the album as their avatar, we:

  1. Sample the four corners of the source image to estimate the
     background colour.
  2. Build an alpha mask that fades pixels close to that colour to
     transparent (soft edge — pure binary masks look like paper cut-outs).
  3. Save the result as a PNG with alpha to ~/.hermes/avatar/<id>.png.
  4. Persist the path so the overlay loads it on next paint.

This is the "color-key" approach. It works well when the portrait has a
plain or near-uniform background — which is the dominant case for the
LLM-decided portraits we generate. If the bg is busy or the subject
touches the edges, the result will be poor; that's a known limitation
we accept here in exchange for zero new dependencies (Pillow is already
installed) and instant runtime (no model download).
"""
import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from PIL import Image

log = logging.getLogger("sentinel.avatar")

AVATAR_DIR = Path.home() / ".hermes" / "avatar"
SETTINGS_FILE = Path.home() / ".hermes" / "sentinel_settings.json"


def _avg_color(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    region = img.crop(box).convert("RGB")
    pixels = list(region.getdata())
    n = len(pixels)
    r = sum(p[0] for p in pixels) // n
    g = sum(p[1] for p in pixels) // n
    b = sum(p[2] for p in pixels) // n
    return (r, g, b)


def remove_background_color_key(
    src: Path,
    dst: Path,
    *,
    sample_size: int = 12,
    inner: float = 28.0,
    outer: float = 60.0,
) -> bool:
    """Color-key bg removal. Returns True on success.

    inner / outer are RGB-distance thresholds. Pixels within `inner` of
    the estimated background are fully transparent; pixels beyond `outer`
    are fully opaque; in between we ramp linearly so edges feel soft
    instead of stenciled.
    """
    try:
        img = Image.open(src).convert("RGBA")
    except Exception as e:
        log.warning(f"avatar: open failed for {src}: {e}")
        return False

    w, h = img.size
    s = max(4, min(sample_size, w // 4, h // 4))
    corners = [
        _avg_color(img, (0, 0, s, s)),
        _avg_color(img, (w - s, 0, w, s)),
        _avg_color(img, (0, h - s, s, h)),
        _avg_color(img, (w - s, h - s, w, h)),
    ]
    bg_r = sum(c[0] for c in corners) // 4
    bg_g = sum(c[1] for c in corners) // 4
    bg_b = sum(c[2] for c in corners) // 4
    log.info(f"avatar: bg color estimate rgb({bg_r},{bg_g},{bg_b}) from {src.name}")

    # Walk pixels once, replace alpha by distance ramp.
    px = img.load()
    span = max(1.0, outer - inner)
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            d = ((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2) ** 0.5
            if d <= inner:
                px[x, y] = (r, g, b, 0)
            elif d >= outer:
                px[x, y] = (r, g, b, a)
            else:
                ramp = (d - inner) / span
                px[x, y] = (r, g, b, int(a * ramp))

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst, "PNG")
    except Exception as e:
        log.warning(f"avatar: save failed for {dst}: {e}")
        return False
    log.info(f"avatar: cutout saved to {dst}")
    return True


def make_avatar_from_expression(expression_id: str, source_image: Path) -> Optional[Path]:
    """Run bg removal on `source_image` and write the cutout under
    AVATAR_DIR keyed by expression_id. Returns the cutout path on
    success, None on failure.
    """
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    dst = AVATAR_DIR / f"{expression_id}.png"
    if remove_background_color_key(source_image, dst):
        return dst
    # Fallback: copy original (no transparency) so the user at least
    # gets the image as their avatar even if bg removal fails — better
    # than silent no-op.
    try:
        shutil.copyfile(source_image, dst)
        log.info(f"avatar: bg removal failed, copied raw {source_image} → {dst}")
        return dst
    except Exception as e:
        log.warning(f"avatar: fallback copy failed: {e}")
        return None


def set_avatar_override(path: Optional[Path]) -> None:
    """Persist the avatar override path into sentinel_settings.json.
    Pass None to clear and revert to procedural slime.
    """
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if SETTINGS_FILE.exists():
        try:
            existing = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing["avatar_override_path"] = str(path) if path else ""
    SETTINGS_FILE.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_avatar_override() -> Optional[Path]:
    """Read the persisted avatar override path. Returns None if absent
    or the file no longer exists on disk."""
    if not SETTINGS_FILE.exists():
        return None
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    raw = data.get("avatar_override_path") or ""
    if not raw:
        return None
    p = Path(raw)
    return p if p.exists() else None
