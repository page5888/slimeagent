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
        # Also stub the consultation-log writer at the import boundary
        # inside esm so tests don't pollute ~/.hermes/. We don't need
        # to verify log calls here — that's the job of test_emergent_log.
        self._patches = [
            mock.patch("sentinel.learner.load_memory", side_effect=fake_load),
            mock.patch("sentinel.learner.save_memory", side_effect=fake_save),
            mock.patch("sentinel.emergent_log.record_consultation"),
            # _load_recent_master_words reads ~/.hermes/sentinel_chats.jsonl
            # by default — pin to empty list so tests don't depend on
            # whatever the real file happens to contain. Tests that
            # need a non-empty source patch this individually.
            mock.patch.object(esm, "_load_recent_master_words", return_value=[]),
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

    def test_letter_to_master_persisted_when_present(self):
        # ADR 2026-04-30 first (b) channel — when LLM provides a
        # letter, it should land in the moment dict so the GUI can
        # render it.
        reply = json.dumps({
            "mark": True,
            "headline": "今天的你格外安靜",
            "detail": "我注意到你連著兩個小時沒切視窗",
            "letter_to_master": "如果你想休息一下，就休息一下吧。",
        })
        with self._patch_evo(), self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertEqual(len(moments), 1)
        self.assertIn("letter_to_master", moments[0])
        self.assertIn("休息", moments[0]["letter_to_master"])

    def test_no_letter_field_when_llm_omits_it(self):
        # The common case — slime marks but doesn't have anything
        # specific to say to the master. The moment dict should NOT
        # carry the letter key at all (so GUI's `if letter` gate is
        # cleanly off).
        reply = json.dumps({
            "mark": True,
            "headline": "新的一天的開始",
            "detail": "今天起得早",
        })
        with self._patch_evo(), self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertEqual(len(moments), 1)
        self.assertNotIn("letter_to_master", moments[0])

    def test_empty_string_letter_not_persisted(self):
        # If the LLM returns "" explicitly, treat it as "no letter".
        reply = json.dumps({
            "mark": True,
            "headline": "什麼都沒發生的一天",
            "detail": "可是還是過完了",
            "letter_to_master": "",
        })
        with self._patch_evo(), self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertNotIn("letter_to_master", moments[0])

    def test_unsafe_letter_drops_whole_mark(self):
        # The safety filter must apply to the letter too — it's the
        # most public surface (rendered prominently, addressed to
        # master), so unsafe content there is the worst leak.
        reply = json.dumps({
            "mark": True,
            "headline": "今天的天氣",
            "detail": "天氣很好",
            "letter_to_master": "別記得我",
        })
        with self._patch_evo(), self._patch_llm(reply):
            self.assertFalse(esm.record_emergent_moment_if_due())
        # No moment should have been written.
        self.assertEqual(len(self.fake_memory.get("memorable_moments", [])), 0)

    def test_long_letter_truncated(self):
        long_letter = "x" * 5000
        reply = json.dumps({
            "mark": True,
            "headline": "h",
            "detail": "d",
            "letter_to_master": long_letter,
        })
        with self._patch_evo(), self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertLessEqual(len(moments[0]["letter_to_master"]), 200)

    # ── master_phrase / co-reference anchor (ADR 共同沉積 mech 3) ──

    def _patch_master_words(self, words):
        return mock.patch.object(
            esm, "_load_recent_master_words", return_value=list(words),
        )

    def test_master_phrase_persisted_when_quoted_from_source(self):
        # Slime picked a phrase that's literally in the source words.
        # That's the legitimate path — it should land in the moment.
        reply = json.dumps({
            "mark": True,
            "headline": "主人今天用了一個我之前沒聽過的比喻",
            "detail": "我把那句話收進來了。",
            "master_phrase": "像在水底",
        })
        with self._patch_evo(), \
             self._patch_master_words(["最近寫 code 像在水底",
                                       "今天好累"]), \
             self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertEqual(len(moments), 1)
        self.assertEqual(moments[0]["master_phrase"], "像在水底")

    def test_master_phrase_dropped_when_not_in_source(self):
        # Anti-hallucination guard: the LLM returned a phrase that
        # doesn't appear in the source. Drop the phrase but KEEP the
        # mark — headline/detail can still be valid.
        reply = json.dumps({
            "mark": True,
            "headline": "今天值得記",
            "detail": "原因略",
            "master_phrase": "主人從沒說過的句子",
        })
        with self._patch_evo(), \
             self._patch_master_words(["主人說過的話一",
                                       "主人說過的話二"]), \
             self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertEqual(len(moments), 1)
        # Mark survived...
        self.assertEqual(moments[0]["headline"], "今天值得記")
        # ...but the hallucinated phrase did not.
        self.assertNotIn("master_phrase", moments[0])

    def test_master_phrase_dropped_when_no_source_words(self):
        # No source was given — any phrase the LLM returns is
        # hallucinated. Drop unconditionally.
        reply = json.dumps({
            "mark": True,
            "headline": "今天值得記",
            "detail": "可是沒有對話",
            "master_phrase": "我直接編一句",
        })
        with self._patch_evo(), \
             self._patch_master_words([]), \
             self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertNotIn("master_phrase", moments[0])

    def test_no_master_phrase_field_when_llm_omits_it(self):
        # Common case — most marks won't carry a phrase. Moment dict
        # should NOT carry the master_phrase key at all.
        reply = json.dumps({
            "mark": True,
            "headline": "h",
            "detail": "d",
        })
        with self._patch_evo(), \
             self._patch_master_words(["主人說了什麼"]), \
             self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertNotIn("master_phrase", moments[0])

    def test_empty_string_master_phrase_not_persisted(self):
        reply = json.dumps({
            "mark": True,
            "headline": "h",
            "detail": "d",
            "master_phrase": "",
        })
        with self._patch_evo(), \
             self._patch_master_words(["主人說了什麼"]), \
             self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertNotIn("master_phrase", moments[0])

    def test_unsafe_master_phrase_drops_whole_mark(self):
        # Safety filter must apply to the phrase too — it surfaces
        # later in chat prompts so unsafe content there is a real
        # leak channel.
        reply = json.dumps({
            "mark": True,
            "headline": "h",
            "detail": "d",
            "master_phrase": "去死啦",
        })
        with self._patch_evo(), \
             self._patch_master_words(["你去死啦講真的"]), \
             self._patch_llm(reply):
            self.assertFalse(esm.record_emergent_moment_if_due())
        self.assertEqual(len(self.fake_memory.get("memorable_moments", [])), 0)

    def test_long_master_phrase_truncated(self):
        long_phrase = "啊" * 200
        reply = json.dumps({
            "mark": True,
            "headline": "h",
            "detail": "d",
            "master_phrase": long_phrase,
        })
        # Source contains the same long string so substring guard
        # passes, then 80-char cap kicks in.
        with self._patch_evo(), \
             self._patch_master_words([long_phrase]), \
             self._patch_llm(reply):
            self.assertTrue(esm.record_emergent_moment_if_due())
        moments = self.fake_memory.get("memorable_moments", [])
        self.assertLessEqual(len(moments[0]["master_phrase"]), 80)


# ── _load_recent_master_words file-IO (no LLM, no full flow) ────────


class TestLoadRecentMasterWords(unittest.TestCase):
    def test_returns_empty_when_file_missing(self):
        # Point at a path that definitely doesn't exist by patching
        # Path.home — _load_recent_master_words has its own
        # exists()-check that should swallow this.
        from pathlib import Path
        with mock.patch.object(Path, "home", return_value=Path("/nonexistent_xyzzy_root")):
            self.assertEqual(esm._load_recent_master_words(time.time()), [])

    def test_skips_assistant_rows(self):
        import tempfile, os, json as _j
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".hermes").mkdir()
            chat_path = home / ".hermes" / "sentinel_chats.jsonl"
            now = time.time()
            with chat_path.open("w", encoding="utf-8") as f:
                f.write(_j.dumps({"time": now - 60, "role": "assistant", "text": "ai 講"}) + "\n")
                f.write(_j.dumps({"time": now - 30, "role": "user", "text": "主人說一"}) + "\n")
                f.write(_j.dumps({"time": now - 10, "role": "user", "text": "主人說二"}) + "\n")
            with mock.patch.object(Path, "home", return_value=home):
                got = esm._load_recent_master_words(now)
        # Both user lines, in chronological order (newest last).
        self.assertEqual(got, ["主人說一", "主人說二"])

    def test_skips_lines_older_than_lookback(self):
        import tempfile, json as _j
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".hermes").mkdir()
            chat_path = home / ".hermes" / "sentinel_chats.jsonl"
            now = time.time()
            with chat_path.open("w", encoding="utf-8") as f:
                # Way old — should be filtered.
                f.write(_j.dumps({"time": now - 30 * 86400, "role": "user", "text": "古早"}) + "\n")
                # Recent — should appear.
                f.write(_j.dumps({"time": now - 60, "role": "user", "text": "剛剛"}) + "\n")
            with mock.patch.object(Path, "home", return_value=home):
                got = esm._load_recent_master_words(now)
        self.assertEqual(got, ["剛剛"])

    def test_caps_at_limit(self):
        import tempfile, json as _j
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".hermes").mkdir()
            chat_path = home / ".hermes" / "sentinel_chats.jsonl"
            now = time.time()
            with chat_path.open("w", encoding="utf-8") as f:
                for i in range(20):
                    f.write(_j.dumps({"time": now - i, "role": "user", "text": f"n={i}"}) + "\n")
            with mock.patch.object(Path, "home", return_value=home):
                got = esm._load_recent_master_words(now)
        self.assertEqual(len(got), esm._MASTER_WORDS_LIMIT)

    def test_tolerates_corrupt_lines(self):
        import tempfile, json as _j
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".hermes").mkdir()
            chat_path = home / ".hermes" / "sentinel_chats.jsonl"
            now = time.time()
            with chat_path.open("w", encoding="utf-8") as f:
                f.write("this is not json\n")
                f.write(_j.dumps({"time": now - 5, "role": "user", "text": "ok"}) + "\n")
                f.write("garbage 2\n")
            with mock.patch.object(Path, "home", return_value=home):
                got = esm._load_recent_master_words(now)
        self.assertEqual(got, ["ok"])


if __name__ == "__main__":
    unittest.main()
