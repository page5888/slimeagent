"""Trigger judge — LLM checks current context before a routine fires.

Phase H. Routines now have an optional `judge_prompt`. When set, the
scheduler calls evaluate() between trigger-match and step-execution.
The LLM sees:

  - The routine's name and step list (so it knows what would run)
  - The user-supplied judge_prompt (the question to answer)
  - Current Context Bus render (system / files / activity / screen / memory)
  - The trigger that fired

…and returns {"decide": "go"|"skip", "reason": "..."}. Audit log
records the decision so the user can later see why a routine did
or didn't fire.

Why the LLM, not a Python predicate?

  Phase F+G's trigger language can express "when X happens" but not
  "X happened AND it's the right context". Most "right context"
  questions don't reduce to a clean Boolean — they involve fuzzy
  reasoning about activity logs, recent chat, etc. An LLM judge
  with the same context_bus the chat sees gives the user one mental
  model: the slime weighs evidence the same way whether you're
  asking it directly or it's deciding alone.

Failure modes:

  - LLM down / rate-limited → fail-CLOSED (decide=skip with reason
    "judge unavailable"). Better to miss a fire than auto-fire when
    the user expected the judge to gate.
  - LLM emits malformed JSON → same: skip with "couldn't parse".
  - judge_prompt is empty → caller skipped this whole flow (decide
    is implicit "go" — Phase F/G behavior preserved for routines
    without a judge).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional


log = logging.getLogger("sentinel.routines.judge")


@dataclass
class JudgeDecision:
    """Result from the LLM judge. `go == True` means fire."""
    go: bool
    reason: str
    raw: str = ""           # original LLM text, for audit / debugging


JUDGE_SYSTEM_PROMPT = """\
你是 AI Slime 的 routine 判斷者。當一個 routine 的觸發條件已經滿足,
**但還沒執行**前,你看當前情境決定:現在做這件事是對的嗎?

輸入:
- routine 名稱跟步驟(它要做什麼)
- 觸發來源(時間、視窗、檔案、閒置)
- 當前 context(系統狀態、最近活動、相關記憶)
- 主人寫的判斷規則(judge_prompt)

回 JSON(純粹,不要 markdown wrap):
{"decide": "go" | "skip", "reason": "<簡短中文,給主人看的原因>"}

原則:
- 不確定就 skip。失誤跑沒意義的事比錯過一次糟。
- 主人寫的規則(judge_prompt)是最高權威,看不懂就 skip。
- reason 要明確,讓主人從 audit log 看懂你為什麼這樣決定。
"""


def _build_user_prompt(
    routine_name: str,
    judge_prompt: str,
    steps: list[dict],
    trigger: dict,
    context_text: str,
) -> str:
    """Assemble the per-call user prompt the judge LLM evaluates."""
    step_lines = []
    for i, s in enumerate(steps[:5], 1):
        if not isinstance(s, dict):
            continue
        title = s.get("title") or s.get("action_type", "?")
        at = s.get("action_type", "?")
        step_lines.append(f"  {i}. {title} ({at})")
    steps_text = "\n".join(step_lines) or "(無步驟)"

    return f"""\
=== Routine ===
名稱: {routine_name}
步驟:
{steps_text}

=== 觸發 ===
{json.dumps(trigger, ensure_ascii=False)}

=== 主人的判斷規則 (judge_prompt) ===
{judge_prompt.strip() or "(沒寫,你自己合理判斷)"}

=== 當前情境 ===
{context_text or "(no context)"}

請決定: go 或 skip。"""


def _parse_decision(text: str) -> JudgeDecision:
    """Extract {decide, reason} from LLM output. Tolerates code fences,
    trailing prose, malformed JSON via one repair pass.

    On any failure we fall through to skip — fail-closed. The caller
    will log the raw text so we can debug what the LLM said.
    """
    if not text:
        return JudgeDecision(go=False, reason="judge: empty response")
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return JudgeDecision(go=False,
                             reason="judge: no JSON in response",
                             raw=text)
    body = m.group(0)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return JudgeDecision(go=False,
                             reason="judge: malformed JSON",
                             raw=text)
    if not isinstance(data, dict):
        return JudgeDecision(go=False, reason="judge: not an object",
                             raw=text)
    decide = str(data.get("decide", "")).strip().lower()
    reason = str(data.get("reason", "")).strip() or "(no reason)"
    if decide == "go":
        return JudgeDecision(go=True, reason=reason, raw=text)
    if decide == "skip":
        return JudgeDecision(go=False, reason=reason, raw=text)
    return JudgeDecision(
        go=False,
        reason=f"judge: unknown decide={decide!r}",
        raw=text,
    )


def evaluate(
    routine_name: str,
    judge_prompt: str,
    steps: list[dict],
    trigger: dict,
) -> JudgeDecision:
    """Run the judge for one routine. Returns a decision.

    If judge_prompt is empty, returns "go" without calling the LLM —
    routines without a judge keep their Phase F/G unconditional fire
    behavior.

    LLM failure → "skip" with reason. The scheduler logs the audit
    entry; the user sees skip + reason in the routines audit log.
    """
    if not judge_prompt or not judge_prompt.strip():
        return JudgeDecision(go=True, reason="(no judge configured)")

    # Pull current Context Bus render so the judge sees what the chat
    # LLM would see — same observability surface, no special hooks.
    try:
        from sentinel.context_bus import get_bus
        context_text = get_bus().render()
    except Exception as e:
        context_text = f"(context bus unavailable: {e})"

    prompt = _build_user_prompt(
        routine_name=routine_name,
        judge_prompt=judge_prompt,
        steps=steps or [],
        trigger=trigger or {},
        context_text=context_text,
    )

    try:
        from sentinel import config
        from sentinel.llm import call_llm
        text = call_llm(
            prompt=prompt,
            system=JUDGE_SYSTEM_PROMPT,
            temperature=0.2,         # judgement should be steady
            max_tokens=300,
            model_pref=config.ANALYSIS_MODEL_PREF,
            task_type="analysis",
        )
    except Exception as e:
        log.warning(f"judge LLM call failed: {e}")
        return JudgeDecision(
            go=False,
            reason=f"judge LLM unavailable: {e}",
        )

    decision = _parse_decision(text or "")
    log.info(
        f"judge for {routine_name!r}: "
        f"{'go' if decision.go else 'skip'} ({decision.reason[:80]})"
    )
    return decision
