"""Tests for sentinel/birth_signature.py — Layer 1 of slime physical
individuation.

The non-negotiables (per ADR 2026-05-01-slime-physical-individuation):

  - Same birth_time → same signature, every time. This is the
    'genetic' contract; if it ever breaks, existing slimes get
    silently re-rolled on the next launch.
  - All numeric axes stay inside the documented ranges. The render
    layer (next PR) will trust this without re-validating.
  - Marking is a roll-on-roll-off thing — most slimes don't have one
    (see MARKING_PROBABILITY).

The migration / persistence half (lazy-fill on existing saves, save-
once-on-birth for new slimes) lives in evolution.py and is exercised
in test_birth_signature_migration.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sentinel.birth_signature import (
    BirthSignature, Marking,
    HUE_OFFSET_RANGE, SATURATION_RANGE,
    HEIGHT_FACTOR_RANGE, WIDTH_FACTOR_RANGE,
    MARKING_TYPES, MARKING_PROBABILITY,
    MARKING_POS_RANGE, MARKING_HUE_DELTA_RANGE,
    MARKING_LIGHTNESS_DELTA_RANGE,
    generate_birth_signature, signature_to_dict, signature_from_dict,
)


def _in_range(value: float, range_tuple: tuple[float, float]) -> bool:
    low, high = range_tuple
    return low <= value <= high


class TestDeterminism(unittest.TestCase):
    """Same birth_time → identical signature. The whole 'this slime is
    this slime' guarantee depends on it."""

    def test_same_birth_time_same_signature(self):
        for bt in (0.001, 1234567.89, 1700000000.0, 9999999999.999):
            sig_a = generate_birth_signature(bt)
            sig_b = generate_birth_signature(bt)
            self.assertEqual(signature_to_dict(sig_a),
                             signature_to_dict(sig_b),
                             f"non-deterministic at birth_time={bt}")

    def test_different_birth_times_produce_different_signatures(self):
        # Not a hard requirement (collisions theoretically possible)
        # but at the second-precision level over 100 close times, we
        # should see significant variation. If this ever returns the
        # same dict for distinct inputs, the seed function is broken.
        sigs = {
            json.dumps(signature_to_dict(generate_birth_signature(1700000000 + i)),
                       sort_keys=True)
            for i in range(100)
        }
        self.assertGreater(len(sigs), 90,
                           "seed function appears to collapse — "
                           "100 distinct inputs gave <90 distinct signatures")


class TestRangeGuardrails(unittest.TestCase):
    """ADR 護欄 #2: 出生簽名生成必須在合理 / 可愛範圍. The generator
    must never produce a value outside the documented ranges, ever.
    Render trusts this. We sample 1000 seeds to cover the full
    parameter space."""

    @classmethod
    def setUpClass(cls):
        cls.signatures = [
            generate_birth_signature(1700000000.0 + i)
            for i in range(1000)
        ]

    def test_hue_offset_within_range(self):
        for sig in self.signatures:
            self.assertTrue(_in_range(sig.body_hue_offset, HUE_OFFSET_RANGE),
                            f"hue_offset out of range: {sig.body_hue_offset}")

    def test_saturation_factor_within_range(self):
        for sig in self.signatures:
            self.assertTrue(_in_range(sig.body_saturation_factor, SATURATION_RANGE))

    def test_height_factor_within_range(self):
        for sig in self.signatures:
            self.assertTrue(_in_range(sig.body_height_factor, HEIGHT_FACTOR_RANGE))

    def test_width_factor_within_range(self):
        for sig in self.signatures:
            self.assertTrue(_in_range(sig.body_width_factor, WIDTH_FACTOR_RANGE))

    def test_marking_position_within_range(self):
        for sig in self.signatures:
            if sig.marking:
                self.assertTrue(_in_range(sig.marking.position_x, MARKING_POS_RANGE))
                self.assertTrue(_in_range(sig.marking.position_y, MARKING_POS_RANGE))

    def test_marking_hue_delta_within_range(self):
        for sig in self.signatures:
            if sig.marking:
                self.assertTrue(_in_range(sig.marking.hue_delta, MARKING_HUE_DELTA_RANGE))

    def test_marking_lightness_delta_within_range(self):
        for sig in self.signatures:
            if sig.marking:
                self.assertTrue(_in_range(sig.marking.lightness_delta,
                                          MARKING_LIGHTNESS_DELTA_RANGE))


class TestMarking(unittest.TestCase):
    def test_marking_type_always_valid(self):
        for i in range(500):
            sig = generate_birth_signature(1700000000.0 + i)
            if sig.marking:
                self.assertIn(sig.marking.type, MARKING_TYPES)

    def test_marking_probability_roughly_matches_constant(self):
        # 5000 samples → tolerance of ±5 percentage points is plenty
        # given the 30% target. If this ever fails, the generator
        # logic for the marking gate has shifted.
        n = 5000
        marked = sum(
            1 for i in range(n)
            if generate_birth_signature(1700000000.0 + i).marking is not None
        )
        ratio = marked / n
        self.assertAlmostEqual(ratio, MARKING_PROBABILITY, delta=0.05,
                               msg=f"marking ratio {ratio} far from target "
                                   f"{MARKING_PROBABILITY}")

    def test_marking_does_not_shift_when_we_add_future_axes(self):
        """The 'spend the seeds' pattern in generate_birth_signature
        (always advance rng for marking_type / pos / hue / lightness
        regardless of whether marking lands) protects existing slimes
        from re-roll if a future PR adds new axes after marking. This
        test snapshots one specific seed so any accidental reorder
        of rng draws will visibly change the snapshot."""
        sig = generate_birth_signature(1700000000.0)
        # Snapshot — these specific values are checked-in. If this
        # PR's generator changes order of draws, this fails. Captured
        # 2026-05-02 from the original implementation; updating these
        # numbers without bumping a "signature_schema_version" field
        # would silently re-roll every existing slime.
        d = signature_to_dict(sig)
        self.assertAlmostEqual(d["body_hue_offset"], -7.00, places=2)
        self.assertAlmostEqual(d["body_saturation_factor"], 0.866, places=3)
        self.assertAlmostEqual(d["body_height_factor"], 0.966, places=3)
        self.assertAlmostEqual(d["body_width_factor"], 0.986, places=3)
        self.assertIsNone(d["marking"])


class TestRoundTrip(unittest.TestCase):
    def test_dict_round_trip_preserves_signature_with_marking(self):
        for i in range(50):
            sig = generate_birth_signature(1700000000.0 + i)
            if sig.marking is None:
                continue
            d = signature_to_dict(sig)
            sig_back = signature_from_dict(d)
            self.assertEqual(sig, sig_back)

    def test_dict_round_trip_preserves_signature_without_marking(self):
        # Pick a seed known to produce no marking. Brute-force find one.
        for i in range(200):
            sig = generate_birth_signature(1700000000.0 + i)
            if sig.marking is None:
                d = signature_to_dict(sig)
                sig_back = signature_from_dict(d)
                self.assertEqual(sig, sig_back)
                self.assertIsNone(sig_back.marking)
                return
        self.fail("could not find a seed producing no marking — "
                  "either probability constant changed or RNG broke")

    def test_from_dict_tolerates_partial_data(self):
        """Defensive — if a save is corrupted to only include a
        subset of fields, fall back to centred defaults rather than
        crashing the whole evolution load."""
        sig = signature_from_dict({"body_hue_offset": 5.0})
        self.assertEqual(sig.body_hue_offset, 5.0)
        # Defaults for missing fields
        self.assertEqual(sig.body_saturation_factor, 1.0)
        self.assertEqual(sig.body_height_factor, 1.0)
        self.assertEqual(sig.body_width_factor, 1.0)
        self.assertIsNone(sig.marking)


class TestEvolutionIntegration(unittest.TestCase):
    """The migration path in evolution.load_evolution. Existing slimes
    (saved before v0.8) must get a signature backfilled, deterministic
    from their birth_time. New slimes get one at first boot."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.evolution_file = Path(self.tmp.name) / "aislime_evolution.json"
        self.patcher = mock.patch(
            "sentinel.evolution.EVOLUTION_FILE", self.evolution_file)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_existing_save_without_signature_gets_backfilled(self):
        # Simulate a pre-v0.8 save: birth_time set, no birth_signature.
        old_save = {
            "form": "Slime+",
            "birth_time": 1700000000.0,
            "total_observations": 42,
            "skills": [],
            "evolution_log": [],
            "dominant_traits": [],
            "affinity_scores": {},
        }
        self.evolution_file.write_text(json.dumps(old_save), encoding="utf-8")

        from sentinel.evolution import load_evolution
        state = load_evolution()

        # Backfilled.
        self.assertTrue(state.birth_signature)
        self.assertIn("body_hue_offset", state.birth_signature)

        # Persisted — second load is a no-op, signature unchanged.
        first = state.birth_signature
        state2 = load_evolution()
        self.assertEqual(first, state2.birth_signature)

        # Deterministic — equals what the generator gives directly.
        from sentinel.birth_signature import (
            generate_birth_signature, signature_to_dict,
        )
        expected = signature_to_dict(generate_birth_signature(1700000000.0))
        self.assertEqual(state.birth_signature, expected)

    def test_first_boot_gets_signature_immediately(self):
        # No save file → birth path. The new slime should have a
        # signature derived from its birth_time before anyone reads
        # the state.
        self.assertFalse(self.evolution_file.exists())

        from sentinel.evolution import load_evolution
        state = load_evolution()

        self.assertTrue(state.birth_signature)
        self.assertGreater(state.birth_time, 0)

        # And it should match the deterministic generator on its
        # own birth_time.
        from sentinel.birth_signature import (
            generate_birth_signature, signature_to_dict,
        )
        expected = signature_to_dict(generate_birth_signature(state.birth_time))
        self.assertEqual(state.birth_signature, expected)


if __name__ == "__main__":
    unittest.main()
