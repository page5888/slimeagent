"""Tests for identity.list_box_entries — backend for the 箱子 browse UI.

ADR 共同沉積 mechanism 1: 「箱子要可以被主人翻」. The box is every
memorable_moment in one place. This helper enriches each moment
with day-of-life and convenience flags + sorts for browse UX.
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

from sentinel.identity import list_box_entries


def _moment(time_offset_days: float, **kw) -> dict:
    """Build a memorable_moments-shape dict at `time_offset_days` ago."""
    base = {
        "time": time.time() - time_offset_days * 86400,
        "category": "emergent_self_mark",
        "headline": f"moment at -{time_offset_days}d",
        "detail": "",
    }
    base.update(kw)
    return base


def _patched_memory(moments):
    return mock.patch(
        "sentinel.learner.load_memory",
        return_value={"memorable_moments": moments},
    )


class TestListBoxEntries(unittest.TestCase):
    def test_empty_returns_empty(self):
        with _patched_memory([]):
            self.assertEqual(list_box_entries(birth_time=time.time() - 30 * 86400), [])

    def test_day_n_computed_from_birth(self):
        # Slime born 30 days ago, moment placed 10 days ago → day 21 (1-indexed)
        birth = time.time() - 30 * 86400
        with _patched_memory([_moment(10)]):
            entries = list_box_entries(birth_time=birth)
        self.assertEqual(len(entries), 1)
        # ±1 to absorb int floor on the boundary
        self.assertIn(entries[0]["day_n"], (20, 21))

    def test_newest_first_default(self):
        birth = time.time() - 100 * 86400
        with _patched_memory([_moment(50), _moment(10), _moment(80)]):
            entries = list_box_entries(birth_time=birth)
        # Entries sorted newest-first → 10d ago first, then 50, then 80
        days = [e["day_n"] for e in entries]
        self.assertEqual(days, sorted(days, reverse=True))

    def test_oldest_first_when_flag_off(self):
        birth = time.time() - 100 * 86400
        with _patched_memory([_moment(50), _moment(10), _moment(80)]):
            entries = list_box_entries(birth_time=birth, newest_first=False)
        days = [e["day_n"] for e in entries]
        self.assertEqual(days, sorted(days))

    def test_has_letter_flag(self):
        birth = time.time() - 30 * 86400
        with _patched_memory([
            _moment(5, letter_to_master="hello"),
            _moment(3),  # no letter
            _moment(1, letter_to_master=""),  # explicit empty
        ]):
            entries = list_box_entries(birth_time=birth)
        flags = sorted(e["has_letter"] for e in entries)
        self.assertEqual(flags, [False, False, True])

    def test_has_phrase_flag(self):
        birth = time.time() - 30 * 86400
        with _patched_memory([
            _moment(5, master_phrase="像在水底"),
            _moment(3),
        ]):
            entries = list_box_entries(birth_time=birth)
        with_phrase = [e for e in entries if e["has_phrase"]]
        self.assertEqual(len(with_phrase), 1)
        self.assertEqual(with_phrase[0]["master_phrase"], "像在水底")

    def test_zero_birth_time_falls_back_to_day_1(self):
        # Pre-naming or evolution-data-missing case: no birth_time
        # available. Don't crash; just put everyone at day_n=1.
        with _patched_memory([_moment(5), _moment(10)]):
            entries = list_box_entries(birth_time=0)
        for e in entries:
            self.assertEqual(e["day_n"], 1)

    def test_preserves_original_moment_fields(self):
        birth = time.time() - 30 * 86400
        original = _moment(5, headline="特殊標題", letter_to_master="一句話",
                           master_phrase="像在水底")
        with _patched_memory([original]):
            entries = list_box_entries(birth_time=birth)
        e = entries[0]
        self.assertEqual(e["headline"], "特殊標題")
        self.assertEqual(e["letter_to_master"], "一句話")
        self.assertEqual(e["master_phrase"], "像在水底")
        # And the new fields are added
        self.assertIn("day_n", e)
        self.assertTrue(e["has_letter"])
        self.assertTrue(e["has_phrase"])

    def test_does_not_mutate_underlying_moments(self):
        birth = time.time() - 30 * 86400
        original = _moment(5)
        original_keys = set(original.keys())
        with _patched_memory([original]):
            list_box_entries(birth_time=birth)
        # The returned dicts are copies, so the underlying memory's
        # moments list shouldn't have gained day_n / has_* fields.
        self.assertEqual(set(original.keys()), original_keys)


if __name__ == "__main__":
    unittest.main()
