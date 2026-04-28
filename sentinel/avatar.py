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

import numpy as np
from PIL import Image

log = logging.getLogger("sentinel.avatar")

AVATAR_DIR = Path.home() / ".hermes" / "avatar"
SETTINGS_FILE = Path.home() / ".hermes" / "sentinel_settings.json"


def remove_background_color_key(
    src: Path,
    dst: Path,
    *,
    sample_size: int = 12,
    inner: float = 28.0,
    outer: float = 60.0,
    max_dimension: int = 512,
) -> bool:
    """Color-key bg removal. Returns True on success.

    inner / outer are RGB-distance thresholds. Pixels within `inner` of
    the estimated background are fully transparent; pixels beyond `outer`
    are fully opaque; in between we ramp linearly so edges feel soft
    instead of stenciled. The whole pass is vectorised in numpy so
    even a 2K-square image takes <1s — pure-Python pixel loops were
    taking 30+ seconds on real LLM-generated portraits.

    `max_dimension` downscales the source before processing. The overlay
    only ever displays this image at ~120px, so doing the per-pixel
    distance calc at the source resolution is wasted work; capping at
    512 keeps the cutout sharp enough at any reasonable overlay size
    while making the bg removal effectively instant.
    """
    try:
        img = Image.open(src).convert("RGBA")
    except Exception as e:
        log.warning(f"avatar: open failed for {src}: {e}")
        return False

    # Downscale large images. The overlay renders at ~120×120 and even
    # at 4× DPI we don't need the source 1024+ resolution.
    if max(img.size) > max_dimension:
        ratio = max_dimension / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)
        log.info(f"avatar: downscaled to {new_size} for bg removal")

    arr = np.array(img, dtype=np.int16)  # int16 for safe subtract
    h, w = arr.shape[:2]

    # Sample the four corners; clamp the box if the image is small.
    s = max(4, min(sample_size, w // 4, h // 4))
    corner_boxes = [
        arr[0:s, 0:s, :3],
        arr[0:s, w - s:w, :3],
        arr[h - s:h, 0:s, :3],
        arr[h - s:h, w - s:w, :3],
    ]
    bg = np.mean(np.concatenate([c.reshape(-1, 3) for c in corner_boxes]), axis=0)
    log.info(f"avatar: bg color estimate rgb({bg[0]:.0f},{bg[1]:.0f},{bg[2]:.0f}) from {src.name}")

    # Vectorised distance + alpha ramp.
    rgb = arr[..., :3].astype(np.float32)
    diff = rgb - bg
    dist = np.sqrt(np.sum(diff * diff, axis=-1))

    span = max(1.0, outer - inner)
    ramp = np.clip((dist - inner) / span, 0.0, 1.0)
    new_alpha = (arr[..., 3].astype(np.float32) * ramp).astype(np.uint8)

    out = arr.astype(np.uint8)
    out[..., 3] = new_alpha

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(out, mode="RGBA").save(dst, "PNG")
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
