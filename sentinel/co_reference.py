"""Co-reference anchor block builder for the chat system prompt.

ADR 2026-04-30 共同沉積 mechanism 3:

    當主人跟 Slime 之間發生某個「值得記住的瞬間」時, Slime 自主標記它
    (對應 emergent milestones ADR)。之後 Slime 引用這個瞬間時,**用主人
    記得的方式引用**。

    例:
    - D178 主人在跟 Slime 講工作壓力時,用了一個比喻「像在水底」
    - D456 主人又遇到類似情境,Slime 可以說:**「水底嗎?」**

This module is the chat-side surface for that mechanism. It pulls the
master_phrase entries out of memorable_moments and renders them as a
prompt block so the LLM can choose to echo a phrase verbatim when the
current conversation calls for it.

We intentionally don't tell the LLM *which* phrase to use — that's a
judgement call that depends on the live conversation. We just make the
verbatim phrases available and tell it: don't paraphrase, don't force.

Empty → "" (no block). Chat splices it in unconditionally so missing
data doesn't require conditional rendering at the call site.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger("sentinel.co_reference")

# Render at most this many anchors. Beyond ~10 the LLM starts ignoring
# them anyway, and a long list pulls focus from the actual conversation.
DEFAULT_LIMIT = 10


def build_block(limit: int = DEFAULT_LIMIT, now: Optional[float] = None) -> str:
    """Return a chat-prompt-ready Slime 之語 block, or "" if no anchors.

    Format (when there are entries)::

        === Slime 之語 — 你跟主人之間專屬的語彙 ===
        這些是主人過去說過、被你自己選下來記下的一些話。
        不要每句都念，也不要逐條解釋；只有當下對話的氣氛
        剛好對得上時，可以**逐字回引**其中一句（不要改寫）。
        - 第 178 天 主人說「像在水底」
        - 第 215 天 主人說「等等啦」

    Defensive: any error in retrieval / formatting returns "" so chat
    builds the rest of its prompt without the block.
    """
    try:
        from sentinel.identity import get_co_reference_phrases
        from sentinel.evolution import load_evolution
    except Exception as e:
        log.debug("co_reference build_block import failed: %s", e)
        return ""

    try:
        anchors = get_co_reference_phrases(limit=limit)
    except Exception as e:
        log.debug("co_reference get_co_reference_phrases failed: %s", e)
        return ""

    if not anchors:
        return ""

    try:
        evo = load_evolution()
        birth = float(getattr(evo, "birth_time", 0) or 0)
    except Exception as e:
        log.debug("co_reference load_evolution failed: %s", e)
        birth = 0

    if now is None:
        now = time.time()

    lines: list[str] = []
    for m in anchors:
        phrase = (m.get("master_phrase") or "").strip()
        if not phrase:
            continue
        ts = float(m.get("time", 0) or 0)
        if birth and ts:
            day = max(1, int((ts - birth) / 86400) + 1)
            lines.append(f"- 第 {day} 天 主人說「{phrase}」")
        else:
            # Missing birth/time — render plain. Better than dropping.
            lines.append(f"- 主人說「{phrase}」")

    if not lines:
        return ""

    return (
        "=== Slime 之語 — 你跟主人之間專屬的語彙 ===\n"
        "這些是主人過去說過、被你自己選下來記下的一些話。\n"
        "不要每句都念，也不要逐條解釋；只有當下對話的氣氛剛好對得上時，\n"
        "可以**逐字回引**其中一句（不要改寫、不要翻譯、不要塞進句子裡）。\n"
        + "\n".join(lines)
        + "\n\n"
    )
