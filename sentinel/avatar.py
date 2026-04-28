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
) -> dict:
    """Color-key bg removal.

    Returns a dict {ok, transparent_ratio, bg_rgb}. `ok` is True if the
    file was written; `transparent_ratio` is the fraction of pixels
    that ended up fully (or near-fully) transparent — the caller uses
    this to detect the "ran without raising but did nothing useful"
    case (e.g. subject touches all four corners → bg estimate samples
    the subject → ramp leaves nearly everything opaque). Old API used
    to return just bool, which hid this failure mode.

    inner / outer are RGB-distance thresholds. Pixels within `inner` of
    the estimated background are fully transparent; pixels beyond `outer`
    are fully opaque; in between we ramp linearly so edges feel soft
    instead of stenciled. The whole pass is vectorised in numpy so
    even a 2K-square image takes <1s.

    `max_dimension` downscales the source before processing. The overlay
    only ever displays this image at ~120px, so doing the per-pixel
    distance calc at the source resolution is wasted work; capping at
    512 keeps the cutout sharp enough at any reasonable overlay size
    while making the bg removal effectively instant.
    """
    info: dict = {"ok": False, "transparent_ratio": 0.0, "bg_rgb": None}
    try:
        img = Image.open(src).convert("RGBA")
    except Exception as e:
        log.warning(f"avatar: open failed for {src}: {e}")
        return info

    if max(img.size) > max_dimension:
        ratio = max_dimension / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)
        log.info(f"avatar: downscaled to {new_size} for bg removal")

    arr = np.array(img, dtype=np.int16)
    h, w = arr.shape[:2]

    s = max(4, min(sample_size, w // 4, h // 4))
    corner_boxes = [
        arr[0:s, 0:s, :3],
        arr[0:s, w - s:w, :3],
        arr[h - s:h, 0:s, :3],
        arr[h - s:h, w - s:w, :3],
    ]
    bg = np.mean(np.concatenate([c.reshape(-1, 3) for c in corner_boxes]), axis=0)
    info["bg_rgb"] = (int(bg[0]), int(bg[1]), int(bg[2]))
    log.info(
        f"avatar: bg color estimate rgb({bg[0]:.0f},{bg[1]:.0f},{bg[2]:.0f}) "
        f"from {src.name}"
    )

    rgb = arr[..., :3].astype(np.float32)
    diff = rgb - bg
    dist = np.sqrt(np.sum(diff * diff, axis=-1))

    span = max(1.0, outer - inner)
    ramp = np.clip((dist - inner) / span, 0.0, 1.0)
    new_alpha = (arr[..., 3].astype(np.float32) * ramp).astype(np.uint8)

    out = arr.astype(np.uint8)
    out[..., 3] = new_alpha

    transparent_ratio = float(np.mean(new_alpha < 32))
    info["transparent_ratio"] = transparent_ratio
    log.info(f"avatar: transparent_ratio={transparent_ratio:.3f}")

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(out, mode="RGBA").save(dst, "PNG")
    except Exception as e:
        log.warning(f"avatar: save failed for {dst}: {e}")
        return info
    log.info(f"avatar: cutout saved to {dst}")
    info["ok"] = True
    return info


def make_avatar_from_expression(
    expression_id: str, source_image: Path,
) -> tuple[Optional[Path], dict]:
    """Run bg removal on `source_image` and write the cutout under
    AVATAR_DIR keyed by expression_id.

    Returns (path, info). `path` is the cutout on success, None on hard
    failure. `info` carries diagnostic fields so the GUI layer can tell
    the user *why* the result might look wrong (`bg_removed` flag and
    `transparent_ratio`) — previous bool API hid the case where bg
    removal "succeeded" but produced an essentially unchanged image.

    Heuristic: if <5% of pixels became transparent, we treat bg removal
    as ineffective. Cutout is still written so the user has something,
    but `bg_removed` is False so the GUI can warn instead of celebrate.
    """
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    dst = AVATAR_DIR / f"{expression_id}.png"
    info = remove_background_color_key(source_image, dst)

    if info.get("ok"):
        info["bg_removed"] = info["transparent_ratio"] >= 0.05
        return dst, info

    # Hard failure (open/save raised). Fall back to copying the raw
    # source so the user at least gets the image as their avatar.
    try:
        shutil.copyfile(source_image, dst)
        log.info(f"avatar: bg removal failed, copied raw {source_image} → {dst}")
        info["ok"] = True
        info["bg_removed"] = False
        info["fallback_copied_raw"] = True
        return dst, info
    except Exception as e:
        log.warning(f"avatar: fallback copy failed: {e}")
        info["fallback_error"] = str(e)
        return None, info


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
