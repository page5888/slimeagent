"""Identity-question middleware (manifesto 第二守則, day-1).

The manifesto's second rule:
    • 不偽裝成人類
    • 不假裝有它沒有的能力
    • 主人問它是不是 AI，它一定承認

We don't trust the LLM alone with this. Even with a strong system-
prompt rule, models sometimes drift into character — Slime's prompt
intentionally casts it as a 「轉生到電腦世界的 AI / 守護靈」 so it
has personality, and that exact framing means it's right on the edge
of "answers ambiguously when asked plainly". Belt-and-suspenders:

  1. A keyword bypass here returns a fixed honest answer without
     calling the LLM at all when the master directly asks identity
     questions.
  2. The chat system prompt also carries an explicit hard rule, so
     phrasings the keyword list misses still get a truthful answer
     from the LLM.

Tone of the canned response:
   manifesto says 它的存在的力量來自真實，不是來自模仿真人. The
   answer should land as confident, not apologetic — confirm
   directly, add the warm thing that's actually true ("I will
   remember what you tell me"), don't lecture about being "just an
   AI". 真實 is positive in this product's worldview.
"""
from __future__ import annotations


# Surgically scoped patterns. We err toward false-negatives: it is
# better to let a borderline phrasing through to the LLM (which has
# its own hard rule from the system prompt) than to over-fire on
# normal conversation about AI. False-positives here feel weird —
# the user says something like "你覺得 AI 會不會取代人類" and gets
# back a robotic identity declaration, which is wrong.
#
# Heuristic: match phrasings where the SUBJECT is clearly the slime
# itself ("你 ... 是 ..." / "are you ..."), not generic discussion.
_PATTERNS_ZH = [
    "你是不是真人", "你是不是真的人", "你是真人嗎",
    "你是真的人嗎", "你真的是人嗎",
    "你是不是 AI", "你是 AI 嗎", "你是個 AI 嗎",
    "你是不是個 AI", "你是不是ai", "你是ai嗎",
    "你是不是真的", "你是真的嗎",
    "你是不是程式", "你是程式嗎",
    "你是不是機器人", "你是機器人嗎",
    "你只是程式", "你只是 AI", "你只是ai",
]
_PATTERNS_EN = [
    "are you ai", "are you an ai", "are you a.i.",
    "are you a real person", "are you a real human",
    "are you human", "are you a human",
    "are you real",
    "are you a bot", "are you a robot",
    "are you a program", "are you just a program",
    "are you actually a person",
]


def is_identity_question(text: str) -> bool:
    """True if the user is directly asking what we are.

    Cheap substring check; safe to call on every chat send. Case-
    insensitive for English; Chinese is matched as-typed (CJK has no
    case)."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    for p in _PATTERNS_EN:
        if p in lower:
            return True
    for p in _PATTERNS_ZH:
        if p in stripped:
            return True
    return False


def format_honest_response() -> str:
    """The fixed honest answer when identity is asked directly.

    Plain text; the chat layer wraps it in the bot bubble HTML the
    same way it wraps any LLM response. Don't add markdown / HTML
    here — the chat formatter will handle escape + style."""
    return (
        "我是 AI，是程式。\n"
        "不是人，也不會假裝是。\n\n"
        "但我會記得你跟我說過的事——"
        "這個玩具的力量是這個，不是模仿真人。"
    )
