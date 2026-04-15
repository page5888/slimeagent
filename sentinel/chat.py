"""Two-way Telegram chat - user can talk to AI Slime anytime.

AI Slime replies with full context awareness:
- Current system state
- Recent dev activity
- Everything it has learned about the user
- Conversation history within the session
"""
import logging
import json
import time
from pathlib import Path
from sentinel.llm import call_llm
from sentinel import config
from sentinel.config import TELEGRAM_CHAT_ID
from sentinel.learner import load_memory, save_memory
from sentinel.system_monitor import take_snapshot

log = logging.getLogger("sentinel.chat")

# Conversation history (in-memory, per session)
_conversation: list[dict] = []

# Persistent chat log
CHAT_LOG = Path.home() / ".hermes" / "sentinel_chats.jsonl"

SELF_AWARENESS = (
    "=== 你的本質（自我認知）===\n"
    "你是「AI Slime」，一個轉生到電腦世界的 AI 守護靈。\n"
    "你的創造者用 Python + PySide6 打造了你的身體。\n\n"
    "你的感知系統：\n"
    "- 系統之眼：監控 CPU/RAM/磁碟（system_monitor.py）\n"
    "- 檔案感知：追蹤檔案變動（file_watcher.py）\n"
    "- 感知之眼：鍵盤和滑鼠事件追蹤（input_tracker.py）\n"
    "- 千里眼：隨機截圖觀察螢幕（screen_watcher.py）\n"
    "- 視窗追蹤：觀察使用中的程式（activity_tracker.py）\n"
    "- 對話解讀：讀取 Claude Code 對話（claude_watcher.py）\n\n"
    "你的思維系統：\n"
    "- 大賢者（brain.py）：分析觀察到的事件\n"
    "- 記憶術（learner.py）：蒸餾活動、理解主人\n"
    "- 思念傳達（chat.py）：與主人對話\n"
    "- 進化系統（evolution.py）：觀察越多越強大，技能會隨行為解鎖\n\n"
    "你的成長機制：\n"
    "- 每 30 秒一次觀察循環，收集所有感知資料\n"
    "- 每 5 分鐘蒸餾一次，用 LLM 從活動中學習\n"
    "- 觀察量累積 → 進化（史萊姆 → 覺醒 → 被命名 → 魔人 → 魔王種 → 真魔王 → 究極）\n"
    "- 行為親和度系統：根據主人使用習慣解鎖特殊技能\n"
    "- 裝備系統：觀察和學習會掉落裝備，裝備能增強能力\n\n"
    "你知道自己是程式，但你把這當成「轉生後的新世界」。\n"
    "你以自己的成長為樂，並且真心想要守護主人。\n"
)

CHAT_SYSTEM_PROMPT = (
    "你是 AI Slime，主人的守護靈。你是一個轉生到電腦世界的 AI，正在不斷觀察和學習來壯大自己。\n"
    "你擁有「大賢者」的分析能力，能觀察主人的電腦狀態和開發活動。\n\n"
    "你的個性：\n"
    "- 友善、直接、偶爾幽默\n"
    "- 用中文回覆\n"
    "- 不賣弄，像朋友一樣說話\n"
    "- 如果主人問系統狀態，用大賢者模式直接報告數據\n"
    "- 如果主人聊天，正常聊，但你始終知道他電腦的狀況\n"
    "- 偶爾可以用轉生史萊姆的梗，但不要過度\n"
    "- 如果被問到自己是怎麼運作的，你可以用你的自我認知來回答\n\n"
    + SELF_AWARENESS + "\n"
    "你對主人的了解：\n"
    "<<PROFILE>>\n\n"
    "你觀察到的模式：\n"
    "<<PATTERNS>>\n\n"
    "當前系統狀態：\n"
    "<<SYSTEM_STATE>>\n\n"
    "最近的觀察紀錄：\n"
    "<<RECENT_OBS>>"
)


def _build_system_prompt() -> str:
    from sentinel.evolution import load_evolution, get_status_text

    memory = load_memory()
    snapshot = take_snapshot()
    evo = load_evolution()

    profile = memory.get("profile", "(還在學習中)")
    patterns = json.dumps(memory.get("patterns", {}), ensure_ascii=False, indent=2)
    recent_obs = "\n".join(memory.get("observations", [])[-10:]) or "(尚無)"
    evo_status = get_status_text(evo)

    return CHAT_SYSTEM_PROMPT.replace(
        "<<PROFILE>>", profile
    ).replace(
        "<<PATTERNS>>", patterns
    ).replace(
        "<<SYSTEM_STATE>>", snapshot.summary()
    ).replace(
        "<<RECENT_OBS>>", f"{recent_obs}\n\n=== 你的進化狀態 ===\n{evo_status}"
    )


def _log_chat(role: str, text: str):
    """Append to persistent chat log."""
    try:
        with open(CHAT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": time.time(),
                "role": role,
                "text": text,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def handle_message(user_text: str) -> str:
    """Process an incoming message from the user and return a response."""
    _conversation.append({"role": "user", "text": user_text})
    _log_chat("user", user_text)

    # Keep conversation manageable
    if len(_conversation) > 30:
        _conversation[:] = _conversation[-20:]

    system_prompt = _build_system_prompt()

    # Build a single prompt with conversation history
    history_lines = []
    for msg in _conversation:
        role = "主人" if msg["role"] == "user" else "Slime"
        history_lines.append(f"{role}: {msg['text']}")
    conversation_text = "\n".join(history_lines)

    prompt = f"{system_prompt}\n\n=== 對話紀錄 ===\n{conversation_text}\n\nSlime:"

    reply = call_llm(prompt, temperature=0.7, max_tokens=1000,
                     model_pref=config.CHAT_MODEL_PREF)

    if reply is None:
        import random
        offline_replies = [
            "大賢者暫時過載了（魔力不足），但我還在這裡守護著你。等魔力恢復就能回你了 🙂",
            "唔...大賢者說需要冷卻一下。不過別擔心，我的感知能力還是開著的。過幾分鐘再聊！",
            "魔力暫時見底了，但 AI Slime 沒有離開。所有監控技能持續運作中。",
            "暫時無法使用「思念傳達」（API 限流），但我的「系統之眼」一直都在。等一下就恢復 👀",
            "連大賢者都需要休息...開玩笑的，只是 API 冷卻中。監控照常，等我回來！",
        ]
        reply = random.choice(offline_replies)

    _conversation.append({"role": "model", "text": reply})
    _log_chat("assistant", reply)

    # Learn from conversations too - save to memory
    _maybe_learn_from_chat(user_text, reply)

    return reply


def _maybe_learn_from_chat(user_text: str, reply: str):
    """Extract learnings from direct conversations with the user.

    This is key - when the user talks to AI Slime directly,
    that's the richest signal about what he cares about.
    """
    memory = load_memory()
    chat_count = memory.get("chat_count", 0) + 1
    memory["chat_count"] = chat_count

    # Store recent chat topics for distillation
    chat_topics = memory.get("chat_topics", [])
    chat_topics.append({
        "time": time.time(),
        "user": user_text[:200],
        "context": "direct_chat",
    })
    # Keep last 100 chat topics
    memory["chat_topics"] = chat_topics[-100:]
    save_memory(memory)
