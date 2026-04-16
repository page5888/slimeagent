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

class ChatTab(QWidget):
    def __init__(self, bridge: SignalBridge):
        super().__init__()
        self.bridge = bridge
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # Chat display
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        layout.addWidget(self.chat_display, stretch=1)

        # Input area
        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText(t("chat_placeholder"))
        self.input_field.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.input_field, stretch=1)

        self.send_btn = QPushButton(t("chat_send"))
        self.send_btn.clicked.connect(self.send_message)
        input_layout.addWidget(self.send_btn)

        layout.addLayout(input_layout)

        # Connect response signal
        self.bridge.chat_response.connect(self._on_response)

        self._append_system("AI Slime 已就緒。你可以開始對話。")

    def _append_system(self, text: str):
        self.chat_display.append(f'<p style="color:#888;"><i>{text}</i></p>')

    def _append_user(self, text: str):
        self.chat_display.append(
            f'<p style="color:#00dcff;"><b>You:</b> {text}</p>'
        )

    def _show_thinking(self):
        self.chat_display.append(
            '<p id="thinking" style="color:#ffa502;"><i>大賢者分析中...</i></p>'
        )
        self.chat_display.moveCursor(QTextCursor.End)

    def _remove_thinking(self):
        html = self.chat_display.toHtml()
        html = html.replace('<p id="thinking" style="color:#ffa502;"><i>大賢者分析中...</i></p>', '')
        self.chat_display.setHtml(html)
        self.chat_display.moveCursor(QTextCursor.End)

    def _append_bot(self, text: str):
        self._remove_thinking()
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace("\n", "<br>")
        self.chat_display.append(
            f'<p style="color:#e0e0e0;"><b>AI Slime:</b> {text}</p>'
        )
        self.chat_display.moveCursor(QTextCursor.End)

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

    def retranslate(self):
        self.input_field.setPlaceholderText(t("chat_placeholder"))
        self.send_btn.setText(t("chat_send"))


# ─── Home Tab (首頁) ─────────────────────────────────────────────────────

class HomeTab(QWidget):
    """首頁：史萊姆狀態總覽 + 錢包連結。"""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 歡迎區 ──
        welcome = QLabel(
            "<b style='font-size:20px; color:#00dcff;'>AI Slime Agent</b><br>"
            "<span style='color:#aaa;'>你的轉生守護靈，正在觀察並學習中</span>"
        )
        welcome.setAlignment(Qt.AlignCenter)
        layout.addWidget(welcome)

        # ── 狀態卡片 ──
        cards = QHBoxLayout()
        cards.setSpacing(12)

        self.evo_card = self._make_card("🧬 進化", "載入中...")
        cards.addWidget(self.evo_card["frame"])

        self.obs_card = self._make_card("👁 觀察", "載入中...")
        cards.addWidget(self.obs_card["frame"])

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
            from sentinel.relay_client import AUTH_FILE
            if AUTH_FILE.exists():
                data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
                name = data.get("display_name", data.get("email", "?"))
                balance = data.get("balance", 0)
                self.wallet_status.setText(f"已登入：{name}")
                self.login_btn.setText("登出")
                if balance:
                    self.balance_label.setText(f"餘額：{balance:,} 點")
                else:
                    self.balance_label.setText("")
            else:
                self.wallet_status.setText("尚未登入")
                self.balance_label.setText("")
                self.login_btn.setText(t("wallet_login"))
        except Exception:
            self.wallet_status.setText("尚未登入")

    def _on_login(self):
        """Google OAuth login from home page."""
        from sentinel.relay_client import AUTH_FILE
        if AUTH_FILE.exists():
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
                auth_data = full_login_flow(client_id, relay_url)
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
        self.sub_tabs.addTab(self.vote_tab, "🗳 社群投票")
        self.sub_tabs.addTab(self.trade_tab, "💰 裝備交易")

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
        avatar_page = QWidget()
        avatar_layout = QVBoxLayout(avatar_page)

        from sentinel.slime_avatar import SlimeWidget
        self.slime_widget = SlimeWidget()
        self.slime_widget.setMinimumHeight(260)
        avatar_layout.addWidget(self.slime_widget)

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

        # Render slime widget onto card
        slime_pixmap = QPixmap(280, 280)
        slime_pixmap.fill(QColor(0, 0, 0, 0))
        self.slime_widget.render(slime_pixmap)

        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(100, 80, slime_pixmap)

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

        # Footer
        p.setPen(QColor(80, 80, 80))
        small_font = QFont("Segoe UI", 9)
        p.setFont(small_font)
        now = datetime.datetime.now().strftime("%Y/%m/%d")
        p.drawText(QRect(0, card_h - 35, card_w, 20), Qt.AlignCenter, f"AI Slime Agent  {now}")

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

        # 未來上架後改成官網 URL（不是 GitHub，一般人不知道 GitHub）
        SITE_URL = "https://slime.5888.tw"

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

        if platform == "x":
            webbrowser.open(f"https://x.com/intent/tweet?text={encoded}")
        elif platform == "facebook":
            # Facebook sharer 只支援 u 參數，文字由 OG meta 決定
            webbrowser.open(f"https://www.facebook.com/sharer/sharer.php?u={encoded_url}")
        elif platform == "reddit":
            webbrowser.open(
                f"https://www.reddit.com/submit?title={encoded_title}&url={encoded_url}&selftext=true&text={encoded}"
            )
        elif platform == "threads":
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


# ─── Settings Tab ────────────────────────────────────────────────────────

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

        self.apikey_input = QLineEdit(provider.get("api_key", ""))
        self.apikey_input.setEchoMode(QLineEdit.Password)
        self.apikey_input.setPlaceholderText("sk-... / AIza...")
        layout.addRow("金鑰", self.apikey_input)

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QVBoxLayout(inner)

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
        for tid, tname in list_themes():
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
        self.ollama_models_label.setStyleSheet("color: #888; font-size: 12px;")
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

        # Google Client ID (usually pre-filled, only admin changes this)
        self.gcid_input = QLineEdit(config.GOOGLE_CLIENT_ID)
        self.gcid_input.setPlaceholderText("xxxx.apps.googleusercontent.com")
        mode_layout.addWidget(QLabel("Google Client ID"))
        mode_layout.addWidget(self.gcid_input)

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

        # Telegram
        tg_group = QGroupBox(t("settings_telegram"))
        tg_layout = QFormLayout()
        self.token_input = QLineEdit(config.TELEGRAM_BOT_TOKEN)
        self.token_input.setEchoMode(QLineEdit.Password)
        tg_layout.addRow(t("settings_bot_token"), self.token_input)
        self.chatid_input = QLineEdit(str(config.TELEGRAM_CHAT_ID))
        tg_layout.addRow(t("settings_chat_id"), self.chatid_input)
        tg_group.setLayout(tg_layout)
        form.addWidget(tg_group)

        # LLM Providers (multi-provider with fallback)
        llm_label = QLabel("<b style='color:#00dcff;'>AI 模型提供者</b>"
                           "  <span style='color:#666;'>（由上到下依序嘗試，失敗自動換下一個）</span>")
        form.addWidget(llm_label)

        self.provider_rows = []
        for provider in config.LLM_PROVIDERS:
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

            # Pull
            result = subprocess.run(
                ["git", "pull", "origin", "main"],
                capture_output=True, text=True, timeout=60,
                cwd=str(Path(__file__).parent.parent),
            )

            if result.returncode != 0:
                QMessageBox.warning(
                    self, "更新失敗",
                    f"git pull 失敗：\n{result.stderr[:500]}",
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
        from sentinel.relay_client import AUTH_FILE
        logged_in = False
        if AUTH_FILE.exists():
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
        client_id = self.gcid_input.text().strip()
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
        """Save settings to a JSON config file."""
        # Build updated providers list
        updated_providers = []
        for row, original in zip(self.provider_rows, config.LLM_PROVIDERS):
            updated_providers.append(row.to_dict(original))

        settings = {
            "language": self.lang_combo.currentData(),
            "theme": self.theme_combo.currentData(),
            "user_mode": self.mode_combo.currentData(),
            "google_client_id": self.gcid_input.text().strip(),
            "chat_model_pref": self.chat_pref_combo.currentData(),
            "analysis_model_pref": self.analysis_pref_combo.currentData(),
            "telegram_bot_token": self.token_input.text(),
            "telegram_chat_id": self.chatid_input.text(),
            "llm_providers": updated_providers,
            "check_interval": self.interval_spin.value(),
            "idle_report_interval": self.idle_spin.value(),
            "watch_dirs": [
                d.strip() for d in self.dirs_input.toPlainText().split("\n") if d.strip()
            ],
        }

        settings_file = Path.home() / ".hermes" / "sentinel_settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

        # Apply to runtime config
        config.GOOGLE_CLIENT_ID = settings["google_client_id"]
        config.CHAT_MODEL_PREF = settings["chat_model_pref"]
        config.ANALYSIS_MODEL_PREF = settings["analysis_model_pref"]
        config.TELEGRAM_BOT_TOKEN = settings["telegram_bot_token"]
        config.TELEGRAM_CHAT_ID = int(settings["telegram_chat_id"])
        config.LLM_PROVIDERS = updated_providers
        config.SYSTEM_CHECK_INTERVAL = settings["check_interval"]
        config.IDLE_REPORT_INTERVAL = settings["idle_report_interval"]
        config.WATCH_DIRS = [Path(d) for d in settings["watch_dirs"]]

        # Handle autostart
        from sentinel.autostart import enable_autostart, disable_autostart
        if self.autostart_combo.currentData():
            enable_autostart()
        else:
            disable_autostart()

        QMessageBox.information(self, "AI Slime", t("settings_saved"))

    def retranslate(self):
        self.save_btn.setText(t("settings_save"))


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


# ─── Main Window ─────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.bridge = SignalBridge()

        self.setWindowTitle("AI Slime Agent")
        self.setMinimumSize(700, 600)
        self.setWindowIcon(create_icon())

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
        self.evolution_tab = EvolutionTab()
        self.equipment_tab = EquipmentTab()
        self.market_tab = MarketTab()
        self.settings_tab = SettingsTab()

        # 裝備變更時刷新形象
        self.equipment_tab.equipment_changed.connect(self.evolution_tab.refresh)

        self.tabs.addTab(self.home_tab, t("tab_home"))
        self.tabs.addTab(self.evolution_tab, t("tab_evolution"))
        self.tabs.addTab(self.equipment_tab, t("tab_equipment"))
        self.tabs.addTab(self.chat_tab, t("tab_chat"))
        self.tabs.addTab(self.memory_tab, t("tab_memory"))
        self.tabs.addTab(self.market_tab, t("tab_market"))
        self.tabs.addTab(self.settings_tab, t("tab_settings"))

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
        status_panel = QWidget()
        status_panel.setStyleSheet(
            "background-color: rgba(0,0,0,0.3); border-top: 1px solid #333;"
        )
        sp_layout = QVBoxLayout(status_panel)
        sp_layout.setContentsMargins(12, 4, 12, 4)
        sp_layout.setSpacing(2)

        self.status_bar = QLabel("○ 等待甦醒...")
        self.status_bar.setStyleSheet("color: #888; font-size: 11px;")
        sp_layout.addWidget(self.status_bar)

        self.sensor_bar = QLabel("")
        self.sensor_bar.setStyleSheet("color: #666; font-size: 10px;")
        sp_layout.addWidget(self.sensor_bar)

        main_layout.addWidget(status_panel)

        # 狀態列即時更新
        self.bridge.status_update.connect(self.status_bar.setText)
        self.bridge.sensor_update.connect(self.sensor_bar.setText)

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
        self.evo_timer.timeout.connect(self.home_tab.refresh)
        self.evo_timer.timeout.connect(self._sync_overlay_state)
        self.evo_timer.start(30000)  # 每 30 秒刷新進化、裝備、首頁

        # Initial overlay state sync
        QTimer.singleShot(1000, self._sync_overlay_state)

        # Daemon state - auto awaken on launch
        self.daemon_thread = None
        self.daemon_running = False
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
        settings_action.triggered.connect(lambda: (self._show_window(), self.tabs.setCurrentIndex(6)))
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
                    ctx = build_context(snapshot, file_events, claude_act, user_act)
                    if input_act:
                        ctx += "\n\n" + input_act

                    # 千里眼：隨機截圖觀察
                    screen_act = ""
                    if screen_watcher.should_capture():
                        screen_obs = screen_watcher.capture_and_learn()
                        if screen_obs:
                            screen_act = screen_obs["analysis"]
                            ctx += "\n\n=== 螢幕觀察（千里眼）===\n" + screen_act

                    # 更新底部狀態列（第一行：主狀態 + 倒數計時）
                    import datetime as _dt
                    _now_str = _dt.datetime.now().strftime("%H:%M:%S")
                    _next_sense = config.SYSTEM_CHECK_INTERVAL
                    _next_distill = max(0, int(300 - (now - last_distill)))
                    _next_report = max(0, int(config.IDLE_REPORT_INTERVAL - (now - last_idle)))
                    _buf_count = len(activity_buf)
                    _status = (
                        f"● 觀察中　|　{_now_str}　|　"
                        f"總觀察 {evo.total_observations:,}　|　"
                        f"學習 {evo.total_learnings}　|　"
                        f"緩衝 {_buf_count} 筆　|　"
                        f"感知 {_next_sense}s　|　"
                        f"蒸餾 {_next_distill}s　|　"
                        f"報告 {_next_report}s"
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
                    # Feed activity to adaptive evolution
                    record_activity_affinities(evo, user_act + "\n" + (input_act or ""))
                    record_observation(evo, obs_count, sources=exp_sources)

                    # 裝備掉落：每 100 次觀察有機會
                    obs_since_drop += obs_count
                    if obs_since_drop >= 100:
                        obs_since_drop = 0
                        # Reload from disk to pick up GUI-side equip/unequip changes
                        equip_state = load_equipment()
                        drop = try_drop(equip_state, "observation_100")
                        if drop:
                            drop_msg = (f"🎁 *裝備掉落！*\n"
                                        f"[{drop['rarity_zh']}] {drop['name']}\n"
                                        f"部位：{drop['slot_zh']}\n"
                                        f"{drop['desc']}")
                            send_notification(drop_msg, category="equipment")
                            self.bridge.status_update.emit(f"🎁 獲得裝備：{drop['name']}")

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

                    if now - last_distill >= 300 and activity_buf:
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
                            # Reload from disk to pick up GUI-side equip/unequip changes
                            equip_state = load_equipment()
                            drop = try_drop(equip_state, "learning")
                            if drop:
                                drop_msg = (f"🎁 *學習獎勵！*\n"
                                            f"[{drop['rarity_zh']}] {drop['name']}\n"
                                            f"{drop['desc']}")
                                send_notification(drop_msg, category="equipment")

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
        self.tabs.setTabText(5, t("tab_market"))
        self.tabs.setTabText(6, t("tab_settings"))
        self.home_tab.retranslate()
        self.chat_tab.retranslate()
        self.memory_tab.retranslate()
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
