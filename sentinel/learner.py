"""Learning module - observes user's behavior patterns and builds a profile over time.

This is the core of what makes AI Slime different from a dumb monitor.
It doesn't follow preset workflows. It watches, learns, and adapts.
"""
import json
import time
import logging
from pathlib import Path
from sentinel.llm import call_llm
from sentinel import config

log = logging.getLogger("sentinel.learner")

MEMORY_FILE = Path.home() / ".hermes" / "sentinel_memory.json"

DISTILL_TEMPLATE = (
    "你是觀察者 AI，分析使用者活動並更新理解。\n\n"
    "現有理解：<<EXISTING_PROFILE>>\n\n"
    "最近活動：\n<<RECENT_ACTIVITY>>\n\n"
    "用純 JSON 回覆（不要 markdown、不要 ```）：\n"
    '{"observations":["1-2個簡短新發現"],'
    '"patterns":{"work_style":"簡短","preferences":"簡短","pain_points":"簡短"},'
    '"updated_profile":"一段話總結使用者"}'
)


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "profile": "",
        "observations": [],
        "patterns": {},
        "last_updated": 0,
        "session_count": 0,
    }


def save_memory(memory: dict):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding='utf-8')


def distill_from_activity(recent_activity: str):
    """Observe activity and update understanding of the user."""
    memory = load_memory()

    prompt = DISTILL_TEMPLATE.replace(
        "<<EXISTING_PROFILE>>", memory.get("profile", "(尚無資料)")
    ).replace(
        "<<RECENT_ACTIVITY>>", recent_activity
    )

    try:
        text = call_llm(prompt, temperature=0.3, max_tokens=500,
                        model_pref=config.ANALYSIS_MODEL_PREF, task_type="analysis")
        if text is None:
            log.warning("蒸餾失敗：所有 LLM（本地+雲端）都無回應")
            return None

        # 強壯的 JSON 提取
        import re
        # 去掉 markdown 包裝
        text = re.sub(r'^```(?:json)?\s*', '', text.strip())
        text = re.sub(r'\s*```$', '', text.strip())
        # 找到 JSON 物件
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            log.warning(f"No JSON found in response: {text[:100]}")
            return None
        text = match.group(0)

        result = json.loads(text)

        # Update memory
        memory["profile"] = result.get("updated_profile", memory["profile"])
        memory["patterns"] = result.get("patterns", memory["patterns"])
        new_obs = result.get("observations", [])
        memory["observations"] = (memory.get("observations", []) + new_obs)[-50:]  # Keep last 50
        memory["last_updated"] = time.time()
        memory["session_count"] = memory.get("session_count", 0) + 1

        # Save learning log entry
        log_entry = {
            "time": time.time(),
            "observations": new_obs,
            "profile_snippet": memory["profile"][:200],
            "patterns": memory["patterns"],
        }
        _append_learning_log(log_entry)

        save_memory(memory)
        log.info(f"Distilled {len(new_obs)} new observations. Total sessions: {memory['session_count']}")
        return result

    except Exception as e:
        log.error(f"Distill error: {e}")
        return None


LEARNING_LOG_FILE = Path.home() / ".hermes" / "rimuru_learning_log.jsonl"


def _append_learning_log(entry: dict):
    """Append a learning event to the persistent log."""
    try:
        with open(LEARNING_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def get_learning_log(last_n=20) -> list[dict]:
    """Read recent learning log entries."""
    if not LEARNING_LOG_FILE.exists():
        return []
    entries = []
    try:
        with open(LEARNING_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return entries[-last_n:]


def get_profile_summary() -> str:
    memory = load_memory()
    if not memory["profile"]:
        return "(還在學習中，尚未建立 profile)"
    return memory["profile"]
