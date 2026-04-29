"""Evolution System - AI Slime's adaptive growth.

Unlike a fixed skill tree, the slime's evolution is shaped by the user it observes.
A programmer's slime will evolve differently from a designer's slime.
Skills emerge and level up based on actual observed behavior patterns.
"""
import json
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict

log = logging.getLogger("sentinel.evolution")

EVOLUTION_FILE = Path.home() / ".hermes" / "aislime_evolution.json"


@dataclass
class Skill:
    name: str            # Skill name (e.g., "System Monitor")
    jp_name: str         # Japanese-style name (e.g., "系統之眼")
    category: str        # "observation" | "analysis" | "action" | "communication"
    level: int = 1       # 1-5 skill proficiency
    acquired_at: float = 0
    use_count: int = 0
    description: str = ""
    unlocked: bool = True
    affinity: float = 0.0  # How relevant this skill is to the user (0-1)


@dataclass
class EvolutionState:
    """AI Slime's current evolution state."""
    form: str = "Slime"
    title: str = "初生史萊姆"
    total_observations: int = 0
    total_learnings: int = 0
    total_conversations: int = 0
    total_actions: int = 0
    skills: list = field(default_factory=list)
    evolution_log: list = field(default_factory=list)
    birth_time: float = 0
    last_evolution: float = 0
    # Adaptive: personalized direction
    evolution_direction: str = ""       # LLM-generated: what the slime is evolving towards
    dominant_traits: list = field(default_factory=list)  # e.g., ["programmer", "night_owl"]
    affinity_scores: dict = field(default_factory=dict)  # behavior_type -> score
    # Identity & relationship (A + G)
    slime_name: str = ""               # Given by master at Named Slime tier; sacred, immutable after set
    last_seen: float = 0               # Epoch seconds — master last interacted with the slime (startup or chat)
    naming_pending: bool = False       # Set True when tier advances to Named Slime; GUI consumes + clears

    def days_alive(self) -> float:
        if not self.birth_time:
            return 0
        return (time.time() - self.birth_time) / 86400

    def display_name(self) -> str:
        """What the slime calls itself. Falls back to form title before naming."""
        return self.slime_name or "AI Slime"


# ─── Affinity vocabulary ─────────────────────────────────────────────────
#
# Two parallel dicts keyed by the same affinity slug. Plain labels go
# in the 能力 tab and the D7+ chat routine block; poetic titles drive
# the evolution-direction line on the home tab.

AFFINITY_LABELS: dict[str, str] = {
    "coding":        "程式開發",
    "communication": "溝通",
    "research":      "研究探索",
    "creative":      "創作",
    "multitasking":  "多工切換",
    "deep_focus":    "深度專注",
    "late_night":    "夜間活動",
}

AFFINITY_TITLES: dict[str, str] = {
    "coding":        "程式開發守護者",
    "communication": "溝通網絡中樞",
    "research":      "知識探索嚮導",
    "creative":      "創意靈感守護",
    "multitasking":  "多維情境管理者",
    "deep_focus":    "心流守護者",
    "late_night":    "暗夜守望者",
}


# ─── Evolution Milestones ────────────────────────────────────────────────

EVOLUTION_TIERS = [
    (0,       "Slime",           "初生史萊姆"),
    (100,     "Slime+",          "覺醒史萊姆"),
    (500,     "Named Slime",     "被命名的史萊姆"),
    (2000,    "Majin",           "魔人"),
    (10000,   "Demon Lord Seed", "魔王種"),
    (50000,   "True Demon Lord", "真・魔王"),
    (200000,  "Ultimate Slime",  "究極史萊姆"),
]


# ─── Core Skills (always present, but levels differ per user) ────────────

CORE_SKILLS = [
    Skill("System Monitor", "系統之眼", "observation", level=1, unlocked=True,
          description="監控 CPU、記憶體、磁碟使用狀態"),
    Skill("File Watcher", "檔案感知", "observation", level=1, unlocked=True,
          description="追蹤檔案系統的變動"),
    Skill("Window Tracker", "視窗追蹤", "observation", level=1, unlocked=True,
          description="觀察使用者正在使用的程式和切換模式"),
    Skill("Claude Code Reader", "對話解讀", "observation", level=1, unlocked=True,
          description="讀取 Claude Code 的對話紀錄"),
    Skill("Great Sage", "大賢者", "analysis", level=1, unlocked=True,
          description="分析觀察到的事件，判斷是否需要通知"),
    Skill("Memory", "記憶術", "analysis", level=1, unlocked=True,
          description="蒸餾和儲存對使用者的理解"),
    Skill("Telegram Link", "思念傳達", "communication", level=1, unlocked=True,
          description="透過 Telegram 與使用者溝通"),
    Skill("GUI Presence", "具現化", "communication", level=1, unlocked=True,
          description="圖形化介面，在系統匣中常駐"),
    Skill("Input Sense", "感知之眼", "observation", level=1, unlocked=True,
          description="感知使用者的鍵盤輸入和滑鼠操作"),
    Skill("Screen Vision", "千里眼", "observation", level=1, unlocked=True,
          description="隨機截圖觀察螢幕內容，自動過濾敏感資訊"),
]


# ─── Adaptive Skills (discovered based on user behavior) ─────────────────
# These are templates. Each has a "trigger" — a behavior pattern that unlocks it.
# Once unlocked, they evolve based on how dominant that behavior is.

ADAPTIVE_SKILL_TEMPLATES = [
    {
        "skill": Skill("Predator", "捕食者", "analysis", level=1, unlocked=False,
                       description="從觀察中吸收使用者的行為模式和技能"),
        "trigger": {"type": "learnings", "threshold": 5},
        "unlock_msg": "獲得獨特技能「捕食者」— 開始從觀察中吸收使用者的行為模式。",
    },
    {
        "skill": Skill("Code Insight", "程式解析", "analysis", level=0, unlocked=False,
                       description="深度理解程式碼結構和開發模式"),
        "trigger": {"type": "affinity", "key": "coding", "threshold": 0.3},
        "unlock_msg": "觀察到大量程式開發活動 — 獲得「程式解析」技能！",
    },
    {
        "skill": Skill("Night Guard", "夜之守護", "observation", level=0, unlocked=False,
                       description="深夜工作時的特殊守護模式，注意健康提醒"),
        "trigger": {"type": "affinity", "key": "late_night", "threshold": 0.3},
        "unlock_msg": "觀察到經常深夜工作 — 獲得「夜之守護」技能，守護深夜的你。",
    },
    {
        "skill": Skill("Context Weaver", "情境編織", "analysis", level=0, unlocked=False,
                       description="在多個應用程式間快速切換時維持情境理解"),
        "trigger": {"type": "affinity", "key": "multitasking", "threshold": 0.3},
        "unlock_msg": "觀察到高頻多工切換 — 獲得「情境編織」，理解你的多工模式。",
    },
    {
        "skill": Skill("Deep Focus", "集中力場", "analysis", level=0, unlocked=False,
                       description="偵測並保護使用者的深度專注狀態"),
        "trigger": {"type": "affinity", "key": "deep_focus", "threshold": 0.3},
        "unlock_msg": "觀察到長時間專注模式 — 獲得「集中力場」，保護你的心流狀態。",
    },
    {
        "skill": Skill("Communication Hub", "思念網絡", "communication", level=0, unlocked=False,
                       description="多頻道溝通管理，整合各平台訊息"),
        "trigger": {"type": "affinity", "key": "communication", "threshold": 0.3},
        "unlock_msg": "觀察到大量溝通活動 — 獲得「思念網絡」，整合你的溝通模式。",
    },
    {
        "skill": Skill("Research Eye", "探索之眼", "observation", level=0, unlocked=False,
                       description="追蹤和理解使用者的研究與學習路徑"),
        "trigger": {"type": "affinity", "key": "research", "threshold": 0.3},
        "unlock_msg": "觀察到頻繁的研究和探索行為 — 獲得「探索之眼」！",
    },
    {
        "skill": Skill("Prediction", "未來視", "analysis", level=0, unlocked=False,
                       description="根據歷史模式預測使用者的下一步需求"),
        "trigger": {"type": "learnings", "threshold": 50},
        "unlock_msg": "累積足夠的觀察和學習 — 覺醒「未來視」，開始預測你的需求。",
    },
    {
        "skill": Skill("Creative Spark", "創造之火", "analysis", level=0, unlocked=False,
                       description="理解使用者的創意模式和靈感來源"),
        "trigger": {"type": "affinity", "key": "creative", "threshold": 0.3},
        "unlock_msg": "觀察到豐富的創作活動 — 獲得「創造之火」！",
    },
    {
        "skill": Skill("Health Guardian", "生命守護", "action", level=0, unlocked=False,
                       description="根據使用模式提醒休息、喝水、運動"),
        "trigger": {"type": "observations", "threshold": 1000},
        "unlock_msg": "觀察量突破 1000 — 覺醒「生命守護」，開始關注你的健康。",
    },
]

# ─── Behavior → Affinity Mapping ────────────────────────────────────────
# Maps observed process names and patterns to affinity categories.

AFFINITY_MAP = {
    "coding": {
        "processes": ["code.exe", "Code.exe", "pycharm64.exe", "idea64.exe",
                      "devenv.exe", "sublime_text.exe", "atom.exe", "vim.exe",
                      "nvim.exe", "WindowsTerminal.exe", "cmd.exe", "powershell.exe",
                      "python.exe", "node.exe", "git.exe"],
        "window_hints": ["Visual Studio", "PyCharm", "IntelliJ", ".py", ".js", ".ts",
                         "GitHub", "Stack Overflow", "terminal", "claude"],
    },
    "communication": {
        "processes": ["Telegram.exe", "Discord.exe", "slack.exe", "Teams.exe",
                      "LINE.exe", "WeChat.exe", "Messenger.exe"],
        "window_hints": ["Telegram", "Discord", "Slack", "Teams", "LINE", "Gmail",
                         "Outlook", "Mail"],
    },
    "research": {
        "processes": ["chrome.exe", "firefox.exe", "msedge.exe", "brave.exe"],
        "window_hints": ["Google", "Wikipedia", "Reddit", "Medium", "docs.",
                         "documentation", "Stack Overflow", "arxiv", "論文"],
    },
    "creative": {
        "processes": ["Photoshop.exe", "Illustrator.exe", "figma.exe",
                      "blender.exe", "obs64.exe", "Premiere.exe", "After Effects.exe",
                      "Canva.exe"],
        "window_hints": ["Figma", "Canva", "Photoshop", "design", "draw"],
    },
    "multitasking": {
        # This is detected differently - by switch frequency, not process names
        "processes": [],
        "window_hints": [],
    },
}


# ─── State Management ────────────────────────────────────────────────────

def load_evolution() -> EvolutionState:
    if EVOLUTION_FILE.exists():
        try:
            data = json.loads(EVOLUTION_FILE.read_text(encoding='utf-8'))
            # Filter to only fields EvolutionState actually declares — keeps
            # old saves loadable after schema changes, and new fields loadable
            # if older code is run against new saves.
            valid_fields = {f.name for f in EvolutionState.__dataclass_fields__.values()}
            scalar_kwargs = {
                k: v for k, v in data.items()
                if k in valid_fields
                and k not in ('skills', 'evolution_log', 'dominant_traits', 'affinity_scores')
            }
            state = EvolutionState(**scalar_kwargs)
            state.skills = [Skill(**s) for s in data.get('skills', [])]
            state.evolution_log = data.get('evolution_log', [])
            state.dominant_traits = data.get('dominant_traits', [])
            state.affinity_scores = data.get('affinity_scores', {})
            return state
        except Exception as e:
            # CRITICAL: do NOT silently overwrite a corrupt/incompatible save.
            # Back it up with a timestamp so the user's progress isn't erased,
            # and surface the error loudly so we can diagnose schema drift
            # (this is what caused Mac users to keep reverting to newborn —
            # a single load failure wiped the file on every launch).
            backup = EVOLUTION_FILE.with_suffix(
                f".broken.{int(time.time())}.json"
            )
            try:
                EVOLUTION_FILE.rename(backup)
                log.error(
                    f"Failed to load evolution state: {e!r}. "
                    f"Backed up corrupt save to {backup}. "
                    f"Starting a new slime — inspect the backup to recover."
                )
            except Exception as backup_err:
                log.error(
                    f"Failed to load evolution state: {e!r}. "
                    f"Also failed to back up corrupt save: {backup_err!r}. "
                    f"LEAVING {EVOLUTION_FILE} IN PLACE to avoid data loss — "
                    f"fix the file manually before restarting."
                )
                # Don't overwrite — raise so the user sees the problem
                # instead of silently getting a reborn newborn.
                raise

    # First boot - birth!
    state = EvolutionState(
        birth_time=time.time(),
        skills=[Skill(**asdict(s)) for s in CORE_SKILLS],
    )
    for s in state.skills:
        s.acquired_at = time.time()
    state.evolution_log.append({
        "time": time.time(),
        "event": "birth",
        "message": "AI Slime 誕生了。一個小小的史萊姆，開始觀察這個世界。",
    })
    save_evolution(state)
    return state


def save_evolution(state: EvolutionState):
    data = asdict(state)
    EVOLUTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVOLUTION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


EXP_LOG_FILE = Path.home() / ".hermes" / "aislime_exp_log.jsonl"


def record_observation(state: EvolutionState, count: int = 1, sources: dict = None):
    """Record observations with source breakdown.

    Form evolution (Slime → Slime+ → ...) is NO LONGER auto-advanced.
    The user must press the evolve button and pay 2 pts (quota mode)
    to actually change form — see perform_evolution(). Adaptive skill
    unlocks still happen passively based on observation patterns.

    sources example: {"system": 1, "files": 3, "claude": 1, ...}
    """
    state.total_observations += count
    _check_adaptive_unlocks(state)
    save_evolution(state)

    # 記錄經驗來源
    if sources:
        _log_exp(count, sources)

    # Round-number observation milestones become memorable moments (D)
    try:
        from sentinel import identity as _id
        _id.record_milestone_if_hit(state.total_observations)
    except Exception:
        pass


def _log_exp(count: int, sources: dict):
    """Append experience gain to log file."""
    try:
        EXP_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EXP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": time.time(),
                "exp": count,
                "sources": sources,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def get_exp_log(last_n: int = 30) -> list[dict]:
    """Read recent experience gain entries."""
    if not EXP_LOG_FILE.exists():
        return []
    entries = []
    try:
        for line in EXP_LOG_FILE.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return entries[-last_n:]


def record_learning(state: EvolutionState):
    """Record a successful distillation. Also triggers adaptive evolution."""
    state.total_learnings += 1

    # Level up Great Sage
    sage = _find_skill(state, "Great Sage")
    if sage and sage.use_count < 1000:
        sage.use_count += 1
        sage.level = min(5, 1 + sage.use_count // 50)

    # Update affinity scores from current patterns
    _update_affinities(state)

    # Check if new adaptive skills should unlock
    _check_adaptive_unlocks(state)

    # Level up existing skills based on affinity
    _level_up_by_affinity(state)

    # Update evolution direction
    _update_direction(state)

    save_evolution(state)


def record_conversation(state: EvolutionState):
    state.total_conversations += 1
    comm = _find_skill(state, "Telegram Link")
    if comm:
        comm.use_count += 1
        comm.level = min(5, 1 + comm.use_count // 20)

    # Conversations also boost communication affinity
    state.affinity_scores["communication"] = min(1.0,
        state.affinity_scores.get("communication", 0) + 0.02)

    save_evolution(state)


def record_action(state: EvolutionState):
    state.total_actions += 1
    save_evolution(state)


def record_activity_affinities(state: EvolutionState, activity_summary: str):
    """Analyze activity summary to update behavior affinities.

    Called during each observation cycle with the user's activity data.
    This is what makes evolution personalized - the same AI Slime code
    will evolve differently depending on who uses it.
    """
    if not activity_summary:
        return

    summary_lower = activity_summary.lower()

    for category, mapping in AFFINITY_MAP.items():
        score_delta = 0.0

        # Check process names
        for proc in mapping["processes"]:
            if proc.lower() in summary_lower:
                score_delta += 0.01

        # Check window title hints
        for hint in mapping["window_hints"]:
            if hint.lower() in summary_lower:
                score_delta += 0.005

        if score_delta > 0:
            current = state.affinity_scores.get(category, 0)
            # Exponential moving average - recent activity matters more
            state.affinity_scores[category] = min(1.0, current * 0.95 + score_delta)

    # Detect multitasking by counting window switches mentioned
    switch_count = summary_lower.count("切換") + summary_lower.count("switch")
    if switch_count > 5:
        current = state.affinity_scores.get("multitasking", 0)
        state.affinity_scores["multitasking"] = min(1.0, current * 0.95 + 0.02)

    # Detect late night work
    import datetime
    hour = datetime.datetime.now().hour
    if hour >= 23 or hour <= 5:
        current = state.affinity_scores.get("late_night", 0)
        state.affinity_scores["late_night"] = min(1.0, current * 0.95 + 0.03)

    # Detect deep focus (long time on single app)
    if "分鐘" in summary_lower:
        # Look for long durations
        import re
        durations = re.findall(r'(\d+\.?\d*)\s*分鐘', summary_lower)
        for d in durations:
            if float(d) > 15:  # 15+ minutes on one app = deep focus
                current = state.affinity_scores.get("deep_focus", 0)
                state.affinity_scores["deep_focus"] = min(1.0, current * 0.95 + 0.02)
                break

    # Update dominant traits
    _update_dominant_traits(state)


# ─── Internal Helpers ────────────────────────────────────────────────────

def _find_skill(state: EvolutionState, name: str) -> Skill | None:
    for s in state.skills:
        if s.name == name:
            return s
    return None


def _current_tier_index(state: EvolutionState) -> int:
    """Return the index of the slime's current form in EVOLUTION_TIERS."""
    for i, (_, form, _) in enumerate(EVOLUTION_TIERS):
        if form == state.form:
            return i
    return 0


def is_evolution_available(state: EvolutionState) -> dict:
    """Check whether the slime has earned enough to evolve ONE step.

    Returns:
      {
        "available":        bool — True iff next tier exists and is unlocked
        "current_form":     str
        "current_title":    str
        "next_form":        str | ""     (empty if already max)
        "next_title":       str | ""
        "next_threshold":   int          (obs needed for next tier)
        "current_obs":      int
        "progress":         float 0–1    (how close to next tier)
        "at_max":           bool
      }
    """
    idx = _current_tier_index(state)
    at_max = idx >= len(EVOLUTION_TIERS) - 1
    if at_max:
        return {
            "available": False,
            "current_form": state.form,
            "current_title": state.title,
            "next_form": "",
            "next_title": "",
            "next_threshold": 0,
            "current_obs": state.total_observations,
            "progress": 1.0,
            "at_max": True,
        }

    next_threshold, next_form, next_title = EVOLUTION_TIERS[idx + 1]
    current_threshold = EVOLUTION_TIERS[idx][0]
    span = max(1, next_threshold - current_threshold)
    progress = min(1.0, max(0.0,
        (state.total_observations - current_threshold) / span
    ))

    return {
        "available": state.total_observations >= next_threshold,
        "current_form": state.form,
        "current_title": state.title,
        "next_form": next_form,
        "next_title": next_title,
        "next_threshold": next_threshold,
        "current_obs": state.total_observations,
        "progress": progress,
        "at_max": False,
    }


def perform_evolution(state: EvolutionState) -> dict:
    """Advance the slime's form by ONE tier, if eligible.

    Caller is responsible for handling payment (2 pts via 5888 wallet
    in quota mode, free in BYOK mode) BEFORE calling this. This
    function only mutates state; it doesn't know about wallets.

    Returns:
      {"ok": bool, "reason": str, "from": str, "to": str, "title": str}
    """
    info = is_evolution_available(state)
    if info["at_max"]:
        return {"ok": False, "reason": "已達最終型態，無法再進化",
                "from": state.form, "to": state.form, "title": state.title}
    if not info["available"]:
        needed = info["next_threshold"] - state.total_observations
        return {
            "ok": False,
            "reason": (
                f"觀察次數不足：還需 {needed:,} 次才能進化到"
                f"「{info['next_title']}」"
            ),
            "from": state.form,
            "to": state.form,
            "title": state.title,
        }

    old_form = state.form
    state.form = info["next_form"]
    state.title = info["next_title"]
    state.last_evolution = time.time()
    state.evolution_log.append({
        "time": time.time(),
        "event": "evolution",
        "message": f"進化！{old_form} → {state.form}「{state.title}」",
    })
    # A. Naming ceremony — when entering Named Slime tier for the first
    # time, flag for the GUI to prompt the master. This is a one-shot: the
    # GUI clears naming_pending after the dialog resolves.
    if state.form == "Named Slime" and not state.slime_name:
        state.naming_pending = True
    save_evolution(state)
    log.info(f"EVOLUTION: {old_form} -> {state.form} ({state.title})")

    # D. Record evolution as a memorable moment. Dedup-protected, so a
    # duplicate call from the GUI layer is harmless.
    try:
        from sentinel import identity as _id
        _id.record_evolution_moment(old_form, state.form, state.title)
    except Exception:
        pass
    return {
        "ok": True,
        "reason": "進化成功",
        "from": old_form,
        "to": state.form,
        "title": state.title,
    }


def _update_affinities(state: EvolutionState):
    """Update affinity scores from learner memory patterns."""
    try:
        from sentinel.learner import load_memory
        memory = load_memory()
        patterns = memory.get("patterns", {})

        if not patterns:
            return

        # Analyze patterns text for hints
        all_text = json.dumps(patterns, ensure_ascii=False).lower()

        # Boost affinities based on pattern content
        if any(w in all_text for w in ["程式", "coding", "code", "開發", "debug"]):
            state.affinity_scores["coding"] = min(1.0,
                state.affinity_scores.get("coding", 0) + 0.05)
        if any(w in all_text for w in ["深夜", "late", "凌晨", "night"]):
            state.affinity_scores["late_night"] = min(1.0,
                state.affinity_scores.get("late_night", 0) + 0.05)
        if any(w in all_text for w in ["多工", "切換", "multitask", "switch"]):
            state.affinity_scores["multitasking"] = min(1.0,
                state.affinity_scores.get("multitasking", 0) + 0.05)
        if any(w in all_text for w in ["專注", "focus", "集中"]):
            state.affinity_scores["deep_focus"] = min(1.0,
                state.affinity_scores.get("deep_focus", 0) + 0.05)
        if any(w in all_text for w in ["研究", "research", "學習", "search"]):
            state.affinity_scores["research"] = min(1.0,
                state.affinity_scores.get("research", 0) + 0.05)
        if any(w in all_text for w in ["設計", "design", "創作", "creative"]):
            state.affinity_scores["creative"] = min(1.0,
                state.affinity_scores.get("creative", 0) + 0.05)

        _update_dominant_traits(state)

    except Exception as e:
        log.error(f"Affinity update error: {e}")


def _update_dominant_traits(state: EvolutionState):
    """Identify the user's dominant behavior traits."""
    if not state.affinity_scores:
        return
    # Top 3 traits with score > 0.1
    sorted_traits = sorted(state.affinity_scores.items(), key=lambda x: x[1], reverse=True)
    state.dominant_traits = [k for k, v in sorted_traits[:3] if v > 0.1]


def _check_adaptive_unlocks(state: EvolutionState):
    """Check if any adaptive skills should be unlocked based on behavior."""
    for template in ADAPTIVE_SKILL_TEMPLATES:
        skill_name = template["skill"].name
        # Already have this skill?
        if _find_skill(state, skill_name):
            continue

        trigger = template["trigger"]
        should_unlock = False

        if trigger["type"] == "learnings":
            should_unlock = state.total_learnings >= trigger["threshold"]
        elif trigger["type"] == "observations":
            should_unlock = state.total_observations >= trigger["threshold"]
        elif trigger["type"] == "affinity":
            score = state.affinity_scores.get(trigger["key"], 0)
            should_unlock = score >= trigger["threshold"]

        if should_unlock:
            new_skill = Skill(**asdict(template["skill"]))
            new_skill.unlocked = True
            new_skill.level = 1
            new_skill.acquired_at = time.time()
            state.skills.append(new_skill)
            state.evolution_log.append({
                "time": time.time(),
                "event": "skill_unlock",
                "message": template["unlock_msg"],
            })
            log.info(f"Adaptive skill unlocked: {skill_name}")
            # Record as memorable moment — an awakening is always special.
            # Lazy import to avoid circular dep (identity → learner → memory).
            try:
                from sentinel import identity as _id
                _id.add_memorable_moment(
                    category="skill",
                    headline=f"覺醒了新技能：{new_skill.jp_name}",
                    detail=new_skill.description,
                )
            except Exception:
                pass


def _level_up_by_affinity(state: EvolutionState):
    """Level up skills based on how relevant they are to user's behavior."""
    # Map affinity categories to skills they boost
    affinity_skill_map = {
        "coding": ["File Watcher", "Claude Code Reader", "Code Insight"],
        "communication": ["Telegram Link", "Communication Hub"],
        "research": ["Research Eye", "Great Sage"],
        "creative": ["Creative Spark"],
        "multitasking": ["Context Weaver", "Window Tracker"],
        "deep_focus": ["Deep Focus"],
        "late_night": ["Night Guard"],
    }

    for category, skill_names in affinity_skill_map.items():
        score = state.affinity_scores.get(category, 0)
        if score < 0.1:
            continue

        for skill_name in skill_names:
            skill = _find_skill(state, skill_name)
            if skill and skill.unlocked:
                # Affinity drives level: 0.1→1, 0.3→2, 0.5→3, 0.7→4, 0.9→5
                target_level = min(5, max(1, int(score * 5) + 1))
                if target_level > skill.level:
                    old_level = skill.level
                    skill.level = target_level
                    skill.affinity = score
                    state.evolution_log.append({
                        "time": time.time(),
                        "event": "skill_levelup",
                        "message": f"「{skill.jp_name}」 Lv.{old_level} → Lv.{target_level}（親和度: {score:.0%}）",
                    })


def _update_direction(state: EvolutionState):
    """Generate a personalized evolution direction description."""
    if not state.dominant_traits:
        state.evolution_direction = "正在觀察中，尚未確定進化方向..."
        return

    TRAIT_DESCRIPTIONS = AFFINITY_TITLES

    top_traits = [TRAIT_DESCRIPTIONS.get(t, t) for t in state.dominant_traits[:2]]
    state.evolution_direction = " × ".join(top_traits)


# ─── Display ─────────────────────────────────────────────────────────────

def get_status_text(state: EvolutionState) -> str:
    """Get a formatted status text for display."""
    days = state.days_alive()
    unlocked = [s for s in state.skills if s.unlocked]
    locked = [s for s in state.skills if not s.unlocked]

    lines = [
        f"【{state.title}】",
        f"存活天數: {days:.1f} 天",
        f"觀察次數: {state.total_observations:,}",
        f"學習次數: {state.total_learnings}",
        f"對話次數: {state.total_conversations}",
    ]

    if state.evolution_direction:
        lines.append(f"進化方向: {state.evolution_direction}")

    if state.dominant_traits:
        lines.append(f"主要特質: {', '.join(state.dominant_traits)}")

    if state.affinity_scores:
        lines.append("")
        lines.append("── 行為親和度 ──")
        sorted_aff = sorted(state.affinity_scores.items(), key=lambda x: x[1], reverse=True)
        for key, score in sorted_aff:
            if score > 0.01:
                bar_len = int(score * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                lines.append(f"  {key}: {bar} {score:.0%}")

    lines.append("")
    lines.append(f"── 已獲得的技能 ({len(unlocked)}) ──")
    for s in unlocked:
        level_bar = "★" * s.level + "☆" * (5 - s.level)
        lines.append(f"  {s.jp_name}（{s.name}） {level_bar}")

    if locked:
        lines.append("")
        lines.append(f"── 未解鎖的技能 ({len(locked)}) ──")
        for s in locked:
            lines.append(f"  🔒 {s.jp_name}（{s.name}）— {s.description}")

    if state.evolution_log:
        lines.append("")
        lines.append("── 最近事件 ──")
        for entry in state.evolution_log[-5:]:
            lines.append(f"  {entry['message']}")

    return "\n".join(lines)
