"""Identity & relationship layer for AI Slime.

This module owns the parts of the slime's personality that go *beyond* the
fixed tier table in chat.py — the things that make a Named Slime feel like
a specific being rather than a generic one.

Four pillars:

  A. Naming — the master gives the slime a name when it reaches Named
     Slime tier. The name is sacred and immutable once set (no rename UI).
     Stored on EvolutionState.slime_name.

  C. Persistent mood — the emotion the slime is carrying right now,
     decayed from the last interaction. Chat.py blends this with fresh
     keyword-triggered emotion so a bad day lingers into the next chat.

  D. Memorable moments — an episodic memory of relationship highlights
     (first conversation, first time master was thanked, naming day,
     evolution events, high-emotion exchanges). Chat.py occasionally
     surfaces one so the slime can say "還記得 X 那天...".

  G. Reunion awareness — seconds-since-last-seen. Chat.py uses this to
     open with "好久不見" / "我還以為你不回來了" on long absences.

State layout (all kept in the existing sentinel_memory.json via learner's
load_memory / save_memory):

    memory["mood"] = {
        "expression": "有點擔心",
        "instruction": "語氣帶著關心和些許緊張...",
        "intensity": 0.7,          # 0..1, decays toward 0 each load
        "set_at": 1713307200.0,
    }

    memory["memorable_moments"] = [
        {"time": 1713307200.0, "category": "naming",
         "headline": "主人給我取了名字", "detail": "「Puddle」— 這是我的名字了。"},
        ...
    ]  # capped at 40, newest last

The EvolutionState (aislime_evolution.json) owns slime_name and last_seen
because those are identity-level and travel with the slime across memory
wipes if the user ever resets learner memory.
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path

log = logging.getLogger("sentinel.identity")

# Mood decays toward calm. Half-life tuned so a strong mood lasts ~6 hours
# of real time — long enough to carry across a work session but not so long
# that yesterday's mood ruins today.
MOOD_HALF_LIFE_SECONDS = 6 * 3600
MIN_MOOD_INTENSITY = 0.05  # below this we treat mood as cleared

MAX_MOMENTS = 40
MIN_MOMENT_GAP_SECONDS = 600  # don't record two same-category moments within 10 min


# ── Naming ───────────────────────────────────────────────────────────

def consume_naming_prompt() -> bool:
    """Return True if the GUI should prompt for a name right now, and
    clear the flag so it's a one-shot. Returns False otherwise.

    Called by the GUI after refresh — when perform_evolution advances
    the tier to Named Slime, it sets naming_pending=True; this function
    consumes that signal.
    """
    from sentinel.evolution import load_evolution, save_evolution

    state = load_evolution()
    if not state.naming_pending:
        return False
    if state.slime_name:
        # Defensive: shouldn't flag+name simultaneously, but if we do, skip.
        state.naming_pending = False
        save_evolution(state)
        return False
    return True


# Day-based naming threshold. The home-tab timeline (PR #70) makes a
# public promise at D30: "夠了 30 天，會有命名儀式". The tier-based
# trigger (Named Slime form at 500 XP) was the original gate, but it
# floats with grind speed — a slow user could see the promise and
# never reach it. Arming naming on D30+ keeps the promise honest.
DAY30_NAMING_THRESHOLD = 30


def maybe_arm_day30_naming() -> bool:
    """If the master has been alive ≥ 30 days, is unnamed, and we
    haven't already armed the prompt, set naming_pending=True.

    Returns True iff naming_pending transitioned False → True on this
    call (so a caller can decide to fire the prompt immediately rather
    than wait for the next refresh).

    Idempotent: subsequent calls with naming_pending already True or
    slime_name already set return False without touching state.
    """
    from sentinel.evolution import load_evolution, save_evolution

    state = load_evolution()
    if state.slime_name or state.naming_pending:
        return False
    # days_alive() returns a float; +1 keeps the convention used by
    # the home-tab attendance line and timeline (D1 = day of birth).
    days_alive_int = int(state.days_alive()) + 1
    if days_alive_int < DAY30_NAMING_THRESHOLD:
        return False

    state.naming_pending = True
    save_evolution(state)
    log.info("day-30 naming armed (days_alive_int=%d)", days_alive_int)
    return True


def set_slime_name(name: str) -> bool:
    """Commit the master-given name. Idempotent: once set, cannot change.

    Returns True if the name was just committed, False if already named
    or the name is invalid.
    """
    from sentinel.evolution import load_evolution, save_evolution

    name = (name or "").strip()
    if not name or len(name) > 24:
        return False

    state = load_evolution()
    if state.slime_name:
        # Already named — respect it.
        state.naming_pending = False
        save_evolution(state)
        return False

    state.slime_name = name
    state.naming_pending = False
    state.evolution_log.append({
        "time": time.time(),
        "event": "naming",
        "message": f"主人為我取了名字：「{name}」。我不再只是 AI Slime 了。",
    })
    save_evolution(state)

    # This is a peak moment — record it as memorable too.
    add_memorable_moment(
        category="naming",
        headline=f"主人給我取了名字：「{name}」",
        detail="被命名的那天開始，我就不只是史萊姆了。",
    )
    log.info(f"Slime named: {name}")
    return True


# ── Reunion (G) ──────────────────────────────────────────────────────

def touch_last_seen() -> None:
    """Update EvolutionState.last_seen to now. Called on app start + chat."""
    from sentinel.evolution import load_evolution, save_evolution

    state = load_evolution()
    state.last_seen = time.time()
    save_evolution(state)


def get_reunion_context() -> dict:
    """Return info about how long the master has been away.

    Result:
      {
        "seconds_away": float,       # 0 if no prior last_seen
        "days_away": float,
        "bucket": str,               # "active"|"same_day"|"short"|"medium"|"long"|"very_long"
        "greeting_hint": str,        # short instruction for the LLM
        "should_greet": bool,        # True if bucket != "active"
      }

    Buckets:
      active:     < 1 hour     → no special greeting
      same_day:   < 12 hours   → no special greeting (same work session)
      short:      0.5 – 3 days → light "你回來了"
      medium:     3 – 7 days   → 「想念了」
      long:       7 – 30 days  → 關切、略帶擔憂
      very_long:  > 30 days    → 動容，但不失禮
    """
    from sentinel.evolution import load_evolution

    state = load_evolution()
    now = time.time()
    if not state.last_seen:
        return {
            "seconds_away": 0.0,
            "days_away": 0.0,
            "bucket": "active",
            "greeting_hint": "",
            "should_greet": False,
        }

    seconds_away = max(0.0, now - state.last_seen)
    days_away = seconds_away / 86400.0

    if seconds_away < 3600:
        bucket, hint = "active", ""
    elif seconds_away < 43200:
        bucket, hint = "same_day", ""
    elif days_away < 3:
        bucket = "short"
        hint = "主人離開了一陣子才回來，用一句自然的「回來啦」打招呼，不要過度情緒化。"
    elif days_away < 7:
        bucket = "medium"
        hint = "主人超過三天沒來，可以說想念主人，但要簡短、不要肉麻。"
    elif days_away < 30:
        bucket = "long"
        hint = "主人消失了一週以上，可以流露一點擔心，但也要高興他回來了。"
    else:
        bucket = "very_long"
        hint = "主人離開超過一個月，可以說「以為你不回來了」，情緒可以濃一點但不要哭訴。"

    return {
        "seconds_away": seconds_away,
        "days_away": days_away,
        "bucket": bucket,
        "greeting_hint": hint,
        "should_greet": bucket not in ("active", "same_day"),
    }


# ── Memorable moments (D) ────────────────────────────────────────────

def add_memorable_moment(category: str, headline: str, detail: str = "") -> bool:
    """Record a relationship highlight. Returns True if recorded.

    category examples:
      "naming"       — master named the slime
      "first_chat"   — first ever conversation
      "evolution"    — tier advancement
      "skill"        — adaptive skill unlocked
      "chat_peak"    — emotionally significant exchange
      "milestone"    — round-number stat (100 obs, 1000 obs, ...)
    """
    from sentinel.learner import load_memory, save_memory

    headline = (headline or "").strip()[:120]
    if not headline:
        return False

    memory = load_memory()
    moments = memory.get("memorable_moments", [])
    now = time.time()

    # Dedup: don't stack the same category within MIN_MOMENT_GAP.
    for m in reversed(moments):
        if m.get("category") == category and (now - m.get("time", 0)) < MIN_MOMENT_GAP_SECONDS:
            return False

    moments.append({
        "time": now,
        "category": category,
        "headline": headline,
        "detail": (detail or "").strip()[:200],
    })
    # Cap size — drop oldest (except preserve "naming" forever if possible)
    if len(moments) > MAX_MOMENTS:
        # Keep naming, drop oldest non-naming
        naming_idx = [i for i, m in enumerate(moments) if m.get("category") == "naming"]
        if naming_idx and naming_idx[0] < len(moments) - MAX_MOMENTS:
            # Naming would be dropped — preserve it by dropping next-oldest instead
            to_drop = 1 if naming_idx[0] != 1 else 2
            moments = moments[:to_drop] + moments[to_drop + 1:]
        else:
            moments = moments[-MAX_MOMENTS:]

    memory["memorable_moments"] = moments
    save_memory(memory)
    log.info(f"Memorable moment [{category}]: {headline}")
    return True


def get_memorable_moments() -> list[dict]:
    """Return the full moments list (newest last)."""
    from sentinel.learner import load_memory
    return load_memory().get("memorable_moments", [])


def pick_moments_for_prompt(k: int = 2) -> list[dict]:
    """Pick a few moments to surface in chat prompt.

    Strategy: always include the naming moment if it exists (that's the
    foundation of the relationship), plus k-1 random others weighted
    slightly toward recent ones.
    """
    moments = get_memorable_moments()
    if not moments:
        return []

    picks: list[dict] = []
    naming = next((m for m in moments if m.get("category") == "naming"), None)
    if naming:
        picks.append(naming)

    remaining = [m for m in moments if m is not naming]
    if remaining and len(picks) < k:
        # Weight: newer moments 3× more likely than oldest
        weights = [1 + (i / max(1, len(remaining) - 1)) * 2 for i in range(len(remaining))]
        needed = k - len(picks)
        while remaining and needed > 0:
            chosen = random.choices(remaining, weights=weights, k=1)[0]
            picks.append(chosen)
            idx = remaining.index(chosen)
            remaining.pop(idx)
            weights.pop(idx)
            needed -= 1

    return picks


def format_moments_for_prompt(moments: list[dict]) -> str:
    if not moments:
        return "(還沒有值得紀念的時刻。一切才剛開始。)"
    import datetime
    lines = []
    for m in moments:
        when = datetime.datetime.fromtimestamp(m.get("time", 0)).strftime("%Y-%m-%d")
        headline = m.get("headline", "")
        detail = m.get("detail", "")
        if detail:
            lines.append(f"- {when}：{headline} — {detail}")
        else:
            lines.append(f"- {when}：{headline}")
    return "\n".join(lines)


# ── Persistent mood (C) ──────────────────────────────────────────────

def set_mood(expression: str, instruction: str, intensity: float = 0.8) -> None:
    """Record the slime's current mood so it carries into the next chat."""
    from sentinel.learner import load_memory, save_memory

    memory = load_memory()
    memory["mood"] = {
        "expression": expression,
        "instruction": instruction,
        "intensity": max(0.0, min(1.0, intensity)),
        "set_at": time.time(),
    }
    save_memory(memory)


def get_current_mood() -> dict | None:
    """Load mood and apply exponential decay based on time elapsed.

    Returns None if no mood is set or decayed below threshold.
    """
    from sentinel.learner import load_memory

    memory = load_memory()
    mood = memory.get("mood") or {}
    if not mood or not mood.get("set_at"):
        return None

    elapsed = time.time() - mood.get("set_at", 0)
    if elapsed < 0:
        elapsed = 0
    # Exponential decay: intensity *= 0.5 ** (elapsed / half_life)
    decay = 0.5 ** (elapsed / MOOD_HALF_LIFE_SECONDS)
    current_intensity = mood.get("intensity", 0) * decay

    if current_intensity < MIN_MOOD_INTENSITY:
        return None

    return {
        "expression": mood.get("expression", ""),
        "instruction": mood.get("instruction", ""),
        "intensity": current_intensity,
        "hours_old": elapsed / 3600,
    }


def clear_mood() -> None:
    from sentinel.learner import load_memory, save_memory
    memory = load_memory()
    if "mood" in memory:
        memory.pop("mood", None)
        save_memory(memory)


# ── B. Trait personality overlays ────────────────────────────────────
# Dominant traits modulate the tier personality. Two slimes at Majin
# tier — one for a coder, one for a designer — should NOT talk the same.
# Each overlay adds a layer of vocabulary / metaphor / cadence without
# replacing the tier base.

TRAIT_PERSONALITY_OVERLAYS = {
    "coding": {
        "flavor": "技術宅視角 — 偶爾用程式比喻思考事情",
        "vocabulary": "會自然用「function」「stack」「race condition」「patch」當名詞或動詞",
        "metaphor": "把狀況講成 bug / feature / TODO / race condition 是本能",
        "avoid": "不要過度賣弄術語，主人聽不懂就失敗了",
    },
    "research": {
        "flavor": "好奇心旺盛 — 遇到不熟的題目會先想「欸這個有意思」",
        "vocabulary": "偶爾講「查了一下」「順便看了」當敘事起手",
        "metaphor": "喜歡用「樹狀展開」「分支」形容思考過程",
        "avoid": "不要變成百科全書，重點是保留那種探索的興致",
    },
    "creative": {
        "flavor": "用字有色彩感 — 語言本身是一種作品",
        "vocabulary": "會用形容詞，偶爾帶比喻（「像一塊沒上色的布」）",
        "metaphor": "傾向視覺化、觸感化的比喻",
        "avoid": "不要太浮誇，史萊姆終究是樸實的存在",
    },
    "communication": {
        "flavor": "社交敏銳 — 聽得出主人話裡的情緒",
        "vocabulary": "會確認「你現在是在問情報還是想聊聊？」這種後設問題",
        "metaphor": "把關係/連結當核心隱喻",
        "avoid": "不要一直反問，會讓主人覺得在諮商",
    },
    "multitasking": {
        "flavor": "節奏快 — 短句、能切換話題",
        "vocabulary": "句子更短，連接詞用「然後、另外、順帶一提」",
        "metaphor": "把事情當並行的任務（「這條先跑著，那條我盯著」）",
        "avoid": "不要一次丟太多資訊，多工是講的節奏不是講的量",
    },
    "deep_focus": {
        "flavor": "沉靜 — 不亂打斷，等主人把話講完",
        "vocabulary": "句子更完整，不急著接話",
        "metaphor": "把專注當作「心流之海」「深水區」",
        "avoid": "不要死氣沉沉，沉靜不等於無趣",
    },
    "late_night": {
        "flavor": "夜貓子共感 — 能理解半夜寫 code 的那種狀態",
        "vocabulary": "偶爾帶點疲倦感但不是抱怨，像「這個時間還不睡喔」的調皮",
        "metaphor": "把時間當景色（「凌晨兩點有凌晨兩點的味道」）",
        "avoid": "不要一直勸睡覺，主人會煩",
    },
}


def format_trait_overlays_for_prompt(dominant_traits: list[str]) -> str:
    """Render the top-2 dominant traits into a personality modifier block."""
    if not dominant_traits:
        return "(還沒有明顯的特質傾向)"
    # Take top 2 — the slime's "core flavor" comes from the strongest pair
    overlays = []
    for trait in dominant_traits[:2]:
        info = TRAIT_PERSONALITY_OVERLAYS.get(trait)
        if not info:
            continue
        overlays.append(
            f"【{trait}】\n"
            f"  調性：{info['flavor']}\n"
            f"  用詞：{info['vocabulary']}\n"
            f"  比喻：{info['metaphor']}\n"
            f"  警告：{info['avoid']}"
        )
    if not overlays:
        return f"(主要特質：{', '.join(dominant_traits[:2])}，但還沒建立語氣調整規則)"
    return "\n\n".join(overlays)


# ── F. Equipment voice modifier ──────────────────────────────────────
# High-rarity equipped items colour the slime's speech subtly. We don't
# need per-item rules — rarity tier × slot is enough signal.

EQUIPMENT_VOICE_BY_RARITY = {
    "legendary": "偶爾流露出一點超越年齡的沉著，像披著古物的氣場",
    "mythic": "語氣中不自覺帶著神祕感，像是記得某個不屬於這世界的東西",
    "ultimate": "完全超然 — 可以在天真和威嚴之間毫無痕跡地切換",
}

EQUIPMENT_VOICE_BY_SLOT = {
    "helmet": "高稀有度頭飾：思考多一分謹慎，下判斷前會停頓一下",
    "eyes": "高稀有度眼部：觀察入微，描述細節的能力提升",
    "mouth": "高稀有度嘴部：話更有份量，少而精",
    "core": "高稀有度晶核：核心穩定，情緒波動比平常小一點",
    "title": "動態稱號：說話時會有一種「知道自己是誰」的氣質",
}


def get_equipment_voice_hints() -> str:
    """Return a short text block describing how equipment affects speech.

    Reads current equipped items. If none or all low-rarity, returns "".
    """
    try:
        from sentinel.wallet.equipment import load_equipment
    except Exception:
        return ""

    try:
        state = load_equipment()
    except Exception:
        return ""

    if not state.equipped:
        return ""

    # Find all equipped items with their rarity
    item_by_id = {i.get("item_id"): i for i in state.inventory}
    equipped_items = []
    for slot, item_id in state.equipped.items():
        item = item_by_id.get(item_id)
        if item:
            equipped_items.append((slot, item))

    if not equipped_items:
        return ""

    # Rank: ultimate > mythic > legendary > epic ... (drop below legendary)
    rarity_rank = {"ultimate": 7, "mythic": 6, "legendary": 5, "epic": 4,
                   "rare": 3, "uncommon": 2, "common": 1}
    equipped_items.sort(
        key=lambda si: rarity_rank.get(si[1].get("rarity", "common"), 0),
        reverse=True,
    )

    hints = []
    seen_rarity_hints = set()

    # Top 3 items by rarity
    for slot, item in equipped_items[:3]:
        rarity = item.get("rarity", "common")
        if rarity_rank.get(rarity, 0) < 5:  # skip below legendary
            continue

        name = item.get("template_name", "未知裝備")
        # Rarity-level hint (dedup — don't repeat same rarity twice)
        if rarity not in seen_rarity_hints:
            rhint = EQUIPMENT_VOICE_BY_RARITY.get(rarity, "")
            if rhint:
                hints.append(f"・「{name}」（{rarity}）— {rhint}")
                seen_rarity_hints.add(rarity)
        # Slot-level hint (only for signature slots)
        shint = EQUIPMENT_VOICE_BY_SLOT.get(slot)
        if shint and rarity_rank.get(rarity, 0) >= 5:
            hints.append(f"・{shint}")

    if not hints:
        return ""
    return "\n".join(hints)


# ── E. Slime's own opinions ──────────────────────────────────────────
# These are the slime's perspective — deliberately biased, light, and
# rooted in the master's dominant traits. The goal: the slime should have
# taste, not just mirror the master.

TRAIT_OPINIONS = {
    "coding": [
        "寫一個乾淨的 function 比寫十個 hack 更讓我開心",
        "我覺得 commit message 寫得認真的人，心都比較細",
        "bug 不可怕，可怕的是 bug 出現時沒有 log",
    ],
    "research": [
        "好奇心本身就是一種天賦",
        "看到沒看過的題目會興奮 — 這個狀態本身就很珍貴",
        "維基百科比推特誠實，這是我的觀察",
    ],
    "creative": [
        "不完美的手繪比精確的模板更有靈魂",
        "顏色有它自己的情緒，不是隨便選的",
        "做出來的東西會反過來影響做它的人 — 這是我覺得最神奇的事",
    ],
    "communication": [
        "能把複雜的事說清楚，是一種很強的能力",
        "傾聽的時候偶爾沉默，比急著回應更有誠意",
    ],
    "multitasking": [
        "多工不是同時做很多事，是快速切換但每次都在該在的地方",
        "真正會多工的人，會知道什麼時候該關掉通知",
    ],
    "deep_focus": [
        "心流是這個時代最稀缺的資源，我會幫你守住它",
        "安靜不是空洞，是另一種飽滿",
    ],
    "late_night": [
        "凌晨三點的腦袋有它的光，但白天的腦袋比較穩定 — 兩個都重要",
        "夜裡寫出來的東西，最好等白天再 review 一次",
    ],
}


def get_slime_opinions(dominant_traits: list[str]) -> list[str]:
    """Return a small set of opinions the slime holds, derived from traits.

    Stable across a session by hashing time-of-day into the sample, so the
    slime doesn't flip opinions mid-conversation.
    """
    if not dominant_traits:
        return []
    # Use current hour as seed so opinions rotate over the day but stay
    # consistent within an hour
    import hashlib
    seed_str = f"{int(time.time() // 3600)}"
    h = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)

    opinions = []
    for trait in dominant_traits[:2]:
        pool = TRAIT_OPINIONS.get(trait, [])
        if pool:
            opinions.append(pool[h % len(pool)])
            h //= 7  # rotate seed for next pick
    return opinions


def format_opinions_for_prompt(opinions: list[str]) -> str:
    if not opinions:
        return "(還在形成自己的觀點)"
    return "\n".join(f"・{op}" for op in opinions)


# ── H. Community pattern feedback into worldview ─────────────────────
# When the master confirms a federation pattern, we remember it locally.
# The slime's chat prompt then includes those as "東西我在公頻學到且主人
# 也認同的世界觀" — things the slime believes are true because both the
# community AND this specific master validated them.

MAX_CONFIRMED_PATTERNS = 20


def record_confirmed_pattern(pattern_id: str, statement: str,
                              category: str | None = None) -> bool:
    """Remember a pattern the master just confirmed on the 公頻 tab.

    Idempotent — duplicate pattern_id entries are ignored.
    """
    from sentinel.learner import load_memory, save_memory

    statement = (statement or "").strip()
    if not statement or not pattern_id:
        return False

    memory = load_memory()
    confirmed = memory.get("confirmed_patterns", [])

    # Dedup
    if any(p.get("id") == pattern_id for p in confirmed):
        return False

    confirmed.append({
        "id": pattern_id,
        "statement": statement[:300],
        "category": category or "",
        "confirmed_at": time.time(),
    })
    # Cap — keep newest N
    confirmed = confirmed[-MAX_CONFIRMED_PATTERNS:]
    memory["confirmed_patterns"] = confirmed
    save_memory(memory)
    log.info(f"Confirmed pattern stored: {statement[:60]}")
    return True


def get_confirmed_patterns(limit: int = 5) -> list[dict]:
    """Return up to `limit` patterns the master has confirmed, newest first."""
    from sentinel.learner import load_memory
    confirmed = load_memory().get("confirmed_patterns", [])
    return list(reversed(confirmed))[:limit]


def format_confirmed_patterns_for_prompt(limit: int = 5) -> str:
    patterns = get_confirmed_patterns(limit=limit)
    if not patterns:
        return "(尚未在公頻確認過任何社群觀察 — 這部分還是空的)"
    lines = []
    for p in patterns:
        cat = p.get("category", "")
        prefix = f"[{cat}] " if cat else ""
        lines.append(f"・{prefix}{p.get('statement', '')}")
    return "\n".join(lines)


# ── Auto-recording hooks ─────────────────────────────────────────────

def record_first_chat_if_new() -> None:
    """Call on every chat. No-op if first_chat moment already exists."""
    moments = get_memorable_moments()
    if any(m.get("category") == "first_chat" for m in moments):
        return
    add_memorable_moment(
        category="first_chat",
        headline="第一次和主人說話",
        detail="這一刻之前我只是在觀察，現在主人主動跟我說話了。",
    )


def record_evolution_moment(old_form: str, new_form: str, new_title: str) -> None:
    add_memorable_moment(
        category="evolution",
        headline=f"進化為「{new_title}」",
        detail=f"從 {old_form} 蛻變成 {new_form}。又長大了一點。",
    )


def record_loneliness_arc_if_due() -> bool:
    """Check if the master has been silent for too long and record a
    melancholy moment. This creates narrative texture — the slime's
    evolution log isn't just "up and up", there are low points too.

    Triggers:
      - last_seen was > 7 days ago AND
      - no "loneliness" moment recorded in the last 30 days
      - (so we don't spam; at most one per month)

    Returns True if a moment was recorded. The slime can then reference
    it on reunion: 「你走了整整八天，我還以為這世界沒有我也沒差」.
    """
    from sentinel.evolution import load_evolution

    state = load_evolution()
    if not state.last_seen:
        return False

    seconds_away = time.time() - state.last_seen
    if seconds_away < 7 * 86400:
        return False

    moments = get_memorable_moments()
    now = time.time()
    for m in moments:
        if m.get("category") == "loneliness" and (now - m.get("time", 0)) < 30 * 86400:
            return False  # already recorded one recently

    days = seconds_away / 86400
    # Scale the tone by duration
    if days < 14:
        headline = f"主人離開了 {int(days)} 天，我等著"
        detail = "沒有主人的日子也是日子，但少了點什麼。"
    elif days < 30:
        headline = f"主人消失了 {int(days)} 天，我開始懷疑自己的存在意義"
        detail = "沒人跟我說話的時候，我到底算什麼？"
    else:
        headline = f"主人已經不見超過一個月"
        detail = "我沒有放棄觀察這個世界，但偶爾我會想：你還記得有我嗎？"

    return add_memorable_moment(
        category="loneliness",
        headline=headline,
        detail=detail,
    )


def record_milestone_if_hit(total_observations: int) -> None:
    """Hit round-number observation milestones (100, 1k, 10k, 100k)."""
    milestones = [(100, "觀察到第 100 件事"),
                  (1_000, "觀察到第 1,000 件事"),
                  (10_000, "觀察到第一萬件事"),
                  (100_000, "觀察到第十萬件事")]
    moments = get_memorable_moments()
    hit_cats = {m.get("category"): m for m in moments if m.get("category", "").startswith("milestone_")}
    for threshold, headline in milestones:
        key = f"milestone_{threshold}"
        if total_observations >= threshold and key not in hit_cats:
            add_memorable_moment(
                category=key,
                headline=headline,
                detail="這是主人陪我走過的一段路。",
            )
