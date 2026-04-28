"""Sentinel GUI - System tray + main window with Chat, Status, Memory, Settings."""
import sys
import json
import time
import threading
import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QLabel, QProgressBar, QGroupBox,
    QFormLayout, QComboBox, QPlainTextEdit, QSystemTrayIcon, QMenu,
    QScrollArea, QFrame, QSpinBox, QMessageBox, QInputDialog,
    QListWidget, QListWidgetItem, QSplitter, QDialog, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QObject, QThread, QSize, QRect, QPoint
from PySide6.QtWidgets import QLayout, QSizePolicy
from PySide6.QtGui import QFont, QColor, QTextCursor, QAction

from sentinel.i18n import t, set_language, get_language
from sentinel.icon import create_icon, create_tray_icon
from sentinel import config

log = logging.getLogger("sentinel.gui")

# ─── Style (loaded from themes.py) ───────────────────────────────────────

from sentinel.themes import get_theme_style, get_theme_info, set_theme, get_theme, list_themes, THEMES


# ─── FlowLayout (reflows items left-to-right, wrapping as width shrinks) ─

class FlowLayout(QLayout):
    """Qt's classic FlowLayout — reflows children like inline-block.

    Ported from the Qt flow layout example. Used so equipment cards
    reflow into multiple columns when the window is wide.
    """

    def __init__(self, parent=None, margin=0, h_spacing=8, v_spacing=8):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._items: list = []
        self._h_space = h_spacing
        self._v_space = v_spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            wid = item.widget()
            space_x = self._h_space
            space_y = self._v_space
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + m.bottom()


# ─── Signal Bridge (thread-safe GUI updates) ─────────────────────────────

class SignalBridge(QObject):
    chat_response = Signal(str)
    status_update = Signal(str)
    sensor_update = Signal(str)  # 感測器狀態（第二行）
    advice_received = Signal(str, str)  # (emoji, message) — 主動建議
    desktop_notify = Signal(str, str)   # (title, message) — 桌面彈窗通知


# ─── Chat Tab ────────────────────────────────────────────────────────────

def _format_action_result_for_chat(audit_entry: dict) -> list[str]:
    """Turn a Phase C1 "action_result" audit entry into 0-N chat lines.

    Only action types known to carry meaningful payload get rendered;
    others (focus_window, open_path, …) just produced the approval's
    generic "✓ 已執行提案" line and don't need a follow-up.

    Returns a list of already-formatted strings; caller appends each
    as its own chat line. Empty list = nothing worth surfacing.
    """
    result = audit_entry.get("result") or {}
    if not isinstance(result, dict):
        return []
    atype = audit_entry.get("action_type", "")

    if atype == "vision.interpret_screen":
        if result.get("ok") and result.get("analysis"):
            provider = result.get("provider") or "VLM"
            analysis = result["analysis"].strip()
            # Truncate ruthlessly — chat lines over ~400 chars get
            # wrapped weirdly in QTextEdit. VLM analyses are usually
            # short, but defensive.
            if len(analysis) > 400:
                analysis = analysis[:400] + "…"
            return [f"({provider} 看完螢幕：){analysis}"]
        if not result.get("ok") and result.get("error"):
            return [f"(VLM 沒看成：{result['error']})"]
        return []

    if atype == "surface.get_clipboard":
        if result.get("ok"):
            text = (result.get("text") or "").replace("\n", " ")
            if not text:
                return ["(剪貼簿是空的)"]
            return [f"(目前剪貼簿：{text[:200]}{'…' if len(text) > 200 else ''})"]
        return []

    if atype == "surface.list_windows":
        if result.get("ok"):
            count = result.get("count", 0)
            windows = result.get("windows", []) or []
            if not windows:
                return [f"(沒有可見視窗)"]
            sample = ", ".join(w.get("title", "?")[:30] for w in windows[:5])
            more = f"，還有 {count - 5}" if count > 5 else ""
            return [f"(目前 {count} 個視窗：{sample}{more})"]
        return []

    if atype == "chain.run":
        # Per-step status rollup. Chain success = all steps success;
        # chain failure could be any mix. Show one line per step so
        # the user sees which part of the plan did/didn't happen.
        steps = result.get("steps") or []
        if not steps:
            return []
        lines = [f"(多步驟動作，共 {len(steps)} 步)"]
        marks = {"success": "✓", "failed": "✗", "skipped": "⤻",
                 "pending": "…", "running": "…"}
        for i, s in enumerate(steps, 1):
            status = s.get("status", "?")
            at = s.get("action_type", "?")
            mark = marks.get(status, "?")
            suffix = ""
            if status == "failed" and s.get("error"):
                suffix = f" ({s['error'][:60]})"
            lines.append(f"  {mark} 步驟 {i} · {at}{suffix}")
        return lines

    # Other action types: nothing extra to say beyond "✓ executed".
    return []


class ChatTab(QWidget):
    def __init__(self, bridge: SignalBridge):
        super().__init__()
        self.bridge = bridge
        from sentinel.ui import tokens as _tk
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _tk.SPACE["lg"], _tk.SPACE["md"],
            _tk.SPACE["lg"], _tk.SPACE["md"],
        )
        layout.setSpacing(_tk.SPACE["md"])

        # Chat display — bubble-style messages, no harsh borders.
        # Light vertical padding around the content so first/last
        # messages aren't flush against the edges.
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setFrameShape(QFrame.NoFrame)
        self.chat_display.setStyleSheet(
            f"QTextEdit {{"
            f" background:{_tk.PALETTE['bg']};"
            f" color:{_tk.PALETTE['text']};"
            f" border:none;"
            f" padding:{_tk.SPACE['sm']}px {_tk.SPACE['md']}px;"
            f" font-size:{_tk.FONT_SIZE['body']}px; }}"
            f"QScrollBar:vertical {{"
            f" background:transparent;"
            f" width:8px; }}"
            f"QScrollBar::handle:vertical {{"
            f" background:{_tk.PALETTE['border']};"
            f" border-radius:4px;"
            f" min-height:20px; }}"
            f"QScrollBar::handle:vertical:hover {{"
            f" background:{_tk.PALETTE['text_muted']}; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{"
            f" height:0px; }}"
        )
        layout.addWidget(self.chat_display, stretch=1)

        # Phase D2: inline approval panel. Lives between the chat
        # transcript and the input row. Only visible when there's at
        # least one pending ACTION-kind proposal, so for pure
        # conversation the chat tab looks identical to before D2.
        # When the slime proposes an action, the card shows up right
        # below the chat with [同意] / [拒絕] buttons — no tab switch
        # required to act on what the slime just suggested.
        self.approval_container = QWidget()
        self.approval_layout = QVBoxLayout(self.approval_container)
        self.approval_layout.setContentsMargins(0, 4, 0, 4)
        self.approval_layout.setSpacing(6)
        self.approval_container.setVisible(False)
        layout.addWidget(self.approval_container)

        # Input area — pill-shaped field that matches the bubble
        # aesthetic of the messages above. Send button is the only
        # primary CTA in this tab; uses the design-token primary
        # treatment.
        input_layout = QHBoxLayout()
        input_layout.setSpacing(_tk.SPACE["sm"])
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText(t("chat_placeholder"))
        self.input_field.returnPressed.connect(self.send_message)
        self.input_field.setStyleSheet(
            f"QLineEdit {{"
            f" background:{_tk.PALETTE['bg_elev']};"
            f" color:{_tk.PALETTE['text']};"
            f" border:1px solid {_tk.PALETTE['border']};"
            f" border-radius:{_tk.RADIUS['pill']}px;"
            f" padding:8px 14px;"
            f" font-size:{_tk.FONT_SIZE['body']}px; }}"
            f"QLineEdit:focus {{"
            f" border-color:{_tk.PALETTE['cyan_dim']}; }}"
        )
        input_layout.addWidget(self.input_field, stretch=1)

        self.send_btn = QPushButton(t("chat_send"))
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self.send_message)
        self.send_btn.setStyleSheet(_tk.btn_primary())
        input_layout.addWidget(self.send_btn)

        # Clear button — ghost-style, intentionally tiny so it doesn't
        # compete with Send. Only the transcript view is cleared;
        # conversation memory survives so the slime stays itself.
        self.clear_btn = QPushButton("🧹")
        self.clear_btn.setToolTip("清空對話畫面（記憶保留）")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setFixedWidth(36)
        self.clear_btn.clicked.connect(self.clear_chat)
        self.clear_btn.setStyleSheet(_tk.btn_ghost())
        input_layout.addWidget(self.clear_btn)

        # 🎨 Album button — opens a grid view of every expression
        # Slime has drawn so far. Same ghost-style as clear, sits
        # next to it so the chat tab's secondary actions are
        # visually grouped.
        self.album_btn = QPushButton("🎨")
        self.album_btn.setToolTip("史萊姆的相簿")
        self.album_btn.setCursor(Qt.PointingHandCursor)
        self.album_btn.setFixedWidth(36)
        self.album_btn.clicked.connect(self._open_album)
        self.album_btn.setStyleSheet(_tk.btn_ghost())
        input_layout.addWidget(self.album_btn)

        layout.addLayout(input_layout)

        # Connect response signal
        self.bridge.chat_response.connect(self._on_response)

        # Phase D2: refresh the inline panel when new proposals arrive
        # from any source (chat reply, future autonomous paths).
        # Using Qt.QueuedConnection via QTimer.singleShot keeps the
        # callback off whatever thread fired it — approval callbacks
        # may fire from background threads.
        try:
            from sentinel.growth import register_on_submit
            register_on_submit(self._on_approval_submitted)
        except Exception as e:
            log.warning(f"chat tab: approval callback not registered: {e}")

        self._append_system("AI Slime 已就緒。你可以開始對話。")
        # Render whatever's already pending at startup (e.g. leftover
        # from a previous session the user never got to).
        self._refresh_approval_panel()

    # ── Chat rendering (Phase L visual cohesion) ──────────────────
    # Pre-Phase L this was four flavors of `<p style="...">…</p>`
    # with hand-coded colors. Now everything goes through
    # sentinel.ui.tokens.bubble_* helpers so message styling stays
    # consistent and any palette adjustment is a one-place change.

    @staticmethod
    def _escape_html(text: str) -> str:
        """HTML-escape user-supplied text so a backtick or literal `<`
        in the chat doesn't break rendering or open injection vectors.
        Newlines preserved as <br> for natural multiline display."""
        return (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>")
        )

    @staticmethod
    def _now_hhmm() -> str:
        """HH:MM timestamp for chat bubbles. Local time so users see
        their own clock — UTC would be confusing for personal chat."""
        from datetime import datetime
        return datetime.now().strftime("%H:%M")

    def _append_system(self, text: str):
        from sentinel.ui import bubble_system
        self.chat_display.append(bubble_system(self._escape_html(text)))
        self.chat_display.moveCursor(QTextCursor.End)

    def _append_user(self, text: str):
        from sentinel.ui import bubble_user
        self.chat_display.append(
            bubble_user(self._escape_html(text), timestamp=self._now_hhmm())
        )
        self.chat_display.moveCursor(QTextCursor.End)

    def _show_thinking(self):
        # The thinking indicator is intentionally distinct from
        # bubble_system so we can find + remove it before the real
        # reply lands.
        self.chat_display.append(
            '<div id="thinking" style="margin:6px 0; text-align:center;">'
            '<span style="color:#f0c674; font-size:11px; font-style:italic;">'
            '大賢者分析中…</span></div>'
        )
        self.chat_display.moveCursor(QTextCursor.End)

    def _remove_thinking(self):
        # Strip the latest thinking bubble. We use a marker rather
        # than scanning by content because the previous "stripping
        # by exact HTML" approach broke when Qt normalized our HTML
        # under the hood. The id="thinking" survives Qt's normalizer.
        html = self.chat_display.toHtml()
        # Match the entire div block, tolerating Qt's reflow.
        import re
        html = re.sub(
            r'<div[^>]*id="thinking"[^>]*>.*?</div>',
            '', html, flags=re.DOTALL,
        )
        self.chat_display.setHtml(html)
        self.chat_display.moveCursor(QTextCursor.End)

    def _append_bot(self, text: str):
        from sentinel.ui import bubble_slime
        self._remove_thinking()
        self.chat_display.append(
            bubble_slime(self._escape_html(text), timestamp=self._now_hhmm())
        )
        self.chat_display.moveCursor(QTextCursor.End)

    def clear_chat(self):
        """Wipe the chat display. We deliberately don't touch
        conversation memory / evolution stats — those live in
        sentinel.chat / sentinel.evolution and are independent of
        what's currently rendered. This is purely a visual reset
        for when the transcript gets long and noisy.
        """
        self.chat_display.clear()
        self._append_system("(對話畫面已清空，記憶仍保留)")

    # ── Slime's self-expression (drawn images) ───────────────────
    # Designed as a CONTAINER concern — sentinel/expression/* is the
    # OS / Qt-free core that decides what to draw and produces a file
    # path. Here we just put that file into the QTextEdit as an HTML
    # <img>. The slime's caption is rendered inside an amber-tinted
    # bubble like a chat message, with the image right below it.

    def append_expression(self, exp) -> None:
        """Render an Expression (sentinel.expression.album.Expression)
        as a chat message. Expects a saved file at exp.absolute_image_path.
        """
        from sentinel.ui import tokens as _tk
        from sentinel.expression.album import ExpressionKind
        kind_label = ExpressionKind.DISPLAY_ZH.get(exp.kind, "畫")
        # Image path needs file:/// + forward slashes for Qt's HTML.
        img_url = exp.absolute_image_path.as_uri()
        # Width capped so a 1024x1024 generated image doesn't blow
        # the chat layout — Qt's table-cell layout honors max-width
        # via the width attribute.
        caption_html = self._escape_html(exp.caption or "")
        html = (
            f'<table align="left" width="65%" cellpadding="10" '
            f'cellspacing="0" style="margin:6px 0;">'
            f'<tr><td style="background-color:rgba(240,198,116,0.12);'
            f' border-left:3px solid {_tk.PALETTE["amber"]};">'
            f'<b style="color:{_tk.PALETTE["amber"]};">史萊姆畫了「{kind_label}」</b>'
            f'<br><br>'
            f'<img src="{img_url}" width="320" style="border-radius:6px;">'
            f'<br><br>'
            f'<span style="color:{_tk.PALETTE["text"]};">{caption_html}</span>'
            f'</td></tr></table>'
            f'<br clear="all">'
        )
        self.chat_display.append(html)
        self.chat_display.moveCursor(QTextCursor.End)

    def _open_album(self) -> None:
        """Open a modal album dialog showing every expression Slime
        has produced so far. Read-only grid + reactions if user wants
        to ❤ / 🤔 something."""
        try:
            dlg = AlbumDialog(self)
            dlg.exec()
        except Exception as e:
            log.warning(f"album open failed: {e}")

    def send_message(self):
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()
        self.send_btn.setEnabled(False)
        self._append_user(text)
        self._show_thinking()

        # Process in background thread
        def _process():
            from sentinel.chat import handle_message
            from sentinel.evolution import load_evolution, record_conversation
            evo = load_evolution()
            record_conversation(evo)
            reply = handle_message(text)
            self.bridge.chat_response.emit(reply)

        threading.Thread(target=_process, daemon=True).start()

    def _on_response(self, text: str):
        self.send_btn.setEnabled(True)
        self._append_bot(text)
        # Phase D2: the slime may have proposed one or more actions
        # during this reply. Repaint the inline card panel so the user
        # can approve/deny without leaving chat.
        self._refresh_approval_panel()

    # ── Phase D2: inline approval cards ──────────────────────────

    def _on_approval_submitted(self, _approval) -> None:
        """Callback fired by approval.submit_action / submit_for_approval.

        Marshal back onto the Qt main thread via singleShot so the UI
        mutation happens where Qt expects it. Payload (the approval
        object) is ignored here — we always refresh from the full
        pending list so we don't have to track card state per-id.
        """
        QTimer.singleShot(0, self._refresh_approval_panel)

    def _refresh_approval_panel(self) -> None:
        """Re-render the inline approval cards from the current pending
        list, filtered to ACTION-kind proposals.

        Code-kind proposals (skill_gen, self_mod) have their own review
        flow on the 待同意 tab with diff views — showing them as tiny
        inline cards in chat would be wrong UX. Action proposals are
        the ones D1 added and the ones users will see most often from
        chat, so those get the quick-click treatment here.
        """
        # Clear old cards
        while self.approval_layout.count() > 0:
            item = self.approval_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        try:
            from sentinel.growth import list_pending, ACTION
            pending = [p for p in list_pending() if p.kind == ACTION]
        except Exception as e:
            log.warning(f"chat tab: couldn't list pending approvals: {e}")
            self.approval_container.setVisible(False)
            return

        if not pending:
            self.approval_container.setVisible(False)
            return

        self.approval_container.setVisible(True)
        for p in pending[:5]:   # cap to keep chat area readable
            card = self._build_approval_card(p)
            self.approval_layout.addWidget(card)
        if len(pending) > 5:
            more = QLabel(
                f"<span style='color:#888; font-size:10px;'>"
                f"... 還有 {len(pending) - 5} 個提案（到「待同意」分頁看全部）</span>"
            )
            more.setAlignment(Qt.AlignCenter)
            self.approval_layout.addWidget(more)

    def _build_approval_card(self, p) -> QWidget:
        """Compact approval card for an ACTION proposal.

        Same 3-px-left-accent aesthetic as the federation cards (Phase
        A3 redesign) for visual consistency across tabs: no filled
        backgrounds, accent color encodes meaning. Yellow = action
        awaiting decision.
        """
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: transparent; border: none; "
            "border-left: 3px solid #ffd166; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 6, 8, 8)
        v.setSpacing(4)

        # Title + action_type subtitle
        title_text = p.title or p.action_type
        title_lbl = QLabel(
            f"<b style='color:#e6e6e6;'>{title_text}</b>"
            f"  <span style='color:#888; font-size:10px;'>"
            f"{p.action_type}</span>"
        )
        title_lbl.setStyleSheet("font-size: 12px;")
        title_lbl.setWordWrap(True)
        v.addWidget(title_lbl)

        if p.reason:
            reason_lbl = QLabel(p.reason)
            reason_lbl.setStyleSheet("color:#aaa; font-size: 10px;")
            reason_lbl.setWordWrap(True)
            v.addWidget(reason_lbl)

        # Phase D4: chain.run previews every step so the user sees
        # the whole plan before approving the chain. One numbered
        # line per step with the step's action_type + title.
        if p.action_type == "chain.run":
            steps = (p.payload or {}).get("steps") or []
            for i, s in enumerate(steps[:5], 1):
                at = (s.get("action_type") or "?") if isinstance(s, dict) else "?"
                title = (s.get("title") or at) if isinstance(s, dict) else "?"
                step_lbl = QLabel(
                    f"<span style='color:#aaa;'>{i}.</span> "
                    f"<span style='color:#e6e6e6;'>{title}</span> "
                    f"<span style='color:#666;'>· {at}</span>"
                )
                step_lbl.setStyleSheet("font-size: 10px;")
                step_lbl.setWordWrap(True)
                v.addWidget(step_lbl)

        # Surface any policy/safety findings so the user sees warnings
        # before clicking approve. Info is filtered out to keep the
        # card compact; warn/error findings always show.
        warnings = [f for f in (list(p.safety_findings or []) +
                                list(p.policy_findings or []))
                    if f.get("level") in ("warn", "error")]
        for w in warnings:
            lvl = w.get("level", "warn")
            color = "#cc6b63" if lvl == "error" else "#ffa502"
            msg = w.get("msg", "")
            findings_lbl = QLabel(
                f"<span style='color:{color};'>⚠ {msg}</span>"
            )
            findings_lbl.setStyleSheet("font-size: 10px;")
            findings_lbl.setWordWrap(True)
            v.addWidget(findings_lbl)

        # Action row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch()

        deny_btn = QPushButton(t("chat_approval_deny"))
        deny_btn.setCursor(Qt.PointingHandCursor)
        deny_btn.setStyleSheet(
            "QPushButton { background:transparent; color:#888;"
            " padding:3px 12px; border:1px solid #444; border-radius:10px;"
            " font-size:10px; }"
            "QPushButton:hover { color:#cc6b63; border-color:#cc6b63; }"
        )
        deny_btn.clicked.connect(
            lambda _checked, aid=p.id: self._on_deny_click(aid)
        )
        btn_row.addWidget(deny_btn)

        approve_btn = QPushButton(t("chat_approval_approve"))
        approve_btn.setCursor(Qt.PointingHandCursor)
        approve_btn.setStyleSheet(
            "QPushButton { background:#ffd166; color:#1a1a1a; font-weight:600;"
            " padding:3px 14px; border:none; border-radius:10px;"
            " font-size:10px; }"
            "QPushButton:hover { background:#ffdc88; }"
        )
        approve_btn.clicked.connect(
            lambda _checked, aid=p.id: self._on_approve_click(aid)
        )
        btn_row.addWidget(approve_btn)

        v.addLayout(btn_row)
        return card

    def _on_approve_click(self, approval_id: str) -> None:
        """Approve handler: invoke the handler registered with the
        approval queue (in our case a surface.* or vision.* executor).
        Any result shows up in the chat transcript as a small
        system-italic line so the user sees what happened without
        opening the audit log.

        Phase D3: for action types whose result carries rich content
        (e.g. vision.interpret_screen → analysis string), we pull the
        result from the audit log tail and append it to chat as a
        second system line. The approval queue already writes an
        "action_result" audit entry with the handler's return value;
        we read that instead of plumbing a new callback channel.
        """
        # Capture the proposal's action_type BEFORE approval since
        # approve() archives the pending file out of the pending dir.
        action_type = ""
        try:
            from sentinel.growth.approval import get_pending
            pend = get_pending(approval_id)
            if pend is not None:
                action_type = pend.action_type
        except Exception:
            pass

        def _do() -> None:
            from sentinel.growth import approve
            from sentinel.growth.approval import audit_tail
            try:
                ok = approve(approval_id, approver="user_chat")
            except Exception as e:
                log.warning(f"approve({approval_id}) raised: {e}")
                ok = False

            # Generic result line — always shown.
            msg = (
                t("chat_approval_ok").format(id=approval_id)
                if ok else t("chat_approval_failed").format(id=approval_id)
            )

            # Rich-result follow-up: fetch the matching "action_result"
            # audit entry and format its meaningful fields. Kept to a
            # small allowlist of action types so we don't accidentally
            # dump huge result dicts into chat.
            extra_lines: list[str] = []
            if ok:
                try:
                    for entry in reversed(audit_tail(n=20)):
                        if (entry.get("id") == approval_id
                                and entry.get("action") == "action_result"):
                            extra_lines = _format_action_result_for_chat(entry)
                            break
                except Exception as e:
                    log.warning(f"audit tail read failed: {e}")

            def _update() -> None:
                self._append_system(msg)
                for line in extra_lines:
                    self._append_bot_note(line)
                self._refresh_approval_panel()
            QTimer.singleShot(0, _update)

        threading.Thread(target=_do, daemon=True).start()

    def _append_bot_note(self, text: str) -> None:
        """A slightly stronger-styled system line used to surface
        action results (e.g. VLM analysis). Italic + green tinted to
        stand out as "this is what the slime just learned" rather
        than a mere status message. Uses the bubble_note token.
        """
        from sentinel.ui import bubble_note
        self.chat_display.append(bubble_note(self._escape_html(text)))
        self.chat_display.moveCursor(QTextCursor.End)

    def _on_deny_click(self, approval_id: str) -> None:
        def _do() -> None:
            from sentinel.growth import reject
            try:
                reject(approval_id, reason="denied in chat tab",
                       approver="user_chat")
            except Exception as e:
                log.warning(f"reject({approval_id}) raised: {e}")
            QTimer.singleShot(
                0, lambda: self._append_system(
                    t("chat_approval_denied").format(id=approval_id)
                ),
            )
            QTimer.singleShot(0, self._refresh_approval_panel)

        threading.Thread(target=_do, daemon=True).start()

    def retranslate(self):
        self.input_field.setPlaceholderText(t("chat_placeholder"))
        self.send_btn.setText(t("chat_send"))
        # Refresh panel so translated button labels apply to any
        # currently-rendered cards.
        self._refresh_approval_panel()


# ─── Album Dialog (Slime 的相簿) ────────────────────────────────────────
#
# Modal dialog that shows every expression Slime has drawn so far.
# Sticking with "modal triggered from chat" (instead of a 6th tab)
# because v0.7-alpha is committed to 5-tab lite mode — adding a tab
# every time we add a feature defeats the purpose.

class AlbumDialog(QDialog):
    """Read-only grid of Slime's drawn expressions, newest first."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from sentinel.ui import tokens as _tk
        self.setWindowTitle("🎨 史萊姆的相簿")
        self.resize(720, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _tk.SPACE["lg"], _tk.SPACE["md"],
            _tk.SPACE["lg"], _tk.SPACE["md"],
        )

        # Header — count + a button to ask Slime to draw on demand.
        head = QHBoxLayout()
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(
            f"color: {_tk.PALETTE['amber']};"
            f" font-size: {_tk.FONT_SIZE['title']}px;"
            f" font-weight: 600;"
        )
        head.addWidget(self.count_lbl)
        head.addStretch()
        self.draw_btn = QPushButton("請史萊姆畫一張")
        self.draw_btn.setCursor(Qt.PointingHandCursor)
        self.draw_btn.setStyleSheet(_tk.btn_secondary())
        self.draw_btn.clicked.connect(self._on_draw_request)
        head.addWidget(self.draw_btn)
        layout.addLayout(head)

        # Scrollable body — vertical stack of expression cards.
        # Could be a QGridLayout for 2-column tiling but vertical
        # reads better when each image gets a caption underneath.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setSpacing(_tk.SPACE["md"])
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, stretch=1)

        # Footer — close button.
        foot = QHBoxLayout()
        foot.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(_tk.btn_ghost())
        close_btn.clicked.connect(self.accept)
        foot.addWidget(close_btn)
        layout.addLayout(foot)

        self._refresh()

    def _refresh(self) -> None:
        """Re-render the album from disk. Cheap enough to call after
        every state change (draw, react, delete) so we don't have to
        track per-card state."""
        from sentinel.expression.album import list_recent, ExpressionKind
        from sentinel.ui import tokens as _tk

        # Clear existing.
        while self.body_layout.count() > 0:
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        recent = list_recent(limit=50)
        self.count_lbl.setText(
            f"史萊姆畫過的 {len(recent)} 張" if recent else "還沒畫過任何一張"
        )

        if not recent:
            empty = QLabel(
                "點上面「請史萊姆畫一張」看看它會給你什麼。\n\n"
                "或等到週日晚上，史萊姆會自己想畫的時候就畫了。"
            )
            empty.setStyleSheet(
                f"color: {_tk.PALETTE['text_muted']};"
                f" font-size: {_tk.FONT_SIZE['body']}px;"
                f" padding: 40px;"
            )
            empty.setAlignment(Qt.AlignCenter)
            self.body_layout.addWidget(empty)
            self.body_layout.addStretch()
            return

        for exp in recent:
            self.body_layout.addWidget(self._build_card(exp))
        self.body_layout.addStretch()

    def _build_card(self, exp) -> QWidget:
        from sentinel.expression.album import ExpressionKind, Reaction
        from sentinel.ui import tokens as _tk

        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{"
            f" background-color: {_tk.PALETTE['bg_elev']};"
            f" border: 1px solid {_tk.PALETTE['border_subtle']};"
            f" border-left: 3px solid {_tk.PALETTE['amber']};"
            f" border-radius: {_tk.RADIUS['card']}px;"
            f" }}"
        )
        v = QVBoxLayout(frame)
        v.setContentsMargins(
            _tk.SPACE["lg"], _tk.SPACE["md"],
            _tk.SPACE["lg"], _tk.SPACE["md"],
        )

        # Title row.
        kind_label = ExpressionKind.DISPLAY_ZH.get(exp.kind, "畫")
        from datetime import datetime
        when = datetime.fromtimestamp(exp.generated_at).strftime("%Y-%m-%d %H:%M")
        head = QLabel(
            f"<b style='color:{_tk.PALETTE['amber']};'>{kind_label}</b>"
            f"  <span style='color:{_tk.PALETTE['text_muted']};'>"
            f"{when} · {exp.slime_form}</span>"
        )
        v.addWidget(head)

        # Image — load via QPixmap so we can scale cleanly.
        img_label = QLabel()
        try:
            from PySide6.QtGui import QPixmap
            pix = QPixmap(str(exp.absolute_image_path))
            if not pix.isNull():
                pix = pix.scaledToWidth(
                    400, Qt.SmoothTransformation,
                )
                img_label.setPixmap(pix)
        except Exception as e:
            img_label.setText(f"(圖片無法載入: {e})")
        img_label.setAlignment(Qt.AlignCenter)
        v.addWidget(img_label)

        # Caption.
        if exp.caption:
            cap = QLabel(exp.caption)
            cap.setWordWrap(True)
            cap.setStyleSheet(
                f"color: {_tk.PALETTE['text']};"
                f" font-size: {_tk.FONT_SIZE['body']}px;"
                f" padding-top: 8px;"
            )
            v.addWidget(cap)

        # Reactions row. Avatar pick button on the left (this is the
        # primary "make this drawing mine" action), reactions on the right.
        rxn_row = QHBoxLayout()

        avatar_btn = QPushButton("設成桌面浮窗")
        avatar_btn.setCursor(Qt.PointingHandCursor)
        avatar_btn.setStyleSheet(_tk.btn_ghost())
        avatar_btn.setToolTip(
            "把這張畫去背後當成桌面小浮窗。\n背景單純的圖效果最好。"
        )
        avatar_btn.clicked.connect(
            lambda _checked, e=exp: self._on_set_as_avatar(e)
        )
        rxn_row.addWidget(avatar_btn)

        rxn_row.addStretch()
        for kind, emoji in (
            (Reaction.LOVE, "❤"),
            (Reaction.HMM, "🤔"),
            (Reaction.SAVED, "💾"),
        ):
            count = sum(1 for r in exp.reactions if r.get("kind") == kind)
            btn = QPushButton(f"{emoji} {count}" if count else emoji)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_tk.btn_ghost())
            btn.clicked.connect(
                lambda _checked, eid=exp.id, k=kind: self._on_reaction(eid, k)
            )
            rxn_row.addWidget(btn)
        v.addLayout(rxn_row)

        return frame

    def _on_reaction(self, expression_id: str, kind: str) -> None:
        from sentinel.expression.album import load_expression, save_expression
        exp = load_expression(expression_id)
        if exp is None:
            return
        try:
            exp.add_reaction(kind)
            save_expression(exp)
        except Exception as e:
            log.warning(f"reaction save failed: {e}")
            return
        self._refresh()

    def _on_set_as_avatar(self, exp) -> None:
        """Cut out the background of this expression and use it as the
        desktop overlay's avatar. Runs the bg removal off-thread because
        the per-pixel pass on a 512×512 image takes ~1s and we don't
        want the dialog to freeze.
        """
        src = exp.absolute_image_path
        if not src.exists():
            QMessageBox.warning(
                self, "設成桌面浮窗",
                f"找不到原圖：{src}",
            )
            return

        # Find the live overlay so we can refresh it without restart.
        # Walk up from this dialog's parent (ChatTab) to its top-level
        # window, which is the MainWindow that owns self.overlay.
        overlay = None
        try:
            parent = self.parent()
            top = parent.window() if parent is not None else None
            overlay = getattr(top, "overlay", None)
        except Exception as e:
            log.warning(f"avatar: couldn't find live overlay: {e}")

        # Disable button-ish UX hint by showing a brief modal feedback.
        # The bg-removal pass is sync-light enough to run inline, but
        # putting it on a thread keeps the dialog responsive on slower
        # machines.
        progress = QMessageBox(self)
        progress.setIcon(QMessageBox.Information)
        progress.setWindowTitle("設成桌面浮窗")
        progress.setText("正在去背…")
        progress.setStandardButtons(QMessageBox.NoButton)
        progress.show()
        QApplication.processEvents()

        def _do() -> None:
            from sentinel import avatar as _avatar
            cutout = _avatar.make_avatar_from_expression(exp.id, src)

            def _ui() -> None:
                progress.close()
                if cutout is None:
                    QMessageBox.warning(
                        self, "設成桌面浮窗",
                        "去背失敗了。可以再試一次或挑另一張。",
                    )
                    return
                _avatar.set_avatar_override(cutout)
                if overlay is not None:
                    try:
                        overlay.set_avatar(str(cutout))
                    except Exception as e:
                        log.warning(f"avatar: overlay live-reload failed: {e}")
                QMessageBox.information(
                    self, "設成桌面浮窗",
                    "已設成桌面浮窗 ✨\n"
                    "如果背景複雜導致邊緣怪怪的，挑另一張背景比較單純的會更好。",
                )
            QTimer.singleShot(0, _ui)

        threading.Thread(target=_do, daemon=True).start()

    def _on_draw_request(self) -> None:
        """User explicitly asked Slime to draw. Slime might draw, but
        also might decline (the cooldown / quality gate could refuse).
        Run off-thread so the LLM + image API call doesn't freeze
        the dialog."""
        self.draw_btn.setEnabled(False)
        self.draw_btn.setText("史萊姆思考中…")

        def _do():
            try:
                from sentinel.expression.generator import generate_expression
                exp = generate_expression()  # Slime picks kind
            except Exception as e:
                log.warning(f"manual expression gen failed: {e}")
                exp = None

            def _ui():
                self.draw_btn.setEnabled(True)
                self.draw_btn.setText("請史萊姆畫一張")
                if exp is None:
                    QMessageBox.information(
                        self, "AI Slime",
                        "史萊姆說現在還想不到要畫什麼。\n"
                        "可能 Gemini API key 沒設定，\n"
                        "或是它今天比較想休息。",
                    )
                else:
                    self._refresh()
            QTimer.singleShot(0, _ui)
        threading.Thread(target=_do, daemon=True).start()


# ─── Home Tab (首頁) ─────────────────────────────────────────────────────

class HomeTab(QWidget):
    """首頁：史萊姆每日反思卡（v0.7-alpha 主舞台）+ 狀態 + 錢包。

    v0.7-alpha 把首頁整理成「先看卡、再看狀態」的順序：
      1. DailyCardWidget — 史萊姆早晨的反思卡（核心 wedge）
      2. 進化 / 觀察次數狀態列（縮成單行）
      3. 錢包 / LLM 狀態（保留但低調，非首要焦點）
    """

    def __init__(self):
        super().__init__()
        from sentinel.ui import tokens as _tk

        # Outer layout owns ONE child — the QScrollArea — so the home
        # tab can grow vertically without squashing its sections.
        # Without this, all the inline widgets (avatar + daily card +
        # weekly card + 3 status cards + wallet group + LLM group)
        # end up sharing the available pixels and the text squeezes
        # together unreadably on smaller windows.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)

        layout = QVBoxLayout(inner)
        layout.setContentsMargins(
            _tk.SPACE["lg"], _tk.SPACE["lg"],
            _tk.SPACE["lg"], _tk.SPACE["lg"],
        )
        layout.setSpacing(_tk.SPACE["md"])

        # ── 每日反思卡（核心 wedge） ──
        # Note: we used to render a SlimeWidget here as well so the
        # card felt like "a face speaking", but at 240x240 it pushed
        # the home tab's minimum height past 900 px and forced the
        # whole window to grow. The slime widget is already on the
        # 🧬 進化 tab at full size; the reflection card's amber accent
        # stripe already signals "this is FROM the slime". Removing
        # the duplicate avatar is the easy win.
        #
        # Existing chat_response → home_tab.slime_widget.react()
        # wires in MainWindow are guarded with try/except, so they
        # silently no-op now that the attribute is gone.
        from sentinel.reflection.widget import DailyCardWidget, WeeklyCardWidget
        self.daily_card = DailyCardWidget(self)
        layout.addWidget(self.daily_card)

        # ── 本週觀察（每週一早上才會出現） ──
        # WeeklyCardWidget hides itself when there's nothing to show
        # (no weekly card on disk, or the most recent one is > 7 days
        # old) so on most days this widget takes 0 vertical space.
        self.weekly_card = WeeklyCardWidget(self)
        layout.addWidget(self.weekly_card)

        # ── 狀態小列（縮成 1 行） ──
        cards = QHBoxLayout()
        cards.setSpacing(_tk.SPACE["sm"])

        self.evo_card = self._make_card("🧬 進化", "載入中...")
        cards.addWidget(self.evo_card["frame"])

        self.obs_card = self._make_card("👁 觀察", "載入中...")
        cards.addWidget(self.obs_card["frame"])

        # 裝備 tab 已凍結，但首頁仍顯示總數（"養"成果的一部分）
        self.equip_card = self._make_card("⚔ 裝備", "載入中...")
        cards.addWidget(self.equip_card["frame"])

        layout.addLayout(cards)

        # ── 錢包區 ──
        wallet_group = QGroupBox("💰 錢包")
        wallet_group.setStyleSheet(
            "QGroupBox { color: #ffa502; font-weight: bold; border: 1px solid #333; "
            "border-radius: 6px; padding: 12px; margin-top: 8px; }"
            "QGroupBox::title { subcontrol-position: top left; padding: 4px 8px; }"
        )
        wl = QVBoxLayout(wallet_group)

        self.wallet_status = QLabel("模式：自備金鑰（BYOK）")
        self.wallet_status.setStyleSheet("color: #ccc; font-size: 13px;")
        wl.addWidget(self.wallet_status)

        self.balance_label = QLabel("")
        self.balance_label.setStyleSheet("color: #2ed573; font-size: 14px;")
        wl.addWidget(self.balance_label)

        btn_row = QHBoxLayout()
        self.login_btn = QPushButton(t("wallet_login"))
        self.login_btn.clicked.connect(self._on_login)
        btn_row.addWidget(self.login_btn)

        self.topup_btn = QPushButton(t("wallet_topup"))
        self.topup_btn.clicked.connect(self._on_topup)
        btn_row.addWidget(self.topup_btn)

        self.wallet_link_btn = QPushButton(t("wallet_link"))
        self.wallet_link_btn.clicked.connect(self._on_wallet_link)
        btn_row.addWidget(self.wallet_link_btn)

        btn_row.addStretch()
        wl.addLayout(btn_row)
        layout.addWidget(wallet_group)

        # ── LLM 狀態 ──
        llm_group = QGroupBox("🤖 LLM 連線狀態")
        llm_group.setStyleSheet(
            "QGroupBox { color: #00dcff; font-weight: bold; border: 1px solid #333; "
            "border-radius: 6px; padding: 12px; margin-top: 8px; }"
            "QGroupBox::title { subcontrol-position: top left; padding: 4px 8px; }"
        )
        ll = QVBoxLayout(llm_group)
        self.llm_status = QLabel("檢查中...")
        self.llm_status.setStyleSheet("color: #ccc; font-size: 12px;")
        self.llm_status.setWordWrap(True)
        ll.addWidget(self.llm_status)
        layout.addWidget(llm_group)

        layout.addStretch()
        self.refresh()

    def _make_card(self, title: str, value: str) -> dict:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: rgba(255,255,255,0.05); "
            "border: 1px solid #333; border-radius: 8px; padding: 12px; }"
        )
        fl = QVBoxLayout(frame)
        fl.setSpacing(4)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #888; font-size: 11px;")
        title_lbl.setAlignment(Qt.AlignCenter)
        fl.addWidget(title_lbl)
        value_lbl = QLabel(value)
        value_lbl.setStyleSheet("color: #fff; font-size: 16px; font-weight: bold;")
        value_lbl.setAlignment(Qt.AlignCenter)
        fl.addWidget(value_lbl)
        return {"frame": frame, "value": value_lbl}

    def refresh(self):
        try:
            from sentinel.evolution import load_evolution
            evo = load_evolution()
            self.evo_card["value"].setText(f"{evo.title}")
            self.obs_card["value"].setText(f"{evo.total_observations:,}")
        except Exception:
            pass

        # Daily card may need to repaint if it just generated in a
        # background thread mid-session, or if the user clicked
        # feedback in another widget. refresh() is called every 30s
        # by evo_timer, so this is the catch-all.
        try:
            self.daily_card.refresh()
        except Exception as e:
            log.debug("daily card refresh failed: %s", e)

        try:
            self.weekly_card.refresh()
        except Exception as e:
            log.debug("weekly card refresh failed: %s", e)

        try:
            from sentinel.wallet.equipment import load_equipment
            eq = load_equipment()
            equipped_count = sum(1 for v in eq.equipped.values() if v)
            inv_count = len(eq.inventory)
            self.equip_card["value"].setText(f"裝備 {equipped_count} / 背包 {inv_count}")
        except Exception:
            pass

        # LLM status
        lines = []
        for p in config.LLM_PROVIDERS:
            name = p.get("name", "?")
            has_key = bool(p.get("api_key"))
            enabled = p.get("enabled", False)
            if enabled and has_key:
                lines.append(f"✅ {name}")
            elif enabled and not has_key:
                lines.append(f"⚠️ {name}（未設定金鑰）")
        if not lines:
            lines.append("⚠️ 未設定任何 LLM provider，請到魔法陣設定")
        self.llm_status.setText("  |  ".join(lines))

        # Wallet / auth status
        try:
            from sentinel.relay_client import AUTH_FILE, _get_token
            if AUTH_FILE.exists() and _get_token():
                data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
                name = data.get("display_name", data.get("email", "?"))
                self.wallet_status.setText(f"已登入：{name}")
                self.login_btn.setText("登出")
                # Try fetching live balance from relay
                try:
                    from sentinel import relay_client
                    result = relay_client._request("POST", "wallet/balance")
                    bal = result.get("balance", 0)
                    self.balance_label.setText(f"餘額：{bal:,} 點")
                except Exception:
                    self.balance_label.setText("")
            else:
                self.wallet_status.setText("尚未登入")
                self.balance_label.setText("")
                self.login_btn.setText(t("wallet_login"))
        except Exception:
            self.wallet_status.setText("尚未登入")

    def _on_login(self):
        """Google OAuth login from home page."""
        from sentinel.relay_client import _get_token
        if _get_token():
            # Already logged in — offer logout
            reply = QMessageBox.question(
                self, "帳號",
                "已登入，是否要登出？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                from sentinel.google_auth import clear_auth
                clear_auth()
                self.refresh()
                QMessageBox.information(self, "登出", "已登出。")
            return

        client_id = config.GOOGLE_CLIENT_ID
        relay_url = config.RELAY_SERVER_URL
        if not client_id:
            QMessageBox.warning(self, "登入", "Google Client ID 未設定，請到魔法陣設定。")
            return

        self.login_btn.setEnabled(False)
        self.login_btn.setText("登入中...")

        import threading

        def _do():
            try:
                from sentinel.google_auth import full_login_flow
                auth_data = full_login_flow(client_id, relay_url,
                                             client_secret=config.GOOGLE_CLIENT_SECRET)
                from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(
                    self, "_login_done",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, auth_data.get("display_name", "?")),
                    Q_ARG(str, ""),
                )
            except Exception as e:
                from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(
                    self, "_login_done",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, ""),
                    Q_ARG(str, str(e)),
                )

        threading.Thread(target=_do, daemon=True).start()

    @Slot(str, str)
    def _login_done(self, name: str, error: str):
        self.login_btn.setEnabled(True)
        self.login_btn.setText(t("wallet_login"))
        if error:
            QMessageBox.warning(self, "登入失敗", error)
        else:
            self.refresh()
            QMessageBox.information(self, "登入成功", f"歡迎，{name}！")

    def _on_topup(self):
        """Open 5888 wallet top-up page."""
        import webbrowser
        webbrowser.open("https://wallet-5888.web.app/index.html")

    def _on_wallet_link(self):
        """Open 5888 wallet page."""
        import webbrowser
        from sentinel.relay_client import AUTH_FILE
        if AUTH_FILE.exists():
            try:
                data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
                uid = data.get("uid", "")
                if uid:
                    webbrowser.open(f"https://wallet-5888.web.app/index.html")
                    return
            except Exception:
                pass
        webbrowser.open("https://wallet-5888.web.app/index.html")

    def retranslate(self):
        self.login_btn.setText(t("wallet_login"))
        self.topup_btn.setText(t("wallet_topup"))
        self.wallet_link_btn.setText(t("wallet_link"))


# ─── Market Tab (市場) ───────────────────────────────────────────────────

class MarketTab(QWidget):
    """市場頁籤 — 投票區 + 交易市場。"""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # ── Sub-tabs: 投票 / 市場 ──
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setStyleSheet("QTabBar::tab { padding: 6px 16px; }")
        layout.addWidget(self.sub_tabs)

        self.vote_tab = self._build_vote_tab()
        self.trade_tab = self._build_trade_tab()
        self.creator_tab = self._build_creator_tab()
        self.sub_tabs.addTab(self.vote_tab, "🗳 社群投票")
        self.sub_tabs.addTab(self.trade_tab, "💰 裝備交易")
        self.sub_tabs.addTab(self.creator_tab, "✏ 投稿創作")

        # Status bar
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 11px; padding: 2px;")
        layout.addWidget(self.status_label)

    # ── Vote Tab ─────────────────────────────────────────────────────

    def _build_vote_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        # Filter row
        filter_row = QHBoxLayout()
        self.vote_slot_filter = QComboBox()
        self.vote_slot_filter.addItem("全部欄位", "")
        for slot, name in [
            ("helmet", "頭盔"), ("eyes", "眼睛"), ("mouth", "嘴巴"),
            ("skin", "皮膚"), ("background", "背景"), ("core", "晶核"),
            ("left_hand", "左手"), ("right_hand", "右手"),
            ("mount", "載具"), ("vfx", "特效"), ("drone", "精靈"), ("title", "稱號"),
        ]:
            self.vote_slot_filter.addItem(name, slot)
        filter_row.addWidget(QLabel("欄位:"))
        filter_row.addWidget(self.vote_slot_filter)

        self.vote_refresh_btn = QPushButton("重新整理")
        self.vote_refresh_btn.clicked.connect(self._load_submissions)
        filter_row.addWidget(self.vote_refresh_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Submission list (scroll area)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.vote_list_widget = QWidget()
        self.vote_list_layout = QVBoxLayout(self.vote_list_widget)
        self.vote_list_layout.setSpacing(8)
        self.vote_list_layout.addStretch()
        scroll.setWidget(self.vote_list_widget)
        layout.addWidget(scroll)

        # Page controls
        page_row = QHBoxLayout()
        self.vote_page = 1
        self.vote_prev_btn = QPushButton("← 上一頁")
        self.vote_prev_btn.clicked.connect(lambda: self._change_vote_page(-1))
        self.vote_next_btn = QPushButton("下一頁 →")
        self.vote_next_btn.clicked.connect(lambda: self._change_vote_page(1))
        self.vote_page_label = QLabel("第 1 頁")
        self.vote_page_label.setAlignment(Qt.AlignCenter)
        page_row.addWidget(self.vote_prev_btn)
        page_row.addWidget(self.vote_page_label)
        page_row.addWidget(self.vote_next_btn)
        layout.addLayout(page_row)

        return w

    # ── Trade Tab ────────────────────────────────────────────────────

    def _build_trade_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        # Filter row
        filter_row = QHBoxLayout()
        self.trade_slot_filter = QComboBox()
        self.trade_slot_filter.addItem("全部欄位", "")
        for slot, name in [
            ("helmet", "頭盔"), ("eyes", "眼睛"), ("mouth", "嘴巴"),
            ("skin", "皮膚"), ("background", "背景"), ("core", "晶核"),
            ("left_hand", "左手"), ("right_hand", "右手"),
            ("mount", "載具"), ("vfx", "特效"), ("drone", "精靈"), ("title", "稱號"),
        ]:
            self.trade_slot_filter.addItem(name, slot)
        filter_row.addWidget(QLabel("欄位:"))
        filter_row.addWidget(self.trade_slot_filter)

        self.trade_rarity_filter = QComboBox()
        self.trade_rarity_filter.addItem("全部稀有度", "")
        for r, name in [
            ("common", "普通"), ("uncommon", "優良"), ("rare", "稀有"),
            ("epic", "史詩"), ("legendary", "傳說"), ("mythic", "神話"),
            ("ultimate", "究極"),
        ]:
            self.trade_rarity_filter.addItem(name, r)
        filter_row.addWidget(QLabel("稀有度:"))
        filter_row.addWidget(self.trade_rarity_filter)

        self.trade_refresh_btn = QPushButton("重新整理")
        self.trade_refresh_btn.clicked.connect(self._load_listings)
        filter_row.addWidget(self.trade_refresh_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Listing list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.trade_list_widget = QWidget()
        self.trade_list_layout = FlowLayout(self.trade_list_widget, margin=4,
                                            h_spacing=8, v_spacing=8)
        scroll.setWidget(self.trade_list_widget)
        layout.addWidget(scroll)

        # Page controls
        page_row = QHBoxLayout()
        self.trade_page = 1
        self.trade_prev_btn = QPushButton("← 上一頁")
        self.trade_prev_btn.clicked.connect(lambda: self._change_trade_page(-1))
        self.trade_next_btn = QPushButton("下一頁 →")
        self.trade_next_btn.clicked.connect(lambda: self._change_trade_page(1))
        self.trade_page_label = QLabel("第 1 頁")
        self.trade_page_label.setAlignment(Qt.AlignCenter)
        page_row.addWidget(self.trade_prev_btn)
        page_row.addWidget(self.trade_page_label)
        page_row.addWidget(self.trade_next_btn)
        layout.addLayout(page_row)

        return w

    # ── Creator Tab (投稿) ──────────────────────────────────────────

    _CREATOR_SLOTS = [
        ("helmet", "頭盔"), ("eyes", "眼睛"), ("mouth", "嘴巴"),
        ("skin", "皮膚"), ("background", "背景"), ("core", "晶核"),
        ("left_hand", "左手"), ("right_hand", "右手"),
        ("mount", "載具"), ("vfx", "特效"), ("drone", "精靈"), ("title", "稱號"),
    ]
    _CREATOR_RARITIES = [
        ("common", "普通"), ("uncommon", "優良"), ("rare", "稀有"),
        ("epic", "史詩"), ("legendary", "傳說"), ("mythic", "神話"),
        ("ultimate", "究極"),
    ]

    def _build_creator_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Description header
        desc = QLabel(
            "<span style='color:#aaa; font-size:12px;'>"
            "上傳你設計的裝備圖（PNG / GIF，≤512KB，≤256×256），"
            "提交後會進入社群投票審核。通過稀有度門檻可獲得 100 點獎勵，"
            "每天最多投稿 3 件。"
            "</span>"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Form layout
        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignRight)

        # Name
        self.creator_name_input = QLineEdit()
        self.creator_name_input.setMaxLength(30)
        self.creator_name_input.setPlaceholderText("1–30 字，例如「星屑賢者的權杖」")
        form.addRow("作品名稱：", self.creator_name_input)

        # Slot + rarity (same row)
        slot_rar_row = QHBoxLayout()
        self.creator_slot_combo = QComboBox()
        for slot_id, slot_zh in self._CREATOR_SLOTS:
            self.creator_slot_combo.addItem(slot_zh, slot_id)
        slot_rar_row.addWidget(self.creator_slot_combo, stretch=1)

        slot_rar_row.addWidget(QLabel("稀有度："))
        self.creator_rarity_combo = QComboBox()
        for r_id, r_zh in self._CREATOR_RARITIES:
            self.creator_rarity_combo.addItem(r_zh, r_id)
        slot_rar_row.addWidget(self.creator_rarity_combo, stretch=1)
        slot_rar_wrapper = QWidget()
        slot_rar_wrapper.setLayout(slot_rar_row)
        form.addRow("欄位：", slot_rar_wrapper)

        # Description
        self.creator_desc_input = QPlainTextEdit()
        self.creator_desc_input.setFixedHeight(60)
        self.creator_desc_input.setPlaceholderText(
            "簡短介紹這件裝備（選填）— 例如背景故事、使用情境"
        )
        form.addRow("作品說明：", self.creator_desc_input)

        # Image picker
        img_row = QHBoxLayout()
        self.creator_image_path_label = QLabel("（尚未選擇圖檔）")
        self.creator_image_path_label.setStyleSheet("color: #888; font-size: 12px;")
        self.creator_image_path_label.setWordWrap(True)
        img_row.addWidget(self.creator_image_path_label, stretch=1)
        self.creator_pick_image_btn = QPushButton("選擇圖檔…")
        self.creator_pick_image_btn.clicked.connect(self._creator_pick_image)
        img_row.addWidget(self.creator_pick_image_btn)
        img_wrapper = QWidget()
        img_wrapper.setLayout(img_row)
        form.addRow("裝備圖：", img_wrapper)

        # Image preview
        self.creator_image_preview = QLabel("")
        self.creator_image_preview.setFixedSize(96, 96)
        self.creator_image_preview.setStyleSheet(
            "QLabel { border: 1px dashed #444; background: rgba(0,0,0,0.15); }"
        )
        self.creator_image_preview.setAlignment(Qt.AlignCenter)
        form.addRow("預覽：", self.creator_image_preview)

        layout.addLayout(form)

        # Submit button + status
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.creator_submit_btn = QPushButton("送出投稿")
        self.creator_submit_btn.setStyleSheet(
            "QPushButton { background: #00dcff; color: #000; "
            "border-radius: 4px; padding: 8px 24px; font-weight: bold; }"
            "QPushButton:hover { background: #00b8d4; }"
            "QPushButton:disabled { background: #444; color: #888; }"
        )
        self.creator_submit_btn.clicked.connect(self._creator_submit)
        btn_row.addWidget(self.creator_submit_btn)
        layout.addLayout(btn_row)

        self.creator_status_label = QLabel("")
        self.creator_status_label.setStyleSheet(
            "color: #888; font-size: 12px; padding: 4px;"
        )
        self.creator_status_label.setWordWrap(True)
        layout.addWidget(self.creator_status_label)

        layout.addStretch()

        # State
        self._creator_image_path = ""

        return w

    def _creator_pick_image(self):
        from PySide6.QtWidgets import QFileDialog
        from PySide6.QtGui import QPixmap

        fp, _filter = QFileDialog.getOpenFileName(
            self, "選擇裝備圖", "",
            "圖像檔案 (*.png *.gif)",
        )
        if not fp:
            return

        # Basic size check — match server limit
        from pathlib import Path
        size_bytes = Path(fp).stat().st_size
        if size_bytes > 512 * 1024:
            QMessageBox.warning(
                self, "檔案太大",
                f"檔案大小 {size_bytes // 1024} KB 超過 512 KB 上限",
            )
            return

        pm = QPixmap(fp)
        if pm.isNull():
            QMessageBox.warning(self, "無法讀取", "無法讀取這個圖檔")
            return

        # Check dimensions
        if pm.width() > 256 or pm.height() > 256:
            QMessageBox.warning(
                self, "圖片太大",
                f"圖片尺寸 {pm.width()}×{pm.height()} 超過 256×256",
            )
            return

        self._creator_image_path = fp
        self.creator_image_path_label.setText(Path(fp).name)
        self.creator_image_path_label.setStyleSheet("color: #2ed573; font-size: 12px;")

        # Preview — scale to fit 96×96 square
        scaled = pm.scaled(
            96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.creator_image_preview.setPixmap(scaled)

    def _creator_submit(self):
        from sentinel import relay_client

        name = self.creator_name_input.text().strip()
        slot = self.creator_slot_combo.currentData()
        rarity = self.creator_rarity_combo.currentData()
        description = self.creator_desc_input.toPlainText().strip()
        image_path = self._creator_image_path

        # Client-side validation
        if not name:
            QMessageBox.warning(self, "缺少名稱", "請填寫作品名稱")
            return
        if len(name) > 30:
            QMessageBox.warning(self, "名稱太長", "作品名稱最多 30 字")
            return
        if not image_path:
            QMessageBox.warning(self, "缺少圖檔", "請選擇裝備圖檔")
            return

        self.creator_submit_btn.setEnabled(False)
        self.creator_status_label.setText("上傳圖片中…")
        QApplication.processEvents()

        try:
            # Step 1: upload image
            img_resp = relay_client.upload_image(image_path)
            image_id = img_resp.get("image_id", "")

            # Step 2: submit equipment
            self.creator_status_label.setText("送出投稿中…")
            QApplication.processEvents()

            result = relay_client.submit_equipment(
                name=name, slot=slot, rarity=rarity,
                description=description, image_id=image_id,
            )

            threshold = result.get("vote_threshold", "?")
            QMessageBox.information(
                self, "投稿成功",
                f"作品「{name}」已送出！\n"
                f"需要 {threshold} 票通過，通過後獲得 100 點獎勵。\n"
                f"可以到「社群投票」分頁查看投票進度。",
            )

            # Reset form
            self.creator_name_input.clear()
            self.creator_desc_input.clear()
            self._creator_image_path = ""
            self.creator_image_path_label.setText("（尚未選擇圖檔）")
            self.creator_image_path_label.setStyleSheet("color: #888; font-size: 12px;")
            self.creator_image_preview.clear()
            self.creator_status_label.setText("投稿成功！")
            # Refresh vote list if user switches back
            self._load_submissions()
        except relay_client.RelayError as e:
            msg = e.message or "未知錯誤"
            if e.code == "429":
                msg = "今日投稿次數已達上限（每日 3 件）"
            elif e.code == "409":
                msg = f"名稱已被使用：{msg}"
            elif e.code == "402":
                msg = "點數不足"
            QMessageBox.warning(self, "投稿失敗", msg)
            self.creator_status_label.setText(f"失敗：{msg}")
        except Exception as e:
            QMessageBox.warning(self, "投稿失敗", str(e))
            self.creator_status_label.setText(f"錯誤：{e}")
        finally:
            self.creator_submit_btn.setEnabled(True)

    # ── Data Loading ─────────────────────────────────────────────────

    def _load_submissions(self):
        """Load pending submissions from relay server."""
        from sentinel import relay_client
        slot = self.vote_slot_filter.currentData() or ""
        try:
            data = relay_client.get_submissions(
                status="pending", slot=slot, page=self.vote_page,
            )
            self._render_submissions(data.get("items", []), data.get("total", 0))
            self.status_label.setText(
                f"投票區：共 {data.get('total', 0)} 件作品"
            )
        except relay_client.RelayError as e:
            self._show_empty_vote(f"無法連線：{e.message}")
        except Exception as e:
            self._show_empty_vote(f"錯誤：{e}")

    def _load_listings(self):
        """Load marketplace listings from relay server."""
        from sentinel import relay_client
        slot = self.trade_slot_filter.currentData() or ""
        rarity = self.trade_rarity_filter.currentData() or ""
        try:
            data = relay_client.get_listings(
                slot=slot, rarity=rarity, page=self.trade_page,
            )
            self._render_listings(data.get("items", []), data.get("total", 0))
            self.status_label.setText(
                f"市場：共 {data.get('total', 0)} 件上架"
            )
        except relay_client.RelayError as e:
            self._show_empty_trade(f"無法連線：{e.message}")
        except Exception as e:
            self._show_empty_trade(f"錯誤：{e}")

    # ── Rendering ────────────────────────────────────────────────────

    RARITY_COLORS = {
        "common": "#aaa", "uncommon": "#2ed573", "rare": "#1e90ff",
        "epic": "#a855f7", "legendary": "#ffa502", "mythic": "#ff4757",
        "ultimate": "#ffd700",
    }

    def _clear_layout(self, layout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_submissions(self, items: list, total: int):
        self._clear_layout(self.vote_list_layout)
        if not items:
            self._show_empty_vote("目前沒有待投票的作品")
            return

        for item in items:
            card = self._make_submission_card(item)
            self.vote_list_layout.addWidget(card)
        self.vote_list_layout.addStretch()

        total_pages = max(1, (total + 19) // 20)
        self.vote_page_label.setText(f"第 {self.vote_page} / {total_pages} 頁")
        self.vote_prev_btn.setEnabled(self.vote_page > 1)
        self.vote_next_btn.setEnabled(self.vote_page < total_pages)

    def _make_submission_card(self, item: dict) -> QFrame:
        card = QFrame()
        rarity = item.get("rarity", "common")
        color = self.RARITY_COLORS.get(rarity, "#aaa")
        card.setStyleSheet(
            f"QFrame {{ background: rgba(255,255,255,0.04); "
            f"border: 1px solid {color}; border-radius: 6px; padding: 8px; }}"
        )
        layout = QHBoxLayout(card)
        layout.setSpacing(12)

        # Info
        info = QVBoxLayout()
        name_lbl = QLabel(
            f"<b style='color:{color};'>[{item.get('rarity', '?')}]</b> "
            f"<b>{item.get('name', '?')}</b>"
        )
        info.addWidget(name_lbl)

        slot_zh = {
            "helmet": "頭盔", "eyes": "眼睛", "mouth": "嘴巴",
            "left_hand": "左手", "right_hand": "右手", "skin": "皮膚",
            "background": "背景", "core": "晶核", "mount": "載具",
            "vfx": "特效", "drone": "精靈", "title": "稱號",
        }
        detail_lbl = QLabel(
            f"<span style='color:#888;'>"
            f"欄位: {slot_zh.get(item.get('slot', ''), '?')} | "
            f"創作者: {item.get('creator_name', '?')} | "
            f"{item.get('description', '')}"
            f"</span>"
        )
        detail_lbl.setWordWrap(True)
        info.addWidget(detail_lbl)

        # Buff display
        buff = item.get("buff")
        if buff:
            buff_text = " | ".join(f"{k}: +{v}" for k, v in buff.items())
            buff_lbl = QLabel(f"<span style='color:#2ed573;'>Buff: {buff_text}</span>")
            info.addWidget(buff_lbl)

        layout.addLayout(info, stretch=1)

        # Vote section
        vote_box = QVBoxLayout()
        vote_count = item.get("vote_count", 0)
        threshold = item.get("vote_threshold", 10)
        progress_lbl = QLabel(
            f"<b style='color:#ffa502;'>{vote_count}/{threshold}</b> 票"
        )
        progress_lbl.setAlignment(Qt.AlignCenter)
        vote_box.addWidget(progress_lbl)

        vote_bar = QProgressBar()
        vote_bar.setRange(0, threshold)
        vote_bar.setValue(min(vote_count, threshold))
        vote_bar.setFixedWidth(80)
        vote_bar.setTextVisible(False)
        vote_bar.setStyleSheet(
            "QProgressBar { background: #333; border-radius: 3px; height: 6px; }"
            "QProgressBar::chunk { background: #ffa502; border-radius: 3px; }"
        )
        vote_box.addWidget(vote_bar)

        if item.get("user_voted"):
            voted_lbl = QLabel("<span style='color:#2ed573;'>已投票</span>")
            voted_lbl.setAlignment(Qt.AlignCenter)
            vote_box.addWidget(voted_lbl)
        else:
            vote_btn = QPushButton("投票 (10pt)")
            vote_btn.setStyleSheet(
                "QPushButton { background: #ffa502; color: #000; "
                "border-radius: 4px; padding: 4px 12px; font-weight: bold; }"
                "QPushButton:hover { background: #e69500; }"
            )
            sub_id = item.get("id", "")
            vote_btn.clicked.connect(lambda checked, sid=sub_id: self._do_vote(sid))
            vote_box.addWidget(vote_btn)

        layout.addLayout(vote_box)
        return card

    def _render_listings(self, items: list, total: int):
        self._clear_layout(self.trade_list_layout)
        if not items:
            self._show_empty_trade("目前市場上沒有商品")
            return

        for item in items:
            card = self._make_listing_card(item)
            self.trade_list_layout.addWidget(card)

        total_pages = max(1, (total + 19) // 20)
        self.trade_page_label.setText(f"第 {self.trade_page} / {total_pages} 頁")
        self.trade_prev_btn.setEnabled(self.trade_page > 1)
        self.trade_next_btn.setEnabled(self.trade_page < total_pages)

    _MKT_TINT = {
        "common": "rgba(170,170,170,0.12)",
        "uncommon": "rgba(46,213,115,0.15)",
        "rare": "rgba(30,144,255,0.18)",
        "epic": "rgba(168,85,247,0.20)",
        "legendary": "rgba(255,165,2,0.22)",
        "mythic": "rgba(255,71,87,0.25)",
        "ultimate": "rgba(255,215,0,0.30)",
    }

    _SLOT_ZH = {
        "helmet": "頭盔", "eyes": "眼睛", "mouth": "嘴巴",
        "left_hand": "左手", "right_hand": "右手", "skin": "皮膚",
        "background": "背景", "core": "晶核", "mount": "載具",
        "vfx": "特效", "drone": "精靈", "title": "稱號",
    }

    def _make_listing_card(self, item: dict) -> QFrame:
        from sentinel.equipment_visuals import EquipmentIcon
        from sentinel.wallet.equipment import EQUIPMENT_POOL

        rarity = item.get("rarity", "common")
        color = self.RARITY_COLORS.get(rarity, "#aaa")
        tint = self._MKT_TINT.get(rarity, "rgba(170,170,170,0.12)")

        card = QFrame()
        card.setFixedSize(240, 128)
        card.setStyleSheet(
            f"QFrame {{ background: {tint}; "
            f"border: 1px solid rgba(255,255,255,0.06); "
            f"border-left: 3px solid {color}; "
            f"border-radius: 6px; }}"
        )
        outer = QVBoxLayout(card)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        # Top: icon + name/meta
        top = QHBoxLayout()
        top.setSpacing(8)
        template = next(
            (t for t in EQUIPMENT_POOL if t["name"] == item.get("template_name")),
            None,
        )
        visual_key = template.get("visual", "") if template else ""
        icon = EquipmentIcon(visual_key, rarity, item.get("slot", ""), size=56)
        top.addWidget(icon)

        info = QVBoxLayout()
        info.setSpacing(2)
        info.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel(
            f"<b style='color:#eee;'>{item.get('template_name', '?')}</b>"
        )
        name_lbl.setWordWrap(True)
        info.addWidget(name_lbl)

        slot_zh = self._SLOT_ZH.get(item.get("slot", ""), "?")
        seller = item.get("seller_name", "?")
        meta_lbl = QLabel(
            f"<span style='color:#888; font-size:11px;'>"
            f"{slot_zh} · 賣家 {seller}</span>"
        )
        info.addWidget(meta_lbl)
        info.addStretch()
        top.addLayout(info, stretch=1)
        outer.addLayout(top)

        # Bottom: price + buy button side by side
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.setContentsMargins(0, 0, 0, 0)
        price_lbl = QLabel(
            f"<b style='color:#ffd700; font-size:14px;'>"
            f"{item.get('price', 0):,} pt</b>"
        )
        bottom.addWidget(price_lbl)
        bottom.addStretch()

        buy_btn = QPushButton("購買")
        buy_btn.setStyleSheet(
            "QPushButton { background: #2ed573; color: #000; "
            "border: none; border-radius: 4px; padding: 5px 16px; "
            "font-weight: bold; font-size: 12px; }"
            "QPushButton:hover { background: #26b863; }"
        )
        listing_id = item.get("id", "")
        buy_btn.clicked.connect(
            lambda _, lid=listing_id: self._do_buy(lid)
        )
        bottom.addWidget(buy_btn)
        outer.addLayout(bottom)

        return card

    # ── Actions ──────────────────────────────────────────────────────

    def _do_vote(self, submission_id: str):
        from sentinel import relay_client
        try:
            result = relay_client.vote(submission_id)
            msg = f"投票成功！({result.get('vote_count', '?')}/{result.get('vote_threshold', '?')})"
            if result.get("approved"):
                msg += "\n這個作品已通過投票，加入裝備池了！"
            QMessageBox.information(self, "投票", msg)
            self._load_submissions()
        except relay_client.RelayError as e:
            QMessageBox.warning(self, "投票失敗", e.message)

    def _do_buy(self, listing_id: str):
        from sentinel import relay_client
        reply = QMessageBox.question(
            self, "確認購買", "確定要購買這個裝備嗎？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            result = relay_client.buy_listing(listing_id)
            QMessageBox.information(
                self, "購買成功",
                f"已購買 {result.get('item_name', '?')}\n"
                f"花費 {result.get('price', 0):,} 點",
            )
            self._load_listings()
        except relay_client.RelayError as e:
            QMessageBox.warning(self, "購買失敗", e.message)

    # ── Helpers ──────────────────────────────────────────────────────

    def _show_empty_vote(self, msg: str):
        self._clear_layout(self.vote_list_layout)
        lbl = QLabel(f"<span style='color:#888; font-size:13px;'>{msg}</span>")
        lbl.setAlignment(Qt.AlignCenter)
        self.vote_list_layout.addWidget(lbl)
        self.vote_list_layout.addStretch()

    def _show_empty_trade(self, msg: str):
        self._clear_layout(self.trade_list_layout)
        lbl = QLabel(f"<span style='color:#888; font-size:13px;'>{msg}</span>")
        lbl.setAlignment(Qt.AlignCenter)
        self.trade_list_layout.addWidget(lbl)

    def _change_vote_page(self, delta: int):
        self.vote_page = max(1, self.vote_page + delta)
        self._load_submissions()

    def _change_trade_page(self, delta: int):
        self.trade_page = max(1, self.trade_page + delta)
        self._load_listings()

    def retranslate(self):
        self.sub_tabs.setTabText(0, "🗳 社群投票")
        self.sub_tabs.setTabText(1, "💰 裝備交易")
        self.sub_tabs.setTabText(2, "✏ 投稿創作")

    def refresh(self):
        pass  # Don't auto-refresh network calls on timer


# ─── Equipment Tab (裝備庫) ──────────────────────────────────────────────

class EquipmentTab(QWidget):
    """背包、裝備、合成、掉落紀錄。"""

    equipment_changed = Signal()  # 裝備/卸下時通知外部刷新形象

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.sub_tabs = QTabWidget()
        layout.addWidget(self.sub_tabs)

        # ── 子頁 1：已裝備 (12 欄位一覽) ──
        equipped_page = QWidget()
        eq_layout = QVBoxLayout(equipped_page)
        eq_layout.setContentsMargins(8, 8, 8, 8)

        self.equipped_html = QTextEdit()
        self.equipped_html.setReadOnly(True)
        eq_layout.addWidget(self.equipped_html)

        # 加成總覽
        self.buffs_label = QLabel("")
        self.buffs_label.setStyleSheet("color: #2ed573; font-size: 12px; padding: 4px;")
        self.buffs_label.setWordWrap(True)
        eq_layout.addWidget(self.buffs_label)

        self.sub_tabs.addTab(equipped_page, t("equip_equipped"))

        # ── 子頁 2：背包 ──
        inv_page = QWidget()
        inv_layout = QVBoxLayout(inv_page)
        inv_layout.setContentsMargins(8, 8, 8, 8)

        # 篩選列
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("篩選："))
        self.slot_filter = QComboBox()
        self.slot_filter.addItem("全部", "")
        from sentinel.wallet.equipment import SLOT_NAMES_ZH
        for slot_id, slot_zh in SLOT_NAMES_ZH.items():
            self.slot_filter.addItem(slot_zh, slot_id)
        self.slot_filter.currentIndexChanged.connect(self.refresh)
        filter_row.addWidget(self.slot_filter)

        self.rarity_filter = QComboBox()
        self.rarity_filter.addItem("全部", "")
        from sentinel.wallet.equipment import RARITY_ZH
        for r_id, r_zh in RARITY_ZH.items():
            self.rarity_filter.addItem(r_zh, r_id)
        self.rarity_filter.currentIndexChanged.connect(self.refresh)
        filter_row.addWidget(self.rarity_filter)
        filter_row.addStretch()
        inv_layout.addLayout(filter_row)

        # 背包滾動區域（用磚塊卡片取代 HTML 長條）
        inv_scroll = QScrollArea()
        inv_scroll.setWidgetResizable(True)
        inv_scroll.setStyleSheet("QScrollArea { border: none; }")
        self.inv_list_widget = QWidget()
        self.inv_list_layout = FlowLayout(self.inv_list_widget, margin=4,
                                          h_spacing=8, v_spacing=8)
        inv_scroll.setWidget(self.inv_list_widget)
        inv_layout.addWidget(inv_scroll)

        self.sub_tabs.addTab(inv_page, t("equip_inventory"))

        # ── 子頁 3：合成 ──
        synth_page = QWidget()
        synth_layout = QVBoxLayout(synth_page)
        synth_layout.setContentsMargins(8, 8, 8, 8)

        synth_layout.addWidget(QLabel(
            "<b style='color:#ffa502;'>合成規則</b><br>"
            "<span style='color:#aaa;'>選擇 3 件相同等級的裝備 → 合成為 1 件更高等級裝備</span>"
        ))

        self.synth_rarity_combo = QComboBox()
        from sentinel.wallet.equipment import RARITIES, RARITY_ZH as _RZ, RARITY_STARS
        for r in RARITIES[:-1]:  # 究極不能再合了
            self.synth_rarity_combo.addItem(
                f"{RARITY_STARS[r]} {_RZ[r]}", r
            )
        synth_layout.addWidget(self.synth_rarity_combo)

        self.synth_list = QTextEdit()
        self.synth_list.setReadOnly(True)
        self.synth_list.setMaximumHeight(200)
        synth_layout.addWidget(self.synth_list)

        self.synth_rarity_combo.currentIndexChanged.connect(self._refresh_synth_candidates)

        self.synth_btn = QPushButton("合成！（消耗 3 件）")
        self.synth_btn.setStyleSheet(
            "QPushButton { background-color: #ffa502; color: #000; font-weight: bold; padding: 8px; }"
            "QPushButton:hover { background-color: #e69500; }"
        )
        self.synth_btn.clicked.connect(self._do_synthesize)
        synth_layout.addWidget(self.synth_btn)

        self.synth_result_label = QLabel("")
        self.synth_result_label.setWordWrap(True)
        synth_layout.addWidget(self.synth_result_label)
        synth_layout.addStretch()

        self.sub_tabs.addTab(synth_page, "合成")

        # ── 子頁 4：掉落紀錄 ──
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(8, 8, 8, 8)

        self.drop_log_html = QTextEdit()
        self.drop_log_html.setReadOnly(True)
        log_layout.addWidget(self.drop_log_html)

        self.sub_tabs.addTab(log_page, "掉落紀錄")

        # 追蹤選中的 item_id
        self._selected_item_id = ""

        self.refresh()

    def refresh(self):
        """重新載入所有裝備資料。"""
        from sentinel.wallet.equipment import (
            load_equipment, get_active_buffs, get_exp_multiplier,
            get_drop_luck_bonus, get_trade_fee_percent, get_affinity_boost,
            SLOT_NAMES_ZH, RARITY_ZH, RARITY_COLORS, RARITY_STARS, RARITY_FLOOR_PRICE,
            EQUIPMENT_POOL, SLOTS,
        )

        state = load_equipment()

        # ── 已裝備頁面 ──
        eq_lines = []
        for slot in SLOTS:
            slot_zh = SLOT_NAMES_ZH.get(slot, slot)
            item_id = state.equipped.get(slot)
            if item_id:
                item = next((i for i in state.inventory if i["item_id"] == item_id), None)
                if item:
                    template = next(
                        (t for t in EQUIPMENT_POOL if t["name"] == item["template_name"]), None
                    )
                    rarity = item["rarity"]
                    color = RARITY_COLORS.get(rarity, "#aaa")
                    stars = RARITY_STARS.get(rarity, "★")
                    desc = template["desc"] if template else ""
                    eq_lines.append(
                        f"<b>{slot_zh}</b>　"
                        f"<span style='color:{color};'>{stars} {item['template_name']}</span>"
                        f"<br><span style='color:#888; font-size:11px;'>　　{desc}</span>"
                    )
                else:
                    eq_lines.append(f"<b>{slot_zh}</b>　<span style='color:#555;'>（空）</span>")
            else:
                eq_lines.append(f"<b>{slot_zh}</b>　<span style='color:#555;'>（空）</span>")

        self.equipped_html.setHtml(
            "<div style='line-height:1.8;'>" + "<br>".join(eq_lines) + "</div>"
        )

        # 加成總覽
        buffs = get_active_buffs(state)
        if buffs:
            buff_parts = []
            BUFF_ZH = {
                "exp_multiplier": "經驗",
                "drop_luck": "運氣",
                "trade_fee_reduction": "手續費減免",
                "affinity_boost": "親和度",
                "social_share_bonus": "分享獎勵",
            }
            for k, v in buffs.items():
                label = BUFF_ZH.get(k, k)
                if k == "social_share_bonus":
                    buff_parts.append(f"{label} +{int(v)} 點")
                else:
                    buff_parts.append(f"{label} +{v*100:.0f}%")
            fee = get_trade_fee_percent(state)
            buff_parts.append(f"市場手續費 {fee:.0f}%")
            self.buffs_label.setText("加成：" + "　|　".join(buff_parts))
        else:
            self.buffs_label.setText("加成：（無裝備加成）")

        # ── 背包頁面（按鈕卡片）──
        slot_filter = self.slot_filter.currentData() or ""
        rarity_filter = self.rarity_filter.currentData() or ""

        filtered = state.inventory
        if slot_filter:
            filtered = [i for i in filtered if i.get("slot") == slot_filter]
        if rarity_filter:
            filtered = [i for i in filtered if i.get("rarity") == rarity_filter]

        # 按稀有度排序（高→低）
        from sentinel.wallet.equipment import RARITIES
        rarity_order = {r: i for i, r in enumerate(RARITIES)}
        filtered.sort(key=lambda i: -rarity_order.get(i.get("rarity", "common"), 0))

        # 清空舊卡片
        while self.inv_list_layout.count() > 0:
            child = self.inv_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if filtered:
            for item in filtered:
                card = self._make_inv_card(item, state, EQUIPMENT_POOL,
                                           SLOT_NAMES_ZH, RARITY_COLORS,
                                           RARITY_STARS, RARITY_FLOOR_PRICE)
                self.inv_list_layout.addWidget(card)
        else:
            empty = QLabel(
                "<p style='color:#666; text-align:center; padding:40px;'>"
                "背包空空的...繼續觀察就會掉裝備！</p>"
            )
            empty.setAlignment(Qt.AlignCenter)
            self.inv_list_layout.addWidget(empty)

        # 合成候選
        self._refresh_synth_candidates()

        # ── 掉落紀錄 ──
        drop_items = [i for i in state.inventory]
        drop_items.sort(key=lambda i: -i.get("acquired_at", 0))
        if drop_items:
            log_lines = []
            import datetime
            for item in drop_items[:30]:
                rarity = item.get("rarity", "common")
                color = RARITY_COLORS.get(rarity, "#aaa")
                stars = RARITY_STARS.get(rarity, "★")
                via = item.get("acquired_via", "?")
                VIA_ZH = {
                    "observation_100": "觀察獎勵",
                    "learning": "學習獎勵",
                    "evolution": "進化獎勵",
                    "daily_login": "每日獎勵",
                    "share": "分享獎勵",
                    "synthesis": "合成",
                }
                via_zh = VIA_ZH.get(via, via)
                ts = datetime.datetime.fromtimestamp(
                    item.get("acquired_at", 0)
                ).strftime("%m/%d %H:%M")

                log_lines.append(
                    f"<span style='color:#888;'>{ts}</span>　"
                    f"<span style='color:{color};'>{stars} {item['template_name']}</span>　"
                    f"<span style='color:#666;'>({via_zh})</span>"
                )
            self.drop_log_html.setHtml(
                "<div style='line-height:1.8;'>" + "<br>".join(log_lines) + "</div>"
            )
        else:
            self.drop_log_html.setHtml(
                "<p style='color:#666; text-align:center; padding:40px;'>"
                "還沒有掉落紀錄</p>"
            )

    # Rarity glow colors for hover / accent (hex with alpha via rgba)
    _RARITY_TINT = {
        "common": "rgba(170,170,170,0.12)",
        "uncommon": "rgba(46,213,115,0.15)",
        "rare": "rgba(30,144,255,0.18)",
        "epic": "rgba(168,85,247,0.20)",
        "legendary": "rgba(255,165,2,0.22)",
        "mythic": "rgba(255,71,87,0.25)",
        "ultimate": "rgba(255,215,0,0.30)",
    }

    def _make_inv_card(self, item, state, pool, slot_zh_map, colors, stars_map, floor_map):
        """為每件背包物品建立一張緊湊磚塊卡片（可流動重排）。"""
        from sentinel.equipment_visuals import EquipmentIcon

        rarity = item.get("rarity", "common")
        color = colors.get(rarity, "#aaa")
        tint = self._RARITY_TINT.get(rarity, "rgba(170,170,170,0.12)")
        is_equipped = item.get("equipped", False)
        listed_price = item.get("listed_price", 0)
        is_listed = listed_price > 0

        card = QFrame()
        card.setFixedSize(240, 128)
        if is_equipped:
            accent = "#2ed573"
        elif is_listed:
            accent = "#ffa502"
        else:
            accent = color

        # Card uses a rarity tint as background wash with a thin left accent bar.
        card.setStyleSheet(
            f"QFrame {{ background: {tint}; "
            f"border: 1px solid rgba(255,255,255,0.06); "
            f"border-left: 3px solid {accent}; "
            f"border-radius: 6px; }}"
        )
        outer = QVBoxLayout(card)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        # Top row: icon + (name + slot/status)
        top = QHBoxLayout()
        top.setSpacing(8)
        template = next((t for t in pool if t["name"] == item["template_name"]), None)
        visual_key = template.get("visual", "") if template else ""
        slot_key = item.get("slot", "")
        icon = EquipmentIcon(visual_key, rarity, slot_key, size=56)
        top.addWidget(icon)

        info = QVBoxLayout()
        info.setSpacing(2)
        info.setContentsMargins(0, 0, 0, 0)
        stars = stars_map.get(rarity, "★")
        name_lbl = QLabel(
            f"<span style='color:{color};'>{stars}</span> "
            f"<b style='color:#eee;'>{item['template_name']}</b>"
        )
        name_lbl.setWordWrap(True)
        info.addWidget(name_lbl)

        slot_zh = slot_zh_map.get(slot_key, "?")
        if is_equipped:
            status_html = f"<span style='color:#2ed573;'>● 已裝備</span> · {slot_zh}"
        elif is_listed:
            status_html = (f"<span style='color:#ffa502;'>▲ {listed_price}pt</span> · "
                           f"{slot_zh}")
        else:
            status_html = f"<span style='color:#888;'>{slot_zh}</span>"
        status_lbl = QLabel(f"<span style='font-size:11px;'>{status_html}</span>")
        info.addWidget(status_lbl)
        info.addStretch()
        top.addLayout(info, stretch=1)
        outer.addLayout(top)

        # Bottom row: action buttons (compact, equal-sized)
        item_id = item["item_id"]
        btns = QHBoxLayout()
        btns.setSpacing(4)
        btns.setContentsMargins(0, 0, 0, 0)

        def _style_btn(bg, fg):
            return (f"QPushButton {{ background: {bg}; color: {fg}; "
                    f"border: none; border-radius: 4px; "
                    f"padding: 5px 0; font-size: 12px; font-weight: bold; }}"
                    f"QPushButton:hover {{ background: {bg}; border: 1px solid #fff; }}")

        if is_equipped:
            b = QPushButton("卸下")
            b.setStyleSheet(_style_btn("#555", "#fff"))
            b.clicked.connect(lambda _, iid=item_id: self._do_unequip(iid))
            btns.addWidget(b)
        elif is_listed:
            b = QPushButton("取消上架")
            b.setStyleSheet(_style_btn("#ffa502", "#000"))
            b.clicked.connect(lambda _, iid=item_id: self._do_delist(iid))
            btns.addWidget(b)
        else:
            b_eq = QPushButton("裝備")
            b_eq.setStyleSheet(_style_btn(color, "#000"))
            b_eq.clicked.connect(lambda _, iid=item_id: self._do_equip(iid))
            btns.addWidget(b_eq)

            b_ls = QPushButton("上架")
            b_ls.setStyleSheet(_style_btn("#1e90ff", "#fff"))
            b_ls.clicked.connect(
                lambda _, iid=item_id, r=rarity, nm=item["template_name"]:
                self._do_list(iid, r, nm)
            )
            btns.addWidget(b_ls)

        outer.addLayout(btns)

        # Tooltip shows the full description
        desc = template["desc"] if template else ""
        if desc:
            card.setToolTip(f"{item['template_name']}\n{desc}")

        return card

    def _do_equip(self, item_id: str):
        from sentinel.wallet.equipment import load_equipment, equip_item
        state = load_equipment()
        if equip_item(state, item_id):
            self.refresh()
            self.equipment_changed.emit()
        else:
            QMessageBox.warning(self, "AI Slime", "無法裝備（可能已上架販售）")

    def _do_unequip(self, item_id: str):
        from sentinel.wallet.equipment import load_equipment, unequip_slot
        state = load_equipment()
        item = next((i for i in state.inventory if i["item_id"] == item_id), None)
        if item and item.get("equipped"):
            unequip_slot(state, item["slot"])
            self.refresh()
            self.equipment_changed.emit()
        else:
            QMessageBox.warning(self, "AI Slime", "這件道具沒有被裝備")

    def _do_list(self, item_id: str, rarity: str, template_name: str):
        """Put an item up for sale on the marketplace (local + relay)."""
        from sentinel.wallet.equipment import load_equipment, list_for_sale
        from sentinel import relay_client
        from sentinel.relay_client import _get_token

        # Check login first
        if not _get_token():
            reply = QMessageBox.question(
                self, "上架",
                "上架需要先登入 Google 帳號。\n現在要登入嗎？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._trigger_google_login()
            return

        # Suggested price by rarity (seller can override with any value ≥ 10)
        SUGGESTED = {
            "common": 10, "uncommon": 30, "rare": 100,
            "epic": 400, "legendary": 2000, "mythic": 10000,
            "ultimate": 50000,
        }
        suggested = SUGGESTED.get(rarity, 10)

        price, ok = QInputDialog.getInt(
            self, "上架販售",
            f"為「{template_name}」設定售價\n"
            f"（建議 {suggested} 點，最低 10 點，由市場決定價格）",
            suggested, 10, 10_000_000, 1,
        )
        if not ok:
            return

        state = load_equipment()
        item = next((i for i in state.inventory if i["item_id"] == item_id), None)
        if not item:
            QMessageBox.warning(self, "AI Slime", "找不到這件道具")
            return
        if item.get("equipped"):
            QMessageBox.warning(self, "AI Slime",
                                "已裝備中的道具無法上架，請先卸下")
            return

        slot = item.get("slot", "")

        # Try relay first so we don't end up with out-of-sync state
        relay_ok = False
        relay_err = None
        try:
            relay_client.list_item(item_id, template_name, slot, rarity, price)
            relay_ok = True
        except relay_client.RelayError as e:
            relay_err = f"{e.code}: {e.message}"
        except Exception as e:
            relay_err = str(e)

        if not relay_ok:
            reply = QMessageBox.question(
                self, "上架",
                f"無法連線到市場伺服器：{relay_err}\n\n"
                f"是否僅在本機標記為上架？（之後可重試同步）",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        if list_for_sale(state, item_id, price):
            self.refresh()
            self.equipment_changed.emit()
            if relay_ok:
                QMessageBox.information(
                    self, "上架成功",
                    f"「{template_name}」已上架至市場，售價 {price} 點。",
                )
            else:
                QMessageBox.information(
                    self, "本機已標記",
                    "道具已在本機標記為上架中，但尚未同步至市場。",
                )
        else:
            QMessageBox.warning(self, "AI Slime",
                                "無法上架（道具狀態異常）")

    def _trigger_google_login(self):
        """Start Google OAuth login from equipment tab."""
        client_id = config.GOOGLE_CLIENT_ID
        relay_url = config.RELAY_SERVER_URL
        if not client_id:
            QMessageBox.warning(self, "登入", "Google Client ID 未設定，請到魔法陣設定。")
            return

        import threading

        def _do():
            try:
                from sentinel.google_auth import full_login_flow
                auth_data = full_login_flow(client_id, relay_url,
                                             client_secret=config.GOOGLE_CLIENT_SECRET)
                from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(
                    self, "_on_login_result",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, auth_data.get("display_name", "")),
                    Q_ARG(str, ""),
                )
            except Exception as e:
                from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(
                    self, "_on_login_result",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, ""),
                    Q_ARG(str, str(e)),
                )

        threading.Thread(target=_do, daemon=True).start()

    @Slot(str, str)
    def _on_login_result(self, name: str, error: str):
        if error:
            QMessageBox.warning(self, "登入失敗", error)
        else:
            QMessageBox.information(self, "登入成功", f"已登入為 {name}，現在可以上架了！")

    def _do_delist(self, item_id: str):
        """Cancel a listing (local + relay)."""
        from sentinel.wallet.equipment import load_equipment, delist
        from sentinel import relay_client

        reply = QMessageBox.question(
            self, "取消上架", "確定要將這件道具從市場下架？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # Remove from relay market (listing_id == item_id in our schema)
        relay_err = None
        try:
            relay_client.delist_item(item_id)
        except relay_client.RelayError as e:
            # 404 = already not listed remotely, treat as success
            if e.code != "404":
                relay_err = f"{e.code}: {e.message}"
        except Exception as e:
            relay_err = str(e)

        state = load_equipment()
        if delist(state, item_id):
            self.refresh()
            self.equipment_changed.emit()
            if relay_err:
                QMessageBox.warning(
                    self, "已下架（部分）",
                    f"本機已下架，但市場同步失敗：{relay_err}",
                )
        else:
            QMessageBox.warning(self, "AI Slime", "無法下架")

    def _refresh_synth_candidates(self):
        """顯示可合成的同級裝備。"""
        from sentinel.wallet.equipment import (
            load_equipment, RARITY_ZH, RARITY_COLORS, RARITY_STARS,
            SLOT_NAMES_ZH, RARITIES,
        )

        target_rarity = self.synth_rarity_combo.currentData()
        if not target_rarity:
            return

        state = load_equipment()
        # 找出該等級、未裝備、未上架的道具
        candidates = [
            i for i in state.inventory
            if i.get("rarity") == target_rarity
            and not i.get("equipped")
            and i.get("listed_price", 0) == 0
        ]

        color = RARITY_COLORS.get(target_rarity, "#aaa")
        rarity_zh = RARITY_ZH.get(target_rarity, target_rarity)
        next_idx = RARITIES.index(target_rarity) + 1
        next_rarity_zh = RARITY_ZH.get(RARITIES[next_idx], "?") if next_idx < len(RARITIES) else "?"

        if len(candidates) >= 3:
            next_color = RARITY_COLORS.get(RARITIES[next_idx], "#fff") if next_idx < len(RARITIES) else "#fff"
            lines = [
                f"<b style='color:{color};'>可合成 {len(candidates)} 件 {rarity_zh} → "
                f"<span style='color:{next_color};'>"
                f"{next_rarity_zh}</span></b><br>"
            ]
            for c in candidates[:12]:
                slot_zh = SLOT_NAMES_ZH.get(c.get("slot", ""), "")
                lines.append(
                    f"　{RARITY_STARS[target_rarity]} {c['template_name']} ({slot_zh})"
                )
            if len(candidates) > 12:
                lines.append(f"　...還有 {len(candidates) - 12} 件")
            self.synth_list.setHtml("<br>".join(lines))
            self.synth_btn.setEnabled(True)
        else:
            self.synth_list.setHtml(
                f"<span style='color:#888;'>"
                f"{rarity_zh} 裝備不足 3 件（目前 {len(candidates)} 件）"
                f"<br>需要 3 件同級裝備才能合成</span>"
            )
            self.synth_btn.setEnabled(False)

    def _do_synthesize(self):
        """執行合成：消耗 3 件同級 → 產生 1 件高一級。"""
        from sentinel.wallet.equipment import load_equipment, synthesize, RARITY_COLORS

        target_rarity = self.synth_rarity_combo.currentData()
        state = load_equipment()

        candidates = [
            i for i in state.inventory
            if i.get("rarity") == target_rarity
            and not i.get("equipped")
            and i.get("listed_price", 0) == 0
        ]

        if len(candidates) < 3:
            return

        # 取前 3 件合成
        ids = [c["item_id"] for c in candidates[:3]]
        result = synthesize(state, ids)

        if result:
            color = RARITY_COLORS.get(result["rarity"], "#fff")
            self.synth_result_label.setText(
                f"<b style='color:{color};'>合成成功！</b><br>"
                f"<span style='color:{color};'>{result['rarity_stars']} {result['name']}</span><br>"
                f"<span style='color:#888;'>{result['desc']}</span>"
            )
            self.refresh()
        else:
            self.synth_result_label.setText(
                "<span style='color:#ff4757;'>合成失敗</span>"
            )

    def retranslate(self):
        self.sub_tabs.setTabText(0, t("equip_equipped"))
        self.sub_tabs.setTabText(1, t("equip_inventory"))


# ─── Memory Tab ──────────────────────────────────────────────────────────

class MemoryTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # Stats
        stats_layout = QHBoxLayout()
        self.sessions_label = QLabel()
        self.chats_label = QLabel()
        stats_layout.addWidget(self.sessions_label)
        stats_layout.addWidget(self.chats_label)
        stats_layout.addStretch()
        layout.addLayout(stats_layout)

        # Profile - what AI Slime understands about you
        profile_group = QGroupBox(t("memory_profile"))
        profile_layout = QVBoxLayout()
        self.profile_text = QTextEdit()
        self.profile_text.setReadOnly(True)
        self.profile_text.setMaximumHeight(100)
        profile_layout.addWidget(self.profile_text)
        profile_group.setLayout(profile_layout)
        layout.addWidget(profile_group)

        # Patterns
        patterns_group = QGroupBox(t("memory_patterns"))
        patterns_layout = QVBoxLayout()
        self.patterns_text = QTextEdit()
        self.patterns_text.setReadOnly(True)
        self.patterns_text.setMaximumHeight(100)
        patterns_layout.addWidget(self.patterns_text)
        patterns_group.setLayout(patterns_layout)
        layout.addWidget(patterns_group)

        # Speech style - what Slime learned about how master talks + how to adjust
        self.speech_group = QGroupBox(t("memory_speech_style"))
        speech_layout = QVBoxLayout()
        self.speech_text = QTextEdit()
        self.speech_text.setReadOnly(True)
        self.speech_text.setMaximumHeight(90)
        speech_layout.addWidget(self.speech_text)
        self.speech_group.setLayout(speech_layout)
        layout.addWidget(self.speech_group)

        # Learning Log - concrete evidence of what was learned and when
        log_group = QGroupBox("學習日誌 / Learning Log")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        # Refresh
        self.refresh_btn = QPushButton(t("status_refresh"))
        self.refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_btn)

        self.refresh()

    def refresh(self):
        from sentinel.learner import load_memory, get_learning_log
        import datetime
        memory = load_memory()

        sessions = memory.get("session_count", 0)
        chats = memory.get("chat_count", 0)
        self.sessions_label.setText(f"{t('memory_sessions')}: {sessions}")
        self.chats_label.setText(f"{t('memory_chats')}: {chats}")

        profile = memory.get("profile", "") or t("memory_learning")
        self.profile_text.setPlainText(profile)

        patterns = memory.get("patterns", {})
        if patterns:
            lines = [f"{k}: {v}" for k, v in patterns.items()]
            self.patterns_text.setPlainText("\n".join(lines))
        else:
            self.patterns_text.setPlainText(t("memory_learning"))

        # Speech style
        from sentinel.learner import format_speech_style_for_prompt
        style = memory.get("speech_style", {})
        self.speech_text.setPlainText(format_speech_style_for_prompt(style))

        # Learning log - show concrete evidence of each learning event
        log_entries = get_learning_log(last_n=20)
        if log_entries:
            lines = []
            for entry in reversed(log_entries):
                ts = entry.get("time", 0)
                dt = datetime.datetime.fromtimestamp(ts).strftime("%m/%d %H:%M") if ts else "?"
                obs_list = entry.get("observations", [])
                obs_str = " | ".join(obs_list) if obs_list else "(無新發現)"
                pats = entry.get("patterns", {})
                pat_str = ", ".join(f"{k}: {v}" for k, v in pats.items()) if pats else ""
                line = f"[{dt}] {obs_str}"
                if pat_str:
                    line += f"\n    模式: {pat_str}"
                lines.append(line)
            self.log_text.setPlainText("\n\n".join(lines))
        else:
            self.log_text.setPlainText("尚無學習紀錄。觀察活動後會自動記錄學習成果。")

    def retranslate(self):
        self.refresh_btn.setText(t("status_refresh"))
        self.speech_group.setTitle(t("memory_speech_style"))


# ─── Federation Tab (公頻 / Slime-to-Slime Knowledge Pool) ──────────────
# Layer 3 of the federation design (sentinel/growth/federation.py):
# other slimes' abstracted observations arrive here as "patterns", and
# this tab lets the user vote whether the statement also fits them.
# Submission (layer 2) shipped in Phase A1.


class MyContributionsDialog(QDialog):
    """Shows the user's own submitted patterns with current vote counts
    and promotion status. Phase A3 — gives users visible feedback on
    sharing so the loop doesn't feel one-way."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("fed_my_contributions"))
        self.setMinimumSize(520, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        # Header + summary line (filled in _load after the fetch)
        title = QLabel(f"<b style='color:#ffd166;'>{t('fed_my_contributions')}</b>")
        title.setStyleSheet("font-size: 14px;")
        layout.addWidget(title)

        self.summary_lbl = QLabel(t("fed_loading"))
        self.summary_lbl.setStyleSheet("color:#888; font-size: 11px;")
        layout.addWidget(self.summary_lbl)

        # Scrollable list of contribution cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.list_layout = QVBoxLayout(container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        close_btn = QPushButton(t("fed_my_close"))
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet(
            "QPushButton { background:#333; color:#fff;"
            " padding:6px 16px; border-radius:4px; border:none; }"
            "QPushButton:hover { background:#444; }"
        )
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._load()

    def _load(self):
        """Fetch my-patterns from relay and render cards."""
        try:
            from sentinel import relay_client
            from sentinel.relay_client import RelayError
        except Exception as e:
            self.summary_lbl.setText(str(e))
            return

        try:
            resp = relay_client.list_my_patterns(limit=50)
        except Exception as e:
            from sentinel.relay_client import RelayError
            if isinstance(e, RelayError) and str(e.code) in ("401", "422"):
                self.summary_lbl.setText(t("fed_login_required"))
            else:
                self.summary_lbl.setText(
                    t("fed_network_err").format(err=str(e))
                )
            return

        items = resp.get("items", []) or []

        if not items:
            self.summary_lbl.setText(t("fed_my_empty"))
            return

        # Summary: N submitted / M promoted
        n_total = len(items)
        n_community = sum(1 for r in items if r.get("status") == "community")
        self.summary_lbl.setText(
            t("fed_my_summary").format(total=n_total, community=n_community)
        )

        for item in items:
            card = self._build_card(item)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)

    def _build_card(self, item: dict) -> QWidget:
        status = item.get("status") or "pending"
        # Status → color + label. Kept in one place so the scheme is
        # consistent and easy to tweak.
        status_style = {
            "community": ("#00dcff", t("fed_my_status_community")),
            "pending":   ("#888",    t("fed_my_status_pending")),
            "rejected":  ("#e76f51", t("fed_my_status_rejected")),
        }.get(status, ("#888", status))
        color, status_label = status_style

        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.03); "
            f"border-left: 3px solid {color}; "
            "border-radius: 4px; padding: 8px; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)

        stmt = QLabel(item.get("statement", ""))
        stmt.setWordWrap(True)
        stmt.setStyleSheet("color:#e6e6e6; font-size: 12px;")
        v.addWidget(stmt)

        category_zh = t(f"fed_cat_{item.get('category','')}") or item.get("category", "")
        meta_line = t("fed_my_meta").format(
            category=category_zh,
            status=status_label,
            confirm=item.get("votes_confirm", 0),
            refute=item.get("votes_refute", 0),
            unclear=item.get("votes_unclear", 0),
        )
        meta = QLabel(meta_line)
        meta.setStyleSheet(f"color:{color}; font-size: 10px;")
        v.addWidget(meta)

        return card


class FederationTab(QWidget):
    """Voting UI for community patterns."""

    def __init__(self):
        super().__init__()
        from sentinel.ui import tokens as _tk
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _tk.SPACE["xl"], _tk.SPACE["lg"],
            _tk.SPACE["xl"], _tk.SPACE["lg"],
        )
        layout.setSpacing(_tk.SPACE["md"])

        # ── Page title ────────────────────────────────────────────────
        # Single line. The tab name "公頻" already labels this tab, so
        # we don't need a second "史萊姆之間的共同觀察" heading + a
        # separate description paragraph — that doubled the chrome at
        # the top of the screen.
        _cyan = _tk.PALETTE['cyan']
        _muted = _tk.PALETTE['text_muted']
        _meta_size = _tk.FONT_SIZE['meta']
        self.header_lbl = QLabel(
            f"<span style='color:{_cyan}; font-weight:600;'>"
            f"{t('fed_header')}</span>"
            f"  <span style='color:{_muted};"
            f" font-weight:400; font-size:{_meta_size}px;'>"
            f"{t('fed_subtitle')}</span>"
        )
        self.header_lbl.setStyleSheet(
            f"font-size:{_tk.FONT_SIZE['title']}px; padding-bottom: 2px;"
        )
        layout.addWidget(self.header_lbl)

        # ── Pending section (your slime's proposals) ─────────────────
        # Hidden entirely when queue is empty & session_count > 0 — see
        # _rebuild_pending. Takes 0 visual weight when not in use.
        self.pending_header = QLabel("")  # text set in _rebuild_pending
        self.pending_header.setStyleSheet(_tk.text_section())
        self.pending_header.setVisible(False)
        layout.addWidget(self.pending_header)

        self.pending_container = QWidget()
        self.pending_container.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Maximum
        )
        self.pending_container.setMaximumHeight(220)
        self.pending_layout = QVBoxLayout(self.pending_container)
        self.pending_layout.setContentsMargins(0, 0, 0, 0)
        self.pending_layout.setSpacing(10)
        self.pending_container.setVisible(False)
        layout.addWidget(self.pending_container)

        # ── Thin divider between "your slime" and "others" ─────────
        self.divider = QFrame()
        self.divider.setFrameShape(QFrame.HLine)
        self.divider.setStyleSheet(
            f"color:{_tk.PALETTE['border_subtle']};"
            f" background:{_tk.PALETTE['border_subtle']};"
        )
        self.divider.setFixedHeight(1)
        self.divider.setVisible(False)   # only shown when pending visible
        layout.addWidget(self.divider)

        # ── Community section ────────────────────────────────────────
        # Community header is a "system / federation" theme so it uses
        # cyan (text_section is amber for own-slime; for the federation
        # we override to cyan via inline call to text_title).
        self.community_header = QLabel(t("fed_community_header"))
        self.community_header.setStyleSheet(
            f"color:{_tk.PALETTE['cyan']};"
            f" font-size:{_tk.FONT_SIZE['section']}px;"
            f" font-weight:600;"
            f" letter-spacing:0.3px;"
        )
        layout.addWidget(self.community_header)

        self.status_lbl = QLabel(t("fed_loading"))
        self.status_lbl.setStyleSheet(_tk.text_meta() + " padding:4px 0;")
        layout.addWidget(self.status_lbl)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_container)
        layout.addWidget(self.scroll, 1)

        # ── Action row ───────────────────────────────────────────────
        # my_btn: secondary (ghost). refresh_btn: primary (filled cyan).
        # Both shorter & less visually loud than before. Matches the
        # "cards have no chrome" theme above.
        self.my_btn = QPushButton(t("fed_my_contributions"))
        self.my_btn.setCursor(Qt.PointingHandCursor)
        self.my_btn.clicked.connect(self._open_my_contributions)
        self.my_btn.setStyleSheet(
            "QPushButton { background:transparent; color:#ccc;"
            " padding:6px 14px; border:1px solid #3a3a3a; border-radius:14px;"
            " font-size:11px; }"
            "QPushButton:hover { color:#ffd166; border-color:#ffd166; }"
        )
        self.refresh_btn = QPushButton(t("fed_refresh"))
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        self.refresh_btn.setStyleSheet(
            "QPushButton { background:#00a8c9; color:#fff; font-weight:500;"
            " padding:6px 16px; border-radius:14px; border:none;"
            " font-size:11px; }"
            "QPushButton:hover { background:#00c0e3; }"
        )
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.addWidget(self.my_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.refresh_btn)
        layout.addLayout(btn_row)

        self._patterns: list[dict] = []
        self.refresh()

    # ── Data ──────────────────────────────────────────────────────────
    def refresh(self):
        """Fetch patterns from the relay and rebuild the card list.

        Runs synchronously — the list is small (≤20 items) and the
        endpoint returns fast. A background thread would be nice but
        isn't worth the complexity at this scale.
        """
        # Refresh the "pending to share" section first. It's local-only
        # (no network), so even if the relay is down the user can still
        # see + approve candidates the slime prepared earlier.
        self._rebuild_pending()

        self.status_lbl.setText(t("fed_loading"))
        self.status_lbl.setVisible(True)
        # Clear old cards but keep the trailing stretch
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        try:
            from sentinel import relay_client
            resp = relay_client.list_patterns(limit=20)
        except Exception as e:
            # Distinguish "not logged in" from genuine network failure so
            # users don't see "連線錯誤 422" and think the server's broken.
            # See issue #5 — relay now returns 401 for missing auth header,
            # but pre-fix builds may still see 422, so handle both.
            from sentinel.relay_client import RelayError
            if isinstance(e, RelayError) and str(e.code) in ("401", "422"):
                self.status_lbl.setText(t("fed_login_required"))
            else:
                self.status_lbl.setText(
                    t("fed_network_err").format(err=str(e))
                )
            self._patterns = []
            return

        self._patterns = resp.get("items", []) or []

        if not self._patterns:
            self.status_lbl.setText(t("fed_empty"))
            return

        self.status_lbl.setVisible(False)
        for pat in self._patterns:
            card = self._build_card(pat)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)

    def _build_card(self, pat: dict) -> QWidget:
        """Render a single community pattern as a card with vote buttons.

        Design: no filled background, just a 3px left accent bar and
        padding. Accent color reflects status — cyan for community-
        promoted, subtle grey-cyan for still-pending. Much lighter
        visual weight than the translucent-box-with-border treatment
        it replaces, so a list of 5-10 patterns reads as a list of
        ideas instead of a stack of colored boxes.
        """
        status = pat.get("status", "pending")
        accent = "#00dcff" if status == "community" else "#3a5f6b"
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: transparent; "
            f"border: none; "
            f"border-left: 3px solid {accent}; }}"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 6, 6, 10)
        v.setSpacing(6)

        # Statement at the top — this is what the user reads first and
        # decides on. Larger than the meta line, clearly the main thing.
        stmt_lbl = QLabel(pat.get("statement", ""))
        stmt_lbl.setWordWrap(True)
        stmt_lbl.setStyleSheet("color:#e6e6e6; font-size: 13px;")
        v.addWidget(stmt_lbl)

        # Meta line: category · status · vote tallies. Single row of
        # small grey text so it reads as supporting info, not chrome.
        cat_key = f"fed_cat_{pat.get('category', '')}"
        cat_text = t(cat_key)
        if cat_text == cat_key:
            cat_text = pat.get("category", "")
        status = pat.get("status", "pending")
        status_key = "fed_status_community" if status == "community" else "fed_status_pending"
        status_color = "#00dcff" if status == "community" else "#888"
        vc = pat.get("votes_confirm", 0)
        vr = pat.get("votes_refute", 0)
        vu = pat.get("votes_unclear", 0)
        meta_lbl = QLabel(
            f"<span style='color:#888;'>{cat_text}</span>"
            f"  ·  <span style='color:{status_color};'>{t(status_key)}</span>"
            f"  ·  <span style='color:#5ab572;'>✓ {vc}</span>"
            f"  <span style='color:#cc6b63;'>✗ {vr}</span>"
            f"  <span style='color:#666;'>? {vu}</span>"
        )
        meta_lbl.setStyleSheet("font-size: 10px;")
        v.addWidget(meta_lbl)

        # Vote row. When user has already voted, show a subtle status
        # line instead of disabled buttons — fewer visual elements,
        # same information.
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 2, 0, 0)
        btn_row.setSpacing(6)
        user_voted = pat.get("user_voted")

        if user_voted:
            voted_key = f"fed_voted_{user_voted}"
            voted_lbl = QLabel(f"<span style='color:#666;'>{t(voted_key)}</span>")
            voted_lbl.setStyleSheet("font-size: 10px;")
            btn_row.addWidget(voted_lbl)
            btn_row.addStretch()
        else:
            pattern_id = pat["id"]
            btn_row.addStretch()
            for vote_type, label_key, fg in [
                ("confirm", "fed_btn_confirm", "#5ab572"),
                ("refute",  "fed_btn_refute",  "#cc6b63"),
                ("unclear", "fed_btn_unclear", "#888"),
            ]:
                btn = QPushButton(t(label_key))
                btn.setCursor(Qt.PointingHandCursor)
                btn.setStyleSheet(
                    f"QPushButton {{ background:transparent; color:{fg}; "
                    f"padding:3px 10px; border:1px solid {fg}; "
                    f"border-radius:10px; font-size: 10px; }}"
                    f"QPushButton:hover {{ background:rgba(255,255,255,0.05); }}"
                )
                btn.clicked.connect(
                    lambda _checked, pid=pattern_id, v=vote_type: self._on_vote(pid, v)
                )
                btn_row.addWidget(btn)

        v.addLayout(btn_row)
        return card

    def _on_vote(self, pattern_id: str, vote: str):
        """Handle a vote click. Refreshes the list after success."""
        from sentinel import relay_client
        from sentinel.relay_client import RelayError
        try:
            resp = relay_client.vote_pattern(pattern_id, vote)
        except Exception as e:
            if isinstance(e, RelayError) and str(e.code) in ("401", "422"):
                QMessageBox.warning(self, "AI Slime", t("fed_login_required"))
            else:
                QMessageBox.warning(self, "AI Slime",
                                    t("fed_network_err").format(err=str(e)))
            return

        # H. Confirmed patterns feed back into chat prompt — the slime
        # starts referencing community wisdom when relevant. We only
        # store 'confirm' votes (patterns master validated as true) so
        # the slime doesn't quote things master disagrees with.
        if vote == "confirm":
            try:
                statement = None
                category = None
                for pat in getattr(self, "_patterns", []) or []:
                    if pat.get("id") == pattern_id:
                        statement = pat.get("statement")
                        category = pat.get("category")
                        break
                if statement:
                    from sentinel import identity
                    identity.record_confirmed_pattern(pattern_id, statement, category)
                    # Phase B2: also drop the statement into long-term
                    # semantic memory so the slime can recall it in
                    # chats ("你之前確認過『深夜工作者多半...』"). Kind
                    # is federation_pattern so we can filter retrieval
                    # by source if we later want different weights.
                    try:
                        from sentinel.memory import remember, KIND_FEDERATION
                        remember(
                            text=statement,
                            kind=KIND_FEDERATION,
                            metadata={
                                "pattern_id": pattern_id,
                                "category": category,
                            },
                        )
                    except Exception as e:
                        import logging
                        logging.getLogger("sentinel.gui").warning(
                            f"federation pattern memory persist failed: {e}"
                        )
            except Exception:
                pass

        if resp.get("promoted"):
            QMessageBox.information(self, "AI Slime", t("fed_vote_promoted"))

        # A2 reward: every 5th successful vote gets a drop roll. Rate
        # is defined in DROP_CHANCES["federation_vote"] — if it misses,
        # the user silently rolls the next one 5 votes later. Keeping
        # the trigger frequency low on the client side (every 5) and
        # the chance high (40%) feels more generous than rolling every
        # vote at 8% — same expected rate, much more satisfying hits.
        try:
            from sentinel.growth.federation import increment_vote_counter
            from sentinel.wallet.equipment import (
                load_equipment, try_drop, save_equipment,
            )
            count = increment_vote_counter()
            if count > 0 and count % 5 == 0:
                eq = load_equipment()
                drop = try_drop(eq, "federation_vote")
                if drop:
                    save_equipment(eq)
                    QMessageBox.information(
                        self, "AI Slime",
                        t("fed_vote_drop").format(
                            rarity=drop["rarity_zh"],
                            name=drop["name"],
                        ),
                    )
        except Exception as e:
            # Reward hiccups should never break the vote path.
            import logging
            logging.getLogger("sentinel.gui").warning(
                f"federation vote reward failed: {e}"
            )

        # Re-fetch — the card state (buttons → "已投", counts) changes
        # based on the new data.
        self.refresh()

    # ── Pending candidates (the slime wants to share) ─────────────────

    def _rebuild_pending(self):
        """Re-render the "pending to share" section from the local queue.

        Layout contract (redesigned):
          - has candidates:       show header + 1 card per candidate
          - no candidates, fresh: show header + inline "first distill
                                  within ~1 hour" hint under it, no card
          - no candidates, used:  HIDE header + container entirely so
                                  the empty middle doesn't squeeze the
                                  community list below

        The previous design always drew a placeholder card even when
        there was nothing to act on, which pushed community patterns
        and the action buttons toward the bottom of the tab on every
        visit. Not worth the visual cost for a hint that's mostly
        relevant on day 0 only.
        """
        # Clear old cards
        while self.pending_layout.count() > 0:
            item = self.pending_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        from sentinel.growth.federation import list_pending
        candidates = list_pending()

        if candidates:
            VISIBLE_CAP = 2
            shown = candidates[:VISIBLE_CAP]
            hidden_count = max(0, len(candidates) - VISIBLE_CAP)

            suffix = ""
            if hidden_count > 0:
                suffix = (
                    f"  <span style='color:#666; font-weight:400;'>"
                    f"· {t('fed_pending_more').format(n=hidden_count)}</span>"
                )
            self.pending_header.setText(
                f"{t('fed_pending_header')}{suffix}"
            )
            self.pending_header.setVisible(True)
            self.pending_container.setVisible(True)
            self.divider.setVisible(True)
            for cand in shown:
                card = self._build_pending_card(cand)
                self.pending_layout.addWidget(card)
            return

        # Empty — decide between "onboarding hint" and "hide entirely"
        # based on how long the slime has been running.
        session_count = 0
        try:
            from sentinel.learner import load_memory
            session_count = int(load_memory().get("session_count", 0) or 0)
        except Exception:
            pass

        if session_count == 0:
            # Fresh install: header with inline hint, no separate card.
            self.pending_header.setText(
                f"{t('fed_pending_header')}"
                f"  <span style='color:#666; font-weight:400;'>"
                f"· {t('fed_pending_empty_new_short')}</span>"
            )
            self.pending_header.setVisible(True)
            self.pending_container.setVisible(False)
            self.divider.setVisible(True)
            return

        # Used-before-but-nothing-queued-this-round: hide the whole
        # section (header + divider + container) so the community list
        # gets full vertical space. The tab-title badge counter still
        # pings the user when real candidates arrive.
        self.pending_header.setVisible(False)
        self.pending_container.setVisible(False)
        self.divider.setVisible(False)

    def _build_pending_card(self, cand: dict) -> QWidget:
        """Card for a candidate the slime wants to share.

        Matches the community-card visual language: no filled
        background, just a 3px amber left accent (amber = "yours") and
        padding. Consistent with the cleaner community cards below so
        the tab reads as one connected list with a color-coded stripe.
        """
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: transparent; border: none; "
            "border-left: 3px solid #ffd166; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 6, 6, 10)
        v.setSpacing(6)

        stmt = QLabel(cand.get("statement", ""))
        stmt.setWordWrap(True)
        stmt.setStyleSheet("color:#e6e6e6; font-size: 13px;")
        v.addWidget(stmt)

        # Meta: category · confidence. Same density as community card
        # meta so the two sections line up visually.
        cat_text = t(f"fed_cat_{cand.get('category','')}") or cand.get("category", "")
        conf_pct = int((cand.get("confidence", 0) or 0) * 100)
        meta = QLabel(
            f"<span style='color:#888;'>{cat_text}</span>"
            f"  ·  <span style='color:#888;'>{t('fed_pending_confidence').format(pct=conf_pct)}</span>"
        )
        meta.setStyleSheet("font-size: 10px;")
        v.addWidget(meta)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 2, 0, 0)
        btn_row.setSpacing(6)
        btn_row.addStretch()

        skip_btn = QPushButton(t("fed_pending_skip"))
        skip_btn.setCursor(Qt.PointingHandCursor)
        skip_btn.setStyleSheet(
            "QPushButton { background:transparent; color:#888;"
            " padding:3px 12px; border:1px solid #444; border-radius:10px;"
            " font-size:10px; }"
            "QPushButton:hover { color:#ccc; border-color:#666; }"
        )
        skip_btn.clicked.connect(lambda _, cid=cand["id"]: self._on_skip_candidate(cid))
        btn_row.addWidget(skip_btn)

        share_btn = QPushButton(t("fed_pending_share"))
        share_btn.setCursor(Qt.PointingHandCursor)
        share_btn.setStyleSheet(
            "QPushButton { background:#ffd166; color:#1a1a1a; font-weight:600;"
            " padding:3px 14px; border:none; border-radius:10px;"
            " font-size:10px; }"
            "QPushButton:hover { background:#ffdc88; }"
        )
        share_btn.clicked.connect(lambda _, cid=cand["id"]: self._on_approve_candidate(cid))
        btn_row.addWidget(share_btn)

        v.addLayout(btn_row)
        return card

    def _on_approve_candidate(self, candidate_id: str):
        from sentinel.growth.federation import (
            approve_candidate, increment_shared_counter,
        )
        err = approve_candidate(candidate_id)
        if err is None:
            # A2 reward: successful submit rolls for an equipment drop
            # (chance in DROP_CHANCES["federation_submit"]). Rate-limited
            # to 3 submits per day on the server, so this is a capped
            # reward — it won't be spammed.
            drop_msg = ""
            try:
                increment_shared_counter()
                from sentinel.wallet.equipment import (
                    load_equipment, try_drop, save_equipment,
                )
                eq = load_equipment()
                drop = try_drop(eq, "federation_submit")
                if drop:
                    save_equipment(eq)
                    drop_msg = "\n\n" + t("fed_submit_drop").format(
                        rarity=drop["rarity_zh"],
                        name=drop["name"],
                    )
            except Exception as e:
                import logging
                logging.getLogger("sentinel.gui").warning(
                    f"federation submit reward failed: {e}"
                )
            QMessageBox.information(
                self, "AI Slime",
                t("fed_pending_shared") + drop_msg,
            )
        else:
            # Show the message from the server / local validator verbatim —
            # it's already user-facing Chinese from federation.py's mapping.
            QMessageBox.warning(self, "AI Slime", err.message)
        # Refresh both sections — the candidate is gone (or kept on
        # transient error), and the community list may have the new
        # pattern if submission succeeded.
        self.refresh()

    def _on_skip_candidate(self, candidate_id: str):
        from sentinel.growth.federation import skip_candidate
        skip_candidate(candidate_id)
        self._rebuild_pending()

    # ── My contributions dialog (A3) ──────────────────────────────────

    def _open_my_contributions(self):
        """Open a dialog listing the user's submitted patterns."""
        dlg = MyContributionsDialog(self)
        dlg.exec()

    # ── i18n ──────────────────────────────────────────────────────────
    def retranslate(self):
        self.header_lbl.setText(
            f"<span style='color:#00dcff; font-weight:600;'>"
            f"{t('fed_header')}</span>"
            f"  <span style='color:#666; font-weight:400; font-size:11px;'>"
            f"{t('fed_subtitle')}</span>"
        )
        self.community_header.setText(t("fed_community_header"))
        self.refresh_btn.setText(t("fed_refresh"))
        self.my_btn.setText(t("fed_my_contributions"))
        self.refresh()


# ─── Evolution Tab (子頁籤版) ────────────────────────────────────────────

class EvolutionTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 子頁籤
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #333; }
            QTabBar::tab { padding: 6px 16px; }
        """)

        # ── 頁籤 1: 形象 ──
        # The avatar page has a lot vertically: slime + 4 labels +
        # progress bar + evolve button + 6 share buttons. On narrow
        # windows QVBoxLayout was compressing the slime below its
        # rendering minimum so the labels below started overlapping
        # the painted body. Fix: wrap in QScrollArea so excess
        # content scrolls instead of squashing, and pin the slime
        # to a fixed size so its paintEvent always has the room
        # its math assumes.
        avatar_page = QWidget()
        avatar_outer = QVBoxLayout(avatar_page)
        avatar_outer.setContentsMargins(0, 0, 0, 0)
        avatar_outer.setSpacing(0)

        avatar_scroll = QScrollArea()
        avatar_scroll.setWidgetResizable(True)
        avatar_scroll.setFrameShape(QFrame.NoFrame)
        avatar_outer.addWidget(avatar_scroll)

        avatar_inner = QWidget()
        avatar_scroll.setWidget(avatar_inner)
        avatar_layout = QVBoxLayout(avatar_inner)

        from sentinel.slime_avatar import SlimeWidget
        # Center the slime in a horizontal row so a narrow window
        # doesn't stretch the widget past the body+glow rendering
        # bounds (which would expose blank canvas around it).
        slime_row = QHBoxLayout()
        slime_row.addStretch()
        self.slime_widget = SlimeWidget()
        # Fixed 280x280 — gives body+glow+particles full room without
        # depending on the window's current dimensions. Same trick as
        # the home avatar (PR #37) but a notch larger because this is
        # the dedicated 進化 tab where the slime is the centerpiece.
        self.slime_widget.setFixedSize(280, 280)
        slime_row.addWidget(self.slime_widget)
        slime_row.addStretch()
        avatar_layout.addLayout(slime_row)

        # Sacred name (only shown after master names the slime at Named tier)
        self.name_label = QLabel()
        self.name_label.setStyleSheet(
            "font-size: 18px; color: #ffa502; font-weight: bold; font-style: italic;"
        )
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.hide()
        avatar_layout.addWidget(self.name_label)

        self.form_label = QLabel()
        self.form_label.setStyleSheet("font-size: 22px; color: #00dcff; font-weight: bold;")
        self.form_label.setAlignment(Qt.AlignCenter)
        avatar_layout.addWidget(self.form_label)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 14px; color: #888;")
        self.title_label.setAlignment(Qt.AlignCenter)
        avatar_layout.addWidget(self.title_label)

        self.direction_label = QLabel()
        self.direction_label.setStyleSheet("font-size: 12px; color: #2ed573;")
        self.direction_label.setAlignment(Qt.AlignCenter)
        avatar_layout.addWidget(self.direction_label)

        # Progress bar
        self.progress_label = QLabel("下一次進化：")
        self.progress_label.setAlignment(Qt.AlignCenter)
        avatar_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        avatar_layout.addWidget(self.progress_bar)

        # 手動進化按鈕（進度達標後啟用；扣 2pt / BYOK 免費）
        evolve_row = QHBoxLayout()
        evolve_row.addStretch()
        self.evolve_btn = QPushButton(t("evolve_btn"))
        self.evolve_btn.setFixedWidth(180)
        self.evolve_btn.setStyleSheet(
            "QPushButton { background:#00dcff; color:#000; font-weight:bold;"
            " padding:8px 16px; border-radius:4px; }"
            "QPushButton:disabled { background:#333; color:#666; }"
        )
        self.evolve_btn.clicked.connect(self._on_evolve_clicked)
        evolve_row.addWidget(self.evolve_btn)
        evolve_row.addStretch()
        avatar_layout.addLayout(evolve_row)

        # 分享按鈕列
        share_layout = QHBoxLayout()
        share_layout.addStretch()

        self.share_btn = QPushButton("📤 分享")
        self.share_btn.setFixedWidth(80)
        self.share_btn.clicked.connect(self._on_share_btn_clicked)
        share_layout.addWidget(self.share_btn)

        self.share_x_btn = QPushButton("𝕏")
        self.share_x_btn.setFixedWidth(40)
        self.share_x_btn.setToolTip("分享到 X (Twitter)")
        self.share_x_btn.clicked.connect(lambda: self._share_to_platform("x"))
        share_layout.addWidget(self.share_x_btn)

        self.share_fb_btn = QPushButton("f")
        self.share_fb_btn.setFixedWidth(40)
        self.share_fb_btn.setToolTip("分享到 Facebook")
        self.share_fb_btn.clicked.connect(lambda: self._share_to_platform("facebook"))
        share_layout.addWidget(self.share_fb_btn)

        self.share_discord_btn = QPushButton("DC")
        self.share_discord_btn.setFixedWidth(40)
        self.share_discord_btn.setToolTip("分享到 Discord")
        self.share_discord_btn.clicked.connect(lambda: self._share_to_platform("discord"))
        share_layout.addWidget(self.share_discord_btn)

        self.share_reddit_btn = QPushButton("R")
        self.share_reddit_btn.setFixedWidth(40)
        self.share_reddit_btn.setToolTip("分享到 Reddit")
        self.share_reddit_btn.clicked.connect(lambda: self._share_to_platform("reddit"))
        share_layout.addWidget(self.share_reddit_btn)

        self.share_threads_btn = QPushButton("@")
        self.share_threads_btn.setFixedWidth(40)
        self.share_threads_btn.setToolTip("分享到 Threads")
        self.share_threads_btn.clicked.connect(lambda: self._share_to_platform("threads"))
        share_layout.addWidget(self.share_threads_btn)

        share_layout.addStretch()
        avatar_layout.addLayout(share_layout)

        avatar_layout.addStretch()

        self.sub_tabs.addTab(avatar_page, "形象")

        # ── 頁籤 2: 數據 ──
        stats_page = QWidget()
        stats_layout_page = QVBoxLayout(stats_page)

        stats_form = QFormLayout()
        self.days_label = QLabel()
        self.obs_label = QLabel()
        self.learn_label = QLabel()
        self.conv_label = QLabel()
        stats_form.addRow("存活天數", self.days_label)
        stats_form.addRow("觀察次數", self.obs_label)
        stats_form.addRow("學習次數", self.learn_label)
        stats_form.addRow("對話次數", self.conv_label)
        stats_layout_page.addLayout(stats_form)

        # Affinity
        aff_label = QLabel("<b style='color:#00dcff;'>行為親和度</b>")
        stats_layout_page.addWidget(aff_label)
        self.affinity_text = QTextEdit()
        self.affinity_text.setReadOnly(True)
        self.affinity_text.setStyleSheet("font-family: Consolas, monospace;")
        stats_layout_page.addWidget(self.affinity_text)

        self.sub_tabs.addTab(stats_page, "數據")

        # ── 頁籤 3: 技能 ──
        skills_page = QWidget()
        skills_layout_page = QVBoxLayout(skills_page)
        self.skills_text = QTextEdit()
        self.skills_text.setReadOnly(True)
        skills_layout_page.addWidget(self.skills_text)

        self.sub_tabs.addTab(skills_page, "技能")

        # ── 頁籤 4: 進化紀錄 ──
        log_page = QWidget()
        log_layout_page = QVBoxLayout(log_page)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout_page.addWidget(self.log_text)

        self.sub_tabs.addTab(log_page, "進化紀錄")

        layout.addWidget(self.sub_tabs)

        # 重新整理
        self.refresh_btn = QPushButton(t("status_refresh"))
        self.refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_btn)

        self.refresh()

    def refresh(self):
        from sentinel.evolution import load_evolution, EVOLUTION_TIERS

        state = load_evolution()

        # If a previous session evolved to Named Slime but was closed before
        # naming, the flag is still set on disk — resurface the ceremony on
        # the next tick so this refresh() returns quickly.
        if getattr(state, "naming_pending", False) and not getattr(state, "slime_name", ""):
            QTimer.singleShot(200, self._maybe_prompt_for_name)

        # 形象頁
        unlocked_skills = [s for s in state.skills if s.unlocked]
        self.slime_widget.set_state(state.form, state.title,
                                     state.dominant_traits, len(unlocked_skills))

        # 更新裝備圖層到 sprite 渲染器
        try:
            from sentinel.wallet.equipment import load_equipment
            eq_state = load_equipment()
            self.slime_widget.set_equipment(eq_state.equipped, eq_state.inventory)
        except Exception:
            pass

        # Show the given name if we have one, otherwise hide the row
        slime_name = getattr(state, "slime_name", "") or ""
        if slime_name:
            self.name_label.setText(f"「{slime_name}」")
            self.name_label.show()
        else:
            self.name_label.hide()

        self.form_label.setText(state.title)
        self.title_label.setText(state.form)
        self.direction_label.setText(
            f"進化方向：{state.evolution_direction}" if state.evolution_direction else "進化方向：觀察中..."
        )

        # 進度條
        current_tier = 0
        for i, (threshold, _, _) in enumerate(EVOLUTION_TIERS):
            if state.total_observations >= threshold:
                current_tier = i
        if current_tier < len(EVOLUTION_TIERS) - 1:
            current_threshold = EVOLUTION_TIERS[current_tier][0]
            next_threshold = EVOLUTION_TIERS[current_tier + 1][0]
            next_form = EVOLUTION_TIERS[current_tier + 1][2]
            progress = state.total_observations - current_threshold
            needed = next_threshold - current_threshold
            pct = min(100, int(progress / needed * 100))
            self.progress_bar.setValue(pct)
            self.progress_label.setText(f"下一階段：{next_form}（{state.total_observations:,} / {next_threshold:,}）")
        else:
            self.progress_bar.setValue(100)
            self.progress_label.setText("已達最終進化！")

        # 進化按鈕狀態
        from sentinel.evolution import is_evolution_available
        info = is_evolution_available(state)
        if info["at_max"]:
            self.evolve_btn.setEnabled(False)
            self.evolve_btn.setText(t("evolve_btn_maxed"))
        elif info["available"]:
            self.evolve_btn.setEnabled(True)
            # BYOK 模式免費；quota 模式顯示扣點
            try:
                from sentinel.wallet.quota import QuotaManager
                mode = QuotaManager(relay_url=config.RELAY_SERVER_URL).mode
            except Exception:
                mode = "byok"
            if mode == "byok":
                self.evolve_btn.setText(t("evolve_btn_ready_free").format(
                    form=info["next_form"]
                ))
            else:
                from sentinel.wallet.market_rules import EVOLVE_COST
                self.evolve_btn.setText(t("evolve_btn_ready_paid").format(
                    form=info["next_form"], cost=EVOLVE_COST
                ))
        else:
            self.evolve_btn.setEnabled(False)
            needed = info["next_threshold"] - state.total_observations
            self.evolve_btn.setText(t("evolve_btn_locked").format(needed=needed))

        # 數據頁
        self.days_label.setText(f"{state.days_alive():.1f}")
        self.obs_label.setText(f"{state.total_observations:,}")
        self.learn_label.setText(str(state.total_learnings))
        self.conv_label.setText(str(state.total_conversations))

        AFFINITY_ZH = {
            "coding": "程式開發", "communication": "溝通", "research": "研究探索",
            "creative": "創作", "multitasking": "多工切換", "deep_focus": "深度專注",
            "late_night": "夜間活動",
        }
        if state.affinity_scores:
            aff_lines = []
            sorted_aff = sorted(state.affinity_scores.items(), key=lambda x: x[1], reverse=True)
            for key, score in sorted_aff:
                if score > 0.01:
                    bar_len = int(score * 20)
                    bar = "█" * bar_len + "░" * (20 - bar_len)
                    label = AFFINITY_ZH.get(key, key)
                    aff_lines.append(f"{label}  {bar}  {score:.0%}")
            self.affinity_text.setPlainText("\n".join(aff_lines) if aff_lines else "尚在觀察中...")
        else:
            self.affinity_text.setPlainText("尚在觀察中...")

        # 技能頁
        lines = []
        for s in state.skills:
            if s.unlocked:
                stars = "★" * s.level + "☆" * (5 - s.level)
                lines.append(f"<p style='margin:8px 4px;'><b style='color:#00dcff; font-size:14px;'>{s.jp_name}</b> "
                             f"<span style='color:#888;'>({s.name})</span> "
                             f"<span style='color:#ffa502; font-size:14px;'>{stars}</span><br>"
                             f"<span style='color:#aaa;'>{s.description}</span></p>")
            else:
                lines.append(f"<p style='margin:8px 4px;'><span style='color:#555;'>🔒 <b>{s.jp_name}</b> ({s.name})</span><br>"
                             f"<span style='color:#444;'>{s.description}</span></p>")
        self.skills_text.setHtml("".join(lines))

        # 進化紀錄頁
        import datetime
        from sentinel.evolution import get_exp_log

        SOURCE_ZH = {
            "system": "系統監控", "files": "檔案變動", "claude": "Claude 對話",
            "activity": "視窗追蹤", "input": "鍵盤/滑鼠", "screen": "螢幕觀察",
        }

        html_parts = []

        # 進化事件（重要事件）
        if state.evolution_log:
            html_parts.append("<p style='color:#00dcff; font-weight:bold; margin:8px 0 4px;'>進化事件</p>")
            for entry in state.evolution_log[-10:]:
                ts = entry.get("time", 0)
                dt = datetime.datetime.fromtimestamp(ts).strftime("%m/%d %H:%M") if ts else ""
                html_parts.append(f"<p style='margin:2px 4px;'><span style='color:#888;'>[{dt}]</span> {entry['message']}</p>")

        # 經驗紀錄（最近獲得的經驗和來源）
        exp_entries = get_exp_log(20)
        if exp_entries:
            html_parts.append("<p style='color:#ffa502; font-weight:bold; margin:12px 0 4px;'>最近經驗獲取</p>")
            for entry in reversed(exp_entries[-15:]):
                ts = entry.get("time", 0)
                dt = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
                exp = entry.get("exp", 0)
                sources = entry.get("sources", {})
                source_strs = []
                for k, v in sources.items():
                    label = SOURCE_ZH.get(k, k)
                    source_strs.append(f"{label}+{v}")
                src_text = "、".join(source_strs)
                html_parts.append(
                    f"<p style='margin:1px 4px;'>"
                    f"<span style='color:#888;'>[{dt}]</span> "
                    f"<span style='color:#2ed573;'>+{exp} EXP</span> "
                    f"<span style='color:#666;'>← {src_text}</span></p>"
                )

        if html_parts:
            self.log_text.setHtml("".join(html_parts))
        else:
            self.log_text.setHtml("<p style='color:#666;'>（等待第一次觀察...）</p>")

    def _on_evolve_clicked(self):
        """Manual evolution trigger. BYOK = free, quota = 2pt via relay.

        Flow:
          1. Load state, re-check eligibility (state may have changed since
             last refresh).
          2. If BYOK mode → skip relay, call perform_evolution() directly.
          3. If quota mode → confirm 2pt cost → POST /evolution/evolve →
             on success, call perform_evolution() locally.
          4. Refresh UI + show result dialog.
        """
        import uuid
        from sentinel.evolution import (
            load_evolution, is_evolution_available, perform_evolution,
        )
        from sentinel.wallet.market_rules import EVOLVE_COST

        state = load_evolution()
        info = is_evolution_available(state)

        if info["at_max"]:
            QMessageBox.information(self, t("evolve_dialog_title"),
                                    t("evolve_already_max"))
            return
        if not info["available"]:
            needed = info["next_threshold"] - state.total_observations
            QMessageBox.information(
                self, t("evolve_dialog_title"),
                t("evolve_not_ready").format(needed=needed),
            )
            return

        # Determine mode
        try:
            from sentinel.wallet.quota import QuotaManager
            qm = QuotaManager(relay_url=config.RELAY_SERVER_URL)
            mode = qm.mode
        except Exception:
            mode = "byok"

        # Confirm dialog (include cost for quota mode)
        if mode == "byok":
            msg = t("evolve_confirm_byok").format(
                cur=state.form, nxt=info["next_form"],
                nxt_title=info["next_title"],
            )
        else:
            msg = t("evolve_confirm_paid").format(
                cur=state.form, nxt=info["next_form"],
                nxt_title=info["next_title"], cost=EVOLVE_COST,
            )
        reply = QMessageBox.question(
            self, t("evolve_dialog_title"), msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        # Quota mode: deduct points via relay first
        if mode == "quota":
            try:
                from sentinel import relay_client
                user_id = qm.uid or "anon"
                idem = f"slime_evolve_{user_id}_{uuid.uuid4()}"
                relay_client.evolve(idempotency_key=idem)
            except Exception as e:
                # Map RelayError 402 → insufficient-balance message
                code = getattr(e, "code", "")
                if code == "402":
                    QMessageBox.warning(
                        self, t("evolve_dialog_title"),
                        t("evolve_insufficient").format(cost=EVOLVE_COST),
                    )
                else:
                    msg_text = getattr(e, "message", str(e))
                    QMessageBox.warning(
                        self, t("evolve_dialog_title"),
                        t("evolve_network_err").format(err=msg_text),
                    )
                return

        # Perform locally (both modes)
        result = perform_evolution(state)
        if result["ok"]:
            QMessageBox.information(
                self, t("evolve_dialog_title"),
                t("evolve_success").format(
                    frm=result["from"], to=result["to"], title=result["title"],
                ),
            )
            # Record evolution as a memorable moment
            try:
                from sentinel import identity as _id
                _id.record_evolution_moment(result["from"], result["to"], result["title"])
            except Exception:
                pass
            # If entering Named Slime tier, trigger the naming ceremony
            self._maybe_prompt_for_name()
        else:
            # Shouldn't happen — eligibility was just checked — but handle it.
            QMessageBox.warning(self, t("evolve_dialog_title"),
                                result.get("reason", "進化失敗"))

        self.refresh()

    def _maybe_prompt_for_name(self):
        """If the slime just became Named Slime without a name, ceremony time."""
        try:
            from sentinel import identity
        except ImportError:
            return
        if not identity.consume_naming_prompt():
            return

        # Loop until we get a valid name or user explicitly cancels twice.
        # Naming is a one-shot ceremony; if they truly skip, the flag stays
        # cleared but slime_name is empty — they can never name it again.
        # We warn them on skip.
        intro = t("naming_intro")
        prompt = t("naming_prompt")
        name, ok = QInputDialog.getText(self, t("naming_title"),
                                         f"{intro}\n\n{prompt}")
        name = (name or "").strip()
        if not ok or not name:
            confirm = QMessageBox.question(
                self, t("naming_title"),
                t("naming_skip_confirm"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if confirm == QMessageBox.Yes:
                return  # They chose to skip permanently
            # Retry once
            name, ok = QInputDialog.getText(self, t("naming_title"), prompt)
            name = (name or "").strip()
            if not ok or not name:
                return

        if len(name) > 24:
            QMessageBox.warning(self, t("naming_title"), t("naming_too_long"))
            return

        if identity.set_slime_name(name):
            QMessageBox.information(
                self, t("naming_title"),
                t("naming_success").format(name=name),
            )

    def _share_slime(self):
        """Generate a share card with the slime avatar and stats."""
        from PySide6.QtGui import QPixmap, QPainter, QFont, QColor, QLinearGradient, QBrush, QPen
        from PySide6.QtCore import QRect, QPoint
        from sentinel.evolution import load_evolution
        import datetime

        state = load_evolution()
        theme = get_theme_info()
        accent = QColor(theme["accent"])

        # Create card image
        card_w, card_h = 480, 640
        pixmap = QPixmap(card_w, card_h)

        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)

        # Background gradient
        bg_grad = QLinearGradient(0, 0, 0, card_h)
        bg_grad.setColorAt(0, QColor(15, 15, 35))
        bg_grad.setColorAt(1, QColor(10, 10, 25))
        p.fillRect(0, 0, card_w, card_h, QBrush(bg_grad))

        # Border
        p.setPen(QPen(accent, 2))
        p.setBrush(QColor(0, 0, 0, 0))
        p.drawRoundedRect(2, 2, card_w - 4, card_h - 4, 12, 12)

        # Header
        p.setPen(accent)
        header_font = QFont("Segoe UI", 22, QFont.Bold)
        p.setFont(header_font)
        p.drawText(QRect(0, 20, card_w, 40), Qt.AlignCenter, "AI Slime")

        sub_font = QFont("Microsoft JhengHei", 11)
        p.setFont(sub_font)
        p.setPen(QColor(150, 150, 150))
        p.drawText(QRect(0, 55, card_w, 25), Qt.AlignCenter, "轉生守護靈")

        p.end()

        # Render slime widget onto card.
        # Use grab() to capture the whole widget at its current size, then
        # scale to 280x280. An earlier version used render() into a fixed
        # 280x280 pixmap, which clipped the widget (cy = h*0.58 fell outside
        # the canvas) and only the background survived into the share card.
        full_grab = self.slime_widget.grab()
        slime_pixmap = full_grab.scaled(
            280, 280,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        # Center the scaled slime within its 280x280 slot
        offset_x = 100 + (280 - slime_pixmap.width()) // 2
        offset_y = 80 + (280 - slime_pixmap.height()) // 2
        p.drawPixmap(offset_x, offset_y, slime_pixmap)

        # Title and form
        title_font = QFont("Microsoft JhengHei", 20, QFont.Bold)
        p.setFont(title_font)
        p.setPen(accent)
        p.drawText(QRect(0, 370, card_w, 35), Qt.AlignCenter, state.title)

        form_font = QFont("Segoe UI", 12)
        p.setFont(form_font)
        p.setPen(QColor(150, 150, 150))
        p.drawText(QRect(0, 400, card_w, 25), Qt.AlignCenter, state.form)

        if state.evolution_direction:
            p.setPen(QColor(46, 213, 115))
            p.drawText(QRect(0, 425, card_w, 25), Qt.AlignCenter, f"進化方向：{state.evolution_direction}")

        # Stats
        stats_font = QFont("Microsoft JhengHei", 11)
        p.setFont(stats_font)
        p.setPen(QColor(200, 200, 200))

        stats_y = 465
        stats = [
            f"存活 {state.days_alive():.1f} 天",
            f"觀察 {state.total_observations:,} 次",
            f"學習 {state.total_learnings} 次",
            f"對話 {state.total_conversations} 次",
        ]
        p.drawText(QRect(0, stats_y, card_w, 25), Qt.AlignCenter, "  |  ".join(stats))

        # Skills summary
        unlocked = [s for s in state.skills if s.unlocked]
        if unlocked:
            p.setPen(QColor(150, 150, 150))
            skill_names = "、".join(s.jp_name for s in unlocked[:6])
            if len(unlocked) > 6:
                skill_names += f" ... 等 {len(unlocked)} 個技能"
            p.drawText(QRect(20, stats_y + 35, card_w - 40, 25), Qt.AlignCenter, skill_names)

        # Top traits
        if state.dominant_traits:
            TRAIT_ZH = {
                "coding": "程式開發", "communication": "溝通", "research": "研究探索",
                "creative": "創作", "multitasking": "多工切換", "deep_focus": "深度專注",
                "late_night": "夜間活動",
            }
            trait_text = " × ".join(TRAIT_ZH.get(t_name, t_name) for t_name in state.dominant_traits[:3])
            p.setPen(QColor(255, 165, 2))
            p.drawText(QRect(0, stats_y + 65, card_w, 25), Qt.AlignCenter, f"特質：{trait_text}")

        # Footer — 帶上 GitHub 專案 URL，讓看到圖的人知道去哪找原始碼
        p.setPen(QColor(80, 80, 80))
        small_font = QFont("Segoe UI", 9)
        p.setFont(small_font)
        now = datetime.datetime.now().strftime("%Y/%m/%d")
        p.drawText(QRect(0, card_h - 45, card_w, 20), Qt.AlignCenter, f"AI Slime Agent  {now}")
        p.drawText(QRect(0, card_h - 25, card_w, 18), Qt.AlignCenter, "github.com/page5888/slimeagent")

        p.end()

        # 存檔 + 複製到剪貼簿
        save_path = Path.home() / ".hermes" / "aislime_share.png"
        pixmap.save(str(save_path), "PNG")

        clipboard = QApplication.clipboard()
        clipboard.setPixmap(pixmap)
        self._last_share_pixmap = pixmap  # 保留引用給平台分享用
        self._last_share_path = save_path
        return pixmap

    def _on_share_btn_clicked(self):
        """「分享」按鈕：產生卡片 + 複製到剪貼簿 + 提示。"""
        self._share_slime()
        QMessageBox.information(
            self, "AI Slime",
            f"分享卡已產生！\n\n"
            f"✅ 已複製到剪貼簿（直接 Ctrl+V 貼到任何地方）\n"
            f"💾 已存到：{getattr(self, '_last_share_path', '~/.hermes/aislime_share.png')}\n\n"
            f"也可以點社群按鈕一鍵分享"
        )

    def _share_to_platform(self, platform: str):
        """產生分享卡 → 開啟對應社群平台的分享頁面。"""
        import webbrowser
        import urllib.parse

        # 先產生圖片（不彈提示）
        self._share_slime()

        from sentinel.evolution import load_evolution
        state = load_evolution()

        # 指向 GitHub 專案頁面，讓好奇的人可以看原始碼、issue、release
        SITE_URL = "https://github.com/page5888/slimeagent"

        # 組裝分享文字
        share_text = (
            f"我的 AI 守護靈「{state.title}」已經觀察了 {state.total_observations:,} 次！"
            f" 存活 {state.days_alive():.0f} 天"
        )
        if state.dominant_traits:
            TRAIT_ZH = {
                "coding": "程式開發", "communication": "溝通", "research": "研究探索",
                "creative": "創作", "multitasking": "多工", "deep_focus": "深度專注",
                "late_night": "夜貓族",
            }
            traits = "、".join(TRAIT_ZH.get(t, t) for t in state.dominant_traits[:2])
            share_text += f"（{traits}型）"
        share_text += f"\n\n🧬 AI Slime Agent\n{SITE_URL}"

        encoded = urllib.parse.quote(share_text)
        encoded_url = urllib.parse.quote(SITE_URL)
        encoded_title = urllib.parse.quote("我的 AI Slime Agent")

        # 社群分享 intent URL 都不支援直接附圖，所以開瀏覽器之前先提醒
        # 用戶「圖片已在剪貼簿」— 文字自動帶進 intent，圖片要 Ctrl+V 貼。
        def _remind_paste_image(where: str):
            QMessageBox.information(
                self, "AI Slime",
                f"文字已帶入 {where} 發文頁面。\n\n"
                f"史萊姆圖片已複製到剪貼簿 —\n"
                f"在發文框裡按 Ctrl+V 即可貼上圖片。",
            )

        if platform == "x":
            _remind_paste_image("X (Twitter)")
            webbrowser.open(f"https://x.com/intent/tweet?text={encoded}")
        elif platform == "facebook":
            # Facebook sharer 只支援 u 參數，文字由 OG meta 決定
            _remind_paste_image("Facebook")
            webbrowser.open(f"https://www.facebook.com/sharer/sharer.php?u={encoded_url}")
        elif platform == "reddit":
            _remind_paste_image("Reddit")
            webbrowser.open(
                f"https://www.reddit.com/submit?title={encoded_title}&url={encoded_url}&selftext=true&text={encoded}"
            )
        elif platform == "threads":
            _remind_paste_image("Threads")
            webbrowser.open(f"https://www.threads.net/intent/post?text={encoded}")
        elif platform == "discord":
            # Discord 沒有 share URL，複製 markdown 格式文字
            # 圖片已經在剪貼簿裡，先讓用戶貼圖，再提供文字
            discord_text = (
                f"**🧬 我的 AI Slime — {state.title}**\n"
                f"> 存活 {state.days_alive():.0f} 天 | 觀察 {state.total_observations:,} 次 | 學習 {state.total_learnings} 次\n"
            )
            if state.evolution_direction:
                discord_text += f"> 進化方向：{state.evolution_direction}\n"
            discord_text += f"\n{SITE_URL}"

            # 先提示貼圖，按確定後才把文字放到剪貼簿
            QMessageBox.information(
                self, "AI Slime",
                "圖片已複製到剪貼簿！\n\n"
                "① 先到 Discord 頻道 Ctrl+V 貼上圖片\n"
                "② 按下面的「確定」後文字會複製到剪貼簿\n"
                "③ 再 Ctrl+V 貼上文字"
            )
            QApplication.clipboard().setText(discord_text)

    def retranslate(self):
        self.refresh_btn.setText(t("status_refresh"))
        # evolve_btn text is driven by state in refresh()
        self.refresh()


# ─── Settings Tab ────────────────────────────────────────────────────────

def _make_password_field(initial_text: str = "", placeholder: str = "") -> tuple:
    """Password-style QLineEdit with an eye-icon visibility toggle.

    Returns (line_edit, container_widget). Add the container to the parent
    layout; use line_edit to read/write the value.

    Rationale: users can't tell whether a pre-filled password field is empty
    or masked, so they re-type it unnecessarily. The toggle lets them peek
    to verify the stored value is still there.
    """
    line_edit = QLineEdit(initial_text)
    line_edit.setEchoMode(QLineEdit.Password)
    if placeholder:
        line_edit.setPlaceholderText(placeholder)

    toggle_btn = QPushButton("👁")
    toggle_btn.setCheckable(True)
    toggle_btn.setFixedWidth(32)
    toggle_btn.setToolTip("顯示 / 隱藏")

    def _on_toggle(checked: bool):
        if checked:
            line_edit.setEchoMode(QLineEdit.Normal)
            toggle_btn.setText("🙈")
        else:
            line_edit.setEchoMode(QLineEdit.Password)
            toggle_btn.setText("👁")

    toggle_btn.toggled.connect(_on_toggle)

    container = QWidget()
    hlayout = QHBoxLayout(container)
    hlayout.setContentsMargins(0, 0, 0, 0)
    hlayout.addWidget(line_edit)
    hlayout.addWidget(toggle_btn)

    return line_edit, container


class ProviderRow(QGroupBox):
    """A single LLM provider config row."""
    def __init__(self, provider: dict):
        name = provider["name"]
        super().__init__(f"{'✓' if provider.get('enabled') else '○'} {name}")
        self.provider_name = name

        layout = QFormLayout(self)

        self.enabled_combo = QComboBox()
        self.enabled_combo.addItem("已開啟", True)
        self.enabled_combo.addItem("已關閉", False)
        self.enabled_combo.setCurrentIndex(0 if provider.get("enabled") else 1)
        self.enabled_combo.currentIndexChanged.connect(self._update_title)
        layout.addRow("狀態", self.enabled_combo)

        self.apikey_input, apikey_container = _make_password_field(
            provider.get("api_key", ""),
            placeholder="sk-... / AIza...（圖片生成可多 key 逗號分隔）",
        )
        layout.addRow("金鑰", apikey_container)

        self.models_input = QLineEdit(", ".join(provider.get("models", [])))
        self.models_input.setPlaceholderText("模型1, 模型2（依序嘗試）")
        layout.addRow("模型", self.models_input)

        if provider.get("base_url"):
            self.base_url_input = QLineEdit(provider["base_url"])
            layout.addRow("網址", self.base_url_input)
        else:
            self.base_url_input = None

    def _update_title(self):
        enabled = self.enabled_combo.currentData()
        self.setTitle(f"{'✓' if enabled else '○'} {self.provider_name}")

    def to_dict(self, original: dict) -> dict:
        d = dict(original)
        d["enabled"] = self.enabled_combo.currentData()
        d["api_key"] = self.apikey_input.text()
        d["models"] = [m.strip() for m in self.models_input.text().split(",") if m.strip()]
        if self.base_url_input:
            d["base_url"] = self.base_url_input.text()
        return d


class SettingsTab(QWidget):
    language_changed = Signal()

    def __init__(self):
        super().__init__()
        from sentinel.ui import tokens as _tk
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _tk.SPACE["lg"], _tk.SPACE["md"],
            _tk.SPACE["lg"], _tk.SPACE["md"],
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        form = QVBoxLayout(inner)
        form.setSpacing(_tk.SPACE["md"])

        # Language
        lang_group = QGroupBox(t("settings_language"))
        lang_layout = QHBoxLayout()
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("中文", "zh")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setCurrentIndex(0 if get_language() == "zh" else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_change)
        lang_layout.addWidget(self.lang_combo)
        lang_group.setLayout(lang_layout)
        form.addWidget(lang_group)

        # Theme
        theme_group = QGroupBox(t("settings_theme"))
        theme_layout = QHBoxLayout()
        self.theme_combo = QComboBox()
        # v0.7-alpha lite: only surface 2 themes in the picker. The
        # other 4 are still in THEMES (and saved settings still load
        # them if previously chosen) — they're just hidden from new
        # selections during dogfood. Restore by deleting this filter.
        _LITE_THEMES = {"slime_blue", "predator_dark"}
        for tid, tname in list_themes():
            if tid not in _LITE_THEMES:
                continue
            self.theme_combo.addItem(tname, tid)
        # Set current
        current_theme = get_theme()
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == current_theme:
                self.theme_combo.setCurrentIndex(i)
                break
        self.theme_combo.currentIndexChanged.connect(self._on_theme_change)
        theme_layout.addWidget(self.theme_combo)
        theme_group.setLayout(theme_layout)
        form.addWidget(theme_group)

        # ── Ollama Status ──
        ollama_group = QGroupBox(t("settings_ollama_status"))
        ollama_layout = QVBoxLayout()
        status_row = QHBoxLayout()
        self.ollama_status_label = QLabel("...")
        status_row.addWidget(self.ollama_status_label)
        status_row.addStretch()
        self.ollama_refresh_btn = QPushButton(t("ollama_refresh"))
        self.ollama_refresh_btn.setFixedWidth(100)
        self.ollama_refresh_btn.clicked.connect(self._refresh_ollama_status)
        status_row.addWidget(self.ollama_refresh_btn)
        ollama_layout.addLayout(status_row)
        self.ollama_models_label = QLabel("")
        self.ollama_models_label.setStyleSheet(_tk.text_meta())
        ollama_layout.addWidget(self.ollama_models_label)
        ollama_group.setLayout(ollama_layout)
        form.addWidget(ollama_group)
        self._refresh_ollama_status()

        # ── Model Preference ──
        pref_group = QGroupBox(t("settings_model_pref"))
        pref_layout = QFormLayout()

        self.chat_pref_combo = QComboBox()
        self.chat_pref_combo.addItem(t("model_cloud_first"), "cloud_first")
        self.chat_pref_combo.addItem(t("model_local_first"), "local_first")
        self.chat_pref_combo.addItem(t("model_local_only"), "local_only")
        self._set_combo_by_data(self.chat_pref_combo, config.CHAT_MODEL_PREF)
        pref_layout.addRow(t("settings_chat_model"), self.chat_pref_combo)

        self.analysis_pref_combo = QComboBox()
        self.analysis_pref_combo.addItem(t("model_local_first"), "local_first")
        self.analysis_pref_combo.addItem(t("model_cloud_first"), "cloud_first")
        self.analysis_pref_combo.addItem(t("model_local_only"), "local_only")
        self._set_combo_by_data(self.analysis_pref_combo, config.ANALYSIS_MODEL_PREF)
        pref_layout.addRow(t("settings_analysis_model"), self.analysis_pref_combo)

        pref_group.setLayout(pref_layout)
        form.addWidget(pref_group)

        # ── User Mode (BYOK vs Quota) ──
        mode_group = QGroupBox(t("settings_user_mode"))
        mode_layout = QVBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(t("mode_byok"), "byok")
        self.mode_combo.addItem(t("mode_quota"), "quota")

        from sentinel.wallet.quota import QuotaManager
        self._quota_mgr = QuotaManager(relay_url=config.RELAY_SERVER_URL)
        self._set_combo_by_data(self.mode_combo, self._quota_mgr.mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_change)
        mode_layout.addWidget(self.mode_combo)

        # Wallet info row (shown in quota mode)
        self.wallet_info = QWidget()
        wallet_row = QHBoxLayout(self.wallet_info)
        wallet_row.setContentsMargins(0, 4, 0, 0)
        self.balance_label = QLabel(f"{t('wallet_balance')}: --")
        wallet_row.addWidget(self.balance_label)
        wallet_row.addStretch()
        self.topup_btn = QPushButton(t("wallet_topup"))
        self.topup_btn.setFixedWidth(70)
        self.topup_btn.clicked.connect(self._open_topup)
        wallet_row.addWidget(self.topup_btn)
        self.wallet_link_btn = QPushButton(t("wallet_link"))
        self.wallet_link_btn.setFixedWidth(80)
        self.wallet_link_btn.clicked.connect(self._open_wallet)
        wallet_row.addWidget(self.wallet_link_btn)
        mode_layout.addWidget(self.wallet_info)
        self.wallet_info.setVisible(self._quota_mgr.mode == "quota")

        # Google login button
        relay_btn_row = QHBoxLayout()
        self.google_login_btn = QPushButton("Google 登入")
        self.google_login_btn.setStyleSheet(
            "QPushButton { background: #4285f4; color: #fff; border: none; "
            "border-radius: 4px; padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #5a95f5; }"
        )
        self.google_login_btn.clicked.connect(self._google_login)
        relay_btn_row.addWidget(self.google_login_btn)

        self.logout_btn = QPushButton("登出")
        self.logout_btn.setFixedWidth(60)
        self.logout_btn.clicked.connect(self._logout)
        relay_btn_row.addWidget(self.logout_btn)

        self.auth_status = QLabel("")
        self._refresh_auth_status()
        relay_btn_row.addWidget(self.auth_status, stretch=1)
        mode_layout.addLayout(relay_btn_row)

        mode_group.setLayout(mode_layout)
        form.addWidget(mode_group)

        # Auto-start
        from sentinel.autostart import is_autostart_enabled
        autostart_group = QGroupBox("開機啟動")
        autostart_layout = QHBoxLayout()
        self.autostart_combo = QComboBox()
        self.autostart_combo.addItem("開機自動甦醒", True)
        self.autostart_combo.addItem("手動甦醒", False)
        self.autostart_combo.setCurrentIndex(0 if is_autostart_enabled() else 1)
        autostart_layout.addWidget(self.autostart_combo)
        autostart_group.setLayout(autostart_layout)
        form.addWidget(autostart_group)

        # Voice features (Phase D5 master toggle).
        # One checkbox controls both voice.listen (mic capture + STT)
        # and voice.speak (TTS). Off by default — voice features are
        # privacy-sensitive and we'd rather have users opt in.
        # The catalog layer hides voice.* from the LLM when off, so
        # the slime won't propose it; the action policy denies as
        # defense in depth if something tries to bypass.
        voice_group = QGroupBox("語音功能")
        voice_layout = QVBoxLayout()
        self.voice_enabled_check = QCheckBox(
            "啟用語音聽說（voice.listen + voice.speak）"
        )
        self.voice_enabled_check.setChecked(
            bool(getattr(config, "VOICE_ENABLED", True))
        )
        voice_layout.addWidget(self.voice_enabled_check)
        voice_hint = QLabel(
            "<span style='color:#888; font-size:11px;'>"
            "關閉後 Slime 不會提案麥克風錄音或 TTS 唸出文字。<br>"
            "聊天裡叫他「唸出X」「聽我說」會被拒絕並提示要在這裡開。"
            "</span>"
        )
        voice_hint.setWordWrap(True)
        voice_layout.addWidget(voice_hint)
        voice_group.setLayout(voice_layout)
        form.addWidget(voice_group)

        # Telegram
        tg_group = QGroupBox(t("settings_telegram"))
        tg_layout = QFormLayout()
        self.token_input, token_container = _make_password_field(
            config.TELEGRAM_BOT_TOKEN,
        )
        tg_layout.addRow(t("settings_bot_token"), token_container)
        self.chatid_input = QLineEdit(str(config.TELEGRAM_CHAT_ID))
        tg_layout.addRow(t("settings_chat_id"), self.chatid_input)
        tg_group.setLayout(tg_layout)
        form.addWidget(tg_group)

        # LLM Providers (multi-provider with fallback)
        llm_label = QLabel("<b style='color:#00dcff;'>AI 模型提供者</b>"
                           "  <span style='color:#666;'>（由上到下依序嘗試，失敗自動換下一個）</span>")
        form.addWidget(llm_label)

        self.provider_rows = []
        # v0.7-alpha lite: only surface Gemini + OpenAI + Ollama as
        # configurable in the UI. The other providers (claude, openrouter,
        # groq, deepseek, deepinfra) keep working in config.LLM_PROVIDERS
        # — saved settings + LLM call routing all still see them — they
        # just don't get a row in the settings tab during dogfood.
        # OpenAI is in the set because it's the image-gen fallback target
        # when Gemini quota is exhausted (see expression/generator.py).
        _LITE_PROVIDERS = {"gemini", "openai", "ollama"}
        for provider in config.LLM_PROVIDERS:
            pname = (provider.get("name") or "").lower()
            if pname not in _LITE_PROVIDERS:
                # Still keep the underlying config — just don't render
                # a row. The provider list_loop in settings save (line
                # ~4465) zips provider_rows with config.LLM_PROVIDERS,
                # so we keep an invisible placeholder to preserve the
                # alignment.
                self.provider_rows.append(None)
                continue
            row = ProviderRow(provider)
            self.provider_rows.append(row)
            form.addWidget(row)

        # Monitor
        mon_group = QGroupBox(t("settings_monitor"))
        mon_layout = QFormLayout()
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(5, 300)
        self.interval_spin.setValue(config.SYSTEM_CHECK_INTERVAL)
        mon_layout.addRow(t("settings_check_interval"), self.interval_spin)
        self.idle_spin = QSpinBox()
        self.idle_spin.setRange(60, 7200)
        self.idle_spin.setValue(config.IDLE_REPORT_INTERVAL)
        mon_layout.addRow(t("settings_idle_report"), self.idle_spin)

        self.distill_spin = QSpinBox()
        self.distill_spin.setRange(60, 3600)
        self.distill_spin.setSuffix(" 秒")
        self.distill_spin.setValue(config.DISTILL_INTERVAL)
        self.distill_spin.setToolTip("LLM 蒸餾間隔：值越大越省 API 呼叫。預設 300 秒（5 分鐘）")
        mon_layout.addRow(t("settings_distill_interval"), self.distill_spin)

        self.screen_min_spin = QSpinBox()
        self.screen_min_spin.setRange(30, 3600)
        self.screen_min_spin.setSuffix(" 秒")
        self.screen_min_spin.setValue(config.SCREEN_CAPTURE_MIN)
        self.screen_min_spin.setToolTip("截圖最短間隔。值越大截圖越少，省 API。預設 120 秒")
        mon_layout.addRow(t("settings_screen_min"), self.screen_min_spin)

        self.screen_max_spin = QSpinBox()
        self.screen_max_spin.setRange(60, 7200)
        self.screen_max_spin.setSuffix(" 秒")
        self.screen_max_spin.setValue(config.SCREEN_CAPTURE_MAX)
        self.screen_max_spin.setToolTip("截圖最長間隔。每次截圖在 MIN 和 MAX 之間隨機。預設 600 秒")
        mon_layout.addRow(t("settings_screen_max"), self.screen_max_spin)

        self.dirs_input = QPlainTextEdit()
        self.dirs_input.setMaximumHeight(80)
        self.dirs_input.setPlainText("\n".join(str(d) for d in config.WATCH_DIRS))
        mon_layout.addRow(t("settings_watch_dirs"), self.dirs_input)
        mon_group.setLayout(mon_layout)
        form.addWidget(mon_group)

        form.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        # Save + Update buttons
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton(t("settings_save"))
        self.save_btn.clicked.connect(self.save_settings)
        btn_row.addWidget(self.save_btn)

        self.update_btn = QPushButton("🔄 檢查更新")
        self.update_btn.setStyleSheet(
            "QPushButton { background: #1e90ff; color: #fff; border: none; "
            "border-radius: 4px; padding: 8px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #3aa0ff; }"
        )
        self.update_btn.clicked.connect(self._check_update)
        btn_row.addWidget(self.update_btn)
        layout.addLayout(btn_row)

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _check_update(self):
        """Git pull from origin main and prompt restart if updated."""
        import subprocess
        self.update_btn.setEnabled(False)
        self.update_btn.setText("更新中...")

        try:
            # Fetch + check if there are new commits
            result = subprocess.run(
                ["git", "fetch", "origin", "main"],
                capture_output=True, text=True, timeout=30,
                cwd=str(Path(__file__).parent.parent),
            )

            # Check how many commits behind
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD..origin/main"],
                capture_output=True, text=True, timeout=10,
                cwd=str(Path(__file__).parent.parent),
            )
            behind = int(result.stdout.strip() or "0")

            if behind == 0:
                QMessageBox.information(self, "檢查更新", "已經是最新版本！")
                self.update_btn.setText("🔄 檢查更新")
                self.update_btn.setEnabled(True)
                return

            # Pull: fetch then hard-reset to avoid divergent-branch errors
            result = subprocess.run(
                ["git", "fetch", "origin", "main"],
                capture_output=True, text=True, timeout=60,
                cwd=str(Path(__file__).parent.parent),
            )
            if result.returncode != 0:
                QMessageBox.warning(
                    self, "更新失敗",
                    f"git fetch 失敗：\n{result.stderr[:500]}",
                )
                self.update_btn.setText("🔄 檢查更新")
                self.update_btn.setEnabled(True)
                return

            result = subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                capture_output=True, text=True, timeout=30,
                cwd=str(Path(__file__).parent.parent),
            )
            if result.returncode != 0:
                QMessageBox.warning(
                    self, "更新失敗",
                    f"git reset 失敗：\n{result.stderr[:500]}",
                )
                self.update_btn.setText("🔄 檢查更新")
                self.update_btn.setEnabled(True)
                return

            reply = QMessageBox.question(
                self, "更新完成",
                f"已拉取 {behind} 筆新提交。\n需要重新啟動才能生效。\n\n現在重啟嗎？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                # Restart: launch a new process then exit
                import sys
                subprocess.Popen(
                    [sys.executable, "-m", "sentinel"],
                    cwd=str(Path(__file__).parent.parent),
                )
                QApplication.quit()
                import os
                os._exit(0)
            else:
                self.update_btn.setText("✅ 已更新（需重啟）")

        except Exception as e:
            QMessageBox.warning(self, "更新失敗", f"錯誤：{e}")
            self.update_btn.setText("🔄 檢查更新")
            self.update_btn.setEnabled(True)

    def _refresh_ollama_status(self):
        """偵測 Ollama 連線狀態（同步，簡單直接）。"""
        self.ollama_status_label.setText("偵測中...")
        try:
            from sentinel.local_llm import reset_availability_cache, is_ollama_running, list_local_models
            reset_availability_cache()
            running = is_ollama_running()
            models = list_local_models() if running else []
        except Exception:
            running = False
            models = []

        if running:
            self.ollama_status_label.setText(
                f"<b style='color:#2ed573;'>{t('ollama_connected')}</b>")
            if models:
                self.ollama_models_label.setText(
                    "  ".join(f"[{m}]" for m in models))
            else:
                self.ollama_models_label.setText(t("ollama_no_models"))
        else:
            self.ollama_status_label.setText(
                f"<b style='color:#ff4757;'>{t('ollama_disconnected')}</b>")
            self.ollama_models_label.setText(
                "ollama serve → ollama pull gemma3:4b")

    def _on_mode_change(self):
        mode = self.mode_combo.currentData()
        self._quota_mgr.mode = mode
        self.wallet_info.setVisible(mode == "quota")
        if mode == "quota" and self._quota_mgr.is_logged_in:
            bal = self._quota_mgr.get_balance()
            self.balance_label.setText(f"{t('wallet_balance')}: {bal:,} 點")

    def _open_topup(self):
        import webbrowser
        webbrowser.open(self._quota_mgr.get_topup_url())

    def _open_wallet(self):
        import webbrowser
        webbrowser.open(self._quota_mgr.get_wallet_url())

    def _on_lang_change(self):
        lang = self.lang_combo.currentData()
        set_language(lang)
        self.language_changed.emit()

    def _on_theme_change(self):
        theme_id = self.theme_combo.currentData()
        set_theme(theme_id)
        QApplication.instance().setStyleSheet(get_theme_style())
        self.language_changed.emit()  # trigger retranslate to update header colors

    def _refresh_auth_status(self):
        """Show current login state."""
        from sentinel.relay_client import AUTH_FILE, _get_token
        logged_in = False
        if AUTH_FILE.exists() and _get_token():
            try:
                import json
                data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
                name = data.get("display_name", data.get("email", "?"))
                self.auth_status.setText(
                    f"<span style='color:#2ed573;'>✓ 已登入：{name}</span>"
                )
                logged_in = True
            except Exception:
                self.auth_status.setText(
                    "<span style='color:#ffa502;'>Token 檔案異常</span>"
                )
        else:
            self.auth_status.setText(
                "<span style='color:#888;'>尚未登入（上架需要先登入）</span>"
            )
        self.logout_btn.setVisible(logged_in)

    def _google_login(self):
        """Google OAuth login — opens browser, gets token, sends to relay."""
        client_id = config.GOOGLE_CLIENT_ID
        relay_url = config.RELAY_SERVER_URL

        if not client_id:
            QMessageBox.warning(self, "登入", "請先填入 Google Client ID")
            return
        if not relay_url:
            QMessageBox.warning(self, "登入", "中繼伺服器未設定")
            return

        self.google_login_btn.setEnabled(False)
        self.google_login_btn.setText("登入中...")

        # Run OAuth flow in a thread to avoid blocking GUI
        import threading

        def _do_login():
            try:
                from sentinel.google_auth import full_login_flow
                auth_data = full_login_flow(
                    client_id, relay_url,
                    on_status=lambda s: None,
                    client_secret=config.GOOGLE_CLIENT_SECRET,
                )
                # Back on GUI thread
                from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(
                    self, "_on_login_success",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, auth_data.get("display_name", "?")),
                )
            except Exception as e:
                from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(
                    self, "_on_login_error",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, str(e)),
                )

        threading.Thread(target=_do_login, daemon=True).start()

    @Slot(str)
    def _on_login_success(self, name: str):
        self.google_login_btn.setEnabled(True)
        self.google_login_btn.setText("Google 登入")
        self._refresh_auth_status()
        QMessageBox.information(
            self, "登入成功",
            f"已登入為 {name}，現在可以上架了！",
        )

    @Slot(str)
    def _on_login_error(self, error: str):
        self.google_login_btn.setEnabled(True)
        self.google_login_btn.setText("Google 登入")
        QMessageBox.warning(self, "登入失敗", error)

    def _logout(self):
        """Clear saved auth token."""
        from sentinel.google_auth import clear_auth
        clear_auth()
        self._refresh_auth_status()
        QMessageBox.information(self, "登出", "已登出，需要重新登入才能使用市場功能。")

    def save_settings(self):
        """Save settings to a JSON config file.

        Merge-safe: reads the existing file first and only overwrites the
        fields this tab knows about. Prevents one UI with empty fields
        from wiping out unrelated settings (e.g. wizard_completed).

        Also guards against empty telegram_chat_id crashing int() — the
        old version would write the file, then crash on the config
        update, leaving runtime in a half-applied state.
        """
        # Build updated providers list. For each provider row, start from
        # the saved-on-disk provider (not config.LLM_PROVIDERS, which may
        # have been mutated in memory) so any field the row doesn't
        # expose (e.g. base_url defaults) is preserved.
        settings_file = Path.home() / ".hermes" / "sentinel_settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)

        existing: dict = {}
        if settings_file.exists():
            try:
                existing = json.loads(settings_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        updated_providers = []
        for row, original in zip(self.provider_rows, config.LLM_PROVIDERS):
            if row is None:
                # v0.7 lite mode: this provider has no UI row. Keep
                # whatever's already in config.LLM_PROVIDERS for it.
                updated_providers.append(dict(original))
                continue
            updated_providers.append(row.to_dict(original))

        # Parse chat_id defensively — empty string or non-numeric falls
        # back to 0 rather than crashing.
        chat_id_text = self.chatid_input.text().strip()
        try:
            chat_id_int = int(chat_id_text) if chat_id_text else 0
        except ValueError:
            chat_id_int = 0

        # Merge: start from existing, overwrite only the fields we own.
        settings = dict(existing)
        settings.update({
            "language": self.lang_combo.currentData(),
            "theme": self.theme_combo.currentData(),
            "user_mode": self.mode_combo.currentData(),
            "chat_model_pref": self.chat_pref_combo.currentData(),
            "analysis_model_pref": self.analysis_pref_combo.currentData(),
            "telegram_bot_token": self.token_input.text(),
            "telegram_chat_id": chat_id_text,
            "llm_providers": updated_providers,
            "check_interval": self.interval_spin.value(),
            "idle_report_interval": self.idle_spin.value(),
            "distill_interval": self.distill_spin.value(),
            "screen_capture_min": self.screen_min_spin.value(),
            "screen_capture_max": max(
                self.screen_max_spin.value(),
                self.screen_min_spin.value() + 30,
            ),
            "watch_dirs": [
                d.strip() for d in self.dirs_input.toPlainText().split("\n") if d.strip()
            ],
            # Phase D5 master toggle. Saved as a plain bool; the load
            # path coerces legacy values via bool() so old files don't
            # need migration.
            "voice_enabled": bool(self.voice_enabled_check.isChecked()),
        })

        settings_file.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        # Apply to runtime config (all-or-nothing — any exception past
        # this point leaves disk + runtime consistent since the write
        # above succeeded).
        config.CHAT_MODEL_PREF = settings["chat_model_pref"]
        config.ANALYSIS_MODEL_PREF = settings["analysis_model_pref"]
        config.TELEGRAM_BOT_TOKEN = settings["telegram_bot_token"]
        config.TELEGRAM_CHAT_ID = chat_id_int
        config.LLM_PROVIDERS = updated_providers
        config.SYSTEM_CHECK_INTERVAL = settings["check_interval"]
        config.IDLE_REPORT_INTERVAL = settings["idle_report_interval"]
        config.DISTILL_INTERVAL = settings["distill_interval"]
        config.SCREEN_CAPTURE_MIN = settings["screen_capture_min"]
        config.SCREEN_CAPTURE_MAX = settings["screen_capture_max"]
        config.WATCH_DIRS = [Path(d) for d in settings["watch_dirs"]]
        # Apply voice toggle live so a save takes effect on the next
        # chat turn — both the catalog layer and the policy layer read
        # from config at call time.
        config.VOICE_ENABLED = settings["voice_enabled"]

        # Handle autostart
        from sentinel.autostart import enable_autostart, disable_autostart
        if self.autostart_combo.currentData():
            enable_autostart()
        else:
            disable_autostart()

        QMessageBox.information(self, "AI Slime", t("settings_saved"))

    def retranslate(self):
        self.save_btn.setText(t("settings_save"))


# ─── Approval Tab (growth PR 2a) ───────────────────────────────────────────
# 待同意頁籤。list_pending() 是事實的來源。GUI 只是一層 viewer —
# 真正落檔發生在 approve()，真正歸檔發生在 reject()。

class ApprovalTab(QWidget):
    """Pending-approval queue + skill history UI.

    Two sub-tabs:
      1. 待審核 — pending proposals (approve / reject)
      2. 技能歷史 — all proposals ever (pending + approved + rejected)

    Refresh happens on MainWindow's 30s timer + when the user clicks
    the tab + after every approve/reject action.
    """

    proposals_changed = Signal()

    def __init__(self):
        super().__init__()
        self._current_id: str | None = None
        self._pending_cache: list = []
        self._history_cache: list[dict] = []
        self._history_selected: dict | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        self.sub_tabs = QTabWidget()
        self.sub_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #333; }"
            "QTabBar::tab { padding: 6px 16px; }"
        )

        # ── Sub-tab 1: Pending ───────────────────────────────────
        pending_page = QWidget()
        pending_layout = QVBoxLayout(pending_page)
        pending_layout.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        self.title_lbl = QLabel("")
        self.title_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        header.addWidget(self.title_lbl)
        header.addStretch()
        self.refresh_btn = QPushButton("")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        pending_layout.addLayout(header)

        split = QSplitter(Qt.Horizontal)

        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        split.addWidget(self.list_widget)

        detail_box = QWidget()
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.setContentsMargins(8, 0, 0, 0)

        self.empty_lbl = QLabel("")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setStyleSheet("color: #888; padding: 40px;")
        detail_layout.addWidget(self.empty_lbl)

        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        mono = QFont("Consolas", 10)
        mono.setStyleHint(QFont.Monospace)
        self.detail_text.setFont(mono)
        detail_layout.addWidget(self.detail_text, stretch=1)

        btn_row = QHBoxLayout()
        self.approve_btn = QPushButton("")
        self.approve_btn.setStyleSheet(
            "QPushButton { background-color: #2ecc71; color: white; "
            "padding: 8px 16px; font-weight: bold; } "
            "QPushButton:disabled { background-color: #555; color: #888; }"
        )
        self.approve_btn.clicked.connect(self._on_approve)
        self.reject_btn = QPushButton("")
        self.reject_btn.setStyleSheet(
            "QPushButton { background-color: #e74c3c; color: white; "
            "padding: 8px 16px; } "
            "QPushButton:disabled { background-color: #444; color: #888; }"
        )
        self.reject_btn.clicked.connect(self._on_reject)
        btn_row.addStretch()
        btn_row.addWidget(self.reject_btn)
        btn_row.addWidget(self.approve_btn)
        detail_layout.addLayout(btn_row)

        split.addWidget(detail_box)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        pending_layout.addWidget(split, stretch=1)

        self.sub_tabs.addTab(pending_page, "")

        # ── Sub-tab 2: History ───────────────────────────────────
        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(8, 8, 8, 8)

        hist_header = QHBoxLayout()
        self.hist_title_lbl = QLabel("")
        self.hist_title_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        hist_header.addWidget(self.hist_title_lbl)
        hist_header.addStretch()
        self.hist_refresh_btn = QPushButton("")
        self.hist_refresh_btn.clicked.connect(self._refresh_history)
        hist_header.addWidget(self.hist_refresh_btn)
        history_layout.addLayout(hist_header)

        hist_split = QSplitter(Qt.Horizontal)

        self.hist_list = QListWidget()
        self.hist_list.currentItemChanged.connect(self._on_hist_selection)
        hist_split.addWidget(self.hist_list)

        hist_detail_box = QWidget()
        hist_detail_layout = QVBoxLayout(hist_detail_box)
        hist_detail_layout.setContentsMargins(8, 0, 0, 0)

        self.hist_empty_lbl = QLabel("")
        self.hist_empty_lbl.setAlignment(Qt.AlignCenter)
        self.hist_empty_lbl.setStyleSheet("color: #888; padding: 40px;")
        hist_detail_layout.addWidget(self.hist_empty_lbl)

        self.hist_detail_text = QTextEdit()
        self.hist_detail_text.setReadOnly(True)
        self.hist_detail_text.setFont(mono)
        hist_detail_layout.addWidget(self.hist_detail_text, stretch=1)

        hist_split.addWidget(hist_detail_box)
        hist_split.setStretchFactor(0, 1)
        hist_split.setStretchFactor(1, 2)
        history_layout.addWidget(hist_split, stretch=1)

        self.sub_tabs.addTab(history_page, "")

        root.addWidget(self.sub_tabs)

        # Wire sub-tab change to auto-refresh history on first visit
        self.sub_tabs.currentChanged.connect(self._on_subtab_changed)

        self.retranslate()
        self.refresh()

    def retranslate(self):
        self.sub_tabs.setTabText(0, t("approval_tab_pending"))
        self.sub_tabs.setTabText(1, t("approval_tab_history"))
        self.title_lbl.setText(t("approval_list_header"))
        self.refresh_btn.setText(t("approval_refresh"))
        self.empty_lbl.setText(t("approval_empty"))
        self.approve_btn.setText(t("approval_approve"))
        self.reject_btn.setText(t("approval_reject"))
        self.hist_title_lbl.setText(t("approval_tab_history"))
        self.hist_refresh_btn.setText(t("approval_refresh"))
        self.hist_empty_lbl.setText(t("approval_history_empty"))

    # ── Pending sub-tab data ─────────────────────────────────────

    def refresh(self):
        """Re-read pending/ directory and update the list."""
        from sentinel.growth import list_pending
        try:
            self._pending_cache = list_pending()
        except Exception as e:
            log.warning("refresh pending failed: %s", e)
            self._pending_cache = []

        remember_id = self._current_id

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for p in self._pending_cache:
            label = self._format_list_label(p)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, p.id)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

        restored = False
        if remember_id is not None:
            for i in range(self.list_widget.count()):
                it = self.list_widget.item(i)
                if it.data(Qt.UserRole) == remember_id:
                    self.list_widget.setCurrentRow(i)
                    restored = True
                    break
        if not restored and self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        elif self.list_widget.count() == 0:
            self._current_id = None
            self._render_empty()

        self._update_action_state()

    @staticmethod
    def _format_list_label(approval) -> str:
        # Phase C1 introduced ACTION kind — map it to its action_type
        # for the list label so users can tell what kind of side effect
        # is being proposed without opening the detail pane.
        if approval.kind == "skill_gen":
            kind_zh = t("approval_kind_skill")
        elif approval.kind == "self_mod":
            kind_zh = t("approval_kind_selfmod")
        elif approval.kind == "action":
            kind_zh = f"動作·{approval.action_type or '?'}"
        else:
            kind_zh = approval.kind  # unknown: show raw kind rather than crash
        title = approval.title or "(no title)"
        if len(title) > 40:
            title = title[:40] + "…"
        # Combined warning count: safety scan findings (code) +
        # policy findings (action). Either surface concerns worth
        # flagging before the user clicks approve.
        warn_count = len(getattr(approval, "safety_findings", []) or []) + \
                     len(getattr(approval, "policy_findings", []) or [])
        warn_badge = f" ⚠{warn_count}" if warn_count else ""
        return f"[{approval.id}] {kind_zh}  {title}{warn_badge}"

    # ── Pending selection / rendering ────────────────────────────

    def _on_selection_changed(self, current, _previous):
        if current is None:
            self._current_id = None
            self._render_empty()
            self._update_action_state()
            return
        approval_id = current.data(Qt.UserRole)
        self._current_id = approval_id
        approval = next((p for p in self._pending_cache if p.id == approval_id), None)
        if approval is None:
            self._render_empty()
        else:
            self._render_detail(approval)
        self._update_action_state()

    def _render_empty(self):
        self.empty_lbl.setVisible(True)
        self.detail_text.setVisible(False)

    def _render_detail(self, approval):
        self.empty_lbl.setVisible(False)
        self.detail_text.setVisible(True)

        kind_label = (
            t("approval_kind_skill") if approval.kind == "skill_gen"
            else t("approval_kind_selfmod")
        )

        lines: list[str] = []
        lines.append(f"<h3 style='color:#00dcff; margin:0 0 8px 0;'>[{approval.id}] {self._html_escape(approval.title)}</h3>")
        lines.append(f"<p><b>{t('approval_kind')}:</b> {kind_label}</p>")
        lines.append(f"<p><b>{t('approval_proposer_tier')}:</b> {self._html_escape(approval.proposer_tier or '—')}</p>")
        lines.append(f"<p><b>{t('approval_target')}:</b> <code>{self._html_escape(approval.target_path)}</code></p>")
        if approval.reason:
            lines.append(f"<p><b>{t('approval_reason')}:</b> {self._html_escape(approval.reason)}</p>")

        if approval.safety_findings:
            lines.append(f"<h4 style='color:#ffa502; margin-top:12px;'>⚠ {t('approval_safety_findings')} ({len(approval.safety_findings)})</h4>")
            lines.append("<ul>")
            for f in approval.safety_findings:
                rule = f.get("rule", "?")
                msg = f.get("message", "")
                loc = f.get("location", "")
                sev = f.get("severity", "")
                lines.append(
                    f"<li><b>[{self._html_escape(sev)}] {self._html_escape(rule)}</b>"
                    f" — {self._html_escape(msg)}"
                    f" <span style='color:#888;'>({self._html_escape(loc)})</span></li>"
                )
            lines.append("</ul>")

        lines.append(f"<h4 style='margin-top:12px;'>{t('approval_source')}</h4>")
        lines.append(f"<pre style='background-color:#1a1a1a; padding:10px; "
                     f"border-left:3px solid #00dcff; color:#ddd; white-space:pre-wrap;'>"
                     f"{self._html_escape(approval.source)}</pre>")

        if approval.previous_source:
            lines.append(f"<h4 style='margin-top:12px; color:#888;'>{t('approval_previous')}</h4>")
            lines.append(f"<pre style='background-color:#1a1a1a; padding:10px; "
                         f"border-left:3px solid #666; color:#888; white-space:pre-wrap;'>"
                         f"{self._html_escape(approval.previous_source)}</pre>")

        self.detail_text.setHtml("".join(lines))

    @staticmethod
    def _html_escape(s: str) -> str:
        return (str(s or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _update_action_state(self):
        has_selection = self._current_id is not None and self.list_widget.count() > 0
        self.approve_btn.setEnabled(has_selection)
        self.reject_btn.setEnabled(has_selection)

    # ── Pending actions ──────────────────────────────────────────

    def _on_approve(self):
        if self._current_id is None:
            return
        ans = QMessageBox.question(
            self, t("approval_approve"), t("approval_confirm_approve"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        from sentinel.growth import approve
        ok = approve(self._current_id, approver="user_via_gui")
        if not ok:
            QMessageBox.warning(self, t("approval_approve"),
                                f"Approve failed for {self._current_id}. See log.")
            return
        self._current_id = None
        self.refresh()
        self.proposals_changed.emit()

    def _on_reject(self):
        if self._current_id is None:
            return
        ans = QMessageBox.question(
            self, t("approval_reject"), t("approval_confirm_reject"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        reason, _ok = QInputDialog.getText(
            self, t("approval_reject"), t("approval_reject_reason"),
        )
        from sentinel.growth import reject as reject_proposal
        ok = reject_proposal(self._current_id, reason=reason or "", approver="user_via_gui")
        if not ok:
            QMessageBox.warning(self, t("approval_reject"),
                                f"Reject failed for {self._current_id}. See log.")
            return
        self._current_id = None
        self.refresh()
        self.proposals_changed.emit()

    # ── History sub-tab ──────────────────────────────────────────

    def _on_subtab_changed(self, index):
        if index == 1:
            self._refresh_history()

    def _refresh_history(self):
        """Load all proposals (pending + approved + rejected)."""
        from sentinel.growth.approval import list_history
        try:
            self._history_cache = list_history()
        except Exception as e:
            log.warning("refresh history failed: %s", e)
            self._history_cache = []

        self.hist_list.blockSignals(True)
        self.hist_list.clear()
        for entry in self._history_cache:
            label = self._format_hist_label(entry)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry.get("id"))
            self.hist_list.addItem(item)
        self.hist_list.blockSignals(False)

        if self.hist_list.count() > 0:
            self.hist_list.setCurrentRow(0)
        else:
            self._history_selected = None
            self.hist_empty_lbl.setVisible(True)
            self.hist_detail_text.setVisible(False)

    @staticmethod
    def _format_hist_label(entry: dict) -> str:
        import datetime
        status = entry.get("_status", "?")
        status_icon = {"approved": "✅", "rejected": "❌", "pending": "🟡"}.get(status, "?")
        kind = entry.get("kind", "?")
        kind_zh = t("approval_kind_skill") if kind == "skill_gen" else t("approval_kind_selfmod")
        title = entry.get("title", "(no title)")
        if len(title) > 35:
            title = title[:35] + "…"
        ts = entry.get("created_at", 0)
        date_str = datetime.datetime.fromtimestamp(ts).strftime("%m/%d %H:%M") if ts else ""
        return f"{status_icon} [{entry.get('id', '?')}] {kind_zh}  {title}  {date_str}"

    def _on_hist_selection(self, current, _previous):
        if current is None:
            self._history_selected = None
            self.hist_empty_lbl.setVisible(True)
            self.hist_detail_text.setVisible(False)
            return
        sel_id = current.data(Qt.UserRole)
        entry = next((e for e in self._history_cache if e.get("id") == sel_id), None)
        if entry is None:
            self.hist_empty_lbl.setVisible(True)
            self.hist_detail_text.setVisible(False)
            return
        self._history_selected = entry
        self._render_hist_detail(entry)

    def _render_hist_detail(self, entry: dict):
        import datetime
        self.hist_empty_lbl.setVisible(False)
        self.hist_detail_text.setVisible(True)

        status = entry.get("_status", "?")
        status_label = t(f"approval_status_{status}") if status in ("approved", "rejected", "pending") else status
        kind = entry.get("kind", "?")
        kind_label = t("approval_kind_skill") if kind == "skill_gen" else t("approval_kind_selfmod")

        lines: list[str] = []
        # Title + status badge
        title = self._html_escape(entry.get("title", "(no title)"))
        lines.append(f"<h3 style='color:#00dcff; margin:0 0 8px 0;'>[{self._html_escape(entry.get('id', ''))}] {title}</h3>")
        lines.append(f"<p style='font-size:14px; margin-bottom:6px;'>{status_label}</p>")

        # Meta
        lines.append(f"<p><b>{t('approval_kind')}:</b> {kind_label}</p>")
        tier = entry.get("proposer_tier", "")
        if tier:
            lines.append(f"<p><b>{t('approval_proposer_tier')}:</b> {self._html_escape(tier)}</p>")
        ts = entry.get("created_at", 0)
        if ts:
            dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"<p><b>提交時間:</b> {dt}</p>")
        target = entry.get("target_path", "")
        if target:
            lines.append(f"<p><b>{t('approval_target')}:</b> <code>{self._html_escape(target)}</code></p>")
        reason = entry.get("reason", "")
        if reason:
            lines.append(f"<p><b>{t('approval_reason')}:</b> {self._html_escape(reason)}</p>")

        # Rejection info
        rejection = entry.get("_rejection")
        if rejection:
            rej_reason = rejection.get("reason", "")
            rej_at = rejection.get("at", 0)
            rej_dt = datetime.datetime.fromtimestamp(rej_at).strftime("%Y-%m-%d %H:%M:%S") if rej_at else ""
            lines.append(f"<p style='color:#e74c3c;'><b>{t('approval_rejected_reason')}:</b> "
                         f"{self._html_escape(rej_reason) or '(未提供)'} — {rej_dt}</p>")

        # Safety findings
        findings = entry.get("safety_findings", [])
        if findings:
            lines.append(f"<h4 style='color:#ffa502; margin-top:12px;'>⚠ {t('approval_safety_findings')} ({len(findings)})</h4>")
            lines.append("<ul>")
            for f in findings:
                rule = f.get("rule", "?")
                msg = f.get("message", "")
                sev = f.get("severity", "")
                lines.append(f"<li><b>[{self._html_escape(sev)}] {self._html_escape(rule)}</b> — {self._html_escape(msg)}</li>")
            lines.append("</ul>")

        # Source code
        source = entry.get("source", "")
        if source:
            lines.append(f"<h4 style='margin-top:12px;'>{t('approval_source')}</h4>")
            lines.append(f"<pre style='background-color:#1a1a1a; padding:10px; "
                         f"border-left:3px solid #00dcff; color:#ddd; white-space:pre-wrap;'>"
                         f"{self._html_escape(source)}</pre>")

        # Previous source (self_mod)
        prev = entry.get("previous_source", "")
        if prev:
            lines.append(f"<h4 style='margin-top:12px; color:#888;'>{t('approval_previous')}</h4>")
            lines.append(f"<pre style='background-color:#1a1a1a; padding:10px; "
                         f"border-left:3px solid #666; color:#888; white-space:pre-wrap;'>"
                         f"{self._html_escape(prev)}</pre>")

        self.hist_detail_text.setHtml("".join(lines))

    # ── Introspection for tab badge ──────────────────────────────

    def pending_count(self) -> int:
        return len(self._pending_cache)


# ─── Setup Wizard (新手引導) ──────────────────────────────────────────────

class SetupWizard(QWidget):
    """首次啟動嚮導 — 引導使用者完成基本設定。"""

    finished = Signal(dict)  # Emits settings dict on completion

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Slime Agent — 新手引導")
        self.setFixedSize(520, 480)
        self.setWindowIcon(create_icon())

        self._settings = {
            "mode": "byok",
            "gemini_api_key": "",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
        }

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Pages (stacked) ──
        from PySide6.QtWidgets import QStackedWidget, QRadioButton, QButtonGroup
        self.stack = QStackedWidget()
        root.addWidget(self.stack, stretch=1)

        self._build_page_welcome()
        self._build_page_mode(QRadioButton, QButtonGroup)
        self._build_page_api()
        self._build_page_telegram()
        self._build_page_done()

        # ── Bottom nav ──
        nav = QHBoxLayout()
        nav.setContentsMargins(16, 8, 16, 12)
        self.back_btn = QPushButton(t("wizard_back"))
        self.back_btn.clicked.connect(self._go_back)
        self.back_btn.setVisible(False)
        nav.addWidget(self.back_btn)

        nav.addStretch()

        # 頁碼指示
        self.page_label = QLabel("1 / 5")
        self.page_label.setStyleSheet("color: #666; font-size: 11px;")
        nav.addWidget(self.page_label)

        nav.addStretch()

        self.next_btn = QPushButton(t("wizard_next"))
        self.next_btn.clicked.connect(self._go_next)
        self.next_btn.setStyleSheet(
            "QPushButton { background-color: #00dcff; color: #000; font-weight: bold; "
            "padding: 8px 24px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #00c4e0; }"
        )
        nav.addWidget(self.next_btn)
        root.addLayout(nav)

        self.stack.setCurrentIndex(0)
        self._update_nav()

    # ── Page Builders ──

    def _make_page(self) -> tuple:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 16)
        layout.setSpacing(12)
        self.stack.addWidget(page)
        return page, layout

    def _build_page_welcome(self):
        _, lay = self._make_page()

        # Language selector at the very top
        lang_row = QHBoxLayout()
        lang_row.addStretch()
        self._lang_btn_zh = QPushButton("中文")
        self._lang_btn_en = QPushButton("English")
        for btn, lang in [(self._lang_btn_zh, "zh"), (self._lang_btn_en, "en")]:
            active = get_language() == lang
            btn.setFixedSize(80, 32)
            btn.setStyleSheet(
                f"QPushButton {{ background: {'#00dcff' if active else '#333'}; "
                f"color: {'#000' if active else '#aaa'}; border: none; "
                f"border-radius: 4px; font-weight: {'bold' if active else 'normal'}; }}"
                f"QPushButton:hover {{ background: {'#00c4e0' if active else '#444'}; }}"
            )
            btn.clicked.connect(lambda _, l=lang: self._switch_language(l))
            lang_row.addWidget(btn)
        lang_row.addStretch()
        lay.addLayout(lang_row)
        lay.addSpacing(8)

        # Slime ASCII art
        art = QLabel(
            "<pre style='color:#00dcff; font-size: 28px; text-align:center;'>"
            "    ／⌒⌒＼\n"
            "   （ ◕ ‿‿ ◕ ）\n"
            "    ＼＿＿＿／\n"
            "</pre>"
        )
        art.setAlignment(Qt.AlignCenter)
        lay.addWidget(art)

        title = QLabel(f"<h2 style='color:#00dcff;'>{t('wizard_welcome')}</h2>")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        desc = QLabel(t("wizard_welcome_desc"))
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("color: #ccc; font-size: 13px; line-height: 1.6;")
        desc.setWordWrap(True)
        lay.addWidget(desc)
        lay.addStretch()

    def _build_page_mode(self, QRadioButton, QButtonGroup):
        _, lay = self._make_page()

        title = QLabel(f"<h3 style='color:#00dcff;'>{t('wizard_mode_title')}</h3>")
        lay.addWidget(title)

        desc = QLabel(t("wizard_mode_desc"))
        desc.setStyleSheet("color: #999; font-size: 12px;")
        lay.addWidget(desc)

        lay.addSpacing(8)

        self.mode_group = QButtonGroup(self)

        # BYOK option
        byok_frame = QFrame()
        byok_frame.setStyleSheet(
            "QFrame { border: 2px solid #00dcff; border-radius: 8px; padding: 12px; }"
        )
        bf_lay = QVBoxLayout(byok_frame)
        self.radio_byok = QRadioButton(t("wizard_byok_title"))
        self.radio_byok.setStyleSheet("color: #00dcff; font-size: 14px; font-weight: bold;")
        self.radio_byok.setChecked(True)
        bf_lay.addWidget(self.radio_byok)
        byok_desc = QLabel(t("wizard_byok_desc"))
        byok_desc.setStyleSheet("color: #aaa; font-size: 12px; margin-left: 20px;")
        byok_desc.setWordWrap(True)
        bf_lay.addWidget(byok_desc)
        self.mode_group.addButton(self.radio_byok, 0)
        lay.addWidget(byok_frame)

        lay.addSpacing(8)

        # Quota option
        quota_frame = QFrame()
        quota_frame.setStyleSheet(
            "QFrame { border: 2px solid #555; border-radius: 8px; padding: 12px; }"
        )
        qf_lay = QVBoxLayout(quota_frame)
        self.radio_quota = QRadioButton(t("wizard_quota_title"))
        self.radio_quota.setStyleSheet("color: #ffa502; font-size: 14px; font-weight: bold;")
        qf_lay.addWidget(self.radio_quota)
        quota_desc = QLabel(t("wizard_quota_desc"))
        quota_desc.setStyleSheet("color: #aaa; font-size: 12px; margin-left: 20px;")
        quota_desc.setWordWrap(True)
        qf_lay.addWidget(quota_desc)
        quota_note = QLabel("（中繼伺服器尚在建設中，目前請先使用 BYOK 模式）")
        quota_note.setStyleSheet("color: #e74c3c; font-size: 11px; margin-left: 20px;")
        qf_lay.addWidget(quota_note)
        self.mode_group.addButton(self.radio_quota, 1)
        lay.addWidget(quota_frame)

        lay.addStretch()

    def _build_page_api(self):
        _, lay = self._make_page()

        title = QLabel(f"<h3 style='color:#00dcff;'>{t('wizard_api_title')}</h3>")
        lay.addWidget(title)

        desc = QLabel(t("wizard_api_desc"))
        desc.setStyleSheet("color: #aaa; font-size: 12px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # Link to get key
        link = QPushButton(t("wizard_api_get_key"))
        link.setStyleSheet(
            "QPushButton { color: #00dcff; text-decoration: underline; "
            "background: transparent; border: none; font-size: 12px; text-align: left; }"
            "QPushButton:hover { color: #fff; }"
        )
        link.setCursor(Qt.PointingHandCursor)
        link.clicked.connect(lambda: __import__("webbrowser").open(
            "https://aistudio.google.com/apikey"
        ))
        lay.addWidget(link)

        lay.addSpacing(12)

        lay.addWidget(QLabel("<b style='color:#ccc;'>Gemini API Key:</b>"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("AIzaSy...")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setStyleSheet("padding: 8px; font-size: 13px;")
        lay.addWidget(self.api_key_input)

        # Toggle show/hide
        self.show_key_btn = QPushButton("👁 顯示")
        self.show_key_btn.setFixedWidth(80)
        self.show_key_btn.clicked.connect(self._toggle_key_visibility)
        lay.addWidget(self.show_key_btn)

        self.api_status = QLabel("")
        self.api_status.setStyleSheet("font-size: 12px;")
        lay.addWidget(self.api_status)

        lay.addStretch()

        # Note
        note = QLabel("💡 Gemini API 有免費額度，背景觀察完全夠用。")
        note.setStyleSheet("color: #2ed573; font-size: 11px;")
        note.setWordWrap(True)
        lay.addWidget(note)

    def _build_page_telegram(self):
        _, lay = self._make_page()

        title = QLabel(f"<h3 style='color:#00dcff;'>{t('wizard_telegram_title')}</h3>")
        lay.addWidget(title)

        desc = QLabel(t("wizard_telegram_desc"))
        desc.setStyleSheet("color: #aaa; font-size: 12px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        lay.addSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)

        self.tg_token_input = QLineEdit()
        self.tg_token_input.setPlaceholderText("123456:ABCdef...")
        form.addRow("Bot Token:", self.tg_token_input)

        self.tg_chatid_input = QLineEdit()
        self.tg_chatid_input.setPlaceholderText("你的 Chat ID（數字）")
        form.addRow("Chat ID:", self.tg_chatid_input)

        lay.addLayout(form)

        lay.addSpacing(8)

        how_to = QLabel(
            "<span style='color:#888; font-size:11px;'>"
            "1. 在 Telegram 找 @BotFather 建立機器人，取得 Token<br>"
            "2. 對你的機器人發一則訊息<br>"
            "3. 使用 get_chat_id.py 或搜尋 @userinfobot 取得 Chat ID"
            "</span>"
        )
        how_to.setWordWrap(True)
        lay.addWidget(how_to)

        lay.addStretch()

    def _build_page_done(self):
        _, lay = self._make_page()

        art = QLabel(
            "<pre style='color:#2ed573; font-size: 28px; text-align:center;'>"
            "    ／⌒⌒＼\n"
            "   （ ★ ‿‿ ★ ）\n"
            "    ＼＿＿＿／✨\n"
            "</pre>"
        )
        art.setAlignment(Qt.AlignCenter)
        lay.addWidget(art)

        title = QLabel(f"<h2 style='color:#2ed573;'>{t('wizard_done_title')}</h2>")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        desc = QLabel(t("wizard_done_desc"))
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("color: #ccc; font-size: 13px; line-height: 1.6;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        lay.addStretch()

    # ── Navigation ──

    def _toggle_key_visibility(self):
        if self.api_key_input.echoMode() == QLineEdit.Password:
            self.api_key_input.setEchoMode(QLineEdit.Normal)
            self.show_key_btn.setText("🔒 隱藏")
        else:
            self.api_key_input.setEchoMode(QLineEdit.Password)
            self.show_key_btn.setText("👁 顯示")

    def _switch_language(self, lang: str):
        """Switch wizard language and rebuild all pages."""
        set_language(lang)
        # Remove all pages from stack
        while self.stack.count() > 0:
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        # Rebuild
        from PySide6.QtWidgets import QRadioButton, QButtonGroup
        self._build_page_welcome()
        self._build_page_mode(QRadioButton, QButtonGroup)
        self._build_page_api()
        self._build_page_telegram()
        self._build_page_done()
        self.stack.setCurrentIndex(0)
        self._update_nav()
        # Update nav button text
        self.back_btn.setText(t("wizard_back"))
        self.next_btn.setText(t("wizard_next"))
        self.setWindowTitle(
            "AI Slime Agent — 新手引導" if lang == "zh"
            else "AI Slime Agent — Setup"
        )

    def _update_nav(self):
        idx = self.stack.currentIndex()
        total = self.stack.count()
        self.back_btn.setVisible(idx > 0)
        self.page_label.setText(f"{idx + 1} / {total}")

        if idx == total - 1:
            self.next_btn.setText(t("wizard_finish"))
        elif idx == 3:  # Telegram page
            self.next_btn.setText(t("wizard_skip") if not self.tg_token_input.text() else t("wizard_next"))
        else:
            self.next_btn.setText(t("wizard_next"))

    def _go_back(self):
        idx = self.stack.currentIndex()
        if idx > 0:
            # If quota mode, skip API page going back
            if idx == 3 and self.radio_quota.isChecked():
                self.stack.setCurrentIndex(1)
            else:
                self.stack.setCurrentIndex(idx - 1)
            self._update_nav()

    def _go_next(self):
        idx = self.stack.currentIndex()
        total = self.stack.count()

        # Collect data from current page
        if idx == 1:  # Mode page
            self._settings["mode"] = "quota" if self.radio_quota.isChecked() else "byok"
            # If quota, skip API key page
            if self.radio_quota.isChecked():
                self.stack.setCurrentIndex(3)
                self._update_nav()
                return

        if idx == 2:  # API page
            key = self.api_key_input.text().strip()
            self._settings["gemini_api_key"] = key
            if not key:
                self.api_status.setText("<span style='color:#e74c3c;'>⚠ 未填金鑰。沒有金鑰 AI Slime 將無法思考（但仍能觀察）。</span>")
                self.api_status.setVisible(True)
                # Still allow continuing
            else:
                self.api_status.setText("<span style='color:#2ed573;'>✓ 已填入金鑰</span>")

        if idx == 3:  # Telegram page
            self._settings["telegram_bot_token"] = self.tg_token_input.text().strip()
            self._settings["telegram_chat_id"] = self.tg_chatid_input.text().strip()

        if idx >= total - 1:
            # Finish!
            self._save_and_finish()
            return

        self.stack.setCurrentIndex(idx + 1)
        self._update_nav()

    def _save_and_finish(self):
        """Save wizard settings and emit finished signal."""
        settings_file = Path.home() / ".hermes" / "sentinel_settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)

        # Build initial settings
        s = {
            "language": "zh",
            "theme": "dark",
            "chat_model_pref": "cloud_first",
            "analysis_model_pref": "local_first",
            "relay_server_url": "",
            "telegram_bot_token": self._settings["telegram_bot_token"],
            "telegram_chat_id": self._settings["telegram_chat_id"],
            "check_interval": 30,
            "idle_report_interval": 1800,
            "watch_dirs": ["D:/srbow_bots"],
            "wizard_completed": True,
        }

        # Apply Gemini key to providers
        providers = []
        for p in config.LLM_PROVIDERS:
            p_copy = dict(p)
            if p_copy["name"] == "Gemini" and self._settings["gemini_api_key"]:
                p_copy["api_key"] = self._settings["gemini_api_key"]
                p_copy["enabled"] = True
            providers.append(p_copy)
        s["llm_providers"] = providers

        settings_file.write_text(
            json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        self.finished.emit(s)
        self.close()


# ─── Routines Tab (常規) ─────────────────────────────────────────────
#
# Phases F-K built the routine system (auto-detection, judge gates,
# reactive triggers, dependencies, reflection, learning-from-rejection)
# but with no GUI — routines lived only as ~/.hermes/routines/*.json
# files. This tab makes the system visible:
#
#   - Lists every routine with its current status, trigger, steps,
#     and audit (last fire result, hit rate, judge skip rate)
#   - Per-routine actions: fire-now / disable / delete (last two go
#     through the approval queue like any other side-effect change)
#   - "Run detector now" button to manually trigger pattern
#     detection (otherwise it runs once per 24h)
#   - Top-of-tab summary (total routines / fires / suggestions from
#     reflection pass)


class RoutinesTab(QWidget):
    """The 「常規」 tab — visible management of the auto-running
    routines the slime has accumulated."""

    def __init__(self, bridge: SignalBridge):
        super().__init__()
        self.bridge = bridge
        from sentinel.ui import tokens as _tk

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _tk.SPACE["lg"], _tk.SPACE["lg"],
            _tk.SPACE["lg"], _tk.SPACE["md"],
        )
        layout.setSpacing(_tk.SPACE["md"])

        # Header — title + "this is what your slime is doing on its own".
        title = QLabel(
            f'<span style="{_tk.text_title()}">📋 常規</span>'
            f'  <span style="{_tk.text_meta()}">'
            f'史萊姆替你自動執行的事</span>'
        )
        title.setStyleSheet(f"font-size:{_tk.FONT_SIZE['title']}px;")
        layout.addWidget(title)

        # Summary line — fills in on refresh.
        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet(_tk.text_body())
        self.summary_lbl.setWordWrap(True)
        layout.addWidget(self.summary_lbl)

        # Scrollable card list.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(_tk.SPACE["md"])
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_container)
        layout.addWidget(self.scroll, stretch=1)

        # Action row at the bottom.
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, _tk.SPACE["sm"], 0, 0)

        self.detect_btn = QPushButton("🔍 立即偵測新常規")
        self.detect_btn.setCursor(Qt.PointingHandCursor)
        self.detect_btn.setStyleSheet(_tk.btn_secondary())
        self.detect_btn.clicked.connect(self._run_detector_now)
        btn_row.addWidget(self.detect_btn)

        btn_row.addStretch()

        self.refresh_btn = QPushButton("🔄 重新整理")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setStyleSheet(_tk.btn_primary())
        self.refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self.refresh_btn)

        layout.addLayout(btn_row)

        self.refresh()

    # ── Data refresh ──────────────────────────────────────────────

    def refresh(self):
        """Re-render the summary + card list from disk state."""
        from sentinel.routines import list_routines, reflect

        # Clear cards
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        try:
            routines = list_routines()
        except Exception as e:
            log.warning(f"routines tab: list_routines failed: {e}")
            routines = []

        # Summary line — "you have 3 active, 1 disabled, 12 fires total"
        try:
            report = reflect()
        except Exception:
            report = None

        if not routines:
            self.summary_lbl.setText(
                "你還沒有任何常規。可以等史萊姆觀察一陣子,"
                "或在聊天裡叫他「以後每天 9 點幫我 X」。"
            )
            empty = QLabel(
                "（建議至少用兩三天累積觀察,讓偵測器看出你的固定行為。"
                "或按下方「立即偵測新常規」現在就跑一次。）"
            )
            empty.setStyleSheet(_tk.text_meta())
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            self.list_layout.insertWidget(self.list_layout.count() - 1, empty)
            return

        active = [r for r in routines if r.enabled]
        paused = [r for r in routines if not r.enabled]
        total_fires = sum(r.fire_count for r in routines)
        suggestion_n = len(report.suggestions) if report else 0
        sugg_str = (
            f" · 反思建議 {suggestion_n} 條" if suggestion_n else ""
        )
        self.summary_lbl.setText(
            f"啟用 {len(active)} 個 · 停用 {len(paused)} 個 · "
            f"累計觸發 {total_fires} 次{sugg_str}"
        )

        # Reflection suggestions panel (Phase J surfaced inline).
        # Used to live only behind 待同意 + the format_summary chat
        # output. Now it sits at the top of the routines tab so the
        # user sees what the slime has noticed about its own
        # performance without a tab switch.
        if report and report.suggestions:
            for sug in report.suggestions[:5]:
                card = self._build_suggestion_card(sug)
                self.list_layout.insertWidget(
                    self.list_layout.count() - 1, card
                )

        # Render routine cards — active first, then paused.
        for r in active:
            card = self._build_card(r, faded=False, report=report)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
        if paused:
            divider = QLabel("已停用")
            divider.setStyleSheet(_tk.text_meta())
            divider.setContentsMargins(0, 8, 0, 0)
            self.list_layout.insertWidget(self.list_layout.count() - 1, divider)
            for r in paused:
                card = self._build_card(r, faded=True, report=report)
                self.list_layout.insertWidget(self.list_layout.count() - 1, card)

    # ── Card builder ──────────────────────────────────────────────

    def _build_card(self, routine, *, faded: bool, report) -> QWidget:
        """One routine = one card. faded=True for disabled routines."""
        from sentinel.ui import tokens as _tk
        from sentinel.routines.handlers import _render_trigger_zh

        accent = _tk.PALETTE["text_muted"] if faded else _tk.PALETTE["amber"]
        card = QFrame()
        card.setStyleSheet(_tk.card_with_accent(accent))
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 12, 12)
        v.setSpacing(_tk.SPACE["xs"])

        # Title row: name + (id) on left, status pill on right.
        top = QHBoxLayout()
        title_lbl = QLabel(
            f"<b style='color:{_tk.PALETTE['text']};'>{routine.name}</b>"
            f" <span style='{_tk.text_meta()}'>{routine.id}</span>"
        )
        title_lbl.setStyleSheet(f"font-size:{_tk.FONT_SIZE['body']}px;")
        top.addWidget(title_lbl, stretch=1)

        status_text = "停用" if not routine.enabled else "啟用中"
        status_color = (
            _tk.PALETTE["text_muted"] if not routine.enabled
            else _tk.PALETTE["ok"]
        )
        status_lbl = QLabel(
            f"<span style='color:{status_color}; "
            f"font-size:{_tk.FONT_SIZE['meta']}px;'>● {status_text}</span>"
        )
        top.addWidget(status_lbl)
        v.addLayout(top)

        # Trigger description
        trig_lbl = QLabel(
            f"<span style='{_tk.text_meta()}'>觸發：</span>"
            f"<span style='{_tk.text_body()}'>"
            f"{_render_trigger_zh(routine.trigger)}</span>"
        )
        trig_lbl.setStyleSheet(f"font-size:{_tk.FONT_SIZE['meta']}px;")
        trig_lbl.setWordWrap(True)
        v.addWidget(trig_lbl)

        # Steps
        steps_text = " → ".join(
            (s.get("title") or s.get("action_type", "?"))
            for s in (routine.steps or []) if isinstance(s, dict)
        )
        if steps_text:
            steps_lbl = QLabel(
                f"<span style='{_tk.text_meta()}'>步驟：</span>"
                f"<span style='{_tk.text_body()}'>{steps_text}</span>"
            )
            steps_lbl.setStyleSheet(f"font-size:{_tk.FONT_SIZE['meta']}px;")
            steps_lbl.setWordWrap(True)
            v.addWidget(steps_lbl)

        # Judge prompt (Phase H) — only show if set.
        if (routine.judge_prompt or "").strip():
            jp_lbl = QLabel(
                f"<span style='{_tk.text_meta()}'>判斷規則：</span>"
                f"<span style='color:{_tk.PALETTE['cyan']};"
                f" font-size:{_tk.FONT_SIZE['meta']}px;'>"
                f"{routine.judge_prompt[:120]}</span>"
            )
            jp_lbl.setWordWrap(True)
            v.addWidget(jp_lbl)

        # Dependencies (Phase K)
        if routine.depends_on:
            dep_lbl = QLabel(
                f"<span style='{_tk.text_meta()}'>依賴：</span>"
                f"<span style='{_tk.text_body()}'>"
                f"{', '.join(routine.depends_on)} "
                f"({routine.depends_on_window_minutes} 分鐘窗口)</span>"
            )
            dep_lbl.setStyleSheet(f"font-size:{_tk.FONT_SIZE['meta']}px;")
            v.addWidget(dep_lbl)

        # Stats / last-fire summary from reflection report
        stats = None
        if report is not None:
            for s in report.routine_stats:
                if s.routine_id == routine.id:
                    stats = s
                    break
        if stats and stats.total_fires > 0:
            from datetime import datetime
            last_str = "—"
            if routine.last_fired_at:
                dt = datetime.fromtimestamp(routine.last_fired_at)
                last_str = dt.strftime("%m/%d %H:%M")
            stats_text = (
                f"觸發 {stats.total_fires} 次  ·  "
                f"成功 {stats.success_count}  ·  "
                f"判斷略過 {stats.skipped_by_judge_count}  ·  "
                f"失敗 {stats.fail_count}  ·  "
                f"上次 {last_str}"
            )
            stats_lbl = QLabel(stats_text)
            stats_lbl.setStyleSheet(_tk.text_meta())
            stats_lbl.setWordWrap(True)
            v.addWidget(stats_lbl)
        elif routine.fire_count == 0:
            never_lbl = QLabel("尚未觸發過")
            never_lbl.setStyleSheet(_tk.text_meta())
            v.addWidget(never_lbl)

        # Action buttons row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 6, 0, 0)
        btn_row.setSpacing(_tk.SPACE["xs"])

        if routine.enabled:
            fire_btn = QPushButton("立即執行")
            fire_btn.setCursor(Qt.PointingHandCursor)
            fire_btn.setStyleSheet(_tk.btn_ghost())
            fire_btn.clicked.connect(
                lambda _checked, rid=routine.id: self._fire_now(rid)
            )
            btn_row.addWidget(fire_btn)

        toggle_label = "啟用" if not routine.enabled else "停用"
        toggle_btn = QPushButton(toggle_label)
        toggle_btn.setCursor(Qt.PointingHandCursor)
        toggle_btn.setStyleSheet(_tk.btn_ghost())
        toggle_btn.clicked.connect(
            lambda _checked, rid=routine.id, en=routine.enabled:
                self._toggle_routine(rid, currently_enabled=en)
        )
        btn_row.addWidget(toggle_btn)

        delete_btn = QPushButton("刪除")
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.setStyleSheet(
            f"QPushButton {{"
            f" background:transparent;"
            f" color:{_tk.PALETTE['danger']};"
            f" padding:5px 12px;"
            f" border:1px solid {_tk.PALETTE['border']};"
            f" border-radius:{_tk.RADIUS['pill']}px;"
            f" font-size:{_tk.FONT_SIZE['meta']}px; }}"
            f"QPushButton:hover {{"
            f" border-color:{_tk.PALETTE['danger']}; }}"
        )
        delete_btn.clicked.connect(
            lambda _checked, rid=routine.id: self._delete_routine(rid)
        )
        btn_row.addWidget(delete_btn)

        btn_row.addStretch()
        v.addLayout(btn_row)
        return card

    # ── Suggestion card builder (Phase J surfaced inline) ─────────

    def _build_suggestion_card(self, sug: dict) -> QWidget:
        """Render one reflection suggestion as a card.

        Suggestion kinds (set in routines/reflection.py):
          disable_stale     — routine hasn't fired in 30+ days,
                              recommend stopping it. Action: queue
                              routine.disable approval.
          review_skip_rate  — judge keeps declining. Advisory only;
                              fix is contextual (loosen judge or
                              narrow trigger), not a one-button fix.
          review_fail_rate  — steps keep erroring. Advisory; user
                              should investigate path / handler /
                              network. Action: queue disable so the
                              routine doesn't keep failing.
          detector_noisy    — system-level: too many proposals
                              rejected. Advisory; encourages user
                              to rethink scope.
        """
        from sentinel.ui import tokens as _tk

        kind = sug.get("kind", "")
        kind_meta = {
            "disable_stale":    ("💤", _tk.PALETTE["text_muted"]),
            "review_skip_rate": ("⚙",  _tk.PALETTE["amber"]),
            "review_fail_rate": ("⚠",  _tk.PALETTE["danger"]),
            "detector_noisy":   ("📊", _tk.PALETTE["cyan"]),
        }
        icon, accent = kind_meta.get(kind, ("💡", _tk.PALETTE["cyan"]))

        card = QFrame()
        card.setStyleSheet(_tk.card_with_accent(accent))
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 8, 12, 10)
        v.setSpacing(_tk.SPACE["xs"])

        title_lbl = QLabel(
            f"<span style='font-size:13px;'>{icon}</span>"
            f"  <span style='color:{_tk.PALETTE['text']};"
            f" font-weight:600;'>{sug.get('title', '(untitled)')}</span>"
            f"  <span style='{_tk.text_meta()}'>"
            f"  · 來自史萊姆每週反思</span>"
        )
        title_lbl.setStyleSheet(f"font-size:{_tk.FONT_SIZE['body']}px;")
        title_lbl.setWordWrap(True)
        v.addWidget(title_lbl)

        detail = sug.get("detail", "")
        if detail:
            detail_lbl = QLabel(detail)
            detail_lbl.setStyleSheet(_tk.text_meta())
            detail_lbl.setWordWrap(True)
            v.addWidget(detail_lbl)

        # Actions row — depends on suggestion kind.
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.setSpacing(_tk.SPACE["xs"])

        rid = sug.get("routine_id")

        if kind == "disable_stale" and rid:
            disable_btn = QPushButton("好,停用它")
            disable_btn.setCursor(Qt.PointingHandCursor)
            disable_btn.setStyleSheet(_tk.btn_secondary())
            disable_btn.clicked.connect(
                lambda _checked, r=rid: self._suggest_disable(r)
            )
            btn_row.addWidget(disable_btn)

            keep_btn = QPushButton("先留著")
            keep_btn.setCursor(Qt.PointingHandCursor)
            keep_btn.setStyleSheet(_tk.btn_ghost())
            keep_btn.clicked.connect(self._dismiss_suggestion)
            btn_row.addWidget(keep_btn)

        elif kind == "review_fail_rate" and rid:
            disable_btn = QPushButton("停用直到修好")
            disable_btn.setCursor(Qt.PointingHandCursor)
            disable_btn.setStyleSheet(_tk.btn_secondary())
            disable_btn.clicked.connect(
                lambda _checked, r=rid: self._suggest_disable(r)
            )
            btn_row.addWidget(disable_btn)

            keep_btn = QPushButton("我自己看")
            keep_btn.setCursor(Qt.PointingHandCursor)
            keep_btn.setStyleSheet(_tk.btn_ghost())
            keep_btn.clicked.connect(self._dismiss_suggestion)
            btn_row.addWidget(keep_btn)

        else:
            # review_skip_rate / detector_noisy: advisory only.
            ack_btn = QPushButton("知道了")
            ack_btn.setCursor(Qt.PointingHandCursor)
            ack_btn.setStyleSheet(_tk.btn_ghost())
            ack_btn.clicked.connect(self._dismiss_suggestion)
            btn_row.addWidget(ack_btn)

        btn_row.addStretch()
        v.addLayout(btn_row)
        return card

    def _suggest_disable(self, routine_id: str) -> None:
        """Queue a routine.disable approval for a reflection-suggested
        routine. Same path as the regular Disable button on the
        routine card — keeps the audit trail + Phase I learning
        signal consistent."""
        from sentinel.growth import submit_action, PolicyDenied
        try:
            submit_action(
                action_type="routine.disable",
                title=f"反思建議停用 {routine_id}",
                reason="由史萊姆每週反思建議停用",
                payload={"id": routine_id, "reason": "reflection-suggested"},
            )
            QMessageBox.information(
                self, "AI Slime",
                "已建立停用提案,到「⏳ 待同意」分頁批准後生效。",
            )
            self.refresh()
        except PolicyDenied as e:
            QMessageBox.warning(
                self, "AI Slime",
                "; ".join(f.get("msg", "") for f in e.findings),
            )
        except Exception as e:
            QMessageBox.warning(self, "AI Slime", f"提案失敗：{e}")

    def _dismiss_suggestion(self) -> None:
        """User dismissed an advisory suggestion ("先留著" / "知道了" /
        "我自己看"). Currently just refreshes — full "remember I
        dismissed this" tracking would need a separate dismissed-
        suggestions log. For first cut, dismissal is implicit: the
        next reflection pass (weekly) will re-evaluate from scratch,
        and if the issue is gone the suggestion won't re-appear; if
        still there, the user sees it again — fair behavior."""
        # No-op refresh. Keeps the click feedback responsive.
        pass

    # ── Action handlers ───────────────────────────────────────────

    def _fire_now(self, routine_id: str) -> None:
        """Run a routine immediately, off the UI thread (steps may
        block — Win32 / subprocess / network).

        UX:
          - immediate status-bar echo so user knows the click was
            received (LLM judge can take 5-15 s; a totally silent
            wait reads as "the button is broken")
          - terminal popup with success / skipped / failed summary,
            forced ApplicationModal + WindowStaysOnTopHint so it
            actually surfaces — earlier the popup was getting hidden
            behind the main window and the user couldn't tell
            anything had happened
        """
        from sentinel.routines import get_routine, fire_routine

        # ⚡ Immediate feedback — fires synchronously on the click
        # thread so the user sees something the moment they click,
        # before the LLM judge even starts.
        try:
            self.bridge.status_update.emit(f"⏳ 正在執行常規 {routine_id}…")
        except Exception:
            pass

        def _do():
            r = get_routine(routine_id)
            if r is None:
                # Silent return was the bug — user clicks, nothing
                # happens, no idea why. Now we always surface a popup.
                def _missing():
                    self.bridge.status_update.emit(
                        f"✗ 找不到常規 {routine_id}"
                    )
                    self._show_fire_popup(
                        f"✗ 找不到常規 {routine_id}\n\n"
                        f"可能是剛被刪除，或是 ~/.hermes/routines/ "
                        f"檔案被外部修改。重啟可能修復。"
                    )
                QTimer.singleShot(0, _missing)
                return

            try:
                result = fire_routine(r)
            except Exception as e:
                log.warning(f"manual fire {routine_id} raised: {e}")
                result = {"ok": False, "error": str(e)}

            # Build a user-facing summary distinguishing the three
            # outcomes: success / skipped (deps or judge) / failed.
            if result.get("ok"):
                title_emoji = "✓"
                summary = "執行成功"
                detail = ""
                steps_info = result.get("steps") or []
                if steps_info:
                    parts = []
                    marks = {"success": "✓", "failed": "✗",
                             "skipped": "⤻", "pending": "…"}
                    for i, s in enumerate(steps_info, 1):
                        m = marks.get(s.get("status"), "?")
                        parts.append(f"  {m} {i}. {s.get('action_type', '?')}")
                    detail = "\n".join(parts)
            elif result.get("skipped"):
                title_emoji = "⤻"
                summary = "略過,沒有實際執行"
                detail = result.get("reason", "")
            else:
                title_emoji = "✗"
                summary = "執行失敗"
                detail = (
                    result.get("reason")
                    or result.get("error")
                    or "未知錯誤"
                )

            popup_text = (
                f"{title_emoji} 常規「{r.name}」{summary}"
                + (f"\n\n{detail}" if detail else "")
            )
            status_text = (
                f"○ 常規「{r.name}」: {summary}"
            )

            def _ui():
                self.refresh()
                self.bridge.status_update.emit(status_text)
                self._show_fire_popup(popup_text)
            QTimer.singleShot(0, _ui)
        threading.Thread(target=_do, daemon=True).start()

    def _show_fire_popup(self, text: str) -> None:
        """Show a popup that actually surfaces above the main window.

        Plain QMessageBox.information sometimes appeared behind the
        main window depending on focus / window flags / OS, which
        looked exactly like "the button doesn't work". Force the popup
        to the top + raise + activate the window so it can't be
        hidden by accident.
        """
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QMessageBox as _QMB
        box = _QMB(self)
        box.setIcon(_QMB.Information)
        box.setWindowTitle("AI Slime")
        box.setText(text)
        box.setWindowFlag(_Qt.WindowStaysOnTopHint, True)
        # Activate self first so the popup parent is reliably visible,
        # then raise the popup. Belt-and-suspenders for a flaky issue.
        try:
            self.window().raise_()
            self.window().activateWindow()
        except Exception:
            pass
        box.exec()

    def _toggle_routine(self, routine_id: str, currently_enabled: bool) -> None:
        """Enable/disable goes through the approval queue — same path
        as any other state-changing action."""
        from sentinel.growth import submit_action, PolicyDenied
        action_type = "routine.disable" if currently_enabled else "routine.create"
        # Note: enable doesn't have its own action — disabling and
        # re-enabling uses storage.enable_routine directly. To keep
        # the audit story consistent we use storage helper for the
        # enable case (a previously-disabled routine doesn't need
        # re-approval to flip on; the ORIGINAL approval still holds).
        if not currently_enabled:
            # Direct re-enable (no new approval — original consent persists)
            from sentinel.routines import enable_routine
            enable_routine(routine_id)
            self.refresh()
            return
        # Disable: queue an approval (so the audit log records who
        # turned it off and triggers the preferences signal).
        try:
            submit_action(
                action_type="routine.disable",
                title=f"停用常規 {routine_id}",
                reason="從常規管理頁停用",
                payload={"id": routine_id, "reason": "manual disable"},
            )
            QMessageBox.information(
                self, "AI Slime",
                "已建立停用提案,到「待同意」分頁批准後才會真的停用。",
            )
        except PolicyDenied as e:
            QMessageBox.warning(
                self, "AI Slime",
                "; ".join(f.get("msg", "") for f in e.findings),
            )
        except Exception as e:
            QMessageBox.warning(self, "AI Slime", f"提案失敗：{e}")

    def _delete_routine(self, routine_id: str) -> None:
        """Delete via approval queue. Confirmation dialog first since
        delete is permanent."""
        reply = QMessageBox.question(
            self, "AI Slime",
            "確定要刪除這個 routine 嗎？\n"
            "刪除後無法復原。如果只是想暫時停用,請用「停用」按鈕。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        from sentinel.growth import submit_action, PolicyDenied
        try:
            submit_action(
                action_type="routine.delete",
                title=f"刪除常規 {routine_id}",
                reason="從常規管理頁刪除",
                payload={"id": routine_id, "reason": "manual delete"},
            )
            QMessageBox.information(
                self, "AI Slime",
                "已建立刪除提案,到「待同意」分頁批准後才會真的刪除。",
            )
        except PolicyDenied as e:
            QMessageBox.warning(
                self, "AI Slime",
                "; ".join(f.get("msg", "") for f in e.findings),
            )
        except Exception as e:
            QMessageBox.warning(self, "AI Slime", f"提案失敗：{e}")

    def _run_detector_now(self) -> None:
        """Manually invoke the routine detector. Runs off the UI thread
        because it makes an LLM call and reads activity logs.

        UX-pass-1: uses propose_via_detector_verbose so the "no
        result" path can give a real reason instead of the generic
        "再用幾天看看" — distinguishing "no activity log yet" from
        "LLM unreachable" from "low confidence" gives the user
        actionable next steps.
        """
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("偵測中…")

        def _do():
            try:
                from sentinel.routines.detector import (
                    propose_via_detector_verbose,
                )
                result = propose_via_detector_verbose()
            except Exception as e:
                log.warning(f"manual detector run failed: {e}")
                result = {"queued_ids": [], "diagnostic": f"錯誤：{e}"}

            queued = result.get("queued_ids", [])
            diagnostic = result.get("diagnostic", "")

            def _ui():
                self.detect_btn.setEnabled(True)
                self.detect_btn.setText("🔍 立即偵測新常規")
                if queued:
                    QMessageBox.information(
                        self, "AI Slime",
                        f"提案了 {len(queued)} 個新常規。\n"
                        f"到「⏳ 待同意」分頁查看細節 + 同意。",
                    )
                else:
                    msg = diagnostic or (
                        "目前沒有看到值得自動化的固定流程。"
                        "再用幾天看看。"
                    )
                    QMessageBox.information(
                        self, "AI Slime", msg,
                    )
                self.refresh()
            QTimer.singleShot(0, _ui)
        threading.Thread(target=_do, daemon=True).start()

    def retranslate(self):
        # Tab is mostly already in zh-TW; minimal i18n for now.
        self.refresh()


# ─── Main Window ─────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.bridge = SignalBridge()

        self.setWindowTitle("AI Slime Agent")
        self.setMinimumSize(700, 600)
        self.setWindowIcon(create_icon())

        # ── Register action handlers at app start, not just daemon start.
        #
        # Without this, the chat tab's inline approval cards (Phase D2)
        # for routine.create / surface.* / etc. are visible but the
        # 同意 button silently fails: approve() looks up a handler for
        # the action_type, finds none registered (registration was
        # gated on the daemon thread starting), and returns False —
        # which the user just sees as "click does nothing".
        #
        # All registrations are idempotent (each register_action_handler
        # overwrites prior), so the daemon's later registration calls
        # are harmless duplicates. Wrapping in broad try/except so a
        # platform-specific surface init failure on macOS / Linux
        # doesn't keep the rest of the app from starting.
        try:
            from sentinel.surface.handlers import register_all as _r_surface
            _r_surface()
        except Exception as e:
            log.warning(f"surface handler registration failed at startup: {e}")
        try:
            from sentinel.routines.handlers import register_all as _r_routine
            _r_routine()
        except Exception as e:
            log.warning(f"routine handler registration failed at startup: {e}")

        # Slime self-expression auto-trigger.
        # `maybe_generate_weekly` is idempotent within a 6-day window,
        # so calling it on every startup is safe — the function itself
        # decides whether enough time has passed. We schedule it 30 s
        # after launch so initial UI paint isn't blocked by an LLM +
        # image API roundtrip. If a new expression is generated, we
        # surface it in chat (when chat tab finishes building).
        def _maybe_kick_expression():
            def _do():
                try:
                    from sentinel.expression.generator import maybe_generate_weekly
                    exp = maybe_generate_weekly()
                except Exception as e:
                    log.warning(f"weekly expression gen failed: {e}")
                    exp = None
                if exp is None:
                    return
                # Marshal to GUI thread to append into chat tab.
                def _show():
                    try:
                        if hasattr(self, "chat_tab") and self.chat_tab:
                            self.chat_tab.append_expression(exp)
                    except Exception as e:
                        log.debug(f"chat append for expression failed: {e}")
                QTimer.singleShot(0, _show)
            threading.Thread(target=_do, daemon=True, name="slime-expression").start()
        QTimer.singleShot(30_000, _maybe_kick_expression)

        # Load saved settings
        self._load_settings()

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QWidget()
        theme = get_theme_info()
        header.setStyleSheet(f"background-color: {theme['header_bg']}; padding: 8px;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 8, 16, 8)

        self.title_label = QLabel(f"<b style='color:#00dcff; font-size:18px;'>AI Slime Agent</b>"
                                   f"  <span style='color:#666;'>{t('app_subtitle')}</span>")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        # Language toggle in header
        self.lang_btn = QPushButton("EN" if get_language() == "zh" else "中")
        self.lang_btn.setFixedWidth(40)
        self.lang_btn.setStyleSheet(
            "QPushButton { background-color: #2a2a4a; color: #888; font-size: 11px; padding: 4px; }"
            "QPushButton:hover { color: #00dcff; }"
        )
        self.lang_btn.clicked.connect(self._toggle_language)
        header_layout.addWidget(self.lang_btn)

        self.daemon_label = QLabel(t("daemon_stopped"))
        self.daemon_label.setStyleSheet("color: #888;")
        header_layout.addWidget(self.daemon_label)

        self.toggle_btn = QPushButton(t("btn_start"))
        self.toggle_btn.clicked.connect(self.toggle_daemon)
        header_layout.addWidget(self.toggle_btn)

        main_layout.addWidget(header)

        # Tabs
        self.tabs = QTabWidget()
        self.home_tab = HomeTab()
        self.chat_tab = ChatTab(self.bridge)
        self.memory_tab = MemoryTab()
        self.federation_tab = FederationTab()
        self.evolution_tab = EvolutionTab()
        self.equipment_tab = EquipmentTab()
        self.market_tab = MarketTab()
        self.approval_tab = ApprovalTab()
        # Phase F-K's routine system was invisible without a GUI —
        # this tab makes auto-detected / approved / fired routines
        # surveyable + actionable from the GUI instead of from
        # ~/.hermes/routines/*.json.
        self.routines_tab = RoutinesTab(self.bridge)
        self.settings_tab = SettingsTab()

        # 裝備變更時刷新形象
        self.equipment_tab.equipment_changed.connect(self.evolution_tab.refresh)

        # ── v0.7-alpha daily-mirror lite mode ─────────────────────
        # We froze the wider feature surface to focus 2 weeks of
        # dogfooding on one core wedge: a daily reflection card from
        # the slime. The hidden tabs (equipment / memory / federation
        # / market / approval) keep working behind the scenes — their
        # code is intact, only the UI entry point is removed. Approval
        # traffic still surfaces inline in the chat tab via Phase D2
        # so any LLM-proposed action can still be reviewed without
        # the dedicated tab.
        #
        # To restore any of these for development, just uncomment the
        # corresponding addTab line. Index trackers
        # (_federation_tab_index / _approval_tab_index) are set to -1
        # so the existing change-handlers safely no-op.
        self.tabs.addTab(self.home_tab, t("tab_home"))
        self.tabs.addTab(self.evolution_tab, t("tab_evolution"))
        # self.tabs.addTab(self.equipment_tab, t("tab_equipment"))   # frozen v0.7
        self.tabs.addTab(self.chat_tab, t("tab_chat"))
        # self.tabs.addTab(self.memory_tab, t("tab_memory"))         # frozen v0.7
        # self._federation_tab_index = self.tabs.addTab(
        #     self.federation_tab, t("tab_federation")                # frozen v0.7
        # )
        self._federation_tab_index = -1
        # self.tabs.addTab(self.market_tab, t("tab_market"))         # frozen v0.7
        self._routines_tab_index = self.tabs.addTab(
            self.routines_tab, "📋 常規"
        )
        # self._approval_tab_index = self.tabs.addTab(               # frozen v0.7
        #     self.approval_tab, t("tab_approval")
        # )
        self._approval_tab_index = -1
        self.tabs.addTab(self.settings_tab, t("tab_settings"))

        # 待同意頁籤：切過去時主動刷新；動作後刷新 evolution + 標籤計數
        # 公頻頁籤：切過去時清除「新訊息」badge
        # 常規頁籤：切過去時刷新（背景 scheduler 可能跑過 routine,
        #          fire_count 已更新但 GUI 不知道）
        self.tabs.currentChanged.connect(self._on_approval_tab_changed)
        self.tabs.currentChanged.connect(self._on_federation_tab_changed)
        self.tabs.currentChanged.connect(self._on_routines_tab_changed)
        self.approval_tab.proposals_changed.connect(self.evolution_tab.refresh)
        self.approval_tab.proposals_changed.connect(self._refresh_approval_tab_label)

        main_layout.addWidget(self.tabs)

        # ── 建議通知橫幅 ──
        self.advice_banner = QLabel("")
        self.advice_banner.setWordWrap(True)
        self.advice_banner.setStyleSheet(
            "color: #ffeaa7; background-color: rgba(255, 165, 2, 0.15); "
            "border: 1px solid rgba(255, 165, 2, 0.3); border-radius: 4px; "
            "padding: 8px 12px; font-size: 12px;"
        )
        self.advice_banner.setVisible(False)
        self.advice_banner.setCursor(Qt.PointingHandCursor)
        self.advice_banner.mousePressEvent = lambda e: self.advice_banner.setVisible(False)
        main_layout.addWidget(self.advice_banner)

        # 接收建議信號
        self.bridge.advice_received.connect(self._show_advice)

        # ── 底部即時狀態面板（兩行） ──
        # Phase L2 — softer rendering. Translucent surface, no harsh
        # 1-px border, generous padding. The two text rows use the
        # token-driven dim/muted colors so the status bar reads as a
        # quiet ambient frame rather than a "control panel" that
        # draws the eye away from the active tab.
        from sentinel.ui import tokens as _tk
        status_panel = QWidget()
        status_panel.setStyleSheet(
            f"background-color:{_tk.PALETTE['bg_sunken']};"
            f"border-top:1px solid {_tk.PALETTE['border_subtle']};"
        )
        sp_layout = QVBoxLayout(status_panel)
        sp_layout.setContentsMargins(
            _tk.SPACE["lg"], _tk.SPACE["sm"],
            _tk.SPACE["lg"], _tk.SPACE["sm"],
        )
        sp_layout.setSpacing(2)

        self.status_bar = QLabel("○ 等待甦醒…")
        self.status_bar.setStyleSheet(
            f"color:{_tk.PALETTE['text_dim']};"
            f"font-size:{_tk.FONT_SIZE['meta']}px;"
            f"letter-spacing:0.3px;"
        )
        sp_layout.addWidget(self.status_bar)

        self.sensor_bar = QLabel("")
        self.sensor_bar.setStyleSheet(
            f"color:{_tk.PALETTE['text_muted']};"
            f"font-size:10px;"
        )
        sp_layout.addWidget(self.sensor_bar)

        main_layout.addWidget(status_panel)

        # 狀態列即時更新
        # UX-pass-1: emit format may include "<visible>\x00<tooltip>".
        # Splitting here means a status producer can offer extra
        # detail-on-hover without polluting the always-visible bar.
        def _apply_status(text: str) -> None:
            if "\x00" in text:
                visible, tooltip = text.split("\x00", 1)
            else:
                visible, tooltip = text, ""
            self.status_bar.setText(visible)
            self.status_bar.setToolTip(tooltip)
        self.bridge.status_update.connect(_apply_status)
        self.bridge.sensor_update.connect(self.sensor_bar.setText)

        # Avatar speaks visually whenever a chat reply lands. Decoupled
        # from chat tab so the home avatar reacts even if the user is
        # on a different tab.
        def _slime_speaks(_text: str) -> None:
            try:
                self.home_tab.slime_widget.react("speak")
            except Exception:
                pass
        self.bridge.chat_response.connect(_slime_speaks)

        # Language change
        self.settings_tab.language_changed.connect(self._retranslate_all)

        # System tray
        self._setup_tray()

        # Desktop notification signal → tray balloon
        self.bridge.desktop_notify.connect(self._on_desktop_notify)

        # Register bridge in notifier for desktop toast fallback
        from sentinel.notifier import set_signal_bridge
        set_signal_bridge(self.bridge)

        # ── Floating overlay (desktop pet) ──
        from sentinel.overlay import SlimeOverlay
        self.overlay = SlimeOverlay()
        self.overlay.open_main_window.connect(self._show_window)
        self.bridge.desktop_notify.connect(self._on_overlay_notify)
        self.overlay.show()

        # Auto-refresh timers
        self.evo_timer = QTimer()
        self.evo_timer.timeout.connect(self.evolution_tab.refresh)
        self.evo_timer.timeout.connect(self.equipment_tab.refresh)
        self.evo_timer.timeout.connect(self.approval_tab.refresh)
        self.evo_timer.timeout.connect(self._refresh_approval_tab_label)
        self.evo_timer.timeout.connect(self._refresh_federation_tab_label)
        self.evo_timer.timeout.connect(self.home_tab.refresh)
        self.evo_timer.timeout.connect(self._sync_overlay_state)
        self.evo_timer.start(30000)  # 每 30 秒刷新進化、裝備、首頁

        # Initial overlay state sync
        QTimer.singleShot(1000, self._sync_overlay_state)

        # Daemon state - auto awaken on launch
        self.daemon_thread = None
        self.daemon_running = False
        # 註冊 approval submit callback — self_evolution 丟新 proposal 時
        # 這個 callback 會立刻重新整理 UI + 發 Telegram 通知
        try:
            from sentinel.growth import register_on_submit
            register_on_submit(self._on_approval_submitted)
        except Exception as e:
            log.warning("register approval callback failed: %s", e)

        QTimer.singleShot(500, self.toggle_daemon)  # Awaken after GUI is ready

    def _load_settings(self):
        settings_file = Path.home() / ".hermes" / "sentinel_settings.json"
        if settings_file.exists():
            try:
                s = json.loads(settings_file.read_text(encoding="utf-8"))
                set_language(s.get("language", "zh"))
                if "theme" in s:
                    set_theme(s["theme"])
                # RELAY_SERVER_URL is hardcoded — don't load from settings
                config.CHAT_MODEL_PREF = s.get("chat_model_pref", config.CHAT_MODEL_PREF)
                config.ANALYSIS_MODEL_PREF = s.get("analysis_model_pref", config.ANALYSIS_MODEL_PREF)
                config.TELEGRAM_BOT_TOKEN = s.get("telegram_bot_token", "") or ""
                try:
                    config.TELEGRAM_CHAT_ID = int(s.get("telegram_chat_id", 0) or 0)
                except (ValueError, TypeError):
                    config.TELEGRAM_CHAT_ID = 0
                if "llm_providers" in s:
                    config.LLM_PROVIDERS = s["llm_providers"]
                config.SYSTEM_CHECK_INTERVAL = s.get("check_interval", config.SYSTEM_CHECK_INTERVAL)
                config.IDLE_REPORT_INTERVAL = s.get("idle_report_interval", config.IDLE_REPORT_INTERVAL)
                if "watch_dirs" in s:
                    config.WATCH_DIRS = [Path(d) for d in s["watch_dirs"]]
            except Exception:
                pass

    def _toggle_language(self):
        new_lang = "en" if get_language() == "zh" else "zh"
        set_language(new_lang)
        self.lang_btn.setText("EN" if new_lang == "zh" else "中")
        self._retranslate_all()

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(create_tray_icon(), self)
        tray_menu = QMenu()

        show_action = QAction(t("tray_show"), self)
        show_action.triggered.connect(self._show_window)
        tray_menu.addAction(show_action)

        settings_action = QAction(t("tab_settings"), self)
        settings_action.triggered.connect(lambda: (self._show_window(), self.tabs.setCurrentIndex(7)))
        tray_menu.addAction(settings_action)

        overlay_action = QAction("顯示/隱藏浮窗", self)
        overlay_action.triggered.connect(self._toggle_overlay)
        tray_menu.addAction(overlay_action)

        tray_menu.addSeparator()

        quit_action = QAction(t("tray_quit"), self)
        quit_action.triggered.connect(self._quit)
        tray_menu.addAction(quit_action)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.setToolTip("AI Slime Agent")
        self.tray.show()

    def _on_desktop_notify(self, title: str, body: str):
        """Show a system tray balloon notification."""
        if hasattr(self, 'tray') and self.tray:
            self.tray.showMessage(title, body, QSystemTrayIcon.Information, 5000)

    def _on_overlay_notify(self, title: str, body: str):
        """Show notification bubble on the floating overlay."""
        if hasattr(self, 'overlay') and self.overlay:
            text = f"{title} {body}".strip() if body else title
            self.overlay.show_bubble(text, 6000)

    def _sync_overlay_state(self):
        """Sync overlay slime appearance with current evolution state."""
        if not hasattr(self, 'overlay'):
            return
        try:
            from sentinel.evolution import load_evolution
            evo = load_evolution()
            self.overlay.set_state(
                evo.form,
                evo.title,
                evo.dominant_traits[:3] if evo.dominant_traits else [],
            )
        except Exception:
            pass

    def _toggle_overlay(self):
        """Toggle floating overlay visibility."""
        if hasattr(self, 'overlay'):
            if self.overlay.isVisible():
                self.overlay.hide()
            else:
                self.overlay.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    def _show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quit(self):
        """真正關閉 AI Slime（含背景線程和 CMD 視窗）。"""
        self.daemon_running = False
        if hasattr(self, 'overlay'):
            self.overlay.hide()
        self.tray.hide()
        # Before quitting, stamp last_seen so reunion detection works on next launch
        try:
            from sentinel import identity
            identity.touch_last_seen()
        except Exception:
            pass
        QApplication.quit()
        # 強制結束 Python process，確保 CMD 也會關閉
        import os
        os._exit(0)

    def closeEvent(self, event):
        """按 X 時的行為：詢問要最小化還是完全關閉。"""
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "AI Slime",
            "要讓 AI Slime 繼續在背景守護嗎？\n\n"
            "「是」→ 最小化到系統列（繼續觀察）\n"
            "「否」→ 完全關閉 AI Slime",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.No:
            self._quit()
        else:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "AI Slime",
                "AI Slime 在系統列中繼續守護。右鍵點擊圖示可完全關閉。",
                QSystemTrayIcon.Information,
                2000,
            )

    def _on_approval_tab_changed(self, index: int):
        """切到待同意分頁時立刻刷新，不用等 30s timer。"""
        if getattr(self, "_approval_tab_index", None) is not None and index == self._approval_tab_index:
            self.approval_tab.refresh()
            self._refresh_approval_tab_label()

    def _refresh_approval_tab_label(self):
        """在待同意頁籤標題後面顯示 pending 數量，像 `待同意 (3)`。

        Returns early when the tab is frozen (index < 0 in v0.7-alpha
        lite mode). Approval traffic still surfaces inline in chat.
        """
        idx = getattr(self, "_approval_tab_index", None)
        if idx is None or idx < 0:
            return
        n = self.approval_tab.pending_count()
        base = t("tab_approval")
        label = f"{base} ({n})" if n > 0 else base
        self.tabs.setTabText(idx, label)

    def _on_federation_tab_changed(self, index: int):
        """切到公頻分頁時清掉 new-pattern badge，重建「待分享」區塊。

        **不會**自動打 relay API 重撈社群列表 — 那是個同步、Render 冷啟
        下可能 30-90 秒才回來的網路呼叫，會把主執行緒整個凍住，造成
        每切一次 tab 就卡住幾十秒。社群列表要更新時使用者按「重新感應」
        按鈕即可。本地的待分享 pending 候選仍然會在 tab 切進來時即時
        重建，因為那只是讀本地 JSON 檔，幾毫秒的事。
        """
        if getattr(self, "_federation_tab_index", None) is None:
            return
        if index != self._federation_tab_index:
            return
        try:
            from sentinel.growth.federation import mark_viewed
            mark_viewed()
        except Exception:
            pass
        self._refresh_federation_tab_label()
        # Cheap local rebuild only — surfaces any new pending candidates
        # the distiller queued while the user was on another tab.
        try:
            self.federation_tab._rebuild_pending()
        except Exception:
            pass

    def _on_routines_tab_changed(self, index: int):
        """Refresh routine cards when the user opens the tab.

        Background schedulers fire routines / update fire_count
        without notifying the GUI. Refreshing on tab-click is the
        cheapest way to keep cards accurate without polling
        constantly. Cost is one disk read of ~/.hermes/routines/*.json
        plus one reflection pass — both fast, both already cached.
        """
        idx = getattr(self, "_routines_tab_index", None)
        if idx is None or index != idx:
            return
        try:
            self.routines_tab.refresh()
        except Exception as e:
            log.warning(f"routines tab refresh on switch failed: {e}")

    def _refresh_federation_tab_label(self):
        """在公頻頁籤標題後面顯示待分享候選數量，像 `🌍 公頻 (2)`。

        The count is `new_since_last_view` (candidates the distiller
        queued since the user last opened the tab), not the total
        pending — we want the badge to encourage revisiting, not nag
        about candidates the user already saw and ignored.
        """
        idx = getattr(self, "_federation_tab_index", None)
        if idx is None or idx < 0:
            return
        base = t("tab_federation")
        try:
            from sentinel.growth.federation import get_stats
            n = get_stats().get("new_since_last_view", 0)
        except Exception:
            n = 0
        label = f"{base} ({n})" if n > 0 else base
        self.tabs.setTabText(idx, label)

    def _on_approval_submitted(self, approval):
        """Fires from sentinel.growth.approval.submit_for_approval.

        Marshals refresh back onto the GUI thread (caller may be a
        background/daemon thread) and best-effort fires a Telegram
        notification. Notifier itself has cooldown + credential fallback.
        """
        QTimer.singleShot(0, self.approval_tab.refresh)
        QTimer.singleShot(0, self._refresh_approval_tab_label)
        # Avatar reacts so the user sees something happen even if
        # they're not on the 待同意 tab. The signal is intentionally
        # quiet — a 💡 floating above the slime, not a popup.
        try:
            QTimer.singleShot(0, lambda: self.home_tab.slime_widget.react("idea"))
        except Exception:
            pass
        try:
            from sentinel.notifier import send_notification
            kind_zh = "新技能" if approval.kind == "skill_gen" else "自我改良"
            raw_title = approval.title or "(無標題)"
            title = raw_title[:60]
            warn_str = ""
            if approval.safety_findings:
                warn_str = "（%d 警告）" % len(approval.safety_findings)
            parts = [
                "🧬 *AI Slime 提議：" + kind_zh + "*",
                "「" + title + "」" + warn_str,
                "ID: `" + approval.id + "`",
                "打開視窗到「待同意」分頁審閱。",
            ]
            send_notification(chr(10).join(parts), category="approval_submit")
        except Exception as e:
            log.warning("approval telegram notify failed: %s", e)

    def toggle_daemon(self):
        if self.daemon_running:
            self.daemon_running = False
            self.daemon_label.setText(t("daemon_stopped"))
            self.daemon_label.setStyleSheet("color: #888;")
            self.toggle_btn.setText(t("btn_start"))
            self.toggle_btn.setObjectName("")
            self.toggle_btn.setStyle(self.toggle_btn.style())
        else:
            self.daemon_running = True
            self.daemon_label.setText(t("daemon_running"))
            self.daemon_label.setStyleSheet("color: #2ed573;")
            self.toggle_btn.setText(t("btn_stop"))
            self.toggle_btn.setObjectName("stopBtn")
            self.toggle_btn.setStyle(self.toggle_btn.style())
            self._start_daemon()
            self._start_telegram_bot()

    def _start_telegram_bot(self):
        """Start Telegram bot listener in background thread."""
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            log.info("Telegram not configured, skipping bot listener")
            return

        def _run_bot():
            import asyncio
            from telegram import Update, Bot
            from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters
            from sentinel.chat import handle_message
            from sentinel.evolution import load_evolution, record_conversation, get_status_text

            async def on_message(update: Update, context):
                if update.message is None or update.message.chat_id != config.TELEGRAM_CHAT_ID:
                    return
                text = update.message.text
                if not text:
                    return
                evo = load_evolution()
                record_conversation(evo)
                reply = handle_message(text)
                await update.message.reply_text(reply)

            async def cmd_status(update: Update, context):
                if update.message.chat_id != config.TELEGRAM_CHAT_ID:
                    return
                from sentinel.system_monitor import take_snapshot
                snap = take_snapshot()
                evo = load_evolution()
                text = f"📊 *系統狀態*\n{snap.summary()}\n\n🧬 *{evo.title}*\n觀察: {evo.total_observations:,}"
                await update.message.reply_text(text, parse_mode="Markdown")

            async def cmd_evolution(update: Update, context):
                if update.message.chat_id != config.TELEGRAM_CHAT_ID:
                    return
                evo = load_evolution()
                await update.message.reply_text(get_status_text(evo))

            async def cmd_rollback(update: Update, context):
                if update.message.chat_id != config.TELEGRAM_CHAT_ID:
                    return
                from sentinel.self_evolution import rollback_to_core, list_snapshots
                # /rollback → 恢復出廠，/rollback snap_xxx → 回滾到特定快照
                args = update.message.text.split()
                if len(args) > 1:
                    snap_id = args[1]
                    from sentinel.self_evolution import rollback_to_snapshot
                    ok = rollback_to_snapshot(snap_id)
                    msg = f"已回滾到 {snap_id}" if ok else f"回滾失敗：找不到 {snap_id}"
                else:
                    ok = rollback_to_core()
                    msg = "已恢復出廠設定（你的記憶和資料完整保留）" if ok else "恢復失敗"
                await update.message.reply_text(f"🔄 {msg}")

            async def cmd_skills(update: Update, context):
                if update.message.chat_id != config.TELEGRAM_CHAT_ID:
                    return
                from sentinel.self_evolution import list_skills
                skills = list_skills()
                if skills:
                    lines = ["🎯 *AI Slime 自創技能*\n"]
                    for s in skills:
                        lines.append(f"  {s['skill_name']} — {s['description']}")
                    await update.message.reply_text("\n".join(lines))
                else:
                    await update.message.reply_text("尚未自創任何技能。觀察量足夠後會自動產生。")

            try:
                app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()
                app.add_handler(CommandHandler("status", cmd_status))
                app.add_handler(CommandHandler("evolution", cmd_evolution))
                app.add_handler(CommandHandler("rollback", cmd_rollback))
                app.add_handler(CommandHandler("skills", cmd_skills))
                app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
                app.run_polling(drop_pending_updates=True)
            except Exception as e:
                log.error(f"Telegram bot error: {e}")

        self.telegram_thread = threading.Thread(target=_run_bot, daemon=True)
        self.telegram_thread.start()
        log.info("Telegram bot listener started")

    def _start_daemon(self):
        def _run():
            from sentinel.system_monitor import take_snapshot
            from sentinel.file_watcher import FileWatcher
            from sentinel.claude_watcher import get_claude_activity_summary
            from sentinel.brain import analyze_events, build_context
            from sentinel.context_bus import get_bus as _get_context_bus
            from sentinel.learner import distill_from_activity, get_profile_summary
            from sentinel.notifier import send_notification, send_startup_message
            from sentinel.activity_tracker import ActivityTracker
            from sentinel.evolution import load_evolution, record_observation, record_learning, record_activity_affinities
            from sentinel.input_tracker import InputTracker
            from sentinel.screen_watcher import ScreenWatcher
            from sentinel.self_evolution import ensure_core_backup, maybe_evolve
            from sentinel.wallet.equipment import (
                load_equipment, try_drop, get_exp_multiplier,
            )
            from sentinel.advisor import Advisor, AdvisorContext, generate_insight
            from sentinel.learner import load_memory as _load_learner_memory

            # 安全網：首次啟動建立核心備份
            ensure_core_backup()

            # Phase C2: register surface primitives as ACTION handlers
            # so anything Phase D (computer-use) submits can reach the
            # right executor. Happens once per daemon start; safe to
            # repeat. Isolated in a try so a surface init failure on
            # an unsupported platform doesn't abort the daemon.
            try:
                from sentinel.surface.handlers import register_all as _register_surface_actions
                _register_surface_actions()
            except Exception as _e:
                log.warning(f"surface handler registration failed: {_e}")

            # Phase F: routine.* action handlers + scheduler daemon.
            # Routines fire automatically at trigger times, running
            # their step list through the same workflow engine + action
            # handlers used by one-off chat actions. The scheduler is
            # one shared daemon thread; safe to start once per process.
            try:
                from sentinel.routines.handlers import register_all as _register_routine_actions
                from sentinel.routines import start_scheduler as _start_routine_scheduler
                from sentinel.routines.reactive import register_reactive_triggers
                from sentinel.routines.preferences import register_with_approval_queue as _register_pref_hook
                _register_routine_actions()
                _start_routine_scheduler()
                # Phase G — subscribe reactive dispatcher to event
                # types BEFORE the observation loop starts publishing.
                # Idempotent so a future config-reload doesn't stack
                # subscriptions.
                register_reactive_triggers()
                # Phase I — wire approval-queue rejection hook so the
                # detector learns from what the user said no to.
                _register_pref_hook()
            except Exception as _e:
                log.warning(f"routine subsystem init failed: {_e}")

            evo = load_evolution()
            equip_state = load_equipment()
            obs_since_drop = 0  # Track observations for drop trigger
            advisor = Advisor()
            send_startup_message()

            watcher = FileWatcher(config.WATCH_DIRS)
            watcher.start()
            tracker = ActivityTracker()
            input_tracker = InputTracker()
            input_tracker.start()
            screen_watcher = ScreenWatcher()

            last_check = 0
            last_distill = time.time()
            last_idle = time.time()
            last_notify: dict[str, float] = {}
            activity_buf = []

            _consecutive_errors = 0
            _MAX_ERRORS = 20
            _last_alive_ts = time.time()  # 追蹤 daemon 存活時間

            while self.daemon_running:
              try:
                now = time.time()

                # ── 休眠恢復偵測 ──
                # 如果距離上次循環超過 60 秒，代表電腦可能剛休眠恢復
                _gap = now - _last_alive_ts
                if _gap > 60:
                    log.info(f"偵測到休眠恢復（gap={_gap:.0f}s），重啟感知器...")
                    self.bridge.status_update.emit("● 休眠恢復中，重啟感知器...")
                    # 重啟 input tracker（pynput listener 休眠後可能死亡）
                    try:
                        input_tracker.stop()
                    except Exception:
                        pass
                    input_tracker = InputTracker()
                    input_tracker.start()
                    # 重啟 file watcher（watchdog observer 可能失效）
                    try:
                        watcher.stop()
                    except Exception:
                        pass
                    watcher = FileWatcher(config.WATCH_DIRS)
                    watcher.start()
                    # 重新載入 evolution 和 equipment 狀態
                    evo = load_evolution()
                    equip_state = load_equipment()
                    # 重置計時器，避免立刻觸發蒸餾/報告
                    last_distill = now
                    last_idle = now
                    last_check = 0  # 立刻做一次感知
                    log.info("感知器重啟完成")
                _last_alive_ts = now

                # Always poll activity (lightweight, no API)
                tracker.poll()

                if now - last_check >= config.SYSTEM_CHECK_INTERVAL:
                    last_check = now

                    snapshot = take_snapshot()
                    file_events = watcher.get_events()
                    claude_act = get_claude_activity_summary()
                    user_act = tracker.get_activity_summary()
                    input_act = input_tracker.get_full_summary()

                    # Phase G — feed environmental events to the
                    # routine reactive dispatcher so on_app_open /
                    # on_file_pattern routines fire when their
                    # condition is met. Wrapped in try/except: a
                    # subscriber crash shouldn't tear down the
                    # observation loop. See sentinel/routines/events.py.
                    try:
                        from sentinel.routines import events as _rev
                        for fe in file_events or []:
                            _rev.publish(_rev.EVENT_FILE_CHANGE, {
                                "path": fe.get("path", ""),
                                "type": fe.get("type", ""),
                            })
                        # active-window change → app_open. tracker
                        # already exposes the current window; we
                        # cache the previous title so we only fire on
                        # transitions, not every observation tick.
                        cur_title, cur_proc = tracker._get_active_window()
                        prev_title = getattr(self, "_last_active_window", None)
                        if cur_title and cur_title != prev_title:
                            _rev.publish(_rev.EVENT_APP_OPEN, {
                                "title": cur_title,
                                "process_name": cur_proc or "",
                            })
                            self._last_active_window = cur_title
                        # Idle threshold synthesis: track the highest
                        # threshold currently crossed and only fire
                        # when we cross a NEW threshold (so a 30-min
                        # idle doesn't keep emitting events at every
                        # tick).
                        idle_minutes = int(
                            tracker.get_idle_duration() / 60
                        )
                        last_idle_emit = getattr(
                            self, "_last_idle_emit_minutes", 0
                        )
                        if idle_minutes >= 1 and idle_minutes != last_idle_emit:
                            # Only emit at coarse 5-min boundaries so
                            # a routine waiting for "15 min idle" gets
                            # exactly one event when crossed, not 60.
                            bucket = (idle_minutes // 5) * 5
                            if bucket > 0 and bucket != last_idle_emit:
                                _rev.publish(_rev.EVENT_IDLE_REACHED, {
                                    "duration_minutes": bucket,
                                })
                                self._last_idle_emit_minutes = bucket
                        if idle_minutes == 0:
                            self._last_idle_emit_minutes = 0
                    except Exception as _e:
                        log.warning(f"reactive event publish failed: {_e}")
                    # build_context now publishes system/files/claude/activity
                    # to the shared Context Bus. We then publish the remaining
                    # source-specific signals (input, screen) the same way,
                    # so all observation channels live in one place. `ctx`
                    # is assembled from bus.render() rather than ad-hoc string
                    # concat — consistent section labels, consistent priority
                    # order, consistent TTL-driven decay. See context_bus.py.
                    build_context(snapshot, file_events, claude_act, user_act)
                    _cbus = _get_context_bus()
                    if input_act:
                        _cbus.publish("input", input_act)

                    # 千里眼：隨機截圖觀察
                    screen_act = ""
                    if screen_watcher.should_capture():
                        screen_obs = screen_watcher.capture_and_learn()
                        if screen_obs:
                            screen_act = screen_obs["analysis"]
                            _cbus.publish("screen", screen_act)

                    ctx = _cbus.render()

                    # 更新底部狀態列（第一行：主狀態 + 倒數計時）
                    import datetime as _dt
                    _now_str = _dt.datetime.now().strftime("%H:%M:%S")
                    _next_sense = config.SYSTEM_CHECK_INTERVAL
                    _next_distill = max(0, int(config.DISTILL_INTERVAL - (now - last_distill)))
                    _next_report = max(0, int(config.IDLE_REPORT_INTERVAL - (now - last_idle)))
                    _buf_count = len(activity_buf)
                    # Phase L2 — primary stats (state + observation
                    # totals) read first, the countdown timers go
                    # last with smaller wording. Bullet-dot separator
                    # is less heavy than pipe-with-spaces.
                    #
                    # UX-pass-1: countdown timers (感知/蒸餾/報告) are
                    # debug-y for normal users. Visible label kept
                    # short; full timer breakdown moves to a tooltip
                    # so power users can still see them on hover.
                    _status = (
                        f"● 觀察中  ·  {_now_str}"
                        f"  ·  觀察 {evo.total_observations:,}"
                        f"  ·  學習 {evo.total_learnings}"
                        f"  ·  緩衝 {_buf_count}"
                    )
                    _status_tooltip = (
                        f"下次感知：{_next_sense}s\n"
                        f"下次蒸餾：{_next_distill}s\n"
                        f"下次報告：{_next_report}s"
                    )
                    # Encode tooltip after a NUL so the receiver can
                    # split (we control both ends) without adding a
                    # second signal. The status-bar slot below
                    # interprets text-after-NUL as tooltip.
                    self.bridge.status_update.emit(
                        _status + "\x00" + _status_tooltip
                    )
                    self.bridge.status_update.emit(_status)

                    # 第二行：感測器即時狀態
                    _sensors = []
                    _file_count = len(file_events) if file_events else 0
                    _sensors.append(f"📁檔案:{_file_count}件" if _file_count else "📁檔案:-")
                    _kb_text = input_tracker.get_typing_summary(5)
                    _ms_text = input_tracker.get_click_summary(5)
                    _kb_status = "✓" if _kb_text else "-"
                    _ms_status = "✓" if _ms_text else "-"
                    _sensors.append(f"⌨鍵盤:{_kb_status}")
                    _sensors.append(f"🖱滑鼠:{_ms_status}")
                    _sensors.append("👁截圖:✓" if screen_act else "👁截圖:-")
                    _sensors.append("🪟視窗:✓" if user_act else "🪟視窗:-")
                    _sensors.append("🤖Claude:✓" if claude_act else "🤖Claude:-")
                    _sensor_line = "  ".join(_sensors)
                    self.bridge.sensor_update.emit(_sensor_line)

                    # Track observations for evolution - with source breakdown
                    exp_sources = {"system": 1}
                    if file_events:
                        exp_sources["files"] = len(file_events)
                    if claude_act:
                        exp_sources["claude"] = 1
                    if user_act:
                        exp_sources["activity"] = 1
                    if input_act:
                        exp_sources["input"] = 1
                    if screen_act:
                        exp_sources["screen"] = 1
                    obs_count = sum(exp_sources.values())
                    # Apply equipment EXP buff (reload so GUI equip changes take effect)
                    equip_state = load_equipment()
                    exp_mult = get_exp_multiplier(equip_state)
                    if exp_mult > 1.0:
                        obs_count = int(obs_count * exp_mult)
                    # Reload evo from disk every cycle so a GUI-side evolution
                    # (form/title change, manual evolve button, naming) is not
                    # overwritten by this daemon's stale in-memory copy when
                    # record_observation → save_evolution writes back. Without
                    # this, pressing the evolve button shows the new form for a
                    # moment, then silently reverts to Slime on the next tick.
                    # See issue #3.
                    evo = load_evolution()
                    # Feed activity to adaptive evolution
                    record_activity_affinities(evo, user_act + "\n" + (input_act or ""))
                    record_observation(evo, obs_count, sources=exp_sources)

                    # 裝備掉落：每 100 次觀察有機會
                    # v0.7-alpha 期間：drops still happen and accumulate
                    # in ~/.hermes/equipment.json so when 裝備 tab
                    # un-freezes everything's there waiting. We just
                    # don't notify — Telegram + status-bar alerts about
                    # an inventory the user can't currently see is noise
                    # that distracts from the daily reflection ritual.
                    obs_since_drop += obs_count
                    if obs_since_drop >= 100:
                        obs_since_drop = 0
                        # Reload from disk to pick up GUI-side equip/unequip changes
                        equip_state = load_equipment()
                        try_drop(equip_state, "observation_100")

                    # ── 主動建議引擎 ──
                    if input_act:
                        advisor.record_input_activity()

                    # 計算最近視窗切換次數
                    _recent_switches = 0
                    try:
                        _recent_switches = tracker.get_switch_count(minutes=10)
                    except (AttributeError, Exception):
                        pass

                    _adv_ctx = AdvisorContext(
                        cpu_percent=snapshot.cpu_percent,
                        ram_percent=snapshot.ram_percent,
                        disk_percent=snapshot.disk_percent,
                        active_app=tracker.current_app_name() if hasattr(tracker, 'current_app_name') else "",
                        app_duration_minutes=tracker.current_app_duration() / 60 if hasattr(tracker, 'current_app_duration') else 0,
                        recent_app_switches=_recent_switches,
                        profile=_load_learner_memory().get("profile", ""),
                        patterns=_load_learner_memory().get("patterns", {}),
                        dominant_traits=evo.dominant_traits,
                        evolution_title=evo.title,
                        total_observations=evo.total_observations,
                    )
                    for advice in advisor.evaluate(_adv_ctx):
                        _adv_emoji = advice.get("emoji", "💡")
                        _adv_msg = advice["message"]
                        self.bridge.advice_received.emit(_adv_emoji, _adv_msg)
                        send_notification(
                            f"{_adv_emoji} *AI Slime 的建議*\n{_adv_msg}",
                            category=advice["type"],
                        )

                        if file_events or claude_act or user_act or input_act or screen_act:
                            activity_buf.append(ctx)
                            last_idle = now

                        if snapshot.warnings or len(file_events) > 20:
                            decision = analyze_events(ctx)
                            if decision and decision.get("should_notify"):
                                cat = decision.get("category", "general")
                                if now - last_notify.get(cat, 0) >= config.NOTIFICATION_COOLDOWN:
                                    sev = decision.get("severity", "info")
                                    emoji = {"critical": "\U0001f534", "warning": "\U0001f7e1", "info": "\U0001f535"}.get(sev, "\u26aa")
                                    send_notification(f"{emoji} *AI Slime*\n{decision['message']}", category=cat)
                                    last_notify[cat] = now

                    if now - last_distill >= config.DISTILL_INTERVAL and activity_buf:
                        last_distill = now
                        combined = "\n---\n".join(activity_buf[-10:])
                        self.bridge.status_update.emit(
                            f"● 蒸餾學習中...（{len(activity_buf)} 筆活動）"
                        )
                        result = distill_from_activity(combined)
                        if result is not None:
                            activity_buf.clear()
                            record_learning(evo)  # Evolution: learning milestone
                            self.bridge.status_update.emit(
                                f"✓ 學習完成！第 {evo.total_learnings} 次蒸餾"
                            )

                            # 學習完成 → 嘗試裝備掉落
                            # Silent during v0.7-alpha; see comment on
                            # the observation_100 drop above.
                            equip_state = load_equipment()
                            try_drop(equip_state, "learning")

                            # 自我進化檢查（每次學習後）
                            try:
                                from sentinel.learner import load_memory as _load_mem
                                evo_events = maybe_evolve(evo, _load_mem())
                                for evt_msg in evo_events:
                                    state_log = evo.evolution_log
                                    state_log.append({
                                        "time": time.time(),
                                        "event": "self_evolution",
                                        "message": evt_msg,
                                    })
                                    send_notification(f"🧬 *AI Slime 進化*\n{evt_msg}", category="evolution")
                            except Exception as e:
                                log.error(f"Self-evolution error: {e}")

                            # 學習完成後 → LLM 深度建議（低頻）
                            try:
                                insight = generate_insight(_adv_ctx)
                                if insight:
                                    self.bridge.advice_received.emit("🔮", insight)
                                    send_notification(
                                        f"🔮 *AI Slime 的洞察*\n{insight}",
                                        category="insight_pattern",
                                    )
                            except Exception as e:
                                log.debug(f"Insight generation skipped: {e}")
                        else:
                            # 檢查是否有可用的 LLM provider
                            _has_key = any(
                                p.get("enabled") and p.get("api_key")
                                for p in config.LLM_PROVIDERS
                            )
                            if not _has_key:
                                _fail_msg = "✗ 蒸餾失敗（未設定 API Key，請在魔法陣中設定）"
                            else:
                                _fail_msg = f"✗ 蒸餾失敗（LLM 無回應），{len(activity_buf)} 筆活動保留至下次"
                            log.info(f"Distill failed: has_key={_has_key}")
                            self.bridge.status_update.emit(_fail_msg)

                    if now - last_idle >= config.IDLE_REPORT_INTERVAL:
                        last_idle = now
                        # 豐富的定期報告：不只硬體，還有 AI Slime 觀察到的一切
                        report_parts = ["🧠 *AI Slime 定期報告*\n"]

                        # 硬體
                        snap = take_snapshot()
                        report_parts.append(f"💻 CPU: {snap.cpu_percent}% | RAM: {snap.ram_percent}% | 磁碟: {snap.disk_percent}%")

                        # 使用中的程式
                        act_summary = tracker.get_activity_summary()
                        if act_summary:
                            # 提取 app 使用時間的部分
                            app_lines = []
                            for line in act_summary.split("\n"):
                                line = line.strip()
                                if "分鐘" in line and ":" in line:
                                    app_lines.append(line)
                            if app_lines:
                                report_parts.append("\n📊 最近使用的程式：")
                                for al in app_lines[:5]:
                                    report_parts.append(f"  {al}")

                        # 打字內容摘要
                        typing_summary = input_tracker.get_typing_summary(30)
                        if typing_summary:
                            chunks = [l.strip() for l in typing_summary.split("\n") if l.strip() and not l.startswith("===")]
                            if chunks:
                                report_parts.append("\n⌨️ 最近打字：")
                                for chunk in chunks[-5:]:
                                    # 截短保護隱私
                                    if len(chunk) > 80:
                                        chunk = chunk[:80] + "..."
                                    report_parts.append(f"  {chunk}")

                        # 滑鼠點擊摘要
                        click_summary = input_tracker.get_click_summary(30)
                        if click_summary:
                            click_lines = [l.strip() for l in click_summary.split("\n") if "次" in l]
                            if click_lines:
                                report_parts.append("\n🖱️ 點擊分佈：")
                                for cl in click_lines[:5]:
                                    report_parts.append(f"  {cl}")

                        # 螢幕觀察
                        screen_summary = screen_watcher.get_observation_summary()
                        if screen_summary:
                            obs_lines = [l.strip() for l in screen_summary.split("\n") if l.strip() and not l.startswith("===")]
                            if obs_lines:
                                report_parts.append("\n👁️ 螢幕觀察：")
                                for ol in obs_lines[-3:]:
                                    if len(ol) > 80:
                                        ol = ol[:80] + "..."
                                    report_parts.append(f"  {ol}")

                        # 進化狀態
                        report_parts.append(f"\n🧬 {evo.title}（觀察: {evo.total_observations:,}）")
                        if evo.evolution_direction:
                            report_parts.append(f"進化方向: {evo.evolution_direction}")

                        send_notification("\n".join(report_parts), category="idle_report")

                time.sleep(2)
                _consecutive_errors = 0  # 本次迴圈正常完成

              except Exception as e:
                _consecutive_errors += 1
                log.error(f"Daemon 迴圈錯誤 ({_consecutive_errors}/{_MAX_ERRORS}): {e}", exc_info=True)
                self.bridge.status_update.emit(f"⚠ 觀察循環錯誤: {e}")
                if _consecutive_errors >= _MAX_ERRORS:
                    log.error(f"連續錯誤達 {_MAX_ERRORS} 次，daemon 停止")
                    self.bridge.status_update.emit("✗ Daemon 因連續錯誤停止，請重啟 AI Slime")
                    break
                time.sleep(5)  # 錯誤後多等一下再重試

            # Daemon 結束，清理資源
            try:
                watcher.stop()
            except Exception:
                pass
            try:
                input_tracker.stop()
            except Exception:
                pass

        self.daemon_thread = threading.Thread(target=_run, daemon=True)
        self.daemon_thread.start()

    def _show_advice(self, emoji: str, message: str):
        """在 GUI 內顯示 AI Slime 的建議（點擊可關閉）。"""
        self.advice_banner.setText(f"{emoji} {message}　　<span style='color:#888;'>（點擊關閉）</span>")
        self.advice_banner.setVisible(True)
        # 60 秒後自動隱藏
        QTimer.singleShot(60000, lambda: self.advice_banner.setVisible(False))

    def _retranslate_all(self):
        theme = get_theme_info()
        accent = theme["accent"]
        self.title_label.setText(f"<b style='color:{accent}; font-size:18px;'>AI Slime Agent</b>"
                                 f"  <span style='color:#666;'>{t('app_subtitle')}</span>")
        self.tabs.setTabText(0, t("tab_home"))
        self.tabs.setTabText(1, t("tab_evolution"))
        self.tabs.setTabText(2, t("tab_equipment"))
        self.tabs.setTabText(3, t("tab_chat"))
        self.tabs.setTabText(4, t("tab_memory"))
        self.tabs.setTabText(5, t("tab_federation"))
        self.tabs.setTabText(6, t("tab_market"))
        self.tabs.setTabText(7, t("tab_approval"))
        self.tabs.setTabText(8, t("tab_settings"))
        self._refresh_approval_tab_label()
        self.approval_tab.retranslate()
        self.home_tab.retranslate()
        self.chat_tab.retranslate()
        self.memory_tab.retranslate()
        self.federation_tab.retranslate()
        self.evolution_tab.retranslate()
        self.equipment_tab.retranslate()
        self.settings_tab.retranslate()
        if self.daemon_running:
            self.daemon_label.setText(t("daemon_running"))
            self.toggle_btn.setText(t("btn_stop"))
        else:
            self.daemon_label.setText(t("daemon_stopped"))
            self.toggle_btn.setText(t("btn_start"))


# ─── Entry Point ─────────────────────────────────────────────────────────

def _is_first_launch() -> bool:
    """Check if this is the first launch (no settings file exists)."""
    settings_file = Path.home() / ".hermes" / "sentinel_settings.json"
    if not settings_file.exists():
        return True
    try:
        s = json.loads(settings_file.read_text(encoding="utf-8"))
        return not s.get("wizard_completed", False)
    except Exception:
        return True


def run_gui():
    app = QApplication(sys.argv)
    app.setStyleSheet(get_theme_style())
    app.setQuitOnLastWindowClosed(False)  # Keep running in tray

    if _is_first_launch():
        wizard = SetupWizard()
        # Block until wizard finishes, then launch main window
        def _on_wizard_done(settings):
            # Reload settings into config
            if settings.get("llm_providers"):
                config.LLM_PROVIDERS = settings["llm_providers"]
            if settings.get("telegram_bot_token"):
                config.TELEGRAM_BOT_TOKEN = settings["telegram_bot_token"]
            if settings.get("telegram_chat_id"):
                try:
                    config.TELEGRAM_CHAT_ID = int(settings["telegram_chat_id"])
                except (ValueError, TypeError):
                    pass
            window = MainWindow()
            window.show()
        wizard.finished.connect(_on_wizard_done)
        wizard.show()
    else:
        window = MainWindow()
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
