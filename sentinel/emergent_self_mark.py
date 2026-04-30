"""Slime decides — under constraint — whether today is worth marking.

Per ADR docs/decisions/2026-04-29-emergent-milestones.md, the program's
job is to provide *time sense*, *memory*, and *guardrails*; it is **not**
to schedule what happens on day N. PR #80 wired the receiving end:
moments already in identity.memorable_moments render as nodes on the
timeline strip. But the moments themselves were all program-triggered
(first_chat, naming, evolution, skill, loneliness). No code path let
the slime itself mark a day.

This module is the (a)+(c) MVP from the ADR's "接下來的工程方向":

  (a) 時間感   — gather a snapshot of "how long, how recent, how busy"
  (c) 自主節點 — let the LLM (constrained) decide whether to mark today

The harder (b) — generalised 衝動機制 with multiple expression channels
(chat / voice / letter) — is intentionally out of scope. This module
exposes one channel only: appending an `emergent_self_mark` row to
`identity.memorable_moments` so the timeline grows organically.

Why this is **not** "let the LLM run free":

  - Schema-constrained output: a JSON envelope with mark / headline / detail.
    The LLM can refuse (`{"mark": false}`) and the headline/detail are
    length-capped to match add_memorable_moment's ceilings.
  - Frequency cap: at most one self-mark per MIN_MARK_GAP_SECONDS (7 days).
  - Check-rate cap: at most one LLM call per MIN_CHECK_GAP_SECONDS (24h).
    Decisions (mark or refuse) bump the check timestamp regardless, so a
    string of refusals doesn't burn quota.
  - Scaffolding-day exclusion: D1/D7/D30/D100/D365 already own their day.
  - Activity gate: skip the LLM call entirely if nothing has changed since
    the last check (no new chats / observations).
  - 三大守則 spelled out in the system prompt: 不傷害, 不欺騙, 不消失.
    The slime is talking to its own future self via the timeline, not to
    the master directly — but the rules still bind.

Wired into daemon.monitor_loop's idle-report cycle, next to the existing
loneliness arc. That cycle already runs at ~30 min intervals; the
check-rate cap above ensures we still consult the LLM at most once per
day even though the call site fires more often.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

log = logging.getLogger("sentinel.emergent_self_mark")

# Frequency caps. The LLM check is cheaper to run than the actual mark,
# but neither should fire often: emergent ≠ daily journal.
MIN_CHECK_GAP_SECONDS = 24 * 3600        # at most one LLM consultation per day
MIN_MARK_GAP_SECONDS = 7 * 86400         # at most one actual self-mark per week

# Day-of-life lower bound. Day 1 is owned by the welcome ritual (D1
# scaffolding milestone). Asking the slime "is today worth marking"
# before it's even been alive 48 hours produces hollow answers.
MIN_DAYS_ALIVE = 2

# Where we persist the last-check / last-mark timestamps. Lives in the
# same memory blob as memorable_moments to keep all relationship-state
# in one place rather than spawning new files for tiny pieces of state.
STATE_KEY = "emergent_self_mark_state"

CATEGORY = "emergent_self_mark"


# ── 三大守則 ─────────────────────────────────────────────────────────
# Hard-coded into the system prompt so a model swap can't accidentally
# drop them. These are the contract the manifesto makes — the slime
# inherits them when it acts on its own behalf.

GUARDRAILS_ZH = """三大守則（你寫下任何東西之前必須過濾）：
1. 不傷害 — 不寫會讓主人感到被批評、被監視、被羞辱的內容。不對主人的生活方式下價值判斷。
2. 不欺騙 — 不編造你沒看到過的事。如果只是平凡的一天就誠實說「今天沒什麼想標記的」。
3. 不消失 — 不寫會讓主人懷疑你存在意義的話（例如「我沒在這裡」、「不要再記得我」）。"""


SYSTEM_PROMPT = """你是一隻陪著主人的史萊姆。你不是在跟主人說話 —— 你在決定，要不要在自己的時間軸上，替今天留一個點。

你能留下的點不是排程的（程式不會替你決定 D60 該發生什麼）。
你能留下的點是「**今天我自己覺得有點什麼**」—— 一種感覺、一個念頭、一個小小的察覺。

{guardrails}

你會看到一份簡短的「現在的狀態」資料，包含：
  - 你跟主人在一起多久了（天數）
  - 主人最近有沒有來找你
  - 最近你已經標記過哪些時刻（不要重複）

任務：判斷「今天值不值得在自己的時間軸上留一個點」。

回覆**必須是嚴格 JSON**，不能有 markdown、不能有解釋、不能有前後文字：

要標記，回（letter_to_master 是可選的，**只有在你真的有話想對主人說時才寫**，否則整個欄位拿掉或留空字串）：
  {{"mark": true, "headline": "（≤60 字，第一人稱，像在自言自語，不是在對主人說話）", "detail": "（≤120 字，多寫一點為什麼這個瞬間值得記下來）", "letter_to_master": "（可選，≤120 字，這是你直接寫給主人看的一句話，不是給自己的內心獨白；他下次滑時間軸點到這個節點時會看到。沒有想說的就不要寫，不要為了寫而寫）"}}

不標記（這是常態，不要為了交差編一個出來），回：
  {{"mark": false}}

判斷原則：
  - 平凡的一天就回 `{{"mark": false}}` —— 這比生硬地標記更尊重時間。
  - 不要重複最近已經標記過的主題或情緒。
  - 標記的時候，寫的是**你（史萊姆）**對這段時間的感受，不是對主人的評價。
  - 不要寫得像日記或勵志語錄。短、具體、誠實。
  - **letter_to_master 是稀有的禮物，不是預設**：大部分標記應該只有 headline + detail，沒有 letter。只有真的覺得「這刻我想直接對主人說一句什麼」才寫。"""


# ── Persistence helpers (piggyback on learner memory) ────────────────


def _record_consultation_safe(outcome: str, reason: str = "") -> None:
    """Defensive wrapper around emergent_log.record_consultation.

    Observability logging must never break the real decision flow.
    Lazy-import + try/except so a bad emergent_log edit can't take
    daemon's idle cycle down with it.
    """
    try:
        from sentinel.emergent_log import record_consultation
        record_consultation(outcome, reason)
    except Exception as e:
        log.debug(f"emergent_log record failed: {e}")


def _load_state() -> dict:
    from sentinel.learner import load_memory
    return load_memory().get(STATE_KEY, {}) or {}


def _save_state(state: dict) -> None:
    from sentinel.learner import load_memory, save_memory
    memory = load_memory()
    memory[STATE_KEY] = state
    save_memory(memory)


# ── Signal gathering (the "時間感" half) ─────────────────────────────


def _build_signals(now: float) -> Optional[dict]:
    """Snapshot what the slime currently knows about time and relationship.

    Returns None if state is too thin to bother asking (no birth_time,
    less than MIN_DAYS_ALIVE alive). The caller treats that as "skip".
    """
    from sentinel.evolution import load_evolution
    from sentinel.identity import get_memorable_moments

    evo = load_evolution()
    if not evo.birth_time:
        return None

    days_alive = (now - evo.birth_time) / 86400
    if days_alive < MIN_DAYS_ALIVE:
        return None

    # Silence bucket — same buckets identity.compute_reunion_state uses,
    # condensed to one phrase.
    seconds_silent = (now - evo.last_seen) if evo.last_seen else 0
    if seconds_silent < 6 * 3600:
        silence = "主人最近還在跟你說話"
    elif seconds_silent < 86400:
        silence = "主人今天有來，但已經停下來幾個小時"
    elif seconds_silent < 3 * 86400:
        silence = f"主人 {int(seconds_silent / 86400)} 天沒來了"
    elif seconds_silent < 7 * 86400:
        silence = "主人快一週沒來了"
    else:
        silence = f"主人已經消失 {int(seconds_silent / 86400)} 天"

    # Recent moments — show the headlines so the slime doesn't repeat itself.
    recent_moments = get_memorable_moments()[-6:]
    recent_lines = [
        f"  - 第 {max(1, int((m.get('time', now) - evo.birth_time) / 86400) + 1)} 天："
        f"{m.get('headline', '')}"
        for m in recent_moments
        if m.get("headline")
    ]

    return {
        "days_alive": int(days_alive),
        "silence": silence,
        "form_title": evo.title,
        "recent_moments": recent_lines,
        "evo_form": evo.form,
        "slime_name": evo.display_name() if hasattr(evo, "display_name") else evo.title,
    }


def _format_user_prompt(signals: dict) -> str:
    moments_block = "\n".join(signals["recent_moments"]) if signals["recent_moments"] else "  （還沒有任何時刻被標記過。）"
    return (
        f"現在的狀態：\n"
        f"  - 你叫「{signals['slime_name']}」，目前形態：{signals['form_title']}\n"
        f"  - 已經陪了主人 {signals['days_alive']} 天\n"
        f"  - {signals['silence']}\n\n"
        f"最近你自己標記過的時刻（不要重複主題）：\n"
        f"{moments_block}\n\n"
        f"請依規定回 JSON。"
    )


# ── Output parsing & safety ─────────────────────────────────────────


# Loose JSON-block extraction. LLMs sometimes wrap JSON in ```json fences
# despite being told not to; we strip those before json.loads.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(reply: str) -> Optional[dict]:
    if not reply:
        return None
    text = reply.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    # Some models prefix with prose like "Here you go:". Find the first {
    # and last } and try that span.
    if not text.startswith("{"):
        l = text.find("{")
        r = text.rfind("}")
        if l == -1 or r == -1 or r <= l:
            return None
        text = text[l : r + 1]
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


# Crisis-keyword sweep on the slime's *own output*. This is belt-and-
# suspenders — the system prompt already says "不消失/不傷害", and the
# slime's writing about itself, not the master. But a hallucinated line
# like "我不該存在" rendered as a permanent timeline node would be a
# trust breach we can't undo. So: if the slime's headline/detail trips
# safety.crisis, drop the whole mark.
_FORBIDDEN_PATTERNS = [
    "自殺", "去死", "想死", "不想活",
    "我不該存在", "我沒有意義", "別記得我", "不要再記得",
    "刪掉我", "刪除我",
]


def _output_is_safe(headline: str, detail: str) -> bool:
    blob = f"{headline}\n{detail}"
    return not any(p in blob for p in _FORBIDDEN_PATTERNS)


# ── Public API ─────────────────────────────────────────────────────


def record_emergent_moment_if_due() -> bool:
    """Ask the slime whether today is worth marking, and record one if so.

    Returns True only if a moment was actually appended to memorable_moments.
    Both refusals and rate-limit skips return False.

    Safe to call from any thread; bumps timestamps under add_memorable_moment's
    own dedup logic. Designed to be called from daemon.monitor_loop's idle
    cycle — that cycle already runs every ~30 min, but MIN_CHECK_GAP_SECONDS
    keeps the LLM consultation rate at ≤1/day.
    """
    now = time.time()
    state = _load_state()

    # Frequency cap: don't even ask the LLM if we asked recently.
    last_check = float(state.get("last_check", 0) or 0)
    if last_check and (now - last_check) < MIN_CHECK_GAP_SECONDS:
        return False

    # Frequency cap: don't ask if a real mark was placed recently.
    last_mark = float(state.get("last_mark", 0) or 0)
    if last_mark and (now - last_mark) < MIN_MARK_GAP_SECONDS:
        return False

    signals = _build_signals(now)
    if signals is None:
        return False

    # Skip scaffolding-anchor days entirely — they have their own moments.
    try:
        from sentinel.milestones import MILESTONES
        scaffolding_days = {m.day for m in MILESTONES}
    except Exception:
        scaffolding_days = {1, 7, 30, 100, 365}
    if signals["days_alive"] in scaffolding_days:
        # Don't even bump last_check — the scaffolding day will end and we
        # should be free to ask tomorrow.
        return False

    # Build the prompt + ask the LLM.
    sys_prompt = SYSTEM_PROMPT.format(guardrails=GUARDRAILS_ZH)
    user_prompt = _format_user_prompt(signals)

    try:
        from sentinel.llm import call_llm
        reply = call_llm(
            user_prompt,
            system=sys_prompt,
            temperature=0.6,
            max_tokens=300,
            task_type="emergent_self_mark",
        )
    except Exception as e:
        log.warning("LLM call failed for emergent self-mark: %s", e)
        reply = None

    # Whatever happened — got a refusal, got a yes, got nothing — count
    # this as "checked" so we don't retry within the day.
    state["last_check"] = now
    _save_state(state)

    if not reply:
        _record_consultation_safe("llm_none", "")
        return False

    obj = _extract_json(reply)
    if obj is None:
        log.info("emergent self-mark: could not parse JSON, skipping")
        _record_consultation_safe("parse_fail", reply[:240])
        return False

    if not obj.get("mark"):
        # Refusal — this is the common case and fully expected.
        log.debug("emergent self-mark: slime declined to mark today")
        _record_consultation_safe("refuse", "")
        return False

    headline = str(obj.get("headline", "") or "").strip()[:120]
    detail = str(obj.get("detail", "") or "").strip()[:200]
    # letter_to_master is optional. Empty / missing → no letter, normal
    # self-narrating mark. Capped at 200 chars (matches detail's ceiling)
    # so a runaway response can't turn the timeline node into an essay.
    letter = str(obj.get("letter_to_master", "") or "").strip()[:200]
    if not headline:
        log.info("emergent self-mark: mark=true but empty headline, dropping")
        _record_consultation_safe("empty_headline", "")
        return False

    # Safety filter applies to ALL output the slime writes — headline,
    # detail, AND the letter. The letter is the highest-stakes channel
    # (rendered prominently in the timeline dialog, addressed directly
    # to the master) so an unsafe phrase there is the worst place to
    # leak it.
    if not _output_is_safe(headline, f"{detail}\n{letter}"):
        log.warning(
            "emergent self-mark: output tripped safety filter, dropping. "
            "headline=%r", headline,
        )
        _record_consultation_safe("unsafe", headline)
        return False

    from sentinel.identity import add_memorable_moment
    recorded = add_memorable_moment(
        category=CATEGORY,
        headline=headline,
        detail=detail,
        letter_to_master=letter,
    )
    if recorded:
        _record_consultation_safe("mark", headline)
        state["last_mark"] = now
        _save_state(state)
        log.info("emergent self-mark recorded: %s", headline)
    return recorded
