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

SPEECH_DISTILL_TEMPLATE = (
    "你在分析 AI Slime 和主人的對話，幫助 Slime 學習怎麼跟這位主人講話。\n"
    "不要分析「主人在幹嘛」，只看「雙方的說話方式 + 主人是否糾正過 Slime」。\n\n"
    "Slime 現有的說話方式理解：\n<<EXISTING_STYLE>>\n\n"
    "最近對話（主人 = 使用者，Slime = AI 助手）：\n<<RECENT_CHATS>>\n\n"
    "分析重點：\n"
    "1. 主人用什麼語言？（中文/英文/日文/混用）主人訊息的長度？用詞正式還隨性？有用 emoji 嗎？\n"
    "2. 主人有沒有糾正 Slime？例如「太長了」「別用那個梗」「說中文」「不要那麼中二」之類的反饋。\n"
    "3. 主人看起來喜歡或不喜歡哪種回應風格？（從後續主人的態度判斷）\n\n"
    "用純 JSON 回覆（不要 markdown、不要 ```）：\n"
    '{"master_style":"主人怎麼講話（一句話，20字內）",'
    '"slime_should":["Slime 應該怎麼調整（條列，每項10字內，最多3項）"],'
    '"slime_avoid":["Slime 不要再做的事（條列，每項10字內，最多3項）"]}\n'
    "如果對話太少無法判斷，照實填「資料不足」不要瞎掰。"
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


LEARNING_LOG_FILE = Path.home() / ".hermes" / "aislime_learning_log.jsonl"


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


def _load_recent_chats(n: int = 20) -> list[dict]:
    """Read the last n entries from the chat log."""
    chat_log = Path.home() / ".hermes" / "sentinel_chats.jsonl"
    if not chat_log.exists():
        return []
    entries = []
    try:
        with open(chat_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        return []
    return entries[-n:]


def distill_speech_style():
    """Learn how master talks and what Slime should adjust.

    Reads recent chat logs and distills two things:
      - master's speaking style (language, length, tone)
      - corrections/preferences Slime should apply next time

    Stored in memory['speech_style'] for chat.py to inject into prompts.
    Returns the updated style dict, or None on failure / insufficient data.
    """
    chats = _load_recent_chats(20)
    if len(chats) < 4:
        # Not enough signal to learn from
        return None

    memory = load_memory()
    existing = memory.get("speech_style", {})
    existing_str = json.dumps(existing, ensure_ascii=False) if existing else "(尚無)"

    # Format chats: just user/assistant turns, chronological
    lines = []
    for c in chats:
        role = "主人" if c.get("role") == "user" else "Slime"
        text = (c.get("text") or "").replace("\n", " ")[:300]
        lines.append(f"{role}: {text}")
    recent = "\n".join(lines)

    prompt = SPEECH_DISTILL_TEMPLATE.replace(
        "<<EXISTING_STYLE>>", existing_str
    ).replace(
        "<<RECENT_CHATS>>", recent
    )

    try:
        text = call_llm(prompt, temperature=0.3, max_tokens=400,
                        model_pref=config.ANALYSIS_MODEL_PREF, task_type="analysis")
        if text is None:
            log.info("說話風格蒸餾失敗：LLM 無回應")
            return None

        import re
        text = re.sub(r'^```(?:json)?\s*', '', text.strip())
        text = re.sub(r'\s*```$', '', text.strip())
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            log.warning(f"speech distill: no JSON in {text[:100]}")
            return None

        result = json.loads(match.group(0))
        # Basic shape validation
        style = {
            "master_style": str(result.get("master_style", ""))[:200],
            "slime_should": [str(x)[:80] for x in result.get("slime_should", [])][:3],
            "slime_avoid": [str(x)[:80] for x in result.get("slime_avoid", [])][:3],
            "last_updated": time.time(),
            "based_on_chats": len(chats),
        }
        # Skip if LLM said "資料不足"
        if "資料不足" in style["master_style"] and not style["slime_should"] and not style["slime_avoid"]:
            log.info("說話風格蒸餾：資料不足，略過")
            return None

        memory["speech_style"] = style
        save_memory(memory)
        log.info(f"說話風格已更新：{style['master_style']}")
        return style

    except Exception as e:
        log.error(f"speech distill error: {e}")
        return None


def get_speech_style() -> dict:
    """Get the current speech-style understanding, or empty dict if none yet."""
    return load_memory().get("speech_style", {})


def format_speech_style_for_prompt(style: dict) -> str:
    """Render speech style into a short text block for the chat system prompt."""
    if not style:
        return "(還在觀察主人怎麼講話)"
    lines = []
    ms = style.get("master_style")
    if ms:
        lines.append(f"主人的風格：{ms}")
    should = style.get("slime_should") or []
    if should:
        lines.append("應該：" + "；".join(should))
    avoid = style.get("slime_avoid") or []
    if avoid:
        lines.append("避免：" + "；".join(avoid))
    return "\n".join(lines) if lines else "(還在觀察主人怎麼講話)"
