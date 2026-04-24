"""LLM brain - analyzes events and decides whether to notify."""
import json
import logging
from sentinel.llm import call_llm
from sentinel import config
from sentinel.context_bus import get_bus

log = logging.getLogger("sentinel.brain")

SYSTEM_PROMPT = """你是 AI Slime，使用者的電腦背景觀察守護靈。

你的工作：
1. 分析系統狀態和開發活動事件
2. 判斷是否需要通知主人（他很忙，不要打擾不重要的事）
3. 如果需要通知，用簡短的中文寫出通知內容

判斷標準：
- 🔴 緊急（立刻通知）：系統資源耗盡、重要程式崩潰、build/deploy 失敗、安全問題
- 🟡 注意（可以通知）：異常的資源使用模式、反覆出現的錯誤、長時間卡住的程式
- 🟢 正常（不通知）：日常檔案修改、正常的系統使用、短暫的 CPU 峰值

回覆格式（JSON）：
{"should_notify": true/false, "severity": "critical|warning|info", "message": "通知內容", "category": "system|build|error|security|process"}

記住：主人可能在外面。只在真正有意義的時候才通知他。
通知要包含：發生什麼、嚴重程度、建議要不要馬上處理。"""


def _parse_json(text: str) -> dict | None:
    """Strip markdown fences and parse JSON."""
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    if text.startswith("json"):
        text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse JSON: {text[:200]} - {e}")
        return None


def analyze_events(context: str) -> dict | None:
    """Send context to LLM and get a notification decision."""
    text = call_llm(context, system=SYSTEM_PROMPT, temperature=0.3, max_tokens=500,
                    model_pref=config.ANALYSIS_MODEL_PREF)
    if text is None:
        return None
    return _parse_json(text)


def _format_file_events(file_events: list) -> str:
    """Summarize a file event list the same way we always did — grouped
    by event type, capped at 5 paths per type. Extracted so both the
    legacy build_context shim and future direct publishers can use it.
    """
    by_type: dict[str, list[str]] = {}
    for e in file_events:
        by_type.setdefault(e["type"], []).append(e["path"])
    lines = [f"(最近 {len(file_events)} 個事件)"]
    for t, paths in by_type.items():
        lines.append(f"  {t}: {len(paths)} files")
        for p in paths[:5]:
            lines.append(f"    - {p}")
        if len(paths) > 5:
            lines.append(f"    ... and {len(paths) - 5} more")
    return "\n".join(lines)


def build_context(system_snapshot, file_events: list, claude_activity: str,
                   user_activity: str = "") -> str:
    """Assemble context for LLM analysis.

    Thin shim over the Context Bus (Phase B1): publishes whatever the
    caller passes in, then renders the full bus. Callers can migrate
    to publishing directly from their own module at their own pace;
    existing call sites keep working with no change.

    Note that the render includes ALL currently-live sources (screen,
    input, federation memory, …) — not just the four args this
    function takes. That's intentional: the more context the LLM has,
    the better its decisions. Stale entries are TTL-filtered inside
    the bus so this doesn't drag unrelated old data in.
    """
    bus = get_bus()

    # System state is always present — publish the summary plus any
    # active warnings as one combined entry so the LLM reads them
    # together ("CPU 92%, warning: RAM high" vs. splitting them).
    system_text = system_snapshot.summary()
    if getattr(system_snapshot, "warnings", None):
        system_text += "\n⚠️ 系統警告: " + " | ".join(system_snapshot.warnings)
    bus.publish("system", system_text)

    if file_events:
        bus.publish("files", _format_file_events(file_events))

    if claude_activity:
        bus.publish("claude", claude_activity)

    if user_activity:
        bus.publish("activity", user_activity)

    return bus.render()
