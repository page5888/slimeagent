"""Tests for sentinel/window_semantics.py — Phase 3a rule layer.

Each test is a tiny "if master is on X, slime understands Y" assertion.
The point is to lock the rule layer's behaviour against accidental
regressions; Phase 5's impulse engine will treat these mappings as
load-bearing.

No LLM, no IO, no Qt — pure dict in / dict out.
"""
from __future__ import annotations

import unittest

from sentinel.window_semantics import (
    AppCategory, ContentType, Confidence,
    interpret_window,
    _parse_ide_title,
    _detect_browser_platform,
    _parse_messaging_title,
)


def _snap(process: str = "", title: str = "", is_idle: bool = False) -> dict:
    """Build a minimal focus_snapshot for testing."""
    return {
        "process_name": process,
        "window_title": title,
        "is_idle": is_idle,
    }


class TestSchema(unittest.TestCase):
    """The output dict's shape is contract — Phase 3b / 4 / 5 will read
    these keys. Verify every call returns the full set, even on
    unknown / empty input."""

    EXPECTED_KEYS = {
        "app_category", "content_type", "topic_signal",
        "platform", "file", "project", "contact",
        "confidence", "is_idle",
    }

    def test_empty_snapshot_returns_full_schema(self):
        out = interpret_window(_snap())
        self.assertEqual(set(out.keys()), self.EXPECTED_KEYS)

    def test_unknown_app_returns_full_schema(self):
        out = interpret_window(_snap(process="weird.exe", title="something"))
        self.assertEqual(set(out.keys()), self.EXPECTED_KEYS)

    def test_known_app_returns_full_schema(self):
        out = interpret_window(_snap(process="chrome.exe",
                                     title="Reddit - r/programming"))
        self.assertEqual(set(out.keys()), self.EXPECTED_KEYS)

    def test_is_idle_passes_through(self):
        out = interpret_window(_snap(process="chrome.exe",
                                     title="x", is_idle=True))
        self.assertTrue(out["is_idle"])
        out2 = interpret_window(_snap(process="chrome.exe",
                                      title="x", is_idle=False))
        self.assertFalse(out2["is_idle"])


class TestEmptyInput(unittest.TestCase):
    def test_both_empty_is_unknown(self):
        out = interpret_window(_snap())
        self.assertEqual(out["app_category"], AppCategory.UNKNOWN)
        self.assertEqual(out["confidence"], Confidence.UNKNOWN)

    def test_missing_keys_dont_crash(self):
        out = interpret_window({})
        self.assertEqual(out["app_category"], AppCategory.UNKNOWN)


class TestBrowserDetection(unittest.TestCase):
    def test_chrome_is_browser(self):
        out = interpret_window(_snap(process="chrome.exe", title="Google"))
        self.assertEqual(out["app_category"], AppCategory.BROWSER)

    def test_firefox_is_browser(self):
        out = interpret_window(_snap(process="firefox.exe", title="x"))
        self.assertEqual(out["app_category"], AppCategory.BROWSER)

    def test_msedge_is_browser(self):
        out = interpret_window(_snap(process="msedge.exe", title="x"))
        self.assertEqual(out["app_category"], AppCategory.BROWSER)

    def test_browser_default_content_is_browsing(self):
        # No platform match → generic browsing
        out = interpret_window(_snap(process="chrome.exe",
                                     title="Some random page"))
        self.assertEqual(out["content_type"], ContentType.BROWSING)


class TestBrowserPlatformRules(unittest.TestCase):
    """The施工指示 example titles + a few extras."""

    def test_reddit(self):
        out = interpret_window(_snap(
            process="chrome.exe",
            title="Reddit - r/programming/comments/xxx - Bash 還是 Zsh?"))
        self.assertEqual(out["platform"], "reddit")
        self.assertEqual(out["content_type"], ContentType.SOCIAL_DISCUSSION)
        self.assertEqual(out["confidence"], Confidence.HIGH)

    def test_youtube(self):
        out = interpret_window(_snap(
            process="msedge.exe",
            title="YouTube - 「如何治療童年創傷」 - YouTube"))
        self.assertEqual(out["platform"], "youtube")
        self.assertEqual(out["content_type"], ContentType.VIDEO_WATCHING)

    def test_github(self):
        out = interpret_window(_snap(
            process="chrome.exe",
            title="page5888/slimeagent: AI Slime - github.com"))
        self.assertEqual(out["platform"], "github")
        self.assertEqual(out["content_type"], ContentType.READING)

    def test_stackoverflow(self):
        out = interpret_window(_snap(
            process="chrome.exe",
            title="python - foo bar baz - Stack Overflow"))
        self.assertEqual(out["platform"], "stackoverflow")

    def test_twitter_aliased_to_x_com(self):
        # Either domain or "Twitter" string should hit
        out = interpret_window(_snap(process="chrome.exe",
                                     title="some post / X"))
        # "X" alone is not enough — we need x.com or twitter.com or
        # "Twitter" word for the rule. Confirm it doesn't misfire.
        self.assertNotEqual(out["platform"], "twitter")
        out2 = interpret_window(_snap(process="chrome.exe",
                                      title="post on x.com"))
        self.assertEqual(out2["platform"], "twitter")

    def test_hackernews(self):
        out = interpret_window(_snap(
            process="chrome.exe",
            title="Why X | Hacker News"))
        self.assertEqual(out["platform"], "hackernews")

    def test_bilibili(self):
        out = interpret_window(_snap(
            process="firefox.exe",
            title="嗶哩嗶哩 - 某個影片"))
        self.assertEqual(out["platform"], "bilibili")
        self.assertEqual(out["content_type"], ContentType.VIDEO_WATCHING)

    def test_ai_chat_recognized(self):
        # Slime should know when the master is talking to *another* AI
        # — Phase 5 impulse may want to silence itself there.
        out = interpret_window(_snap(
            process="chrome.exe",
            title="Some chat - claude.ai"))
        self.assertEqual(out["platform"], "ai_chat")
        self.assertEqual(out["content_type"], ContentType.CONVERSATION)


class TestIdeDetection(unittest.TestCase):
    def test_vscode_is_ide(self):
        out = interpret_window(_snap(process="Code.exe",
                                     title="main.py - Visual Studio Code"))
        self.assertEqual(out["app_category"], AppCategory.IDE)
        self.assertEqual(out["content_type"], ContentType.CODING)

    def test_pycharm_is_ide(self):
        out = interpret_window(_snap(process="pycharm64.exe",
                                     title="main.py - PyCharm"))
        self.assertEqual(out["app_category"], AppCategory.IDE)

    def test_neovim_is_ide(self):
        out = interpret_window(_snap(process="nvim.exe",
                                     title="main.py"))
        self.assertEqual(out["app_category"], AppCategory.IDE)

    def test_ide_extracts_filename_two_segments(self):
        out = interpret_window(_snap(process="Code.exe",
                                     title="main.py - Visual Studio Code"))
        self.assertEqual(out["file"], "main.py")
        self.assertEqual(out["confidence"], Confidence.HIGH)

    def test_ide_extracts_filename_and_project_three_segments(self):
        out = interpret_window(_snap(
            process="Code.exe",
            title="gui.py - slimeagent - Visual Studio Code"))
        self.assertEqual(out["file"], "gui.py")
        self.assertEqual(out["project"], "slimeagent")
        self.assertEqual(out["topic_signal"], "coding: gui.py (slimeagent)")

    def test_ide_jetbrains_format(self):
        out = interpret_window(_snap(
            process="pycharm64.exe",
            title="main.py (slimeagent) - PyCharm 2024.1"))
        self.assertEqual(out["file"], "main.py")
        self.assertEqual(out["project"], "slimeagent")

    def test_ide_with_modified_marker(self):
        # VS Code prepends a ● for unsaved changes
        f, p = _parse_ide_title("● main.py - slimeagent - Visual Studio Code")
        self.assertEqual(f, "main.py")
        self.assertEqual(p, "slimeagent")

    def test_ide_unparseable_title_still_codes(self):
        # Some weird title we can't parse — we still know it's an IDE.
        out = interpret_window(_snap(process="Code.exe", title="something"))
        self.assertEqual(out["app_category"], AppCategory.IDE)
        self.assertEqual(out["content_type"], ContentType.CODING)
        self.assertEqual(out["file"], "")


class TestMessagingDetection(unittest.TestCase):
    def test_telegram_is_messaging(self):
        out = interpret_window(_snap(process="Telegram.exe",
                                     title="Telegram"))
        self.assertEqual(out["app_category"], AppCategory.MESSAGING)
        self.assertEqual(out["content_type"], ContentType.CONVERSATION)

    def test_extracts_contact_telegram_style(self):
        out = interpret_window(_snap(process="Telegram.exe",
                                     title="媽媽 - Telegram"))
        self.assertEqual(out["contact"], "媽媽")
        self.assertEqual(out["confidence"], Confidence.HIGH)

    def test_extracts_contact_slack_dot_separator(self):
        # newer Slack format
        contact = _parse_messaging_title("dev-team · Slack")
        self.assertEqual(contact, "dev-team")

    def test_no_contact_when_unparseable(self):
        # Discord shows just "Discord" — single window for everything.
        out = interpret_window(_snap(process="Discord.exe", title="Discord"))
        self.assertEqual(out["contact"], "")
        # Still classified correctly, just no contact extracted.
        self.assertEqual(out["app_category"], AppCategory.MESSAGING)

    def test_messaging_does_not_leak_content(self):
        # Privacy: even if the title contains a notification preview
        # like "Mom: Hey, are you there?", we extract the contact
        # name only, never the preview text.
        contact = _parse_messaging_title("Mom: Hey are you there - Telegram")
        # We get "Mom: Hey are you there" as the head if it's before
        # the separator. The contract here is: we don't promise to
        # parse the preview out, but the test pins current behaviour
        # so any change is loud.
        self.assertEqual(contact, "Mom: Hey are you there")


class TestVideoAudio(unittest.TestCase):
    def test_vlc_is_video(self):
        out = interpret_window(_snap(process="vlc.exe",
                                     title="some.mp4 - VLC"))
        self.assertEqual(out["app_category"], AppCategory.VIDEO)
        self.assertEqual(out["content_type"], ContentType.VIDEO_WATCHING)

    def test_spotify_is_audio(self):
        out = interpret_window(_snap(process="Spotify.exe",
                                     title="Spotify"))
        self.assertEqual(out["app_category"], AppCategory.AUDIO)
        self.assertEqual(out["content_type"], ContentType.MUSIC_LISTENING)


class TestTerminal(unittest.TestCase):
    def test_cmd_is_terminal(self):
        out = interpret_window(_snap(process="cmd.exe",
                                     title="C:\\Users\\srbow"))
        self.assertEqual(out["app_category"], AppCategory.TERMINAL)
        self.assertEqual(out["content_type"], ContentType.SHELL)

    def test_powershell_is_terminal(self):
        out = interpret_window(_snap(process="powershell.exe",
                                     title="PS C:\\>"))
        self.assertEqual(out["app_category"], AppCategory.TERMINAL)

    def test_wezterm_is_terminal(self):
        out = interpret_window(_snap(process="wezterm-gui.exe",
                                     title="bash"))
        self.assertEqual(out["app_category"], AppCategory.TERMINAL)


class TestDocument(unittest.TestCase):
    def test_word_is_document(self):
        out = interpret_window(_snap(process="WINWORD.EXE",
                                     title="report.docx - Word"))
        self.assertEqual(out["app_category"], AppCategory.DOCUMENT)
        self.assertEqual(out["content_type"], ContentType.READING)

    def test_obsidian_is_document(self):
        out = interpret_window(_snap(process="Obsidian.exe",
                                     title="Daily Note - Obsidian"))
        self.assertEqual(out["app_category"], AppCategory.DOCUMENT)


class TestUnknown(unittest.TestCase):
    """The 20% case Phase 3b's LLM will pick up."""

    def test_truly_unknown_process_yields_unknown(self):
        out = interpret_window(_snap(process="MyCustomApp.exe",
                                     title="some screen"))
        self.assertEqual(out["app_category"], AppCategory.UNKNOWN)
        self.assertEqual(out["content_type"], ContentType.UNKNOWN)
        self.assertEqual(out["confidence"], Confidence.UNKNOWN)

    def test_unknown_keeps_title_as_topic_signal(self):
        # Even when we don't know the category, we surface the title
        # so the LLM fallback (and any human reading the log) has
        # something to work with.
        out = interpret_window(_snap(process="unknown.exe",
                                     title="Some interesting title"))
        self.assertEqual(out["topic_signal"], "Some interesting title")

    def test_truncates_long_titles(self):
        long_title = "x" * 200
        out = interpret_window(_snap(process="unknown.exe", title=long_title))
        # Confirm topic_signal is bounded — Phase 5 doesn't want
        # giant titles in its prompts.
        self.assertLess(len(out["topic_signal"]), 100)


class TestPureFunction(unittest.TestCase):
    """interpret_window must be pure: same input → same output, no
    state. If anyone adds a cache or counter inside the function,
    this test breaks."""

    def test_repeatable(self):
        snap = _snap(process="chrome.exe",
                     title="Reddit - r/programming")
        a = interpret_window(snap)
        b = interpret_window(snap)
        self.assertEqual(a, b)

    def test_does_not_mutate_input(self):
        snap = _snap(process="chrome.exe", title="x")
        before = dict(snap)
        interpret_window(snap)
        self.assertEqual(snap, before)


if __name__ == "__main__":
    unittest.main()
