"""Pydantic models for equipment endpoints."""
from pydantic import BaseModel
from typing import Optional

VALID_SLOTS = [
    "helmet", "eyes", "mouth", "left_hand", "right_hand", "skin",
    "background", "core", "mount", "vfx", "drone", "title",
]

VALID_RARITIES = [
    "common", "uncommon", "rare", "epic", "legendary", "mythic", "ultimate",
]

# Max buff values per rarity (prevent overpowered community items)
MAX_BUFF_VALUES = {
    "common":    {"exp_multiplier": 0.03, "drop_luck": 0.02, "affinity_boost": 0.05, "trade_fee_reduction": 0.01},
    "uncommon":  {"exp_multiplier": 0.05, "drop_luck": 0.05, "affinity_boost": 0.08, "trade_fee_reduction": 0.02},
    "rare":      {"exp_multiplier": 0.08, "drop_luck": 0.08, "affinity_boost": 0.10, "trade_fee_reduction": 0.03},
    "epic":      {"exp_multiplier": 0.12, "drop_luck": 0.10, "affinity_boost": 0.15, "trade_fee_reduction": 0.03},
    "legendary": {"exp_multiplier": 0.15, "drop_luck": 0.12, "affinity_boost": 0.15, "trade_fee_reduction": 0.04},
    "mythic":    {"exp_multiplier": 0.20, "drop_luck": 0.15, "affinity_boost": 0.20, "trade_fee_reduction": 0.05},
    "ultimate":  {"exp_multiplier": 0.25, "drop_luck": 0.20, "affinity_boost": 0.25, "trade_fee_reduction": 0.06},
}


class SubmitRequest(BaseModel):
    name: str
    slot: str
    rarity: str
    visual: str = ""
    buff: Optional[dict] = None
    description: str = ""
    image_id: Optional[str] = None


class SubmissionResponse(BaseModel):
    id: str
    name: str
    slot: str
    rarity: str
    visual: str
    buff: Optional[dict]
    description: str
    image_url: str
    status: str
    vote_count: int
    vote_threshold: int
    creator_name: str
    created_at: str
    user_voted: bool = False
