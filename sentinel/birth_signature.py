"""Birth signature — Layer 1 of the slime physical individuation system.

Per ADR `docs/decisions/2026-05-01-slime-physical-individuation.md`:

  > 第一次啟動 slime 時，generate 一個 per-instance 的視覺種子。
  > ...存進 evolution.json 的 birth_signature 欄位。寫死之後不能改
  > ——像基因一樣，是「這隻 slime 之所以是這隻 slime」的一部分。
  > 技術上是 deterministic 隨機種子。同一個 slime 一輄子長這樣。

This module is **pure logic**. It produces a signature dict from a
deterministic seed (the slime's birth_time). No Qt, no IO, no global
state. Render path is intentionally NOT wired here — that's the next
PR. This one only locks the schema + generator + ranges.

The ranges below are the ADR's 護欄 #2 ("出生簽名生成必須在合理 /
可愛範圍"). Violating them needs an ADR amendment, not a code edit.

The marking probability (~30%) is the ADR's spirit of 護欄 #5
("subtle but visible") — most slimes have a clean body, a minority
carry a small mark. Distinct enough that two side-by-side slimes
visibly differ from D1 (護欄 #1 + manifesto), restrained enough that
the differences read as 個體, not as 抽卡稀有度 (護欄 #6).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional
import hashlib
import random


# ─── Range guardrails (ADR 護欄 #2) ───────────────────────────────────────
#
# All numeric axes have to live inside these. Generator clamps inside
# the range. Render layer (next PR) trusts the ranges and does not
# re-validate. If you change a range here, update the ADR.

HUE_OFFSET_RANGE = (-30.0, 30.0)        # degrees on the colour wheel
SATURATION_RANGE = (0.85, 1.10)         # multiplicative
HEIGHT_FACTOR_RANGE = (0.95, 1.05)      # multiplicative
WIDTH_FACTOR_RANGE = (0.95, 1.05)       # multiplicative

MARKING_TYPES = ("swirl", "dot", "line")
MARKING_PROBABILITY = 0.30              # ~30% of slimes carry one
# Marking position is in body-relative [-1, 1] coords (body center = 0,0).
# We restrict to the body region (no markings floating in empty space).
MARKING_POS_RANGE = (-0.6, 0.6)
# Marking colour is a small offset on the body's base hue. Render layer
# applies it on top of body colour; the absolute RGB lives in the
# render path, this layer only decides hue/lightness deltas.
MARKING_HUE_DELTA_RANGE = (-40.0, 40.0)
MARKING_LIGHTNESS_DELTA_RANGE = (-0.20, 0.20)


@dataclass
class Marking:
    """A subtle visual mark on the body — swirl / dot / line."""
    type: str                   # one of MARKING_TYPES
    position_x: float           # body-relative, [-1, 1]
    position_y: float           # body-relative, [-1, 1]
    hue_delta: float            # offset from body hue, degrees
    lightness_delta: float      # offset from body lightness, [-1, 1]


@dataclass
class BirthSignature:
    """Layer 1 — the per-instance visual seed. Generated once at first
    boot, stored in evolution.json, never changes.

    Render flow (next PR): base sprite → apply this signature →
    apply title visual signatures (Layer 2) → final pixel.
    """
    body_hue_offset: float          # degrees, HUE_OFFSET_RANGE
    body_saturation_factor: float   # multiplicative, SATURATION_RANGE
    body_height_factor: float       # multiplicative, HEIGHT_FACTOR_RANGE
    body_width_factor: float        # multiplicative, WIDTH_FACTOR_RANGE
    marking: Optional[Marking] = None  # ~30% of slimes carry one


def _seed_from_birth_time(birth_time: float) -> int:
    """Stable 64-bit int seed from a slime's birth_time.

    `birth_time` is the only input. Two slimes born at the exact same
    epoch second collide — that's accepted (the chance is essentially
    zero in practice; if it ever matters we'd extend the seed to also
    take install_id).

    sha256 over the textual representation, take low 64 bits. Plain
    `hash(str(birth_time))` would not be stable across Python runs
    (PYTHONHASHSEED randomization), and we need this exact same int
    every time the slime is loaded — that's the whole 'genetic'
    promise of the ADR.
    """
    raw = f"birth_signature:{birth_time:.6f}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _uniform(rng: random.Random, low: float, high: float) -> float:
    """Sample uniformly from [low, high]. Wraps random for clarity."""
    return rng.uniform(low, high)


def generate_birth_signature(birth_time: float) -> BirthSignature:
    """Deterministic generator — same birth_time → same signature.

    Re-running this for an existing slime regenerates the exact bytes
    we wrote on day 1 (that's how migration works: existing pre-v0.8
    slimes get the signature their birth_time deterministically maps
    to, so no slime is "newly assigned" a signature when v0.8 lands).
    """
    rng = random.Random(_seed_from_birth_time(birth_time))

    sig = BirthSignature(
        body_hue_offset=_uniform(rng, *HUE_OFFSET_RANGE),
        body_saturation_factor=_uniform(rng, *SATURATION_RANGE),
        body_height_factor=_uniform(rng, *HEIGHT_FACTOR_RANGE),
        body_width_factor=_uniform(rng, *WIDTH_FACTOR_RANGE),
    )

    # Marking is rolled separately and gated by probability. Note we
    # always advance the rng by the same number of draws regardless
    # of whether the marking lands, so future schema additions don't
    # silently change every existing slime's signature when the order
    # of generation shifts. This 'spend the seeds' pattern is why we
    # don't early-return.
    marking_roll = rng.random()
    marking_type = rng.choice(MARKING_TYPES)
    marking_x = _uniform(rng, *MARKING_POS_RANGE)
    marking_y = _uniform(rng, *MARKING_POS_RANGE)
    marking_hue = _uniform(rng, *MARKING_HUE_DELTA_RANGE)
    marking_lightness = _uniform(rng, *MARKING_LIGHTNESS_DELTA_RANGE)

    if marking_roll < MARKING_PROBABILITY:
        sig.marking = Marking(
            type=marking_type,
            position_x=marking_x,
            position_y=marking_y,
            hue_delta=marking_hue,
            lightness_delta=marking_lightness,
        )

    return sig


def signature_to_dict(sig: BirthSignature) -> dict:
    """Convert to a plain dict for evolution.json storage.

    Uses asdict() so nested Marking flattens correctly. Marking
    becomes None or a {type, position_x, ...} dict.
    """
    return asdict(sig)


def signature_from_dict(data: dict) -> BirthSignature:
    """Reconstruct from the evolution.json dict.

    Tolerant of older saves that might be missing fields — defaults
    to the centre of each range. This shouldn't fire in practice
    (we generate the full signature once and keep it), but it keeps
    a corrupt-save back-channel from wiping the whole evolution
    state.
    """
    marking_data = data.get("marking")
    marking = None
    if marking_data:
        marking = Marking(
            type=marking_data.get("type", "dot"),
            position_x=float(marking_data.get("position_x", 0.0)),
            position_y=float(marking_data.get("position_y", 0.0)),
            hue_delta=float(marking_data.get("hue_delta", 0.0)),
            lightness_delta=float(marking_data.get("lightness_delta", 0.0)),
        )

    return BirthSignature(
        body_hue_offset=float(data.get("body_hue_offset", 0.0)),
        body_saturation_factor=float(data.get("body_saturation_factor", 1.0)),
        body_height_factor=float(data.get("body_height_factor", 1.0)),
        body_width_factor=float(data.get("body_width_factor", 1.0)),
        marking=marking,
    )
