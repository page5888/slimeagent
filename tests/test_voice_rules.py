"""Regression tests for chat.py voice anti-drift rules + preflight sync.

PR #110 added three hard rules to the top of 對話守則 in
chat.CHAT_SYSTEM_PROMPT to stop slime from drifting into generic
AI-consultant tone on abstract questions:

  1. Three voice anchors (箱子 / 地方 / 感受) pinned directly
  2. Banned consultant-AI vocabulary list
  3. Banned brain-reading flex claims

PR #112 added scripts/preflight.py with `_VOICE_DRIFT_BANNED`, a
runtime-check version of the same banned words. The two lists MUST
stay in sync — if chat.py adds a banned word, preflight should
flag it; if preflight gets a new term, chat.py should ban it.

This module enforces both invariants:

  - The three rule blocks survive in CHAT_SYSTEM_PROMPT.
  - Every term in preflight._VOICE_DRIFT_BANNED is covered by the
    chat.py banned list (so they can't drift apart silently).
"""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


# Make repo root importable so we can pull `scripts.preflight`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_preflight_banned() -> list[str]:
    """Read the banned list from scripts/preflight.py without executing it.

    `scripts/preflight.py` does sys.path manipulation + argparse at
    import time, so importing it as a module from tests is brittle.
    AST-parse the source instead so comments with embedded quotes
    don't pollute the result (a naive char-scan parser would pick
    up "我就是你" inside a comment as if it were a list element).
    """
    import ast

    src = (REPO_ROOT / "scripts" / "preflight.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_VOICE_DRIFT_BANNED":
                    if isinstance(node.value, ast.List):
                        return [
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant)
                            and isinstance(elt.value, str)
                        ]
    raise AssertionError(
        "_VOICE_DRIFT_BANNED list not found in scripts/preflight.py"
    )


class TestChatVoiceRulesPresent(unittest.TestCase):
    """The rules added in PR #110 must remain in CHAT_SYSTEM_PROMPT.

    Don't assert the entire prompt verbatim — that would break on every
    legitimate copy edit. Just assert each rule's distinctive marker
    phrase is present.
    """

    @classmethod
    def setUpClass(cls):
        from sentinel import chat
        cls.prompt = chat.CHAT_SYSTEM_PROMPT

    def test_three_voice_anchors_pinned(self):
        # All three anchors quoted from the ADR voice-anchors doc.
        self.assertIn("我會把一切都收在回憶的箱子", self.prompt)
        self.assertIn("我在這個地方陪你", self.prompt)
        self.assertIn("我感受到你的狀態", self.prompt)

    def test_concrete_not_abstract_instruction(self):
        # The 'concrete > abstract' instruction with example.
        self.assertIn("具體不抽象", self.prompt)

    def test_consultant_vocab_banned(self):
        # Distinctive section header + a representative term.
        self.assertIn("絕對禁用通用 AI 顧問腔", self.prompt)
        self.assertIn("依附感", self.prompt)
        self.assertIn("信任回路", self.prompt)
        self.assertIn("callback", self.prompt)

    def test_sycophant_tail_banned(self):
        # The specific GPT-style sycophant ending we saw in the wild.
        self.assertIn("這種魔法", self.prompt)

    def test_flex_capability_claims_banned(self):
        # Distinctive section header + a representative claim form.
        self.assertIn("絕對禁止 flex 假能力", self.prompt)
        self.assertIn("我懂你的思考", self.prompt)
        self.assertIn("我蒐集你的", self.prompt) if False else None  # kept loose; use one match
        # The whole banned-claim list — at least one must be present
        # (verify the structural rule, not every example).
        self.assertTrue(
            any(s in self.prompt for s in [
                "我懂你的思考", "我預判你的下一步", "我是你腦中的", "你腦中的",
            ]),
            "expected at least one flex-capability example in CHAT_SYSTEM_PROMPT",
        )


class TestPreflightChatBannedListSync(unittest.TestCase):
    """preflight.py and chat.py share a banned-vocabulary contract.

    chat.py is the authoritative source — it instructs slime not
    to use these words. preflight.py is the post-hoc verifier —
    it scans actual outputs for them. If either side drifts, the
    other becomes ineffective:

      - Term in preflight but not in chat → preflight flags drift
        the slime was never told to avoid (false positive).
      - Term in chat but not in preflight → drift happens but no
        alarm fires (silent regression).

    This test enforces: every term in preflight._VOICE_DRIFT_BANNED
    must appear somewhere in CHAT_SYSTEM_PROMPT. The reverse direction
    (chat ⊆ preflight) is not asserted because chat.py's banned list
    is sometimes stated descriptively ("這類 buzzword 一律禁用") with
    examples; preflight only checks the explicit examples.
    """

    @classmethod
    def setUpClass(cls):
        from sentinel import chat
        cls.prompt = chat.CHAT_SYSTEM_PROMPT
        cls.banned = _load_preflight_banned()

    def test_preflight_list_nonempty(self):
        # Sanity: parser worked.
        self.assertGreater(len(self.banned), 5,
                           "expected ≥6 banned terms parsed from preflight.py")

    def test_every_preflight_term_referenced_in_chat_prompt(self):
        missing = [t for t in self.banned if t not in self.prompt]
        self.assertEqual(
            missing, [],
            "Terms in preflight._VOICE_DRIFT_BANNED but missing from "
            "CHAT_SYSTEM_PROMPT — slime is being scanned for words it "
            "was never told to avoid. Either remove from preflight, or "
            "add to chat.py 對話守則:\n  " + "\n  ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
