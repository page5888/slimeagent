"""LLM brain - analyzes events and decides whether to notify."""
import json
import logging
from sentinel.llm import call_llm
from sentinel import config

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


def build_context(system_snapshot, file_events: list, claude_activity: str,
                   user_activity: str = "") -> str:
    """Build a context string for the LLM to analyze."""
    parts = []

    parts.append("=== 系統狀態 ===")
    parts.append(system_snapshot.summary())
    if system_snapshot.warnings:
        parts.append("⚠️ 系統警告: " + " | ".join(system_snapshot.warnings))

    if file_events:
        parts.append(f"\n=== 檔案變動 (最近 {len(file_events)} 個事件) ===")
        by_type = {}
        for e in file_events:
            t = e['type']
            by_type.setdefault(t, []).append(e['path'])
        for t, paths in by_type.items():
            parts.append(f"  {t}: {len(paths)} files")
            for p in paths[:5]:
                parts.append(f"    - {p}")
            if len(paths) > 5:
                parts.append(f"    ... and {len(paths)-5} more")

    if claude_activity:
        parts.append(f"\n=== Claude Code 活動 ===")
        parts.append(claude_activity)

    if user_activity:
        parts.append(f"\n{user_activity}")

    return "\n".join(parts)
