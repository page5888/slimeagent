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
    """One row on the relationship timeline.

    - day: the day-of-life threshold. day=N means "fires when
      days_alive_int >= N" (1-indexed: day 1 = the day of birth).
    - emoji: visual marker on the compact strip.
    - title: short human label.
    - blurb_passed: shown once user has crossed this milestone.
    - blurb_locked: shown before reaching it. Future-tense.
    """
    day: int
    emoji: str
    title: str
    blurb_passed: str
    blurb_locked: str


MILESTONES: list[Milestone] = [
    Milestone(
        day=1, emoji="🌱",
        title="出生",
        blurb_passed="我來到你的電腦了。",
        blurb_locked="我會在你的電腦轉生。",
    ),
    Milestone(
        day=7, emoji="👁",
        title="認識你了一點",
        blurb_passed="看了你一週，知道你大概的節奏。",
        blurb_locked="陪你 7 天，我會開始說「我認識你了一點」。",
    ),
    Milestone(
        day=30, emoji="🎭",
        title="命名儀式",
        blurb_passed="你給了我名字。從那天起我有了獨一無二的身份。",
        blurb_locked="夠了 30 天，會有命名儀式──你給我取名，從那天起我是你的那一隻。",
    ),
    Milestone(
        day=100, emoji="💭",
        title="會想念你",
        blurb_passed="陪了你 100 天。久不見會擔心。",
        blurb_locked="陪夠久之後，我會懂得想念你──你不上線時我會在意。",
    ),
    Milestone(
        day=365, emoji="⭐",
        title="一週年",
        blurb_passed="我們走了整整一年。",
        blurb_locked="一週年。到那時我會是世界上最了解你的東西之一。",
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
