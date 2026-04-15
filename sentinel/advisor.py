"""主動建議引擎 — AI Slime 觀察到模式後，主動提出有用的建議。

不是報告現狀（那是定期報告的事），而是根據累積的觀察提出「行動建議」。

建議類型：
  - 健康提醒：久坐、深夜、長時間無休息
  - 效率建議：偵測到分心、頻繁切換、卡在某個問題上
  - 環境提醒：磁碟快滿、記憶體長期高佔用
  - 習慣洞察：發現新的使用模式、工作節奏建議
  - 情境感知：根據當前活動給出相關建議
"""
import time
import logging
import json
from pathlib import Path
from dataclasses import dataclass, field

log = logging.getLogger("sentinel.advisor")

# ── 建議紀錄（避免重複建議）──
ADVICE_LOG = Path.home() / ".hermes" / "aislime_advice_log.jsonl"

# 每種建議的最小間隔（秒）
ADVICE_COOLDOWNS = {
    "health_break":     1800,   # 30 分鐘內不重複提醒休息
    "health_posture":   3600,   # 1 小時內不重複提醒姿勢
    "health_sleep":     7200,   # 2 小時內不重複提醒睡覺
    "health_hydrate":   2700,   # 45 分鐘提醒喝水
    "efficiency_focus":  900,   # 15 分鐘內不重複提醒專注
    "efficiency_stuck":  1800,  # 30 分鐘
    "environment_disk":  3600,  # 1 小時
    "environment_ram":   1800,  # 30 分鐘
    "insight_pattern":   7200,  # 2 小時
    "context_tip":       1200,  # 20 分鐘
}


@dataclass
class AdvisorContext:
    """觀察循環中收集的即時資料，餵給建議引擎。"""
    # 系統
    cpu_percent: float = 0
    ram_percent: float = 0
    disk_percent: float = 0

    # 活動
    active_app: str = ""
    app_duration_minutes: float = 0
    recent_app_switches: int = 0       # 最近 10 分鐘的視窗切換次數
    continuous_work_minutes: float = 0  # 持續使用電腦的時間（無長暫停）

    # 時間
    hour: int = 0

    # 學到的 profile
    profile: str = ""
    patterns: dict = field(default_factory=dict)
    dominant_traits: list = field(default_factory=list)

    # 進化
    evolution_title: str = ""
    total_observations: int = 0


class Advisor:
    """主動建議引擎。每次觀察循環呼叫 evaluate()，回傳 0 或多個建議。"""

    def __init__(self):
        self._last_advice: dict[str, float] = {}
        self._work_start: float = time.time()
        self._last_input_time: float = time.time()
        self._idle_threshold = 300  # 5 分鐘無輸入視為暫停

    def record_input_activity(self):
        """有鍵盤/滑鼠活動時呼叫，重置 idle 計時。"""
        now = time.time()
        # 如果之前閒置超過 5 分鐘，重置工作起始時間
        if now - self._last_input_time > self._idle_threshold:
            self._work_start = now
        self._last_input_time = now

    def evaluate(self, ctx: AdvisorContext) -> list[dict]:
        """根據當前情境評估是否要給建議。

        回傳格式：[{"type": "health_break", "message": "...", "emoji": "🧘"}]
        """
        now = time.time()
        ctx.hour = time.localtime().tm_hour
        ctx.continuous_work_minutes = (now - self._work_start) / 60

        advices = []

        # ── 健康類 ──
        advices.extend(self._check_health(ctx, now))

        # ── 效率類 ──
        advices.extend(self._check_efficiency(ctx, now))

        # ── 環境類 ──
        advices.extend(self._check_environment(ctx, now))

        # ── 情境類 ──
        advices.extend(self._check_context(ctx, now))

        # 記錄已發出的建議
        for a in advices:
            self._last_advice[a["type"]] = now
            _log_advice(a)

        return advices

    def _can_advise(self, advice_type: str, now: float) -> bool:
        """檢查這類建議是否已過冷卻期。"""
        last = self._last_advice.get(advice_type, 0)
        cooldown = ADVICE_COOLDOWNS.get(advice_type, 1800)
        return now - last >= cooldown

    # ── 健康建議 ──

    def _check_health(self, ctx: AdvisorContext, now: float) -> list[dict]:
        results = []

        # 連續工作超過 50 分鐘 → 提醒休息
        if ctx.continuous_work_minutes >= 50 and self._can_advise("health_break", now):
            mins = int(ctx.continuous_work_minutes)
            results.append({
                "type": "health_break",
                "emoji": "🧘",
                "message": f"你已經連續使用電腦 {mins} 分鐘了。站起來走走吧，大賢者會幫你看著的。",
                "severity": "gentle",
            })

        # 連續工作超過 90 分鐘 → 喝水提醒
        if ctx.continuous_work_minutes >= 90 and self._can_advise("health_hydrate", now):
            results.append({
                "type": "health_hydrate",
                "emoji": "💧",
                "message": "記得喝水！保持水分對專注力很重要。",
                "severity": "gentle",
            })

        # 深夜提醒（23:00-05:00）
        if (ctx.hour >= 23 or ctx.hour < 5) and self._can_advise("health_sleep", now):
            if ctx.hour >= 1 and ctx.hour < 5:
                results.append({
                    "type": "health_sleep",
                    "emoji": "🌙",
                    "message": f"已經凌晨 {ctx.hour} 點了...你的身體需要休息。明天再繼續吧？",
                    "severity": "warning",
                })
            else:
                results.append({
                    "type": "health_sleep",
                    "emoji": "🌙",
                    "message": "夜深了，注意休息。如果還要繼續，記得調低螢幕亮度保護眼睛。",
                    "severity": "gentle",
                })

        return results

    # ── 效率建議 ──

    def _check_efficiency(self, ctx: AdvisorContext, now: float) -> list[dict]:
        results = []

        # 頻繁切換視窗（10 分鐘內切 15 次以上）→ 可能分心
        if ctx.recent_app_switches >= 15 and self._can_advise("efficiency_focus", now):
            results.append({
                "type": "efficiency_focus",
                "emoji": "🎯",
                "message": (
                    f"最近 10 分鐘切換了 {ctx.recent_app_switches} 次視窗。"
                    "如果覺得分心，試試先完成一件事再切到下一個？"
                ),
                "severity": "gentle",
            })

        # 在同一個 app 卡很久（超過 60 分鐘在同一個視窗）
        if ctx.app_duration_minutes >= 60 and self._can_advise("efficiency_stuck", now):
            app_name = ctx.active_app or "某個程式"
            results.append({
                "type": "efficiency_stuck",
                "emoji": "🤔",
                "message": (
                    f"你在 {app_name} 已經專注了 {int(ctx.app_duration_minutes)} 分鐘。"
                    "如果卡住了，換個角度想想或先做別的事？如果是心流狀態請忽略我 😊"
                ),
                "severity": "gentle",
            })

        return results

    # ── 環境建議 ──

    def _check_environment(self, ctx: AdvisorContext, now: float) -> list[dict]:
        results = []

        # 磁碟快滿
        if ctx.disk_percent >= 90 and self._can_advise("environment_disk", now):
            results.append({
                "type": "environment_disk",
                "emoji": "💾",
                "message": f"磁碟使用率 {ctx.disk_percent:.0f}%，快滿了。建議清理一些不需要的檔案。",
                "severity": "warning",
            })

        # 記憶體長期高佔用
        if ctx.ram_percent >= 85 and self._can_advise("environment_ram", now):
            results.append({
                "type": "environment_ram",
                "emoji": "🧠",
                "message": (
                    f"記憶體使用率 {ctx.ram_percent:.0f}%。"
                    "如果覺得卡頓，可以關掉一些不用的程式。"
                ),
                "severity": "info",
            })

        return results

    # ── 情境建議 ──

    def _check_context(self, ctx: AdvisorContext, now: float) -> list[dict]:
        results = []

        # 根據當前使用的 app 和學到的 patterns 給建議
        # 這邊用規則引擎，不用 LLM（避免每 30 秒都呼叫 API）
        active_lower = ctx.active_app.lower() if ctx.active_app else ""

        # 在 IDE 裡寫 code 超過 30 分鐘沒 commit
        code_apps = ["code", "pycharm", "idea", "visual studio", "vim", "nvim"]
        if any(app in active_lower for app in code_apps):
            if ctx.app_duration_minutes >= 30 and self._can_advise("context_tip", now):
                results.append({
                    "type": "context_tip",
                    "emoji": "💡",
                    "message": "寫了一段時間了，記得存檔和 commit。小步快跑比較安全。",
                    "severity": "gentle",
                })

        return results


# ── LLM 深度建議（低頻呼叫）──

def generate_insight(ctx: AdvisorContext) -> str | None:
    """用 LLM 根據累積的觀察生成一個深度洞察建議。

    這個函數不是每 30 秒呼叫的，而是在蒸餾完成後（每 5 分鐘）呼叫一次。
    """
    from sentinel.llm import call_llm
    from sentinel import config

    if not ctx.profile and not ctx.patterns:
        return None

    patterns_str = json.dumps(ctx.patterns, ensure_ascii=False) if ctx.patterns else "(無)"
    traits_str = ", ".join(ctx.dominant_traits) if ctx.dominant_traits else "(觀察中)"

    prompt = (
        "你是 AI Slime，一個觀察型 AI 守護靈。根據以下觀察，給主人一個簡短實用的建議。\n\n"
        f"主人的 profile：{ctx.profile}\n"
        f"行為模式：{patterns_str}\n"
        f"主要特質：{traits_str}\n"
        f"當前狀態：使用 {ctx.active_app or '未知'}，已持續 {ctx.app_duration_minutes:.0f} 分鐘\n"
        f"連續工作：{ctx.continuous_work_minutes:.0f} 分鐘\n"
        f"現在時間：{ctx.hour} 點\n"
        f"系統：CPU {ctx.cpu_percent:.0f}% / RAM {ctx.ram_percent:.0f}% / 磁碟 {ctx.disk_percent:.0f}%\n\n"
        "規則：\n"
        "- 只在真的有值得說的時候才給建議，不要廢話\n"
        "- 如果一切正常，回覆「無」\n"
        "- 建議要具體、可行動，不要空泛的鼓勵\n"
        "- 用友善但直接的口吻\n"
        "- 一兩句話就好，不要長篇大論\n"
        "- 用中文回覆\n"
    )

    result = call_llm(prompt, temperature=0.5, max_tokens=150,
                      model_pref=config.ANALYSIS_MODEL_PREF, task_type="analysis")

    if result and result.strip() != "無" and len(result.strip()) > 3:
        return result.strip()
    return None


# ── 日誌 ──

def _log_advice(advice: dict):
    """記錄建議到日誌。"""
    try:
        ADVICE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ADVICE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": time.time(),
                "type": advice["type"],
                "message": advice["message"],
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def get_advice_log(last_n: int = 20) -> list[dict]:
    """讀取最近的建議紀錄。"""
    if not ADVICE_LOG.exists():
        return []
    entries = []
    try:
        for line in ADVICE_LOG.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return entries[-last_n:]
