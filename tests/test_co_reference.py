"""Tests for sentinel.co_reference (Slime 之語 chat-prompt block).

ADR 2026-04-30 共同沉積 mechanism 3. The block surfaces past verbatim
master quotes into chat so slime can echo them when contextually
relevant. Tests cover the formatting + degenerate cases — empty data,
missing fields, capping, error tolerance.
"""
import time
import unittest
from unittest import mock

from sentinel import co_reference as cref


class _FakeEvo:
    def __init__(self, days_alive: float = 365.0):
        self.birth_time = time.time() - days_alive * 86400


def _moment(phrase: str, day_offset_from_now: int = 0, **kw) -> dict:
    """Construct a memorable_moments-shaped dict.

    day_offset_from_now: how many days ago this moment happened
    (positive = in the past).
    """
    base = {
        "time": time.time() - day_offset_from_now * 86400,
        "category": "emergent_self_mark",
        "headline": "h",
        "detail": "d",
    }
    if phrase:
        base["master_phrase"] = phrase
    base.update(kw)
    return base


class TestBuildBlock(unittest.TestCase):
    def test_returns_empty_string_when_no_anchors(self):
        with mock.patch("sentinel.identity.get_co_reference_phrases",
                        return_value=[]):
            self.assertEqual(cref.build_block(), "")

    def test_returns_empty_string_when_anchors_have_no_phrase(self):
        # Defensive: anchors list contains rows without master_phrase.
        # build_block should still return "" rather than render an
        # empty list with a header.
        with mock.patch("sentinel.identity.get_co_reference_phrases",
                        return_value=[_moment(phrase="")]):
            self.assertEqual(cref.build_block(), "")

    def test_renders_block_with_day_of_life(self):
        # Slime is 365d old, moment was 187 days ago → day 178.
        anchors = [_moment("像在水底", day_offset_from_now=187)]
        with mock.patch("sentinel.identity.get_co_reference_phrases",
                        return_value=anchors), \
             mock.patch("sentinel.evolution.load_evolution",
                        return_value=_FakeEvo(days_alive=365.0)):
            block = cref.build_block()
        # Header + the line itself.
        self.assertIn("Slime 之語", block)
        self.assertIn("像在水底", block)
        # Day-of-life. Allow ±1 to absorb floating-point drift on the
        # boundary; the formula uses int floor.
        self.assertTrue(
            "第 178 天" in block or "第 179 天" in block,
            f"day-of-life missing in block: {block!r}",
        )

    def test_renders_multiple_anchors(self):
        anchors = [
            _moment("像在水底", day_offset_from_now=187),
            _moment("等等啦", day_offset_from_now=150),
        ]
        with mock.patch("sentinel.identity.get_co_reference_phrases",
                        return_value=anchors), \
             mock.patch("sentinel.evolution.load_evolution",
                        return_value=_FakeEvo(days_alive=365.0)):
            block = cref.build_block()
        self.assertIn("像在水底", block)
        self.assertIn("等等啦", block)

    def test_falls_back_when_birth_time_missing(self):
        # If evolution has no birth_time, the day-of-life context can't
        # be computed. Fall back to a plain quote line — better than
        # dropping the anchor.
        anchors = [_moment("像在水底", day_offset_from_now=10)]
        evo = _FakeEvo()
        evo.birth_time = 0
        with mock.patch("sentinel.identity.get_co_reference_phrases",
                        return_value=anchors), \
             mock.patch("sentinel.evolution.load_evolution",
                        return_value=evo):
            block = cref.build_block()
        self.assertIn("像在水底", block)
        self.assertNotIn("第 0 天", block)

    def test_returns_empty_string_on_retrieval_error(self):
        # Defensive: any exception in retrieval → empty block, chat
        # builds rest of prompt without it.
        with mock.patch("sentinel.identity.get_co_reference_phrases",
                        side_effect=RuntimeError("boom")):
            self.assertEqual(cref.build_block(), "")

    def test_respects_limit_argument(self):
        anchors = [
            _moment(f"phrase {i}", day_offset_from_now=10 - i)
            for i in range(5)
        ]
        with mock.patch("sentinel.identity.get_co_reference_phrases",
                        return_value=anchors[:2]) as gp, \
             mock.patch("sentinel.evolution.load_evolution",
                        return_value=_FakeEvo()):
            cref.build_block(limit=2)
        gp.assert_called_once_with(limit=2)


# ── identity.get_co_reference_phrases (newest-first, limit, gating) ──


class TestGetCoReferencePhrases(unittest.TestCase):
    """Verify the retrieval helper's contract directly so build_block
    tests don't have to duplicate filter-logic checks.
    """

    def _patched_memory(self, moments):
        # learner.load_memory is the boundary identity reads through.
        return mock.patch(
            "sentinel.learner.load_memory",
            return_value={"memorable_moments": moments},
        )

    def test_filters_to_rows_with_phrase(self):
        from sentinel.identity import get_co_reference_phrases
        moments = [
            _moment(""),
            _moment("有的一句", day_offset_from_now=1),
            _moment(""),
        ]
        with self._patched_memory(moments):
            got = get_co_reference_phrases()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["master_phrase"], "有的一句")

    def test_returns_newest_first(self):
        from sentinel.identity import get_co_reference_phrases
        moments = [
            _moment("舊", day_offset_from_now=10),
            _moment("中", day_offset_from_now=5),
            _moment("新", day_offset_from_now=1),
        ]
        with self._patched_memory(moments):
            got = get_co_reference_phrases()
        self.assertEqual(
            [m["master_phrase"] for m in got],
            ["新", "中", "舊"],
        )

    def test_caps_at_limit(self):
        from sentinel.identity import get_co_reference_phrases
        moments = [
            _moment(f"p{i}", day_offset_from_now=20 - i)
            for i in range(15)
        ]
        with self._patched_memory(moments):
            got = get_co_reference_phrases(limit=5)
        self.assertEqual(len(got), 5)


if __name__ == "__main__":
    unittest.main()
