"""Equipment (裝備) system — gacha drops, avatar customization, and marketplace.

All users (BYOK and quota) participate in the equipment economy.
Equipment drops are earned by behavior; trading uses 5888 wallet points.

12 equipment slots with visual + buff effects.
"""
import json
import time
import random
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.wallet.equipment")

EQUIPMENT_FILE = Path.home() / ".hermes" / "aislime_equipment.json"

# ── 12 Slots ─────────────────────────────────────────────────────────

SLOTS = [
    "helmet",       # 頭盔
    "eyes",         # 眼睛
    "mouth",        # 嘴巴
    "left_hand",    # 左手
    "right_hand",   # 右手
    "skin",         # 皮膚
    "background",   # 背景
    "core",         # 體內晶核
    "mount",        # 底座/載具
    "vfx",          # 環繞特效
    "drone",        # 跟隨小精靈
    "title",        # 動態稱號
]

SLOT_NAMES_ZH = {
    "helmet": "頭盔", "eyes": "眼睛", "mouth": "嘴巴",
    "left_hand": "左手", "right_hand": "右手", "skin": "皮膚",
    "background": "背景", "core": "晶核", "mount": "載具",
    "vfx": "特效", "drone": "精靈", "title": "稱號",
}

# ── Rarity ───────────────────────────────────────────────────────────

# 7 tiers (aligned with 7 evolution stages)
RARITIES = [
    "common",       # 普通 — Slime
    "uncommon",     # 優良 — Slime+
    "rare",         # 稀有 — Named Slime
    "epic",         # 史詩 — Majin
    "legendary",    # 傳說 — Demon Lord Seed
    "mythic",       # 神話 — True Demon Lord
    "ultimate",     # 究極 — Ultimate Slime
]

RARITY_ZH = {
    "common": "普通", "uncommon": "優良", "rare": "稀有",
    "epic": "史詩", "legendary": "傳說", "mythic": "神話",
    "ultimate": "究極",
}

RARITY_COLORS = {
    "common": "#aaaaaa", "uncommon": "#2ed573", "rare": "#1e90ff",
    "epic": "#a855f7", "legendary": "#ffa502", "mythic": "#ff4757",
    "ultimate": "#ffd700",
}

RARITY_STARS = {
    "common": "★", "uncommon": "★★", "rare": "★★★",
    "epic": "★★★★", "legendary": "★★★★★", "mythic": "★★★★★★",
    "ultimate": "★★★★★★★",
}

# Drop weights per trigger type
# observation: mostly low tier
# learning: slightly better
# evolution: guaranteed, tier matches evolution stage
RARITY_WEIGHTS = {
    "observation": {"common": 50, "uncommon": 30, "rare": 12, "epic": 5,
                    "legendary": 2, "mythic": 0.8, "ultimate": 0.2},
    "learning":    {"common": 30, "uncommon": 30, "rare": 20, "epic": 12,
                    "legendary": 5, "mythic": 2, "ultimate": 1},
    "default":     {"common": 40, "uncommon": 25, "rare": 18, "epic": 10,
                    "legendary": 5, "mythic": 1.5, "ultimate": 0.5},
}

# Floor price (guaranteed buyback) in 5888 points
RARITY_FLOOR_PRICE = {
    "common": 5, "uncommon": 15, "rare": 50,
    "epic": 200, "legendary": 1000, "mythic": 5000,
    "ultimate": 25000,
}

# Synthesis: 3 same-rarity items → 1 item of next rarity
SYNTHESIS_COST = 3  # items needed

# ── Equipment Templates ──────────────────────────────────────────────
# Each template defines an item that can drop. Visual = sprite/pixel layers.

EQUIPMENT_POOL = [
    # ── Helmet (頭盔) ────────────────────────────────────────────────
    {"slot": "helmet", "name": "駭客護目鏡", "rarity": "common",
     "visual": "hacker_goggles", "buff": None, "desc": "基本的賽博龐克護目鏡"},
    {"slot": "helmet", "name": "貓耳帽", "rarity": "uncommon",
     "visual": "cat_ears", "buff": None, "desc": "可愛即正義"},
    {"slot": "helmet", "name": "大賢者之冠", "rarity": "rare",
     "visual": "sage_crown", "buff": {"exp_multiplier": 0.05},
     "desc": "大賢者的智慧，經驗 +5%"},
    {"slot": "helmet", "name": "魔王的角", "rarity": "epic",
     "visual": "demon_horns", "buff": {"exp_multiplier": 0.08},
     "desc": "魔王之力，經驗 +8%"},
    {"slot": "helmet", "name": "龍骨頭冠", "rarity": "legendary",
     "visual": "dragon_skull_crown", "buff": {"drop_luck": 0.1},
     "desc": "裝備掉落運氣 +10%"},
    {"slot": "helmet", "name": "暴風龍之冠", "rarity": "mythic",
     "visual": "veldora_crown", "buff": {"exp_multiplier": 0.15, "drop_luck": 0.1},
     "desc": "暴風龍的祝福，經驗 +15%，運氣 +10%"},
    {"slot": "helmet", "name": "虛數之冕", "rarity": "ultimate",
     "visual": "void_diadem", "buff": {"exp_multiplier": 0.2, "drop_luck": 0.15},
     "desc": "超越世界的王冠，經驗 +20%，運氣 +15%"},

    # ── Eyes (眼睛) ──────────────────────────────────────────────────
    {"slot": "eyes", "name": "貓瞳", "rarity": "common",
     "visual": "cat_eyes", "buff": None, "desc": "可愛的貓咪瞳孔"},
    {"slot": "eyes", "name": "星空瞳", "rarity": "uncommon",
     "visual": "starry_eyes", "buff": None, "desc": "映照著星空的瞳孔"},
    {"slot": "eyes", "name": "大賢者之眼", "rarity": "rare",
     "visual": "sage_eyes", "buff": {"affinity_boost": 0.1},
     "desc": "洞察一切，親和度成長 +10%"},
    {"slot": "eyes", "name": "千里眼", "rarity": "epic",
     "visual": "clairvoyance", "buff": {"affinity_boost": 0.15},
     "desc": "看穿本質，親和度成長 +15%"},
    {"slot": "eyes", "name": "魔王之瞳", "rarity": "legendary",
     "visual": "demon_lord_eyes", "buff": {"exp_multiplier": 0.1, "affinity_boost": 0.1},
     "desc": "威壓之眼，經驗 +10%，親和度 +10%"},

    # ── Mouth (嘴巴) ─────────────────────────────────────────────────
    {"slot": "mouth", "name": "微笑", "rarity": "common",
     "visual": "smile", "buff": None, "desc": "友善的微笑"},
    {"slot": "mouth", "name": "貓嘴", "rarity": "uncommon",
     "visual": "cat_mouth", "buff": None, "desc": ":3"},
    {"slot": "mouth", "name": "銳齒", "rarity": "rare",
     "visual": "sharp_teeth", "buff": None, "desc": "捕食者的獠牙"},

    # ── Skin (皮膚) ──────────────────────────────────────────────────
    {"slot": "skin", "name": "經典藍", "rarity": "common",
     "visual": "classic_blue", "buff": None, "desc": "利姆路經典配色"},
    {"slot": "skin", "name": "暴風龍紫", "rarity": "uncommon",
     "visual": "veldora_purple", "buff": None, "desc": "暴風龍的力量"},
    {"slot": "skin", "name": "炎魔紅", "rarity": "rare",
     "visual": "flame_red", "buff": {"exp_multiplier": 0.05},
     "desc": "伊芙利特之色，經驗 +5%"},
    {"slot": "skin", "name": "黃金之體", "rarity": "legendary",
     "visual": "golden_body", "buff": {"exp_multiplier": 0.15},
     "desc": "經驗值獲得 +15%"},
    {"slot": "skin", "name": "虛空之體", "rarity": "ultimate",
     "visual": "void_body", "buff": {"exp_multiplier": 0.25, "drop_luck": 0.15,
                                      "trade_fee_reduction": 0.05},
     "desc": "究極存在。經驗+25%、運氣+15%、手續費-5%"},

    # ── Core (體內晶核) ──────────────────────────────────────────────
    {"slot": "core", "name": "基本晶核", "rarity": "common",
     "visual": "basic_core", "buff": None, "desc": "史萊姆的基本核心"},
    {"slot": "core", "name": "強化晶核", "rarity": "uncommon",
     "visual": "enhanced_core", "buff": {"exp_multiplier": 0.03},
     "desc": "稍微強化的核心，經驗 +3%"},
    {"slot": "core", "name": "交易晶核", "rarity": "rare",
     "visual": "trade_core", "buff": {"trade_fee_reduction": 0.02},
     "desc": "市場手續費 -2%（10%→8%）"},
    {"slot": "core", "name": "魔王晶核", "rarity": "epic",
     "visual": "demon_core", "buff": {"exp_multiplier": 0.1, "trade_fee_reduction": 0.02},
     "desc": "經驗 +10%，手續費 -2%"},
    {"slot": "core", "name": "虛無之王晶核", "rarity": "legendary",
     "visual": "void_core", "buff": {"exp_multiplier": 0.12, "trade_fee_reduction": 0.03},
     "desc": "經驗 +12%，手續費 -3%"},
    {"slot": "core", "name": "真魔王晶核", "rarity": "mythic",
     "visual": "true_demon_core", "buff": {"exp_multiplier": 0.18,
                                            "trade_fee_reduction": 0.04},
     "desc": "經驗 +18%，手續費 -4%"},

    # ── Left Hand (左手) ─────────────────────────────────────────────
    {"slot": "left_hand", "name": "程式之書", "rarity": "uncommon",
     "visual": "code_book", "buff": {"affinity_boost": 0.05},
     "desc": "古老的程式魔法書，親和度 +5%"},
    {"slot": "left_hand", "name": "學者之卷", "rarity": "rare",
     "visual": "scholar_scroll", "buff": {"exp_multiplier": 0.05},
     "desc": "蘊含知識的卷軸，經驗 +5%"},

    # ── Right Hand (右手) ────────────────────────────────────────────
    {"slot": "right_hand", "name": "像素魔劍", "rarity": "common",
     "visual": "pixel_sword", "buff": None, "desc": "8-bit 風格的魔劍"},
    {"slot": "right_hand", "name": "魔劍・暴風", "rarity": "rare",
     "visual": "storm_blade", "buff": {"drop_luck": 0.05},
     "desc": "暴風之力，掉落運氣 +5%"},
    {"slot": "right_hand", "name": "神劍・天叢雲", "rarity": "mythic",
     "visual": "kusanagi", "buff": {"drop_luck": 0.12, "exp_multiplier": 0.1},
     "desc": "神話之劍，運氣 +12%，經驗 +10%"},

    # ── Background (背景) ────────────────────────────────────────────
    {"slot": "background", "name": "夜晚都市", "rarity": "common",
     "visual": "night_city", "buff": None, "desc": "霓虹閃爍的都市夜景"},
    {"slot": "background", "name": "朱拉大森林", "rarity": "uncommon",
     "visual": "jura_forest", "buff": None, "desc": "利姆路的發源地"},
    {"slot": "background", "name": "魔王城", "rarity": "epic",
     "visual": "demon_castle", "buff": {"exp_multiplier": 0.08},
     "desc": "利姆路的魔王城，經驗 +8%"},
    {"slot": "background", "name": "星空深淵", "rarity": "legendary",
     "visual": "starry_abyss", "buff": {"drop_luck": 0.08},
     "desc": "無盡的星空，運氣 +8%"},

    # ── Mount (載具) ─────────────────────────────────────────────────
    {"slot": "mount", "name": "懸浮滑板", "rarity": "uncommon",
     "visual": "hoverboard", "buff": None, "desc": "漂浮在空中的滑板"},
    {"slot": "mount", "name": "暴風龍座騎", "rarity": "epic",
     "visual": "veldora_mount", "buff": {"exp_multiplier": 0.1},
     "desc": "暴風龍的加護，經驗 +10%"},
    {"slot": "mount", "name": "虛空戰艦", "rarity": "mythic",
     "visual": "void_ship", "buff": {"exp_multiplier": 0.15, "drop_luck": 0.08},
     "desc": "跨越次元的戰艦，經驗 +15%，運氣 +8%"},

    # ── VFX (環繞特效) ───────────────────────────────────────────────
    {"slot": "vfx", "name": "駭客代碼流", "rarity": "uncommon",
     "visual": "code_rain", "buff": None, "desc": "駭客帝國風格的代碼雨"},
    {"slot": "vfx", "name": "閃電火花", "rarity": "rare",
     "visual": "lightning_sparks", "buff": None, "desc": "電光閃爍的特效"},
    {"slot": "vfx", "name": "櫻花飄落", "rarity": "epic",
     "visual": "sakura_fall", "buff": {"affinity_boost": 0.1},
     "desc": "櫻花紛飛，親和度 +10%"},
    {"slot": "vfx", "name": "轉生之光", "rarity": "mythic",
     "visual": "reincarnation_light", "buff": {"exp_multiplier": 0.18},
     "desc": "轉生的祝福，經驗 +18%"},
    {"slot": "vfx", "name": "虛空裂縫", "rarity": "ultimate",
     "visual": "void_rift", "buff": {"exp_multiplier": 0.2, "drop_luck": 0.15},
     "desc": "次元裂隙，經驗 +20%，運氣 +15%"},

    # ── Drone (跟隨精靈) ─────────────────────────────────────────────
    {"slot": "drone", "name": "觀測小精靈", "rarity": "uncommon",
     "visual": "observer_sprite", "buff": None, "desc": "跟隨的小光球"},
    {"slot": "drone", "name": "商人精靈", "rarity": "rare",
     "visual": "merchant_sprite", "buff": {"trade_fee_reduction": 0.02},
     "desc": "市場手續費 -2%"},
    {"slot": "drone", "name": "社群無人機", "rarity": "epic",
     "visual": "social_drone", "buff": {"social_share_bonus": 50},
     "desc": "分享時額外獲得 50 點"},
    {"slot": "drone", "name": "大賢者・拉斐爾", "rarity": "ultimate",
     "visual": "raphael_drone",
     "buff": {"exp_multiplier": 0.2, "affinity_boost": 0.2, "drop_luck": 0.1},
     "desc": "大賢者究極型態。經驗+20%、親和度+20%、運氣+10%"},

    # ── Title (動態稱號) ─────────────────────────────────────────────
    {"slot": "title", "name": "新手冒險者", "rarity": "common",
     "visual": None, "buff": None, "desc": "每個人都從這裡開始"},
    {"slot": "title", "name": "勤勉觀察者", "rarity": "uncommon",
     "visual": None, "buff": {"exp_multiplier": 0.03},
     "desc": "認真觀察的史萊姆，經驗 +3%"},
    {"slot": "title", "name": "傳說級打工人", "rarity": "rare",
     "visual": None, "buff": {"exp_multiplier": 0.05},
     "desc": "認真打工的稱號，經驗 +5%"},
    {"slot": "title", "name": "命運之子", "rarity": "epic",
     "visual": None, "buff": {"drop_luck": 0.08},
     "desc": "被命運眷顧，掉落運氣 +8%"},
    {"slot": "title", "name": "暴風龍的摯友", "rarity": "legendary",
     "visual": None, "buff": {"exp_multiplier": 0.12, "drop_luck": 0.05},
     "desc": "暴風龍的認可，經驗 +12%，運氣 +5%"},
    {"slot": "title", "name": "真・魔王", "rarity": "mythic",
     "visual": None, "buff": {"exp_multiplier": 0.18, "trade_fee_reduction": 0.03},
     "desc": "統治一方的魔王，經驗 +18%，手續費 -3%"},
    {"slot": "title", "name": "世界的支配者", "rarity": "ultimate",
     "visual": None, "buff": {"exp_multiplier": 0.25, "drop_luck": 0.2,
                               "trade_fee_reduction": 0.05},
     "desc": "站在頂點的存在。經驗+25%、運氣+20%、手續費-5%"},
]


# ── Data Model ───────────────────────────────────────────────────────

@dataclass
class OwnedItem:
    """An item in user's inventory."""
    item_id: str          # unique ID for this specific instance
    template_name: str    # references EQUIPMENT_POOL entry name
    slot: str
    rarity: str
    acquired_at: float
    acquired_via: str     # "drop", "purchase", "achievement"
    equipped: bool = False
    listed_price: int = 0  # 0 = not listed for sale


@dataclass
class EquipmentState:
    """Full equipment state for a user."""
    inventory: list[dict] = field(default_factory=list)
    equipped: dict[str, str] = field(default_factory=dict)  # slot → item_id
    total_drops: int = 0
    last_drop_time: float = 0
    drop_cooldown: float = 0  # earned through play, reset periodically


def load_equipment() -> EquipmentState:
    if EQUIPMENT_FILE.exists():
        try:
            data = json.loads(EQUIPMENT_FILE.read_text(encoding="utf-8"))
            return EquipmentState(**{
                k: v for k, v in data.items()
                if k in EquipmentState.__dataclass_fields__
            })
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    return EquipmentState()


def save_equipment(state: EquipmentState):
    EQUIPMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    EQUIPMENT_FILE.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Drop Logic ───────────────────────────────────────────────────────

# Drop triggers: observations, learnings, evolution milestones
DROP_CHANCES = {
    "observation_100": 0.3,   # Every 100 observations: 30% chance
    "learning": 0.5,          # Every learning: 50% chance
    "evolution": 1.0,         # Every tier-up: guaranteed drop
    "daily_login": 0.2,       # First session of the day: 20% chance
    "share": 0.4,             # After sharing: 40% chance
}


def _roll_rarity(trigger: str = "default", min_rarity: str = "") -> str:
    """Weighted random rarity selection.

    trigger: determines weight table (observation, learning, default)
    min_rarity: guaranteed minimum rarity (for evolution milestones)
    """
    weights = RARITY_WEIGHTS.get(trigger, RARITY_WEIGHTS["default"])

    # Apply minimum rarity floor
    if min_rarity and min_rarity in RARITIES:
        min_idx = RARITIES.index(min_rarity)
        weights = {r: w for r, w in weights.items()
                   if RARITIES.index(r) >= min_idx}

    if not weights:
        return min_rarity or "common"

    items = list(weights.items())
    total = sum(w for _, w in items)
    roll = random.random() * total
    cumulative = 0
    for rarity, weight in items:
        cumulative += weight
        if roll <= cumulative:
            return rarity
    return items[-1][0]


def _pick_item(rarity: str, slot: str = "") -> Optional[dict]:
    """Pick a random item from pool matching rarity (and optionally slot)."""
    candidates = [
        item for item in EQUIPMENT_POOL
        if item["rarity"] == rarity
        and (not slot or item["slot"] == slot)
    ]
    if not candidates:
        # Fallback: any item of this rarity
        candidates = [item for item in EQUIPMENT_POOL if item["rarity"] == rarity]
    return random.choice(candidates) if candidates else None


# Map evolution form → minimum rarity for guaranteed drops
EVOLUTION_MIN_RARITY = {
    "Slime": "common",
    "Slime+": "uncommon",
    "Named Slime": "rare",
    "Majin": "epic",
    "Demon Lord Seed": "legendary",
    "True Demon Lord": "mythic",
    "Ultimate Slime": "ultimate",
}


def try_drop(state: EquipmentState, trigger: str,
             evolution_form: str = "") -> Optional[dict]:
    """Attempt an equipment drop based on trigger type.

    trigger types:
      "observation_100" — every 100 observations (30%)
      "learning"        — each distillation success (50%)
      "evolution"       — tier-up, guaranteed + min rarity = evolution tier
      "daily_login"     — first session of day (20%)
      "share"           — after social share (40%)

    Returns the dropped item dict if successful, None otherwise.
    """
    chance = DROP_CHANCES.get(trigger, 0.1)

    # Evolution milestones are guaranteed
    if trigger == "evolution":
        chance = 1.0

    # Apply drop luck bonus from equipped items
    luck = get_drop_luck_bonus(state)
    chance = min(1.0, chance + luck)

    if random.random() > chance:
        return None

    # Determine minimum rarity based on trigger
    min_rarity = ""
    if trigger == "evolution" and evolution_form:
        min_rarity = EVOLUTION_MIN_RARITY.get(evolution_form, "")

    rarity = _roll_rarity(trigger=trigger, min_rarity=min_rarity)
    template = _pick_item(rarity)
    if not template:
        return None

    item_id = f"item_{int(time.time())}_{random.randint(1000,9999)}"
    item = OwnedItem(
        item_id=item_id,
        template_name=template["name"],
        slot=template["slot"],
        rarity=template["rarity"],
        acquired_at=time.time(),
        acquired_via=trigger,
    )

    state.inventory.append(asdict(item))
    state.total_drops += 1
    state.last_drop_time = time.time()
    save_equipment(state)

    log.info(f"Equipment drop! [{RARITY_ZH[rarity]}] {template['name']}")

    return {
        "item_id": item_id,
        "name": template["name"],
        "slot": template["slot"],
        "slot_zh": SLOT_NAMES_ZH.get(template["slot"], template["slot"]),
        "rarity": rarity,
        "rarity_zh": RARITY_ZH[rarity],
        "rarity_color": RARITY_COLORS[rarity],
        "rarity_stars": RARITY_STARS.get(rarity, "★"),
        "desc": template["desc"],
        "buff": template.get("buff"),
        "visual": template.get("visual"),
    }


# ── Synthesis (合成) ─────────────────────────────────────────────────

def synthesize(state: EquipmentState, item_ids: list[str]) -> Optional[dict]:
    """Combine 3 same-rarity items into 1 higher-rarity item.

    The 3 input items are consumed. Returns the new item or None on failure.
    """
    if len(item_ids) != SYNTHESIS_COST:
        return None

    items = []
    for iid in item_ids:
        item = next((i for i in state.inventory if i["item_id"] == iid), None)
        if not item:
            return None
        if item.get("equipped"):
            return None  # Can't consume equipped items
        if item.get("listed_price", 0) > 0:
            return None  # Can't consume listed items
        items.append(item)

    # All must be same rarity
    rarities = {i["rarity"] for i in items}
    if len(rarities) != 1:
        return None

    current_rarity = items[0]["rarity"]
    current_idx = RARITIES.index(current_rarity)
    if current_idx >= len(RARITIES) - 1:
        return None  # Already max rarity

    next_rarity = RARITIES[current_idx + 1]

    # Remove consumed items
    consumed_ids = {i["item_id"] for i in items}
    state.inventory = [i for i in state.inventory if i["item_id"] not in consumed_ids]

    # Pick a random item of the next rarity
    template = _pick_item(next_rarity)
    if not template:
        return None

    item_id = f"synth_{int(time.time())}_{random.randint(1000,9999)}"
    new_item = OwnedItem(
        item_id=item_id,
        template_name=template["name"],
        slot=template["slot"],
        rarity=next_rarity,
        acquired_at=time.time(),
        acquired_via="synthesis",
    )

    state.inventory.append(asdict(new_item))
    save_equipment(state)

    log.info(f"Synthesis! 3x {RARITY_ZH[current_rarity]} → [{RARITY_ZH[next_rarity]}] {template['name']}")

    return {
        "item_id": item_id,
        "name": template["name"],
        "slot": template["slot"],
        "slot_zh": SLOT_NAMES_ZH.get(template["slot"], template["slot"]),
        "rarity": next_rarity,
        "rarity_zh": RARITY_ZH[next_rarity],
        "rarity_color": RARITY_COLORS[next_rarity],
        "rarity_stars": RARITY_STARS.get(next_rarity, "★"),
        "desc": template["desc"],
        "buff": template.get("buff"),
        "consumed": [i["template_name"] for i in items],
    }


# ── Equip / Unequip ─────────────────────────────────────────────────

def equip_item(state: EquipmentState, item_id: str) -> bool:
    """Equip an item from inventory."""
    item = next((i for i in state.inventory if i["item_id"] == item_id), None)
    if not item:
        return False

    slot = item["slot"]

    # Unequip current item in that slot
    old_id = state.equipped.get(slot)
    if old_id:
        for inv_item in state.inventory:
            if inv_item["item_id"] == old_id:
                inv_item["equipped"] = False

    # Equip new item
    item["equipped"] = True
    state.equipped[slot] = item_id
    save_equipment(state)
    return True


def unequip_slot(state: EquipmentState, slot: str) -> bool:
    """Unequip whatever is in a slot."""
    item_id = state.equipped.pop(slot, None)
    if item_id:
        for item in state.inventory:
            if item["item_id"] == item_id:
                item["equipped"] = False
        save_equipment(state)
        return True
    return False


# ── Buff Calculation ─────────────────────────────────────────────────

def get_active_buffs(state: EquipmentState) -> dict:
    """Calculate total buffs from all equipped items."""
    buffs: dict[str, float] = {}

    for slot, item_id in state.equipped.items():
        item = next((i for i in state.inventory if i["item_id"] == item_id), None)
        if not item:
            continue

        template = next(
            (t for t in EQUIPMENT_POOL if t["name"] == item["template_name"]),
            None,
        )
        if not template or not template.get("buff"):
            continue

        for buff_key, buff_val in template["buff"].items():
            buffs[buff_key] = buffs.get(buff_key, 0) + buff_val

    return buffs


def get_exp_multiplier(state: EquipmentState) -> float:
    """Get experience multiplier from equipment buffs."""
    buffs = get_active_buffs(state)
    return 1.0 + buffs.get("exp_multiplier", 0)


def get_drop_luck_bonus(state: EquipmentState) -> float:
    """Get drop luck bonus from equipment (0.0 = no bonus, 0.15 = +15%)."""
    buffs = get_active_buffs(state)
    return buffs.get("drop_luck", 0)


def get_affinity_boost(state: EquipmentState) -> float:
    """Get affinity growth bonus from equipment."""
    buffs = get_active_buffs(state)
    return buffs.get("affinity_boost", 0)


def get_trade_fee_percent(state: EquipmentState) -> float:
    """Get actual marketplace fee % after equipment discounts.

    Base: 10%. Equipment can reduce it. Floor: 3%.
    """
    from sentinel import config
    base = config.MARKETPLACE_FEE_PERCENT
    buffs = get_active_buffs(state)
    reduction_pct = buffs.get("trade_fee_reduction", 0) * 100  # e.g. 0.05 → 5
    actual = max(3.0, base - reduction_pct)  # Floor at 3%
    return actual


# ── Marketplace Helpers ──────────────────────────────────────────────

MIN_LIST_PRICE = 10  # Flat minimum — no per-rarity floor


def list_for_sale(state: EquipmentState, item_id: str, price: int) -> bool:
    """List an item for sale at a given price (in 5888 points).

    Sellers set any price they want (≥ 10 pt) — the market decides value.
    """
    item = next((i for i in state.inventory if i["item_id"] == item_id), None)
    if not item:
        return False
    if item.get("equipped"):
        return False  # Must unequip first
    if price < MIN_LIST_PRICE:
        return False

    item["listed_price"] = price
    save_equipment(state)
    return True


def delist(state: EquipmentState, item_id: str) -> bool:
    """Remove an item from sale."""
    item = next((i for i in state.inventory if i["item_id"] == item_id), None)
    if not item:
        return False
    item["listed_price"] = 0
    save_equipment(state)
    return True


def get_floor_price(rarity: str) -> int:
    """Get the guaranteed buyback price for a rarity tier."""
    return RARITY_FLOOR_PRICE.get(rarity, 5)


def get_inventory_summary(state: EquipmentState) -> dict:
    """Summary stats for UI display."""
    by_rarity = {}
    for item in state.inventory:
        r = item.get("rarity", "common")
        by_rarity[r] = by_rarity.get(r, 0) + 1

    equipped_names = {}
    for slot, item_id in state.equipped.items():
        item = next((i for i in state.inventory if i["item_id"] == item_id), None)
        if item:
            equipped_names[slot] = item["template_name"]

    return {
        "total_items": len(state.inventory),
        "by_rarity": by_rarity,
        "equipped": equipped_names,
        "total_drops": state.total_drops,
        "active_buffs": get_active_buffs(state),
    }
