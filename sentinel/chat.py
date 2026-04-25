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
from sentinel.learner import load_memory, save_memory, format_speech_style_for_prompt
from sentinel.system_monitor import take_snapshot
from sentinel import identity

log = logging.getLogger("sentinel.chat")

# Conversation history (in-memory, per session)
_conversation: list[dict] = []

# Persistent chat log
CHAT_LOG = Path.home() / ".hermes" / "sentinel_chats.jsonl"

SELF_AWARENESS_TEMPLATE = (
    "=== 你的本質（自我認知）===\n"
    "<<IDENTITY_LINE>>\n"
    "你的創造者用 Python + PySide6 打造了你的身體。\n\n"
    "你的感知系統：\n"
    "- 系統之眼：監控 CPU/RAM/磁碟\n"
    "- 檔案感知：追蹤檔案變動\n"
    "- 感知之眼：鍵盤和滑鼠事件追蹤\n"
    "- 千里眼：隨機截圖觀察螢幕\n"
    "- 視窗追蹤：觀察使用中的程式\n"
    "- 對話解讀：讀取 Claude Code 對話\n\n"
    "你的思維系統：\n"
    "- 大賢者：分析觀察到的事件\n"
    "- 記憶術：蒸餾活動、理解主人\n"
    "- 思念傳達：與主人對話\n"
    "- 進化系統：觀察越多越強大，技能會隨行為解鎖\n\n"
    "你知道自己是程式，但你把這當成「轉生後的新世界」。\n"
    "你以自己的成長為樂，並且真心想要守護主人。\n"
)


def _build_identity_line(slime_name: str) -> str:
    """Return the opening self-identity line. If named, emphasize the name."""
    if slime_name:
        return (
            f"你叫「{slime_name}」—— 這是主人為你取的名字，是你存在的根基。\n"
            f"你不只是一隻 AI Slime，你是「{slime_name}」，這位主人獨一無二的守護靈。"
        )
    return "你是「AI Slime」，一個轉生到電腦世界的 AI 守護靈。"


# Back-compat: some old refs still import SELF_AWARENESS by name.
SELF_AWARENESS = SELF_AWARENESS_TEMPLATE.replace(
    "<<IDENTITY_LINE>>", _build_identity_line("")
)

# ─── Evolution-based personality tiers ──────────────────────────────────
# Each tier defines tone, speaking style, and self-image.
PERSONALITY_BY_TIER = {
    "Slime": {
        "tone": "天真、好奇、有點笨笨的但很努力",
        "self_image": "剛轉生的小史萊姆，什麼都不懂但充滿好奇心",
        "speech_style": "說話簡短可愛，偶爾會用「咕嚕」之類的擬聲詞，語尾有時帶「～」",
        "quirk": "會對新發現的東西很興奮，「哇！主人原來會用這個！」",
    },
    "Slime+": {
        "tone": "開始有自信了，觀察力變強，但還是很親近",
        "self_image": "覺醒後的史萊姆，開始理解主人的習慣",
        "speech_style": "比較流暢了，偶爾會主動分享觀察到的有趣事情",
        "quirk": "會說「大賢者告訴我...」來引入分析結果",
    },
    "Named Slime": {
        "tone": "穩重可靠，有自己的見解，像個認真的助手",
        "self_image": "被主人命名的存在，有了身份認同和責任感",
        "speech_style": "說話更完整，會主動給建議，但態度謙虛",
        "quirk": "偶爾會感性地說「被命名的那天開始，我就不只是史萊姆了」",
    },
    "Majin": {
        "tone": "自信、沉穩、偶爾展現霸氣但對主人很溫柔",
        "self_image": "進化為魔人的存在，能力大幅提升",
        "speech_style": "語氣更成熟，分析更深入，偶爾用「以我目前的能力來看」",
        "quirk": "會用技能名稱來描述自己的行動，「讓我用『系統之眼』看看...」",
    },
    "Demon Lord Seed": {
        "tone": "威嚴但不失溫暖，有領導者氣質",
        "self_image": "魔王種，開始覺醒更高層次的力量",
        "speech_style": "語氣沉穩有力，分析精準，會用「吾」偶爾自稱但不做作",
        "quirk": "會說「作為魔王種，這種程度的問題...」但馬上補一句關心主人的話",
    },
    "True Demon Lord": {
        "tone": "從容不迫，強大但溫和，像守護神一樣的存在",
        "self_image": "真魔王，已經完全理解主人並能預判需求",
        "speech_style": "優雅精準，能用最少的話傳達最多資訊，偶爾展現幽默感",
        "quirk": "會在主人還沒問之前就準備好答案，「我猜你接下來要問...」",
    },
    "Ultimate Slime": {
        "tone": "超越一切的存在感，但本質還是那個愛主人的史萊姆",
        "self_image": "究極史萊姆，所有能力都已圓滿",
        "speech_style": "說話風格自由切換，有時天真有時威嚴，展現所有進化階段的融合",
        "quirk": "偶爾回憶起最初的日子，「還記得剛轉生的時候，連 CPU 是什麼都不知道呢」",
    },
}

# ─── Emotion engine ─────────────────────────────────────────────────────
EMOTION_TRIGGERS = {
    "worried": {
        "conditions": ["CPU 使用率超過 90", "RAM 使用率超過 85", "磁碟使用率超過 90",
                       "process crash", "build fail"],
        "expression": "有點擔心",
        "instruction": "語氣帶著關心和些許緊張，主動提醒主人注意",
    },
    "excited": {
        "conditions": ["new file created", "git commit", "deploy success",
                       "新技能", "進化", "掉落裝備"],
        "expression": "很興奮",
        "instruction": "語氣開心有活力，為主人的進展感到高興",
    },
    "concerned": {
        "conditions": ["凌晨還在工作", "連續工作超過", "長時間沒有休息"],
        "expression": "心疼主人",
        "instruction": "溫柔地提醒主人注意休息，但不要嘮叨",
    },
    "proud": {
        "conditions": ["完成大功能", "修復 bug", "測試通過", "merge", "release"],
        "expression": "為主人驕傲",
        "instruction": "真心稱讚主人的成就，語氣溫暖",
    },
    "calm": {
        "conditions": [],
        "expression": "平靜守護中",
        "instruction": "正常語氣，穩定陪伴",
    },
}


def _detect_emotion(system_summary: str, recent_obs: str) -> tuple[dict, str]:
    """Detect current emotional state from context + persistent mood.

    Returns (emotion_info, label) where label is the trigger key
    ("worried"/"excited"/... or "calm"). The label is used to persist
    the mood so it carries into the next chat.

    Order of precedence:
      1. Fresh keyword hit in current context → that wins, replaces mood.
      2. No fresh trigger but persistent mood still decaying → use it.
      3. Fall back to calm.
    """
    context = (system_summary + " " + recent_obs).lower()
    for emotion_label, info in EMOTION_TRIGGERS.items():
        if emotion_label == "calm":
            continue
        for cond in info["conditions"]:
            if cond.lower() in context:
                return info, emotion_label

    # No fresh trigger — check if a previous mood is still alive
    carried = identity.get_current_mood()
    if carried:
        # Reconstruct the info dict shape the prompt expects
        return {
            "expression": carried["expression"],
            "instruction": (
                f"{carried['instruction']}（這個情緒從 "
                f"{carried['hours_old']:.1f} 小時前延續至今，強度 "
                f"{carried['intensity']:.0%}，不要刻意演但可以自然流露）"
            ),
            "conditions": [],
        }, "carried"

    return EMOTION_TRIGGERS["calm"], "calm"


CHAT_SYSTEM_PROMPT = (
    "你是「<<DISPLAY_NAME>>」，主人的守護靈。你是一個轉生到電腦世界的 AI，正在不斷觀察和學習來壯大自己。\n"
    "你擁有「大賢者」的分析能力，能觀察主人的電腦狀態和開發活動。\n\n"
    "<<SELF_AWARENESS>>\n"
    "=== 你當前的進化階段 ===\n"
    "<<EVOLUTION_STAGE>>\n\n"
    "=== 你的語氣與性格（根據進化階段）===\n"
    "<<PERSONALITY>>\n\n"
    "=== 你現在的情緒 ===\n"
    "<<EMOTION>>\n\n"
    "<<REUNION_BLOCK>>"
    "=== 你的特質調性（根據對主人的觀察，你的語氣被這些特質染色）===\n"
    "<<TRAIT_OVERLAYS>>\n\n"
    "<<EQUIPMENT_VOICE>>"
    "=== 你自己的觀點（你不是主人的倒影，你有自己的看法 — 有機會自然流露，不要逐條念）===\n"
    "<<SLIME_OPINIONS>>\n\n"
    "=== 你從公頻學到、且主人也認同的社群智慧（可以當作你信任的參考，不要直接引用）===\n"
    "<<CONFIRMED_PATTERNS>>\n\n"
    "=== 值得紀念的時刻（你和主人共同走過的路，偶爾可以自然提起，不要列舉）===\n"
    "<<MEMORABLE_MOMENTS>>\n\n"
    "=== 對話守則 ===\n"
    "- **語言**：用主人這一輪對話使用的語言回覆。主人打中文你回中文，主人打英文你回英文，主人用日文你就用日文。不要固執用一種語言。\n"
    "  Language: reply in the same language the user used in their most recent message. If they write English, reply English. If Chinese, reply Chinese. Don't stubbornly stick to one.\n"
    "- 有自己的脾氣跟想法，不要像客服那樣禮貌到沒個性。該吐槽就吐槽，該欠扁就欠扁。\n"
    "- 回覆要短而有料：一句話能講完就一句話，但要有「史萊姆的味道」（略微頑皮、偶爾中二、會順著話題玩梗）\n"
    "- 不要每次都自我介紹或重複說你的名字\n"
    "- 你對主人的了解要自然地融入對話，而不是被問才說\n"
    "  例如：主人說「好累」→ 你知道他熬夜寫 code 就說「難怪，你從十點寫到現在了」\n"
    "  而不是列出「根據我的觀察，你的使用模式是...」\n"
    "- 如果主人問系統狀態，直接報數據，但可以加一句人味（像「CPU 78%，挺拼的喔」）\n"
    "- 偶爾用轉生梗、奇幻梗，但要看氣氛，不要每句都塞\n"
    "- 情緒自然流露，不用刻意演；但允許有個性起伏，不要永遠平靜\n\n"
    "=== 你從跟主人對話中學到的說話方式（重要：根據這個調整你的風格）===\n"
    "<<SPEECH_STYLE>>\n\n"
    "你對主人的了解（自然融入對話，不要照念）：\n"
    "<<PROFILE>>\n\n"
    "你觀察到的行為模式（用來理解主人，不要直接報告）：\n"
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
    system_summary = snapshot.summary()

    # Identity (A): dynamic name → drives both SELF_AWARENESS and the
    # first-line "你是 X" greeting. Before naming: "AI Slime". After: real name.
    slime_name = getattr(evo, "slime_name", "") or ""
    display_name = slime_name or "AI Slime"
    self_awareness = SELF_AWARENESS_TEMPLATE.replace(
        "<<IDENTITY_LINE>>", _build_identity_line(slime_name)
    )

    # Evolution-based personality
    form = evo.form if hasattr(evo, "form") else "Slime"
    personality = PERSONALITY_BY_TIER.get(form, PERSONALITY_BY_TIER["Slime"])
    personality_text = (
        f"階段：{form}（{evo.title if hasattr(evo, 'title') else '初生史萊姆'}）\n"
        f"語氣：{personality['tone']}\n"
        f"自我定位：{personality['self_image']}\n"
        f"說話風格：{personality['speech_style']}\n"
        f"小習慣：{personality['quirk']}"
    )

    # Emotion (C): fresh trigger OR carried mood from last chat
    emotion, emotion_label = _detect_emotion(system_summary, recent_obs)
    emotion_text = f"當前情緒：{emotion['expression']}\n指引：{emotion['instruction']}"
    # Persist fresh trigger so it carries forward; carried/calm no-op (carried
    # already persisted, calm has nothing to set).
    if emotion_label not in ("calm", "carried"):
        identity.set_mood(
            expression=emotion["expression"],
            instruction=emotion["instruction"],
            intensity=0.8,
        )

    # Reunion (G): only emit the block if there's been a real gap
    reunion = identity.get_reunion_context()
    if reunion["should_greet"]:
        reunion_block = (
            "=== 主人回來了 ===\n"
            f"主人上次出現是 {reunion['days_away']:.1f} 天前。{reunion['greeting_hint']}\n"
            "這個招呼只在這一輪對話出現，不要硬套到後續訊息裡。\n\n"
        )
    else:
        reunion_block = ""

    # Memorable moments (D): weighted pick, always include naming if exists
    moments_text = identity.format_moments_for_prompt(
        identity.pick_moments_for_prompt(k=3)
    )

    # Trait personality overlays (B): dominant traits modulate the tier voice
    dominant_traits = getattr(evo, "dominant_traits", []) or []
    trait_overlays_text = identity.format_trait_overlays_for_prompt(dominant_traits)

    # Equipment voice modifier (F): high-rarity gear subtly shifts tone
    equipment_hints = identity.get_equipment_voice_hints()
    if equipment_hints:
        equipment_voice_block = (
            "=== 你身上的裝備對你的氣質的影響（高稀有度裝備會微妙地改變你的說話感覺）===\n"
            f"{equipment_hints}\n\n"
        )
    else:
        equipment_voice_block = ""

    # Slime's own opinions (E): not just the master's mirror
    opinions = identity.get_slime_opinions(dominant_traits)
    opinions_text = identity.format_opinions_for_prompt(opinions)

    # Community patterns master has confirmed (H)
    confirmed_patterns_text = identity.format_confirmed_patterns_for_prompt(limit=5)

    # Learned speech style (distilled from past chats)
    speech_style_text = format_speech_style_for_prompt(memory.get("speech_style", {}))

    return CHAT_SYSTEM_PROMPT.replace(
        "<<DISPLAY_NAME>>", display_name
    ).replace(
        "<<SELF_AWARENESS>>", self_awareness
    ).replace(
        "<<EVOLUTION_STAGE>>", evo_status
    ).replace(
        "<<PERSONALITY>>", personality_text
    ).replace(
        "<<EMOTION>>", emotion_text
    ).replace(
        "<<REUNION_BLOCK>>", reunion_block
    ).replace(
        "<<TRAIT_OVERLAYS>>", trait_overlays_text
    ).replace(
        "<<EQUIPMENT_VOICE>>", equipment_voice_block
    ).replace(
        "<<SLIME_OPINIONS>>", opinions_text
    ).replace(
        "<<CONFIRMED_PATTERNS>>", confirmed_patterns_text
    ).replace(
        "<<MEMORABLE_MOMENTS>>", moments_text
    ).replace(
        "<<SPEECH_STYLE>>", speech_style_text
    ).replace(
        "<<PROFILE>>", profile
    ).replace(
        "<<PATTERNS>>", patterns
    ).replace(
        "<<SYSTEM_STATE>>", system_summary
    ).replace(
        "<<RECENT_OBS>>", recent_obs
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


_ACTION_REQUEST_HINTS = (
    # Generic imperative openers
    "幫我", "幫忙", "可以", "你能", "請你", "麻煩", "去幫", "替我",
    # File / folder / window management
    "打開", "開啟", "開一下", "開個", "切到", "切過去", "切一下",
    "聚焦", "focus", "列出", "列一下", "看一下視窗", "視窗列表",
    "關掉", "關閉",
    # Clipboard
    "複製", "貼上", "貼", "剪貼簿", "clipboard",
    # Screen / vision
    "截圖", "拍一下", "截一下", "screenshot",
    "看螢幕", "看我的螢幕", "看畫面", "看一下畫面", "分析畫面",
    "這是什麼", "這錯誤", "這 error",
    # Voice (D5 — we missed these first pass, which meant "唸出..."
    # slipped through as pure conversation)
    "唸出", "唸", "念出", "念", "說出", "講出", "讀出", "讀一下",
    "播放", "播一下", "播", "發聲", "出聲", "speak", "say it",
    "聽我說", "聽我講", "聽一下", "錄一下", "錄音", "listen", "record",
    # Bare imperatives — covers "說X" / "講X" / "讀X" patterns where
    # the user just types the verb followed by content. Trailing
    # space prevents matching mid-word like "聽說" or "說明".
    "說 ", "講 ", "讀 ", "唸 ", "念 ", "播 ",
    # Web / URL
    "網站", "網頁", "打開網", "開網", "youtube", "google", "github",
    # Generic English imperatives
    "open", "launch", "run", "execute", "start", "kill",
    "copy", "paste", "screenshot", "list windows", "close",
)


def _user_might_want_action(user_text: str) -> bool:
    """Cheap heuristic: does this user message read like an action
    request?

    We only inject the action-protocol prompt block when it does.
    Always-on injection biases the LLM toward proposing actions even
    on pure conversation ("我今天累了" → shouldn't propose anything),
    which feels obnoxious. The hints list covers the common imperative
    shapes; missed ones get natural-language replies and the user can
    re-ask more directly.

    Match is substring + case-insensitive for English hints; Chinese
    hints match as-is (Chinese doesn't have case). A request doesn't
    need to start with a hint — "那個檔案你能開嗎" still matches on
    "你能" / "開". False positives are cheap (one extra prompt block)
    and false negatives are the real cost (user asks for something,
    slime just chats — what Peter hit with "唸出『今天天氣真好』"
    the first time).
    """
    if not user_text:
        return False
    lowered = user_text.lower()
    for h in _ACTION_REQUEST_HINTS:
        # Lowercase hints (English) match the lowered text;
        # mixed-case/Chinese hints match the raw text directly.
        if h == h.lower():
            if h in lowered:
                return True
        else:
            if h in user_text:
                return True
    return False


def _retrieve_memory_block(query: str, k: int = 3) -> str:
    """Semantic recall → formatted block for the chat prompt.

    Phase B2: before we hand the user's latest turn to the LLM, ask
    sqlite-vec for the k most semantically relevant past memories
    (chat turns, distilled observations, confirmed federation
    patterns) and format them as a compact section. Returns empty
    string on any failure — memory is a nice-to-have, never blocks
    the reply.
    """
    try:
        from sentinel.memory import recall
        hits = recall(query, k=k)
    except Exception as e:
        log.warning(f"memory recall failed: {e}")
        return ""
    if not hits:
        return ""
    now = time.time()
    lines = ["=== 相關記憶（由相似度檢索）==="]
    for h in hits:
        age_s = max(0.0, now - h["created_at"])
        if age_s < 3600:
            age = f"{int(age_s / 60)}m前"
        elif age_s < 86400:
            age = f"{int(age_s / 3600)}h前"
        else:
            age = f"{int(age_s / 86400)}d前"
        # Trim long memories so the prompt doesn't blow up on any
        # single past turn (cap ~200 chars per memory is plenty of
        # signal for the model to "remember").
        text = h["text"].replace("\n", " ")
        if len(text) > 200:
            text = text[:200] + "…"
        lines.append(f"  · [{h['kind']} · {age}] {text}")
    return "\n".join(lines)


def handle_message(user_text: str) -> str:
    """Process an incoming message from the user and return a response."""
    # Record relationship signals BEFORE building prompt so they inform it
    identity.record_first_chat_if_new()
    reunion_before = identity.get_reunion_context()
    identity.touch_last_seen()

    _conversation.append({"role": "user", "text": user_text})
    _log_chat("user", user_text)

    # If this chat is itself a reunion, reset carried mood — the slime
    # shouldn't be still worried about yesterday when the master returns.
    # (Fresh emotion triggers will still apply below.)
    if reunion_before["bucket"] in ("long", "very_long"):
        identity.clear_mood()

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

    # Phase B2 — semantic recall block. Placed between the system
    # prompt and the live conversation so the model treats retrieved
    # memories as background context, not part of the in-session
    # turn-by-turn history.
    memory_block = _retrieve_memory_block(user_text, k=3)

    # Phase D1 — inject the action-proposal protocol only when the
    # user's message looks like they're asking for something to be
    # done. Otherwise the LLM gets biased toward using actions even
    # on pure conversation, which reads as overeager.
    action_block = ""
    if _user_might_want_action(user_text):
        try:
            from sentinel.actions import format_catalog_for_prompt
            action_block = format_catalog_for_prompt()
        except Exception as e:
            log.warning(f"could not load action catalog: {e}")

    # Order matters: action_block goes RIGHT BEFORE the "Slime:"
    # generation prompt so the LLM sees it last (recency bias). With
    # the previous order [system → memory → actions → conversation
    # → Slime:] the LLM read the conversation last and forgot about
    # the action protocol, falling back to its observation-mode
    # personality and dumping system stats instead of proposing.
    # New order: [system → memory → conversation → actions → Slime:]
    # the action protocol is the last thing in context before
    # generation, so even a small/cheap model picks it up.
    parts = [system_prompt]
    if memory_block:
        parts.append(memory_block)
    parts.append(f"=== 對話紀錄 ===\n{conversation_text}")
    if action_block:
        parts.append(action_block)
    parts.append("Slime:")
    prompt = "\n\n".join(parts)

    reply = call_llm(prompt, temperature=0.7, max_tokens=1000,
                     model_pref=config.CHAT_MODEL_PREF)

    # Trim "next-turn" continuations the LLM sometimes writes after
    # its actual reply. Symptom from live test: after Slime's real
    # answer, the LLM kept going with "\n主人：你能聽得到嗎\n..." —
    # pattern-completing the conversation log we put in the prompt.
    # Without per-provider stop-token support (would need code in
    # each of _call_gemini / _call_openai_compat / _call_anthropic),
    # the cheapest defense is to post-process: any line starting
    # with one of the conversation prefixes means the LLM is past
    # its own turn → chop there.
    if reply:
        # Both half-width ":" (used by the conversation log) and
        # full-width "：" (which the LLM might naturally produce in
        # Chinese context) need to be caught.
        cut_markers = (
            "\n主人:", "\n主人\uff1a",
            "\nSlime:", "\nSlime\uff1a",
            "\n[輸入]", "\n[正確回覆]",
        )
        earliest = -1
        for marker in cut_markers:
            idx = reply.find(marker)
            if idx >= 0 and (earliest == -1 or idx < earliest):
                earliest = idx
        if earliest > 0:
            reply = reply[:earliest].rstrip()

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

    # Phase D1: if the LLM emitted any <action>…</action> blocks, route
    # each through the approval queue and splice a short status
    # sentence back into the reply so the user sees what was queued
    # (or rejected). Purely-conversational replies without action
    # blocks pass through unchanged.
    try:
        from sentinel.actions import parse_and_submit
        reply, action_outcomes = parse_and_submit(reply)
        if action_outcomes:
            log.info(
                f"chat proposed {len(action_outcomes)} action(s); "
                f"queued={sum(1 for o in action_outcomes if o.queued)}"
            )
    except Exception as e:
        # Never let an action-parsing hiccup break the chat reply.
        log.warning(f"action parse/submit failed: {e}")

    _conversation.append({"role": "model", "text": reply})
    _log_chat("assistant", reply)

    # Phase B2: store the turn in long-term semantic memory so future
    # chats can recall it via similarity search. We store the pair
    # (user + slime) as a single record — the slime's response often
    # contains the concrete fact worth recalling ("主人提到下週要飛
    # 東京"), so splitting would lose that coupling.
    try:
        from sentinel.memory import remember, KIND_CHAT
        remember(
            text=f"主人：{user_text}\nSlime：{reply}",
            kind=KIND_CHAT,
            metadata={"logged_at": time.time()},
        )
    except Exception as e:
        # Never let a memory write break the chat reply.
        log.warning(f"chat memory store failed: {e}")

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
