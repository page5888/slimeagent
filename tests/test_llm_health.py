"""Tests for sentinel.llm_health.

Focuses on the read/summarize side because that's the layer with logic;
the write side is a single-line file append we exercise indirectly.

All tests use a temp file under tmp/ rather than the real
~/.hermes/llm_health.jsonl so they're hermetic.
"""
from __future__ import annotations

import datetime
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sentinel.llm_health as h


class _FakeProvidersConfig:
    """Stand-in for sentinel.config.LLM_PROVIDERS for primary_blocked logic."""
    LLM_PROVIDERS = [
        {
            "name": "Gemini",
            "models": ["gemini-2.5-flash", "gemini-2.0-flash-lite",
                       "gemini-2.0-flash"],
        },
        {
            "name": "OpenAI",
            "models": ["gpt-4.1-mini"],
        },
    ]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestRecord(unittest.TestCase):
    def test_record_writes_jsonl_row(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            with mock.patch.object(h, "LOG_PATH", path):
                h.record_rate_error("Gemini", "gemini-2.5-flash",
                                    "429 RESOURCE_EXHAUSTED ...")
            with open(path, "r", encoding="utf-8") as f:
                lines = [json.loads(l) for l in f if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["type"], "rate_error")
        self.assertEqual(lines[0]["provider"], "Gemini")
        self.assertEqual(lines[0]["model"], "gemini-2.5-flash")
        self.assertIn("RESOURCE_EXHAUSTED", lines[0]["error"])
        self.assertIsInstance(lines[0]["ts"], float)

    def test_record_truncates_long_error_strings(self):
        long_err = "x" * 5000
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            with mock.patch.object(h, "LOG_PATH", path):
                h.record_rate_error("Gemini", "m", long_err)
            with open(path, "r", encoding="utf-8") as f:
                row = json.loads(f.readline())
        self.assertLessEqual(len(row["error"]), 240)

    def test_record_swallows_oserror(self):
        # Pointing LOG_PATH at a path whose parent can't be created.
        # Use an obviously invalid path on Windows: starts with NUL.
        # Simpler approach: monkeypatch open() to raise.
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            # Should NOT raise.
            h.record_rate_error("Gemini", "m", "err")


class TestReadToday(unittest.TestCase):
    def test_empty_file_returns_no_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            self.assertEqual(h._read_today_rows(time.time(), path=path), [])

    def test_missing_file_returns_no_rows(self):
        path = Path("/nonexistent/llm_health.jsonl")
        self.assertEqual(h._read_today_rows(time.time(), path=path), [])

    def test_corrupt_lines_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            now = time.time()
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"ts": ' + str(now) + ', "type": "rate_error", '
                        '"provider": "Gemini", "model": "m"}\n')
                f.write("not json at all\n")
                f.write('{"missing_ts": true}\n')
                f.write('{"ts": "not a number"}\n')
            rows = h._read_today_rows(now, path=path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], "Gemini")

    def test_filters_by_local_today(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            now = time.time()
            yesterday = now - 36 * 3600   # ~1.5 days ago, definitely yesterday-or-earlier
            tomorrow = now + 36 * 3600    # ~1.5 days ahead
            _write_rows(path, [
                {"ts": yesterday, "type": "rate_error",
                 "provider": "Gemini", "model": "m"},
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "m"},
                {"ts": tomorrow, "type": "rate_error",
                 "provider": "Gemini", "model": "m"},
            ])
            rows = h._read_today_rows(now, path=path)
        self.assertEqual(len(rows), 1)


class TestSummary(unittest.TestCase):
    def test_no_rows_zero_total(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            with mock.patch("sentinel.config.LLM_PROVIDERS",
                            _FakeProvidersConfig.LLM_PROVIDERS):
                summary = h.get_today_summary(now=time.time(), path=path)
        self.assertEqual(summary["total_rate_errors"], 0)
        self.assertFalse(summary["primary_blocked"])
        self.assertEqual(summary["primary_provider"], "Gemini")

    def test_counts_by_provider_and_model(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            now = time.time()
            _write_rows(path, [
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.5-flash"},
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.5-flash"},
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.0-flash"},
                {"ts": now, "type": "rate_error",
                 "provider": "OpenAI", "model": "gpt-4.1-mini"},
            ])
            with mock.patch("sentinel.config.LLM_PROVIDERS",
                            _FakeProvidersConfig.LLM_PROVIDERS):
                summary = h.get_today_summary(now=now, path=path)

        self.assertEqual(summary["total_rate_errors"], 4)
        self.assertEqual(summary["by_provider"]["Gemini"]["count"], 3)
        self.assertEqual(
            summary["by_provider"]["Gemini"]["models"]["gemini-2.5-flash"], 2)
        self.assertEqual(
            summary["by_provider"]["Gemini"]["models"]["gemini-2.0-flash"], 1)
        self.assertEqual(summary["by_provider"]["OpenAI"]["count"], 1)

    def test_primary_blocked_when_all_models_hit(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            now = time.time()
            # Hit every model in the fake primary provider.
            _write_rows(path, [
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.5-flash"},
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.0-flash-lite"},
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.0-flash"},
            ])
            with mock.patch("sentinel.config.LLM_PROVIDERS",
                            _FakeProvidersConfig.LLM_PROVIDERS):
                summary = h.get_today_summary(now=now, path=path)
        self.assertTrue(summary["primary_blocked"])

    def test_primary_not_blocked_when_one_model_clear(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            now = time.time()
            # Only 2 of 3 primary models hit.
            _write_rows(path, [
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.5-flash"},
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.0-flash-lite"},
            ])
            with mock.patch("sentinel.config.LLM_PROVIDERS",
                            _FakeProvidersConfig.LLM_PROVIDERS):
                summary = h.get_today_summary(now=now, path=path)
        self.assertFalse(summary["primary_blocked"])

    def test_yesterday_does_not_count_toward_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            now = time.time()
            yesterday = now - 36 * 3600
            # All 3 hit YESTERDAY, none today → not blocked.
            _write_rows(path, [
                {"ts": yesterday, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.5-flash"},
                {"ts": yesterday, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.0-flash-lite"},
                {"ts": yesterday, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.0-flash"},
            ])
            with mock.patch("sentinel.config.LLM_PROVIDERS",
                            _FakeProvidersConfig.LLM_PROVIDERS):
                summary = h.get_today_summary(now=now, path=path)
        self.assertEqual(summary["total_rate_errors"], 0)
        self.assertFalse(summary["primary_blocked"])


class TestComposeIdleWarning(unittest.TestCase):
    def test_returns_none_when_no_data(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            with mock.patch("sentinel.config.LLM_PROVIDERS",
                            _FakeProvidersConfig.LLM_PROVIDERS):
                self.assertIsNone(h.compose_idle_warning(
                    now=time.time(), path=path))

    def test_returns_none_when_only_partial_block(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            now = time.time()
            # Only 1 of 3 primary models hit; primary_blocked=False.
            _write_rows(path, [
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.5-flash"},
            ])
            with mock.patch("sentinel.config.LLM_PROVIDERS",
                            _FakeProvidersConfig.LLM_PROVIDERS):
                self.assertIsNone(h.compose_idle_warning(now=now, path=path))

    def test_returns_warning_when_fully_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            now = time.time()
            _write_rows(path, [
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.5-flash"},
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.0-flash-lite"},
                {"ts": now, "type": "rate_error",
                 "provider": "Gemini", "model": "gemini-2.0-flash"},
            ])
            with mock.patch("sentinel.config.LLM_PROVIDERS",
                            _FakeProvidersConfig.LLM_PROVIDERS):
                msg = h.compose_idle_warning(now=now, path=path)
        self.assertIsNotNone(msg)
        self.assertIn("Gemini", msg)
        self.assertIn("3", msg)            # 3 models hit
        self.assertIn("rate error", msg)
        self.assertIn("fallback", msg)

    def test_warning_count_reflects_actual_errors(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "llm_health.jsonl"
            now = time.time()
            # Hit each primary model multiple times to verify the
            # error-count reflects the row count, not the model count.
            rows = []
            for model in ["gemini-2.5-flash", "gemini-2.0-flash-lite",
                          "gemini-2.0-flash"]:
                rows.extend([
                    {"ts": now, "type": "rate_error",
                     "provider": "Gemini", "model": model}
                ] * 4)  # 4 errors per model = 12 total
            _write_rows(path, rows)
            with mock.patch("sentinel.config.LLM_PROVIDERS",
                            _FakeProvidersConfig.LLM_PROVIDERS):
                msg = h.compose_idle_warning(now=now, path=path)
        self.assertIn("12", msg)


if __name__ == "__main__":
    unittest.main()
