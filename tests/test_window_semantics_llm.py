"""Tests for sentinel/window_semantics_llm.py — Phase 3b LLM fallback.

Covers:
  - JSON parsing tolerance (markdown fences, prose wrappers, schema enforcement)
  - Decision tree (rule wins / use_llm=False / cache hit / LLM call / failure)
  - Cache persistence + eviction
  - Privacy / schema invariants

call_llm is mocked everywhere — no real network. Cache file path is
redirected to a temp dir so we never touch ~/.hermes.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sentinel.window_semantics import AppCategory, ContentType, Confidence
from sentinel.window_semantics_llm import (
    interpret_window_with_llm,
    _parse_llm_json,
    _make_key,
    _evict_if_oversized,
    reset_cache_for_tests,
    MAX_CACHE_ENTRIES,
)
import sentinel.window_semantics_llm as wsl


def _snap(process: str = "", title: str = "", is_idle: bool = False) -> dict:
    return {
        "process_name": process,
        "window_title": title,
        "is_idle": is_idle,
    }


class TestParseLLMJson(unittest.TestCase):
    """The LLM may return JSON in various wrappers despite our prompt
    asking for raw output. Parser must tolerate the common ones."""

    _GOOD_JSON = (
        '{"app_category": "browser", "content_type": "reading", '
        '"topic_signal": "MyApp docs", "platform": "github", '
        '"file": "", "project": "", "contact": ""}'
    )

    def test_clean_json_parses(self):
        out = _parse_llm_json(self._GOOD_JSON)
        self.assertEqual(out["app_category"], "browser")
        self.assertEqual(out["platform"], "github")

    def test_markdown_fence_stripped(self):
        text = f"```json\n{self._GOOD_JSON}\n```"
        out = _parse_llm_json(text)
        self.assertIsNotNone(out)
        self.assertEqual(out["app_category"], "browser")

    def test_leading_prose_tolerated(self):
        text = "Here's the JSON you asked for:\n" + self._GOOD_JSON
        out = _parse_llm_json(text)
        self.assertIsNotNone(out)
        self.assertEqual(out["app_category"], "browser")

    def test_returns_none_on_unparseable(self):
        self.assertIsNone(_parse_llm_json(""))
        self.assertIsNone(_parse_llm_json("not json at all"))
        self.assertIsNone(_parse_llm_json("{ this is not valid }"))

    def test_returns_none_on_non_dict(self):
        # Array, string, number — none of these are dicts
        self.assertIsNone(_parse_llm_json("[1, 2, 3]"))
        self.assertIsNone(_parse_llm_json('"a string"'))

    def test_schema_keys_always_present(self):
        # Even if LLM omitted some keys, parser fills them with ""
        out = _parse_llm_json('{"app_category": "browser"}')
        for key in ("app_category", "content_type", "topic_signal",
                    "platform", "file", "project", "contact"):
            self.assertIn(key, out)
            self.assertIsInstance(out[key], str)

    def test_unknown_app_category_normalized_to_unknown(self):
        # LLM hallucinated a new category — parser maps it to UNKNOWN.
        out = _parse_llm_json(
            '{"app_category": "fake_new_category", "content_type": "x", '
            '"topic_signal": "", "platform": "", "file": "", '
            '"project": "", "contact": ""}'
        )
        self.assertEqual(out["app_category"], AppCategory.UNKNOWN)
        self.assertEqual(out["content_type"], ContentType.UNKNOWN)

    def test_topic_signal_truncated(self):
        long = "x" * 200
        out = _parse_llm_json(
            f'{{"app_category": "browser", "content_type": "reading", '
            f'"topic_signal": "{long}", "platform": "", "file": "", '
            f'"project": "", "contact": ""}}'
        )
        self.assertLess(len(out["topic_signal"]), 100)

    def test_non_string_values_coerced_to_empty(self):
        # If LLM sends a number / null where we want a string, we
        # don't crash — just zero it out.
        raw = ('{"app_category": "browser", "content_type": "reading", '
               '"topic_signal": "", "platform": null, "file": 42, '
               '"project": "", "contact": ""}')
        out = _parse_llm_json(raw)
        self.assertEqual(out["platform"], "")
        self.assertEqual(out["file"], "")


class TestDecisionTree(unittest.TestCase):
    """The five-branch decision in interpret_window_with_llm."""

    def setUp(self):
        # Each test starts from a clean cache so behaviour is
        # deterministic regardless of order.
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.tmp.name) / "cache.json"
        self.patcher = mock.patch.object(wsl, "CACHE_FILE", self.cache_path)
        self.patcher.start()
        reset_cache_for_tests()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        reset_cache_for_tests()

    def test_known_app_skips_llm_entirely(self):
        # Rule layer returns confidence=high → LLM never called.
        with mock.patch("sentinel.llm.call_llm",
                        side_effect=AssertionError("LLM should not be called")):
            out = interpret_window_with_llm(_snap(
                process="chrome.exe",
                title="Reddit - r/programming"))
        self.assertEqual(out["app_category"], AppCategory.BROWSER)
        self.assertEqual(out["confidence"], Confidence.HIGH)

    def test_use_llm_false_skips_llm(self):
        # Even on unknown rule output, use_llm=False means don't call.
        with mock.patch("sentinel.llm.call_llm",
                        side_effect=AssertionError("LLM should not be called")):
            out = interpret_window_with_llm(
                _snap(process="WeirdApp.exe", title="x"),
                use_llm=False,
            )
        self.assertEqual(out["app_category"], AppCategory.UNKNOWN)

    def test_unknown_with_llm_calls_llm(self):
        llm_response = (
            '{"app_category": "browser", "content_type": "reading", '
            '"topic_signal": "MyApp docs", "platform": "", '
            '"file": "", "project": "", "contact": ""}'
        )
        with mock.patch("sentinel.llm.call_llm",
                        return_value=llm_response) as mock_llm:
            out = interpret_window_with_llm(_snap(
                process="WeirdApp.exe",
                title="WeirdApp - documentation"))
        self.assertEqual(mock_llm.call_count, 1)
        # LLM result wins over rule's "unknown"
        self.assertEqual(out["app_category"], "browser")
        # Confidence stamped MEDIUM (not HIGH) so callers know this
        # was LLM-derived, not rule-derived.
        self.assertEqual(out["confidence"], Confidence.MEDIUM)

    def test_llm_failure_falls_back_to_rule(self):
        # call_llm returns None (transient outage) → keep rule's
        # unknown answer, don't crash.
        with mock.patch("sentinel.llm.call_llm", return_value=None):
            out = interpret_window_with_llm(_snap(
                process="WeirdApp.exe", title="x"))
        self.assertEqual(out["app_category"], AppCategory.UNKNOWN)
        self.assertEqual(out["confidence"], Confidence.UNKNOWN)

    def test_llm_garbled_response_falls_back_to_rule(self):
        # LLM returned a string but it's not parseable JSON → fall
        # back to the rule answer.
        with mock.patch("sentinel.llm.call_llm",
                        return_value="sorry I can't help"):
            out = interpret_window_with_llm(_snap(
                process="WeirdApp.exe", title="x"))
        self.assertEqual(out["app_category"], AppCategory.UNKNOWN)

    def test_llm_failure_does_not_get_cached(self):
        # If we cached failures, a transient outage would poison the
        # cache forever. Verify a None response leaves the cache empty.
        with mock.patch("sentinel.llm.call_llm", return_value=None):
            interpret_window_with_llm(_snap(
                process="WeirdApp.exe", title="x"))
        # The cache file shouldn't have been written
        self.assertFalse(self.cache_path.exists())

    def test_empty_snapshot_skips_llm(self):
        # Both process and title empty → no point asking LLM.
        with mock.patch("sentinel.llm.call_llm",
                        side_effect=AssertionError("should not be called")):
            out = interpret_window_with_llm(_snap())
        self.assertEqual(out["app_category"], AppCategory.UNKNOWN)


class TestCachePersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.tmp.name) / "cache.json"
        self.patcher = mock.patch.object(wsl, "CACHE_FILE", self.cache_path)
        self.patcher.start()
        reset_cache_for_tests()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        reset_cache_for_tests()

    def _llm_response(self):
        return (
            '{"app_category": "ide", "content_type": "coding", '
            '"topic_signal": "MyApp scripting", "platform": "", '
            '"file": "main.lua", "project": "MyApp", "contact": ""}'
        )

    def test_first_call_writes_cache_to_disk(self):
        with mock.patch("sentinel.llm.call_llm",
                        return_value=self._llm_response()):
            interpret_window_with_llm(_snap(
                process="WeirdApp.exe",
                title="WeirdApp - main.lua"))
        self.assertTrue(self.cache_path.exists())
        content = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(len(content), 1)

    def test_second_identical_call_uses_cache(self):
        snap = _snap(process="WeirdApp.exe", title="WeirdApp - main.lua")
        with mock.patch("sentinel.llm.call_llm",
                        return_value=self._llm_response()) as mock_llm:
            interpret_window_with_llm(snap)
            interpret_window_with_llm(snap)
            interpret_window_with_llm(snap)
        # Only the first call hit LLM; the rest came from cache.
        self.assertEqual(mock_llm.call_count, 1)

    def test_cache_survives_module_reload(self):
        # Write something into cache, then simulate a fresh process
        # (clear in-memory cache) — the next call must read disk.
        snap = _snap(process="WeirdApp.exe", title="WeirdApp - main.lua")
        with mock.patch("sentinel.llm.call_llm",
                        return_value=self._llm_response()):
            interpret_window_with_llm(snap)

        reset_cache_for_tests()  # simulate restart

        with mock.patch("sentinel.llm.call_llm",
                        side_effect=AssertionError("cache should serve this")):
            out = interpret_window_with_llm(snap)
        self.assertEqual(out["app_category"], "ide")

    def test_different_titles_get_different_cache_entries(self):
        with mock.patch("sentinel.llm.call_llm",
                        return_value=self._llm_response()) as mock_llm:
            interpret_window_with_llm(_snap(
                process="WeirdApp.exe", title="title 1"))
            interpret_window_with_llm(_snap(
                process="WeirdApp.exe", title="title 2"))
        # Two different titles → two LLM calls
        self.assertEqual(mock_llm.call_count, 2)
        content = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(len(content), 2)

    def test_corrupt_cache_does_not_crash(self):
        # Write bad JSON to the cache path → next interpret call
        # should treat cache as empty, not raise.
        self.cache_path.write_text("not json at all", encoding="utf-8")
        reset_cache_for_tests()
        with mock.patch("sentinel.llm.call_llm",
                        return_value=self._llm_response()):
            out = interpret_window_with_llm(_snap(
                process="WeirdApp.exe", title="x"))
        self.assertEqual(out["app_category"], "ide")  # LLM ran & succeeded


class TestEviction(unittest.TestCase):
    def test_under_cap_no_eviction(self):
        cache = {f"k{i}": {"interpreted_at": i} for i in range(10)}
        _evict_if_oversized(cache)
        self.assertEqual(len(cache), 10)

    def test_over_cap_evicts_oldest_first(self):
        # Build oversized cache with deterministic ages
        n = MAX_CACHE_ENTRIES + 50
        cache = {f"k{i}": {"interpreted_at": float(i)} for i in range(n)}
        _evict_if_oversized(cache)
        self.assertEqual(len(cache), MAX_CACHE_ENTRIES)
        # The 50 oldest (lowest interpreted_at) should be gone.
        for i in range(50):
            self.assertNotIn(f"k{i}", cache)
        # The newest should still be there.
        self.assertIn(f"k{n-1}", cache)


class TestKeyConstruction(unittest.TestCase):
    def test_key_separates_process_and_title(self):
        # Same title, different process — must produce different keys
        # so a generic title (e.g. "untitled") in two different apps
        # gets two cache entries.
        k1 = _make_key("a.exe", "untitled")
        k2 = _make_key("b.exe", "untitled")
        self.assertNotEqual(k1, k2)

    def test_key_handles_empty_components(self):
        # Empty process or title shouldn't crash key construction.
        _make_key("", "title")
        _make_key("proc.exe", "")
        _make_key("", "")


class TestPrivacyAndSchema(unittest.TestCase):
    """interpret_window_with_llm output must always satisfy the same
    schema contract as the rule layer. Nine keys, every call."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = mock.patch.object(
            wsl, "CACHE_FILE", Path(self.tmp.name) / "cache.json")
        self.patcher.start()
        reset_cache_for_tests()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        reset_cache_for_tests()

    EXPECTED_KEYS = {
        "app_category", "content_type", "topic_signal",
        "platform", "file", "project", "contact",
        "confidence", "is_idle",
    }

    def test_rule_path_returns_full_schema(self):
        out = interpret_window_with_llm(_snap(
            process="chrome.exe", title="Reddit"))
        self.assertEqual(set(out.keys()), self.EXPECTED_KEYS)

    def test_llm_path_returns_full_schema(self):
        with mock.patch("sentinel.llm.call_llm", return_value=(
                '{"app_category": "browser", "content_type": "reading", '
                '"topic_signal": "x", "platform": "", "file": "", '
                '"project": "", "contact": ""}')):
            out = interpret_window_with_llm(_snap(
                process="weird.exe", title="x"))
        self.assertEqual(set(out.keys()), self.EXPECTED_KEYS)

    def test_failure_path_returns_full_schema(self):
        with mock.patch("sentinel.llm.call_llm", return_value=None):
            out = interpret_window_with_llm(_snap(
                process="weird.exe", title="x"))
        self.assertEqual(set(out.keys()), self.EXPECTED_KEYS)

    def test_is_idle_passes_through_llm_path(self):
        with mock.patch("sentinel.llm.call_llm", return_value=(
                '{"app_category": "browser", "content_type": "reading", '
                '"topic_signal": "x", "platform": "", "file": "", '
                '"project": "", "contact": ""}')):
            out = interpret_window_with_llm(_snap(
                process="weird.exe", title="x", is_idle=True))
        self.assertTrue(out["is_idle"])


if __name__ == "__main__":
    unittest.main()
