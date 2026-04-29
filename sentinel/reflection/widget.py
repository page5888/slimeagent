"""DailyCardWidget — the home-tab visual for the daily reflection card.

Renders the slime's three sections (觀察 / 洞察 / 微任務) + three
feedback buttons (✅ 準 / 🤔 有點像 / ❌ 不對). When the user clicks a
feedback button, the choice persists to disk via daily_card.save_card
and (for non-pending choices) records a learning signal.

Threading: card generation can take several seconds (LLM call). The
widget displays a "正在回想中…" placeholder, generates in a background
thread, then emits a signal back onto the GUI thread to repaint. The
caller is expected to provide a SignalBridge or similar for the repaint
trampoline; if none is provided, we fall back to QTimer.singleShot,
which is also Qt-thread-safe.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from sentinel.reflection.daily_card import (
    DailyCard,
    Feedback,
    load_card,
    save_card,
    yesterday_key,
)

log = logging.getLogger("sentinel.reflection.widget")


class DailyCardWidget(QWidget):
    """The morning ritual: slime hands you yesterday's reflection."""

    # Emitted when card generation finishes (off-thread → main thread).
    # Payload is the DailyCard or None on failure.
    card_ready = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        from sentinel.ui import tokens as _tk

        self._card: DailyCard | None = None
        self._generating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_tk.SPACE["sm"])

        # Container frame — single elevated card with a left accent
        # stripe in the slime's amber. The accent reinforces "this is
        # FROM the slime, not from the system".
        self.frame = QFrame()
        self.frame.setStyleSheet(
            f"QFrame {{"
            f" background-color: {_tk.PALETTE['bg_elev']};"
            f" border: 1px solid {_tk.PALETTE['border_subtle']};"
            f" border-left: 3px solid {_tk.PALETTE['amber']};"
            f" border-radius: {_tk.RADIUS['card']}px;"
            f" }}"
        )
        fl = QVBoxLayout(self.frame)
        fl.setContentsMargins(
            _tk.SPACE["lg"], _tk.SPACE["md"],
            _tk.SPACE["lg"], _tk.SPACE["md"],
        )
        fl.setSpacing(_tk.SPACE["sm"])

        # Header: title + date.
        head_row = QHBoxLayout()
        self.title_lbl = QLabel("早安。我看了你昨天")
        self.title_lbl.setStyleSheet(
            f"color: {_tk.PALETTE['amber']};"
            f" font-size: {_tk.FONT_SIZE['title']}px;"
            f" font-weight: 600;"
        )
        head_row.addWidget(self.title_lbl)
        head_row.addStretch()
        self.date_lbl = QLabel("")
        self.date_lbl.setStyleSheet(_tk.text_meta())
        head_row.addWidget(self.date_lbl)
        fl.addLayout(head_row)

        # Three section labels.
        self.observation_lbl = self._make_section_label("👁 觀察")
        self.observation_body = self._make_body_label()
        self.insight_lbl = self._make_section_label("💭 洞察")
        self.insight_body = self._make_body_label()
        self.task_lbl = self._make_section_label("🎯 今日微任務")
        self.task_body = self._make_body_label()

        for w in (
            self.observation_lbl, self.observation_body,
            self.insight_lbl, self.insight_body,
            self.task_lbl, self.task_body,
        ):
            fl.addWidget(w)

        # Feedback row.
        self.feedback_row = QHBoxLayout()
        self.feedback_row.setSpacing(_tk.SPACE["sm"])
        self.feedback_label = QLabel("這張卡準嗎？")
        self.feedback_label.setStyleSheet(_tk.text_meta())
        self.feedback_row.addWidget(self.feedback_label)
        self.feedback_row.addStretch()

        self.btn_accurate = self._make_feedback_btn(
            "✅ 準", Feedback.ACCURATE, _tk.PALETTE["ok"],
        )
        self.btn_partial = self._make_feedback_btn(
            "🤔 有點像", Feedback.PARTIAL, _tk.PALETTE["amber"],
        )
        self.btn_wrong = self._make_feedback_btn(
            "❌ 不對", Feedback.WRONG, _tk.PALETTE["danger"],
        )
        for b in (self.btn_accurate, self.btn_partial, self.btn_wrong):
            self.feedback_row.addWidget(b)

        fl.addLayout(self.feedback_row)

        layout.addWidget(self.frame)

        # Wire the off-thread generation hand-off.
        self.card_ready.connect(self._on_card_ready)

        # Initial paint — try to load from disk synchronously so the
        # tab feels instant on startup. If yesterday's card isn't on
        # disk yet, kick off generation in the background.
        self.refresh()

    # ── Helpers for child widgets ────────────────────────────────

    def _make_section_label(self, text: str) -> QLabel:
        from sentinel.ui import tokens as _tk
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {_tk.PALETTE['text_dim']};"
            f" font-size: {_tk.FONT_SIZE['section']}px;"
            f" font-weight: 600;"
            f" letter-spacing: 0.3px;"
            f" margin-top: 6px;"
        )
        return lbl

    def _make_body_label(self) -> QLabel:
        from sentinel.ui import tokens as _tk
        lbl = QLabel("")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color: {_tk.PALETTE['text']};"
            f" font-size: {_tk.FONT_SIZE['body']}px;"
            f" line-height: 1.5;"
        )
        return lbl

    def _make_feedback_btn(self, text: str, state: str, accent: str) -> QPushButton:
        from sentinel.ui import tokens as _tk
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{"
            f" background: transparent;"
            f" color: {accent};"
            f" border: 1px solid {accent};"
            f" border-radius: {_tk.RADIUS['pill']}px;"
            f" padding: 4px 12px;"
            f" font-size: {_tk.FONT_SIZE['meta']}px;"
            f" }}"
            f"QPushButton:hover {{"
            f" background: {accent};"
            f" color: {_tk.PALETTE['text_inverse']};"
            f" }}"
            f"QPushButton:disabled {{"
            f" border-color: {_tk.PALETTE['border']};"
            f" color: {_tk.PALETTE['text_muted']};"
            f" }}"
        )
        btn.clicked.connect(lambda _=False, s=state: self._on_feedback(s))
        return btn

    # ── State transitions ────────────────────────────────────────

    def refresh(self):
        """Load yesterday's card from disk if it exists; otherwise
        kick off background generation. Safe to call repeatedly —
        won't double-trigger generation thanks to `_generating` lock.

        Day-1 special case: if the slime hasn't been alive long
        enough to HAVE a yesterday-with-data, we show an honest
        "I just got here" state instead of either spinning a loader
        forever or rendering a fake-looking error. Manifesto says
        empty IS the truth on day 1; surface that, don't hide it.
        """
        # Day-1 short-circuit. We can't have observations from
        # yesterday if the slime was born today, so don't even try
        # to call the LLM — show the honest day-1 panel.
        try:
            from sentinel.evolution import load_evolution
            evo = load_evolution()
            if evo.days_alive() < 1.0:
                self._render_day_one()
                return
        except Exception as e:
            log.debug(f"day-1 probe failed, falling through: {e}")

        date_iso = yesterday_key()
        existing = load_card(date_iso)
        if existing:
            self._card = existing
            self._render_card()
            return

        # No card yet. Show placeholder and start generation.
        self._render_loading()
        if self._generating:
            return
        self._generating = True

        def _worker():
            try:
                from sentinel.reflection.generator import generate_yesterday
                card = generate_yesterday()
            except Exception as e:
                log.error("background card generation failed: %s", e)
                card = None
            self.card_ready.emit(card)

        threading.Thread(target=_worker, daemon=True, name="daily-card-gen").start()

    def _on_card_ready(self, card: DailyCard | None):
        self._generating = False
        if card is None:
            self._render_error()
            return
        self._card = card
        self._render_card()
        # If today happens to be a week boundary, generate the weekly
        # recap too — also off-thread so the daily card can render
        # immediately. The weekly card surfaces via WeeklyCardWidget
        # if the home tab embeds one; otherwise it just sits on disk
        # for inspection.
        threading.Thread(
            target=self._maybe_generate_weekly,
            daemon=True,
            name="weekly-card-gen",
        ).start()

    def _maybe_generate_weekly(self):
        try:
            from sentinel.reflection.generator import maybe_generate_weekly_card
            maybe_generate_weekly_card()
        except Exception as e:
            log.warning("weekly card generation failed: %s", e)

    # ── Rendering ────────────────────────────────────────────────

    def _render_loading(self):
        self.title_lbl.setText("早安。讓我回想一下昨天…")
        self.date_lbl.setText(yesterday_key())
        self.observation_body.setText("正在翻昨天的記憶…")
        self.insight_body.setText("")
        self.task_body.setText("")
        self._set_feedback_enabled(False)

    def _render_day_one(self):
        """Honest day-1 state. Manifesto says empty IS the truth
        right now — don't fake an error or a spinner.

        Tone notes: same letter-y tone as the welcome modal. Don't
        bullet-list capabilities; don't apologise; do invite the
        user to start the relationship now if they want to."""
        self.title_lbl.setText("我剛剛轉生到你的電腦")
        self.date_lbl.setText("第 1 天")
        self.observation_body.setText(
            "我還沒看到你的昨天——因為昨天的我還沒存在。\n"
            "從今天開始我會慢慢看，明天早上會在這裡寫下我看到的你。"
        )
        self.insight_body.setText(
            "這格之後會放我對你的觀察心得。\n"
            "現在還沒有，是正常的。"
        )
        self.task_body.setText(
            "你不用做什麼。\n"
            "想跟我說話也可以——對話分頁，隨時來。"
        )
        self._set_feedback_enabled(False)

    def _render_error(self):
        self.title_lbl.setText("我有點想不起來。")
        self.date_lbl.setText(yesterday_key())
        self.observation_body.setText(
            "可能是 LLM 連線出問題，或是昨天的觀察紀錄不全。\n"
            "可以到設定確認 API Key，或稍後再試。"
        )
        self.insight_body.setText("")
        self.task_body.setText("")
        self._set_feedback_enabled(False)

    def _render_card(self):
        if not self._card:
            return
        c = self._card
        self.title_lbl.setText(f"早安。{c.title_at_generation}看了你昨天的樣子")
        self.date_lbl.setText(c.date)
        self.observation_body.setText(c.observation or "(這部分沒寫出來)")
        self.insight_body.setText(c.insight or "(這部分沒寫出來)")
        self.task_body.setText(c.micro_task or "(這部分沒寫出來)")

        if c.has_feedback:
            self._lock_feedback_to(c.feedback_state)
        else:
            self._set_feedback_enabled(True)

    def _set_feedback_enabled(self, enabled: bool):
        for b in (self.btn_accurate, self.btn_partial, self.btn_wrong):
            b.setEnabled(enabled)

    def _lock_feedback_to(self, state: str):
        """User already answered — show which button they picked
        (still shaded), disable the others."""
        mapping = {
            Feedback.ACCURATE: self.btn_accurate,
            Feedback.PARTIAL:  self.btn_partial,
            Feedback.WRONG:    self.btn_wrong,
        }
        chosen = mapping.get(state)
        for s, b in mapping.items():
            b.setEnabled(False)
            if b is chosen:
                # Subtle "this was your pick" indicator.
                b.setText(b.text() + "  ✓")
        # Replace the prompt label with a thank-you / lesson-recorded
        # line so the user sees their input was registered.
        self.feedback_label.setText({
            Feedback.ACCURATE: "謝謝。我記住了。",
            Feedback.PARTIAL:  "嗯，我之後再多看看。",
            Feedback.WRONG:    "抱歉看錯了。下次我會調整。",
        }.get(state, ""))

    # ── Feedback handler ─────────────────────────────────────────

    def _on_feedback(self, state: str):
        if not self._card:
            return
        try:
            self._card.record_feedback(state)
            save_card(self._card)
        except Exception as e:
            log.error("could not save feedback: %s", e)
            return

        # Pipe feedback into the routines/preferences card-feedback
        # log so a future generator iteration can avoid re-making the
        # kind of observation the user said WRONG to. We pass a small
        # snapshot of the card content (truncated) so downstream code
        # can correlate "user disliked observations about focus blocks"
        # with the actual text rather than just the date.
        try:
            from sentinel.routines import preferences
            if hasattr(preferences, "record_card_feedback"):
                snapshot = {
                    "observation": (self._card.observation or "")[:200],
                    "insight":     (self._card.insight or "")[:200],
                    "micro_task":  (self._card.micro_task or "")[:120],
                    "form":        self._card.form_at_generation,
                    # Pull a few high-signal raw metric flags so the
                    # feedback log is searchable by metric type.
                    "had_focus_blocks": bool(
                        self._card.raw_metrics.get("focus_blocks")
                    ),
                    "switch_count": self._card.raw_metrics.get("switch_count"),
                }
                preferences.record_card_feedback(
                    date_iso=self._card.date,
                    state=state,
                    snapshot=snapshot,
                )
        except Exception as e:
            log.debug("card feedback hook failed: %s", e)

        self._lock_feedback_to(state)


# ── Weekly card widget ────────────────────────────────────────────


class WeeklyCardWidget(QWidget):
    """Compact weekly recap card.

    Only visible when a weekly card exists for the most recent
    completed week. The home tab embeds this widget below the daily
    card; the widget hides itself when there's nothing to show.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        from sentinel.ui import tokens as _tk

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_tk.SPACE["sm"])

        # A second elevated frame, this time with cyan accent (instead
        # of amber) so the user instantly distinguishes "weekly recap"
        # from "today's reflection".
        self.frame = QFrame()
        self.frame.setStyleSheet(
            f"QFrame {{"
            f" background-color: {_tk.PALETTE['bg_elev']};"
            f" border: 1px solid {_tk.PALETTE['border_subtle']};"
            f" border-left: 3px solid {_tk.PALETTE['cyan']};"
            f" border-radius: {_tk.RADIUS['card']}px;"
            f" }}"
        )
        fl = QVBoxLayout(self.frame)
        fl.setContentsMargins(
            _tk.SPACE["lg"], _tk.SPACE["md"],
            _tk.SPACE["lg"], _tk.SPACE["md"],
        )
        fl.setSpacing(_tk.SPACE["sm"])

        head = QHBoxLayout()
        self.title_lbl = QLabel("📅 本週觀察")
        self.title_lbl.setStyleSheet(
            f"color: {_tk.PALETTE['cyan']};"
            f" font-size: {_tk.FONT_SIZE['title']}px;"
            f" font-weight: 600;"
        )
        head.addWidget(self.title_lbl)
        head.addStretch()
        self.range_lbl = QLabel("")
        self.range_lbl.setStyleSheet(_tk.text_meta())
        head.addWidget(self.range_lbl)
        fl.addLayout(head)

        self.summary_lbl = self._mk_section("總結")
        self.summary_body = self._mk_body()
        self.patterns_lbl = self._mk_section("模式")
        self.patterns_body = self._mk_body()
        self.ahead_lbl = self._mk_section("展望")
        self.ahead_body = self._mk_body()

        for w in (
            self.summary_lbl, self.summary_body,
            self.patterns_lbl, self.patterns_body,
            self.ahead_lbl, self.ahead_body,
        ):
            fl.addWidget(w)

        layout.addWidget(self.frame)

        self.refresh()

    def _mk_section(self, text: str) -> QLabel:
        from sentinel.ui import tokens as _tk
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {_tk.PALETTE['text_dim']};"
            f" font-size: {_tk.FONT_SIZE['section']}px;"
            f" font-weight: 600;"
            f" letter-spacing: 0.3px;"
            f" margin-top: 4px;"
        )
        return lbl

    def _mk_body(self) -> QLabel:
        from sentinel.ui import tokens as _tk
        lbl = QLabel("")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color: {_tk.PALETTE['text']};"
            f" font-size: {_tk.FONT_SIZE['body']}px;"
            f" line-height: 1.5;"
        )
        return lbl

    def refresh(self):
        """Find the latest weekly card on disk; show + populate, or
        hide self if there isn't one yet."""
        try:
            from sentinel.reflection.daily_card import CARDS_DIR
            files = sorted(
                CARDS_DIR.glob("weekly-*.json"),
                reverse=True,
            ) if CARDS_DIR.exists() else []
        except Exception:
            files = []

        if not files:
            self.setVisible(False)
            return

        # Newest. Check it's recent (within 7 days) — older weekly
        # cards stay on disk but don't take up space on the home tab.
        try:
            import json as _json
            with open(files[0], "r", encoding="utf-8") as f:
                data = _json.load(f)
        except Exception as e:
            log.warning("could not load weekly card %s: %s", files[0], e)
            self.setVisible(False)
            return

        from datetime import date as _date, timedelta as _td
        try:
            week_end = _date.fromisoformat(data.get("week_end", ""))
            if (_date.today() - week_end).days > 7:
                self.setVisible(False)
                return
        except ValueError:
            self.setVisible(False)
            return

        self.range_lbl.setText(
            f"{data.get('week_start', '')} → {data.get('week_end', '')}"
        )
        self.summary_body.setText(data.get("summary", "") or "(這部分沒寫出來)")
        self.patterns_body.setText(data.get("patterns", "") or "(這部分沒寫出來)")
        self.ahead_body.setText(data.get("ahead", "") or "(這部分沒寫出來)")
        self.setVisible(True)
