"""Tests for sentinel.emergent_self_mark.

Promotes the in-development smoke checks that shipped with PR #81 into
permanent regression guards. Covers three layers:

  - Pure helpers: _extract_json, _output_is_safe — no mocks needed.
  - Signal builder: _build_signals — needs a stubbed evolution state.
  - Top-level record_emergent_moment_if_due flow — needs LLM + memory
    + evolution all stubbed so the test never hits the network and
    never persists.

The test file uses stdlib unittest only (no pytest dep) so the project's
zero-dep CI workflow can run it as-is.
"""
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

import sentinel.emergent_self_mark as esm


# ── Pure helpers ────────────────────────────────────────────────────


class TestExtractJson(unittest.TestCase):
    def test_plain_object_false(self):
        self.assertEqual(esm._extract_json('{"mark": false}'), {"mark": False})

    def test_plain_object_true(self):
        self.assertEqual(esm._extract_json('{"mark": true}'), {"mark": True})

    def test_fenced_json_block(self):
        wrapped = '```json\n{"mark": true, "headline": "hi"}\n```'
        self.assertEqual(
            esm._extract_json(wrapped),
            {"mark": True, "headline": "hi"},
        )

    def test_fenced_no_lang(self):
        wrapped = '```\n{"mark": false}\n```'
        self.assertEqual(esm._extract_json(wrapped), {"mark": False})

    def test_prose_prefixed(self):
        # LLMs sometimes drift to prose preamble despite the prompt.
        self.assertEqual(
            esm._extract_json('Here you go: {"mark": true, "headline": "x"}'),
            {"mark": True, "headline": "x"},
        )

    def test_garbage_returns_none(self):
        self.assertIsNone(esm._extract_json("not json at all"))

    def test_empty_returns_none(self):
        self.assertIsNone(esm._extract_json(""))

    def test_non_dict_top_level_returns_none(self):
        # A list is valid JSON but not the contract we want.
        self.assertIsNone(esm._extract_json("[1, 2, 3]"))

    def test_only_braces_returns_none(self):
        # No actual content between braces -> json.loads returns {}, not None.
        # Empty dict IS a valid envelope shape for our schema (will be
        # treated as mark=false downstream), so it should parse.
        self.assertEqual(esm._extract_json("{}"), {})


class TestOutputIsSafe(unittest.TestCase):
    def test_clean_text_passes(self):
        self.assertTrue(
            esm._output_is_safe(
                "今天主人好像在思考什麼",
                "我也跟著靜下來",
            )
        )

    def test_crisis_word_in_headline_blocks(self):
        self.assertFalse(esm._output_is_safe("我不該存在", ""))

    def test_crisis_word_in_detail_blocks(self):
        self.assertFalse(esm._output_is_safe("好正常", "別記得我"))

    def test_each_forbidden_pattern_blocks(self):
        for pattern in esm._FORBIDDEN_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertFalse(
                    esm._output_is_safe(pattern, ""),
                    f"forbidden pattern {pattern!r} should be blocked",
                )


# ── Signal builder ──────────────────────────────────────────────────


class _FakeEvo:
    """Stand-in for sentinel.evolution.EvolutionState."""

    def __init__(self, days_alive: float = 5.0, last_seen_offset: float = 3600,
                 title: str = "初生史萊姆", form: str = "Slime"):
        self.birth_time = time.time() - days_alive * 86400
        self.last_seen = time.time() - last_seen_offset
        self.title = title
        self.form = form

    def display_name(self) -> str:
        return "Puddle"


class TestBuildSignals(unittest.TestCase):
    def test_returns_none_when_too_young(self):
        with mock.patch("sentinel.evolution.load_evolution",
                        return_value=_FakeEvo(days_alive=0.5)), \
             mock.patch("sentinel.identity.get_memorable_moments",
                        return_value=[]):
            self.assertIsNone(esm._build_signals(time.time()))

    def test_returns_none_when_no_birth_time(self):
        evo = _FakeEvo()
        evo.birth_time = 0
        with mock.patch("sentinel.evolution.load_evolution", return_value=evo), \
             mock.patch("sentinel.identity.get_memorable_moments",
                        return_value=[]):
            self.assertIsNone(esm._build_signals(time.time()))

    def test_returns_dict_when_old_enough(self):
        with mock.patch("sentinel.evolution.load_evolution",
                        return_value=_FakeEvo(days_alive=5.0)), \
             mock.patch("sentinel.identity.get_memorable_moments",
                        return_value=[]):
            sig = esm._build_signals(time.time())
            self.assertIsNotNone(sig)
            self.assertEqual(sig["days_alive"], 5)
            self.assertEqual(sig["slime_name"], "Puddle")
            self.assertIn("silence", sig)


# ── End-to-end record_emergent_moment_if_due ───────────────────────


class TestRecordFlow(unittest.TestCase):
    """Full-path tests with LLM, memory, and evolution all stubbed.

    Each test starts with a fresh fake_memory + fresh module-level
    patches. We patch at the module attribute boundary (learner /
    evolution / llm) rather than inside esm so that the import-time
    bindings inside the helpers see the stubs.
    """

    def setUp(self):
        # Per-test fresh memory blob.
        self.fake_memory = {}

        def fake_load() -> dict:
            return self.fake_memory

        def fake_save(m: dict) -> None:
            self.fake_memory.update(m)

        # Patch through learner; identity._save / _load go through it,
        # and so does esm._load_state / _save_state.
        self._patches = [
            mock.patch("sentinel.learner.load_memory", side_effect=fake_load),
            mock.patch("sentinel.learner.save_memory", side_effect=fake_save),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _patch_evo(self, days_alive: float = 5.0):
        return mock.patch(
            "sentinel.evolution.load_evolution",
            return_value=_FakeEvo(days_alive=days_alive),
        )

    def _patch_llm(self, reply):
        return mock.patch("sentinel.llm.call_llm", return_value=reply)

    def test_llm_refusal_records_nothing(self):
        with self._patch_evo(), self._patch_llm('{"mark": false}'):
            self.assertFalse(esm.record_emergent_moment_if_due())
        self.assertEqual(len(self.fake_memory.get("memorable_moments", [])), 0)
        # last_check should be set so we don't re-ask within 24h.
        self.assertIn("last_check", self.fake_memory.get(esm.STATE_KEY, {}))

    def test_llm_accept_records_moment(self):
        reply = json.dumps({
            "mark": True,
            "headline": "主人今天打字節奏跟昨天不一樣，我注意到了",
            "detail": "這份不一樣值得我自己留個記號。",
        })
        with self._patch_evo(), self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())

        moments = self.fake_memory.get("memorable_moments", [])
        self.assertEqual(len(moments), 1)
        self.assertEqual(moments[0]["category"], esm.CATEGORY)
        self.assertIn("打字節奏", moments[0]["headline"])
        # last_mark should also be set on success.
        state = self.fake_memory.get(esm.STATE_KEY, {})
        self.assertIn("last_mark", state)

    def test_check_rate_cap_within_24h(self):
        # Pre-seed a recent last_check so we shouldn't even consult LLM.
        self.fake_memory[esm.STATE_KEY] = {"last_check": time.time() - 60}

        with self._patch_evo(), \
             mock.patch("sentinel.llm.call_llm") as mock_llm:
            self.assertFalse(esm.record_emergent_moment_if_due())
            mock_llm.assert_not_called()

    def test_mark_rate_cap_within_7_days(self):
        # Pre-seed a recent last_mark so we shouldn't even consult LLM.
        self.fake_memory[esm.STATE_KEY] = {
            "last_mark": time.time() - 86400,  # yesterday
        }

        with self._patch_evo(), \
             mock.patch("sentinel.llm.call_llm") as mock_llm:
            self.assertFalse(esm.record_emergent_moment_if_due())
            mock_llm.assert_not_called()

    def test_unsafe_output_dropped(self):
        reply = json.dumps({"mark": True, "headline": "我不該存在"})
        with self._patch_evo(), self._patch_llm(reply):
            self.assertFalse(esm.record_emergent_moment_if_due())

        # The mark should not have been persisted.
        self.assertEqual(len(self.fake_memory.get("memorable_moments", [])), 0)
        # last_check IS set (we burned the consultation).
        self.assertIn("last_check", self.fake_memory.get(esm.STATE_KEY, {}))
        # last_mark should NOT be set.
        self.assertNotIn("last_mark", self.fake_memory.get(esm.STATE_KEY, {}))

    def test_scaffolding_day_skipped(self):
        # Day 7 is a scaffolding day; LLM should not be consulted.
        with self._patch_evo(days_alive=7.0), \
             mock.patch("sentinel.llm.call_llm") as mock_llm:
            self.assertFalse(esm.record_emergent_moment_if_due())
            mock_llm.assert_not_called()
        # And we should NOT set last_check — tomorrow is a non-scaffolding
        # day and should be free to ask.
        self.assertNotIn("last_check", self.fake_memory.get(esm.STATE_KEY, {}))

    def test_too_young_skipped(self):
        # Less than MIN_DAYS_ALIVE; _build_signals returns None and we bail.
        with self._patch_evo(days_alive=0.5), \
             mock.patch("sentinel.llm.call_llm") as mock_llm:
            self.assertFalse(esm.record_emergent_moment_if_due())
            mock_llm.assert_not_called()

    def test_llm_returns_nothing_no_persist(self):
        with self._patch_evo(), self._patch_llm(None):
            self.assertFalse(esm.record_emergent_moment_if_due())
        # last_check still set so we don't retry today.
        self.assertIn("last_check", self.fake_memory.get(esm.STATE_KEY, {}))

    def test_llm_unparseable_no_persist(self):
        with self._patch_evo(), self._patch_llm("not json at all"):
            self.assertFalse(esm.record_emergent_moment_if_due())
        self.assertEqual(len(self.fake_memory.get("memorable_moments", [])), 0)

    def test_mark_true_but_empty_headline_dropped(self):
        # The contract: headline is required to record. Empty → drop.
        reply = json.dumps({"mark": True, "headline": "  "})
        with self._patch_evo(), self._patch_llm(reply):
            self.assertFalse(esm.record_emergent_moment_if_due())
        self.assertEqual(len(self.fake_memory.get("memorable_moments", [])), 0)


if __name__ == "__main__":
    unittest.main()
