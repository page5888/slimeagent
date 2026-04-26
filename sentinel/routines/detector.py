"""LLM-driven pattern detector — the "slime notices what you do" half.

Plumbing alone is not the qualitative leap. The leap is "slime
proposes a routine you didn't ask for, that you actually want".
This module scans recent observation logs + chat memory and asks
the LLM to spot recurring task sequences worth automating, then
queues each as a `routine.create` proposal in the approval queue.

When does it run?
  - Once a day, kicked off by the daemon thread in scheduler.py
    (or a future cron-style trigger).
  - User can also invoke manually via "幫我看我有哪些可以自動化"
    in chat — the LLM proposes the action; on approval, it runs.

Why LLM rather than statistical detection?
  - Tens of observations/day, sparse signal. Statistical methods
    over this scale are noisy.
  - LLM has the world-knowledge to interpret "user opens VS Code,
    then activity_tracker shows GitHub URL, then editor focused
    on auth.py" as "morning standup prep" — naming the routine
    is half the value to the user.
  - Output is bounded by the action-type whitelist (we won't
    propose routines that reference unknown actions).

Output is structured: [{name, trigger, steps, confidence, evidence}]
where each candidate becomes one approval queue entry.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.routines.detector")


DETECTOR_PROMPT_TEMPLATE = """\
你是 AI Slime 的「節奏觀察者」。你的工作是看主人最近的活動紀錄，找出**可以自動化的固定流程**。

可用動作（routine 的 step 只能用這些）：
<<ACTION_LIST>>

可用觸發條件（時間 + 環境事件兩類；事件類比時間類更貼近主人實際情境）：

時間類：
- {"kind": "daily_at", "time": "HH:MM"}      — 每天固定時間（24h 制）
- {"kind": "weekly_at", "time": "HH:MM", "days": ["mon","tue",...]}  — 每週指定幾天
- {"kind": "interval", "every_minutes": N}    — 每隔 N 分鐘

事件類（Phase G — 反應式觸發，比 cron 更聰明）：
- {"kind": "on_app_open", "title_match": "VS Code"}
   — 當主人打開符合標題的視窗時觸發
   （比「每天 9 點開檔」更聰明：只在他真的有用電腦時才動）
- {"kind": "on_file_pattern", "pattern": "*.log"}
   — 當符合 glob 的檔案變動時觸發（適合「下載完就整理」之類）
- {"kind": "on_idle", "duration_minutes": 15}
   — 當主人閒置超過 N 分鐘時觸發（適合提醒喝水、休息、定期備份）

選擇原則：
* 如果觀察記錄顯示行為跟「特定 app 啟動」綁定 → 用 on_app_open
* 如果是「下載/儲存某類檔案後該做什麼」→ 用 on_file_pattern
* 如果是「閒一段時間該做的事」→ 用 on_idle
* 真的純粹按時鐘的（晨間例行公事） → daily_at
* 不確定就**寧可不提**

最近幾天的活動 + 聊天記憶：
<<ACTIVITY_LOG>>

<<NEGATIVE_SIGNALS>>

任務：找出 0~3 個**真的重複出現**的工作流，並提案做成 routine。標準：

1. 必須在不同天 / 不同時段重複出現至少 2~3 次（單次不算）
2. 每個 step 必須能用上面的動作做出來
3. confidence 要誠實（看到 5 次以上才填 ≥ 0.7；2~3 次填 0.4~0.6）
4. 範圍要明確（什麼時候做、開哪個檔/網址、多長），不要含糊
5. 不確定就**寧可不提**（空陣列） — 提錯誤的提案會讓主人覺得你亂猜

每個 routine **可以選擇性**加 `judge_prompt`(觸發後執行前 LLM 會用它判斷情境):

什麼時候需要 judge_prompt:
- 觸發了但情境可能不對(例:「打開 VS Code」可能是想看別的專案)
- 主人習慣會變(例:「9 點開專案」但他可能放假在家不想被打擾)
- 步驟裡有需要先確認的前提(例:「目標檔還在嗎」「Zoom 開著嗎」)

不需要 judge_prompt 的時候(常見):
- 純粹「閒置就提醒喝水」這種無腦 routine
- 觸發已經很精確(on_file_pattern 看具體檔名)

回 JSON(純粹,不要 markdown wrap):

{
  "candidates": [
    {
      "name": "<簡短中文名,例 '早晨開發環境啟動'>",
      "trigger": <上面所有觸發之一>,
      "steps": [
        {"action_type": "<從白名單挑>", "payload": {...}, "title": "<這步驟做什麼>"}
      ],
      "judge_prompt": "<可選,40 字內中文,叫 LLM 判斷現在該不該做。空字串表示不用判斷>",
      "confidence": 0.0~1.0,
      "evidence": "<為什麼這是固定流程的證據,1-2 句>"
    }
  ]
}

如果找不到任何值得自動化的固定流程,回 {"candidates": []}.
"""


def _format_action_list_for_detector() -> str:
    """Render only action types safe for autonomous execution as a
    bulleted reference for the detector LLM. Excludes actions whose
    side effects are user-specific or sensitive enough that a routine
    shouldn't autofire them (e.g. voice.listen captures audio without
    real-time user awareness — the user might not be at the keyboard
    when a daily-9am routine starts recording)."""
    from sentinel.actions.catalog import CATALOG

    # Allowlist of action types the detector may propose. Anything
    # else is filtered out so the LLM physically can't suggest a
    # routine that opens the mic or sends screen content to a cloud
    # without the user-in-loop check the realtime D3/D5 paths have.
    SAFE_FOR_ROUTINE = {
        "surface.open_path", "surface.open_url",
        "surface.focus_window", "surface.list_windows",
        "surface.get_clipboard", "surface.set_clipboard",
        "surface.take_screenshot",
        "voice.speak",  # speaking is fine; listening is not
        "chain.run",
    }
    lines = []
    for action_type, spec in CATALOG.items():
        if action_type not in SAFE_FOR_ROUTINE:
            continue
        lines.append(f"- `{action_type}` — {spec.get('desc_zh', '')}")
        if spec.get("payload"):
            payload_str = ", ".join(
                f"{k}: {v}" for k, v in spec["payload"].items()
            )
            lines.append(f"    payload: {{{payload_str}}}")
    return "\n".join(lines)


def _gather_activity_summary(max_chars: int = 4000) -> str:
    """Pull a compact summary of what the slime has seen recently.

    Sources:
      - aislime_learning_log.jsonl (one line per distillation cycle,
        each is the LLM's own summary of what user was doing)
      - sentinel_memory.json's "observations" field (last N raw obs)
      - sentinel_chats.jsonl (recent chat turns reveal user intent)

    Capped at max_chars because the detector prompt is already long
    and we don't want to blow the context window. Newer entries are
    kept; older ones get dropped.
    """
    parts: list[str] = []

    # Distillation log — chronological summaries
    learning = Path.home() / ".hermes" / "aislime_learning_log.jsonl"
    if learning.exists():
        try:
            lines = learning.read_text(encoding="utf-8").strip().split("\n")
            recent = lines[-30:]
            parts.append("=== 最近的蒸餾記錄 ===")
            for line in recent:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    obs = e.get("observations") or []
                    if obs:
                        parts.append("- " + " / ".join(str(o) for o in obs[:3]))
                except Exception:
                    pass
        except OSError:
            pass

    # Memory profile + raw observations
    mem_file = Path.home() / ".hermes" / "sentinel_memory.json"
    if mem_file.exists():
        try:
            mem = json.loads(mem_file.read_text(encoding="utf-8"))
            if mem.get("profile"):
                parts.append("=== 主人 profile ===")
                parts.append(str(mem["profile"])[:600])
            obs = mem.get("observations") or []
            if obs:
                parts.append("=== 最近觀察（原始） ===")
                for o in obs[-10:]:
                    parts.append(f"- {o}")
        except Exception:
            pass

    # Recent chats — chat shows user's stated intent + slime replies
    chats = Path.home() / ".hermes" / "sentinel_chats.jsonl"
    if chats.exists():
        try:
            lines = chats.read_text(encoding="utf-8").strip().split("\n")
            parts.append("=== 最近對話 ===")
            for line in lines[-20:]:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    role = "主人" if e.get("role") == "user" else "Slime"
                    text = (e.get("text") or "").replace("\n", " ")[:200]
                    parts.append(f"{role}: {text}")
                except Exception:
                    pass
        except OSError:
            pass

    text = "\n".join(parts)
    if len(text) > max_chars:
        # Trim from the front (oldest); keep newest
        text = "...(較舊資料省略)\n" + text[-max_chars:]
    return text or "(no activity data yet)"


def _parse_candidates(llm_text: str) -> list[dict]:
    """Extract the candidates list from LLM output. Tolerates code
    fences, leading/trailing prose, bad JSON via a single repair pass.
    """
    if not llm_text:
        return []
    text = llm_text.strip()
    # Strip ```json or ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Find the first {...} that has a "candidates" key
    m = re.search(r"\{[^{}]*\"candidates\".*\}", text, re.DOTALL)
    if not m:
        log.info("detector: no candidates object in output")
        return []
    body = m.group(0)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Try repair: escape unescaped backslashes (Windows paths)
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', body)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as e:
            log.warning(f"detector: candidates JSON unparseable: {e!r}")
            return []
    raw = data.get("candidates")
    if not isinstance(raw, list):
        return []
    return raw


def _validate_candidate(c: dict) -> Optional[dict]:
    """Sanity-check one candidate's shape. Drop obviously bad ones
    so the proposal queue doesn't fill with garbage. Returns the
    normalized candidate, or None to drop."""
    from sentinel.routines.storage import (
        TRIGGER_DAILY_AT, TRIGGER_WEEKLY_AT, TRIGGER_INTERVAL,
        TRIGGER_ON_APP_OPEN, TRIGGER_ON_FILE_PATTERN, TRIGGER_ON_IDLE,
    )
    if not isinstance(c, dict):
        return None
    name = (c.get("name") or "").strip()
    if not name or len(name) > 80:
        return None
    trig = c.get("trigger") or {}
    if not isinstance(trig, dict) or trig.get("kind") not in (
        TRIGGER_DAILY_AT, TRIGGER_WEEKLY_AT, TRIGGER_INTERVAL,
        TRIGGER_ON_APP_OPEN, TRIGGER_ON_FILE_PATTERN, TRIGGER_ON_IDLE,
    ):
        return None
    steps = c.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return None
    if len(steps) > 5:
        steps = steps[:5]
    confidence = c.get("confidence", 0.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.4:
        # Below this threshold the LLM is flagging coincidence, not
        # routine. Skip rather than spam the user.
        return None
    judge_prompt = (c.get("judge_prompt") or "").strip()
    if len(judge_prompt) > 600:
        judge_prompt = judge_prompt[:600]
    return {
        "name": name,
        "trigger": trig,
        "steps": steps,
        "confidence": confidence,
        "evidence": (c.get("evidence") or "")[:300],
        "judge_prompt": judge_prompt,
    }


# ── Public API ────────────────────────────────────────────────────


def propose_via_detector_verbose() -> dict:
    """Verbose variant: returns {queued_ids, diagnostic} so the GUI's
    "立即偵測新常規" button can explain WHY a run produced 0 candidates.
    Same code path as propose_via_detector — that wraps this one for
    the legacy list-only return shape used by the daemon scheduler.

    Diagnostic strings are user-facing Chinese so the GUI can surface
    them verbatim. Examples:
      "history yet — keep using the slime for a few hours / days"
      "history exists but no recurring patterns clear enough yet"
      "candidates found but all dropped under 0.4 confidence"
      "LLM unreachable / no API key configured"
    """
    from sentinel import config
    from sentinel.llm import call_llm
    from sentinel.growth import submit_action, PolicyDenied

    activity = _gather_activity_summary()
    action_list = _format_action_list_for_detector()
    # Phase I: include the user's past rejections / disables so the
    # detector LLM learns what NOT to propose. Empty string when no
    # signals yet (first-run, before any rejection).
    try:
        from sentinel.routines.preferences import render_for_detector_prompt
        negative_signals = render_for_detector_prompt()
    except Exception as e:
        log.warning(f"detector: couldn't load preferences: {e}")
        negative_signals = ""

    # Pre-LLM diagnostic: if there's literally no activity log we can
    # short-circuit + tell the user "give me time" instead of pinging
    # the LLM with nothing useful in context.
    if "(no activity data yet)" in activity or activity.strip() == "":
        return {
            "queued_ids": [],
            "diagnostic": (
                "還沒有任何觀察記錄可以分析。"
                "讓 AI Slime 在背景運行幾個小時 / 天,"
                "等蒸餾器整理過你的活動後再試。"
            ),
        }

    prompt = (DETECTOR_PROMPT_TEMPLATE
              .replace("<<ACTION_LIST>>", action_list)
              .replace("<<ACTIVITY_LOG>>", activity)
              .replace("<<NEGATIVE_SIGNALS>>", negative_signals))

    log.info("running routine detector...")
    text = call_llm(
        prompt,
        temperature=0.3,
        max_tokens=1200,
        model_pref=config.ANALYSIS_MODEL_PREF,
        task_type="analysis",
    )
    if not text:
        log.info("detector: no LLM response")
        return {
            "queued_ids": [],
            "diagnostic": (
                "LLM 沒有回應。檢查「魔法陣」分頁的 API key 或網路連線。"
                "如果用本地 Ollama 也要確認服務啟著。"
            ),
        }

    raw_candidates = _parse_candidates(text)
    log.info(f"detector: LLM produced {len(raw_candidates)} raw candidates")

    if not raw_candidates:
        return {
            "queued_ids": [],
            "diagnostic": (
                "LLM 看完你最近的活動 log,沒有看到固定到值得自動化的流程。"
                "可能因為:活動還太散、習慣還在變、或被「之前不喜歡」的偏好擋掉了。"
                "再用幾天累積。"
            ),
        }

    queued_ids: list[str] = []
    dropped_low_conf = 0
    dropped_bad_shape = 0
    denied_count = 0
    for raw in raw_candidates:
        normalized = _validate_candidate(raw)
        if normalized is None:
            # _validate_candidate returns None for both missing fields
            # and below-confidence-threshold. We can't easily tell
            # apart from here, so treat below-threshold as dominant
            # (most common case) and bucket the rest as bad shape.
            conf_raw = raw.get("confidence", 0) if isinstance(raw, dict) else 0
            try:
                if 0 <= float(conf_raw) < 0.4:
                    dropped_low_conf += 1
                else:
                    dropped_bad_shape += 1
            except (TypeError, ValueError):
                dropped_bad_shape += 1
            continue
        try:
            approval = submit_action(
                action_type="routine.create",
                title=f"自動化建議：{normalized['name']}",
                reason=(
                    f"信心 {int(normalized['confidence'] * 100)}% — "
                    f"{normalized['evidence']}"
                ),
                payload={
                    "name": normalized["name"],
                    "trigger": normalized["trigger"],
                    "steps": normalized["steps"],
                    "evidence": normalized["evidence"],
                    "judge_prompt": normalized.get("judge_prompt", ""),
                    "auto_proposed": True,
                },
            )
            queued_ids.append(approval.id)
        except PolicyDenied as e:
            denied_count += 1
            log.info(
                f"detector candidate denied at submit: "
                f"{[f.get('msg') for f in e.findings]}"
            )
        except Exception as e:
            log.warning(f"detector candidate submit failed: {e}")

    log.info(f"detector: queued {len(queued_ids)} routine proposals")

    diagnostic = ""
    if not queued_ids:
        # Build a reason from the buckets so the user knows what
        # happened to the LLM's candidates.
        parts = []
        if dropped_low_conf:
            parts.append(f"{dropped_low_conf} 個信心不夠 (<40%)")
        if dropped_bad_shape:
            parts.append(f"{dropped_bad_shape} 個格式不對")
        if denied_count:
            parts.append(f"{denied_count} 個政策擋掉")
        if parts:
            diagnostic = (
                f"LLM 提了 {len(raw_candidates)} 個候選,"
                f"但 {' / '.join(parts)}。再累積幾天讓信心提高。"
            )
        else:
            diagnostic = "候選全部處理完畢但沒一個成功進佇列(罕見路徑)。"

    return {"queued_ids": queued_ids, "diagnostic": diagnostic}


def propose_via_detector() -> list[str]:
    """Legacy list-of-IDs return shape used by scheduler. Wraps
    propose_via_detector_verbose and discards the diagnostic string.
    GUI callers (RoutinesTab) should use the verbose variant directly.
    """
    return propose_via_detector_verbose().get("queued_ids", [])
