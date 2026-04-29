"""Manifesto-aligned timeline of relationship milestones.

Why this exists separately: the welcome modal (PR #69) makes the
1 → 7 → 30 → 100 → 365-day promise once, then disappears. The
timeline widget makes that same promise **visible every time the
master opens the home tab**. That repeated visibility is the
"this isn't just another chatbot" signal — no other AI product
has a timeline because no other AI product's pitch IS the
timeline.

Milestones are intentionally **declarative** here so future code
can list them, query them, and decorate the home tab without
digging through GUI plumbing. Copy is held next to the data so
language tweaks don't require touching widget code.

The numbers come straight from the welcome modal copy and from
manifesto §原則 6 (分齡解鎖 in macro form, day-numbers in micro
form). Don't add new milestones casually — each one is a public
promise, and breaking it later is a manifesto-violation level
issue.
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
        implemented=False,  # 尚在打造
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
        implemented=False,  # 尚在打造
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
