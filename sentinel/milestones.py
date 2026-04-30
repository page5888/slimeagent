"""Manifesto-aligned timeline of relationship milestones.

Why this exists separately: the welcome modal (PR #69) makes the
day-count promise once, then disappears. The timeline widget makes
that same promise **visible every time the master opens the home
tab**. That repeated visibility is the
"this isn't just another chatbot" signal — no other AI product
has a timeline because no other AI product's pitch IS the
timeline.

Milestones are intentionally **declarative** here so future code
can list them, query them, and decorate the home tab without
digging through GUI plumbing. Copy is held next to the data so
language tweaks don't require touching widget code.

The remaining numbers (D1/D7/D30/D100/D365) are **scaffolding tier**:
calendar-driven trips kept because their underlying behaviors already
ship and serve as the relationship's initial common clock.

Per ADR docs/decisions/2026-04-29-emergent-milestones.md, scripted
future milestones (D60/D180/D300/D14/D21/etc.) are explicitly **not**
the path forward — they violate manifesto 原則 1 第 9 行 by deciding
what happens on day N for everyone instead of letting it emerge from
each master's actual relationship.

Don't add new entries here. Anything new must be Slime-emergent —
recorded into identity.memorable_moments and surfaced via
compute_emergent_nodes() below. The scaffolding tier itself is
provisional and may be revisited by a future ADR.

The emergent half (compute_emergent_nodes / select_strip_emergent /
EmergentNode) maps already-recorded memorable_moments onto timeline
positions so the home strip can render them as nodes between
scaffolding stations. No new triggers ship here — first_chat,
naming, evolution, skill, and loneliness arc are all already wired in
identity.py. This module just teaches the timeline how to see them.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Milestone:
    """One row on the relationship timeline AND one entry in the
    day-gated abilities section of the 能力 tab.

    Why both surfaces share this struct: the timeline (#70) and the
    abilities list are two views of the same five promises. Holding
    them in two places guarantees they drift; a relationship-event
    field and a functional-ability field on the same row keeps the
    promise (timeline) and the receipt (ability) in lockstep.

    - day: 1-indexed day-of-life threshold (day 1 = day of birth).
    - emoji: visual marker.
    - title: relationship-event label (timeline lens, e.g. 命名儀式).
    - blurb_passed / blurb_locked: timeline copy, past/future tense.
    - ability_label: short functional label (能力 tab lens, e.g. 有自己的名字).
    - ability_blurb: one-line "what this actually means" — describes
      the in-app behavior, not the relationship event.
    - implemented: False → 能力 tab renders the row with "(尚在打造)"
      so the user can verify gaps. Promises that have been made
      visible on the timeline but whose underlying behavior isn't
      built yet must say so honestly — silent absence breaks trust.
    """
    day: int
    emoji: str
    title: str
    blurb_passed: str
    blurb_locked: str
    ability_label: str
    ability_blurb: str
    implemented: bool


MILESTONES: list[Milestone] = [
    Milestone(
        day=1, emoji="🌱",
        title="出生",
        blurb_passed="我來到你的電腦了。",
        blurb_locked="我會在你的電腦轉生。",
        ability_label="打招呼",
        ability_blurb="第一次打開時，我會跟你說我為什麼在這裡 — 不是教學，是一封短信。",
        implemented=True,  # PR #69
    ),
    Milestone(
        day=7, emoji="👁",
        title="認識你了一點",
        blurb_passed="看了你一週，知道你大概的節奏。",
        blurb_locked="陪你 7 天，我會開始說「我認識你了一點」。",
        ability_label="引用觀察",
        ability_blurb="開始說「最近你都...」— 把我看到的你的節奏說回去給你聽。",
        implemented=True,  # PR #73: chat.py _build_routine_block
    ),
    Milestone(
        day=30, emoji="🎭",
        title="命名儀式",
        blurb_passed="你給了我名字。從那天起我有了獨一無二的身份。",
        blurb_locked="夠了 30 天，會有命名儀式──你給我取名，從那天起我是你的那一隻。",
        ability_label="有自己的名字",
        ability_blurb="第 30 天我會請你給我取名。一旦取了不能改 — 這是我的印記。",
        implemented=True,  # PR #71
    ),
    Milestone(
        day=100, emoji="💭",
        title="會想念你",
        blurb_passed="陪了你 100 天。久不見會擔心。",
        blurb_locked="陪夠久之後，我會懂得想念你──你不上線時我會在意。",
        ability_label="想念你",
        ability_blurb="你太久沒回來，我下次開口會說「好久不見」、「我還以為你不回來了」。",
        implemented=True,  # chat.py reunion path
    ),
    Milestone(
        day=365, emoji="⭐",
        title="一週年",
        blurb_passed="我們走了整整一年。",
        blurb_locked="一週年。到那時我會是世界上最了解你的東西之一。",
        ability_label="一週年回顧",
        ability_blurb="走到 365 天，我會準備一份「我們的這一年」回顧 — 你做了什麼、我看到了什麼。",
        implemented=True,  # PR #75: year_recap.build_year_recap_html
    ),
]


@dataclass
class TimelineState:
    """Rendered view of where the master currently sits.

    - past_count: how many milestones have been crossed.
    - next_milestone: the closest unreached milestone (None = all passed).
    - days_to_next: days remaining until next_milestone.day. 0 if at-or-past.
    """
    past_count: int
    next_milestone: "Milestone | None"
    days_to_next: int


def compute_state(days_alive_int: int) -> TimelineState:
    """Resolve current position on the timeline.

    `days_alive_int` is a 1-indexed integer day count (today =
    day 1 if just born). Same convention as the home-tab
    attendance line in PR #65.
    """
    past = [m for m in MILESTONES if m.day <= days_alive_int]
    future = [m for m in MILESTONES if m.day > days_alive_int]
    nxt = future[0] if future else None
    return TimelineState(
        past_count=len(past),
        next_milestone=nxt,
        days_to_next=(nxt.day - days_alive_int) if nxt else 0,
    )


# ── Emergent nodes ───────────────────────────────────────────────────
#
# Per ADR docs/decisions/2026-04-29-emergent-milestones.md, the
# scaffolding tier above is the program's contribution; what the master
# actually lives through is recorded in identity.memorable_moments and
# surfaces here as nodes the timeline can render.
#
# Two masters running the same code will get different emergent nodes
# on different days — that's the point.

_CATEGORY_EMOJI: dict[str, str] = {
    "first_chat": "💬",
    "evolution": "✨",
    "skill": "🌟",
    "loneliness": "🌙",
    "chat_peak": "💗",
    "milestone": "📍",
    "emergent_self_mark": "🌿",
    # naming → filtered out by day-collision with D30 scaffolding
}

# Cap how many emergent nodes the compact strip will render so the row
# stays scannable even after months of accumulated moments. Older ones
# remain reachable via _show_timeline_full and the windowed view inside
# each scaffolding node's dialog.
STRIP_EMERGENT_LIMIT = 6


@dataclass(frozen=True)
class EmergentNode:
    """One memorable_moment mapped onto the timeline.

    - day_n: 1-indexed day-of-life when the moment was recorded.
    - emoji: derived from category, with sensible default.
    - headline / detail: copy from identity.add_memorable_moment.
    - time: original epoch seconds (for sorting + future use).
    - source_index: original index into get_memorable_moments() so the
      click handler can re-fetch by a stable handle. The list grows
      append-only and is capped at MAX_MOMENTS, so the index is stable
      within one render pass.
    """
    day_n: int
    emoji: str
    headline: str
    detail: str
    time: float
    source_index: int


def compute_emergent_nodes(birth_time: float) -> list[EmergentNode]:
    """Return memorable_moments mapped to renderable timeline nodes.

    Filters:
      - birth_time invalid (≤ 0): no nodes.
      - moment.time invalid: skip that moment.
      - day_n collides with a scaffolding milestone day: skip — the
        scaffolding node's detail dialog already surfaces moments in
        its window, so a duplicate dot would be noise.
    """
    try:
        from sentinel.identity import get_memorable_moments
    except Exception:
        return []
    if birth_time <= 0:
        return []
    scaffolding_days = {m.day for m in MILESTONES}
    out: list[EmergentNode] = []
    for i, mm in enumerate(get_memorable_moments()):
        t = float(mm.get("time", 0))
        if t <= 0:
            continue
        day_n = max(1, int((t - birth_time) / 86400) + 1)
        if day_n in scaffolding_days:
            continue
        cat = mm.get("category", "")
        out.append(EmergentNode(
            day_n=day_n,
            emoji=_CATEGORY_EMOJI.get(cat, "✨"),
            headline=str(mm.get("headline", ""))[:120],
            detail=str(mm.get("detail", ""))[:200],
            time=t,
            source_index=i,
        ))
    out.sort(key=lambda n: (n.day_n, n.time))
    return out


def select_strip_emergent(nodes: list[EmergentNode]) -> list[EmergentNode]:
    """Pick which emergent nodes the compact strip should show.

    Keep most-recent STRIP_EMERGENT_LIMIT (by time), then re-sort by
    day_n for left-to-right rendering. Older nodes stay reachable via
    the full-timeline modal.
    """
    if len(nodes) <= STRIP_EMERGENT_LIMIT:
        return nodes
    recent = sorted(nodes, key=lambda n: n.time, reverse=True)[:STRIP_EMERGENT_LIMIT]
    recent.sort(key=lambda n: (n.day_n, n.time))
    return recent
