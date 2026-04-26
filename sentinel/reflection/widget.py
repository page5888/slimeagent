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
        """
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

    # ── Rendering ────────────────────────────────────────────────

    def _render_loading(self):
        self.title_lbl.setText("早安。讓我回想一下昨天…")
        self.date_lbl.setText(yesterday_key())
        self.observation_body.setText("正在翻昨天的記憶…")
        self.insight_body.setText("")
        self.task_body.setText("")
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

        # Hook into the routine preferences system so feedback can
        # eventually feed back into detection / judging. Keep this
        # best-effort — the card's value is in the user feedback
        # loop itself, not in connected systems firing.
        try:
            from sentinel.routines import preferences
            if hasattr(preferences, "record_card_feedback"):
                preferences.record_card_feedback(
                    date_iso=self._card.date,
                    state=state,
                )
        except Exception as e:
            log.debug("preferences hook not available yet: %s", e)

        self._lock_feedback_to(state)
