"""Theme System - Multiple color schemes for AI Slime's GUI.

Each theme changes the overall look. Users can switch in Settings.
Themes are inspired by isekai/anime aesthetics.
"""


def _build_style(bg1: str, bg2: str, bg3: str, text: str, accent: str,
                  accent_hover: str, accent_press: str, dim: str,
                  border: str, danger: str, success: str, warn: str) -> str:
    """Build a full QSS stylesheet from color parameters.

    Phase L2 rewrite: align with sentinel/ui/tokens.py — bigger radii,
    pill buttons, slim scrollbars, cleaner tab bar (no card edges,
    underline-only active indicator), focus-state borders, better
    tooltip + groupbox styling.

    Per-theme parameters still control color so the user's chosen
    palette (slime_blue / tempest_purple / etc.) keeps working — only
    structure improved.
    """
    return f"""
/* ── Base ───────────────────────────────────────── */
QMainWindow, QWidget {{
    background-color: {bg1};
    color: {text};
    font-family: "Segoe UI", "Microsoft JhengHei", sans-serif;
    font-size: 13px;
}}
QToolTip {{
    background-color: {bg3};
    color: {text};
    border: 1px solid {border};
    padding: 6px 10px;
    border-radius: 6px;
}}

/* ── Tab bar (top-level navigation) ────────────────
 * Underline-only active indicator + softer hover.
 * No more boxy "tab cards"; nav reads as a clean menu.
 */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {border};
    background-color: {bg1};
    margin-top: -1px;
}}
QTabBar {{
    qproperty-drawBase: 0;
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {dim};
    padding: 10px 18px;
    margin: 0 2px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 13px;
}}
QTabBar::tab:hover {{
    color: {text};
}}
QTabBar::tab:selected {{
    color: {accent};
    border-bottom: 2px solid {accent};
    font-weight: 600;
}}

/* ── Text inputs ────────────────────────────────── */
QTextEdit, QPlainTextEdit {{
    background-color: {bg3};
    color: {text};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 10px 12px;
    font-family: "Segoe UI", "Microsoft JhengHei", sans-serif;
    font-size: 13px;
    selection-background-color: {accent};
    selection-color: {bg3};
}}
QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {accent};
}}
QLineEdit {{
    background-color: {bg3};
    color: {text};
    border: 1px solid {border};
    border-radius: 14px;
    padding: 8px 14px;
    font-size: 13px;
    selection-background-color: {accent};
    selection-color: {bg3};
}}
QLineEdit:focus {{
    border-color: {accent};
}}
QSpinBox, QComboBox {{
    background-color: {bg3};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    min-height: 18px;
}}
QSpinBox:focus, QComboBox:focus {{
    border-color: {accent};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {bg2};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {accent};
    selection-color: {bg3};
    padding: 4px;
}}

/* ── Buttons ────────────────────────────────────── */
QPushButton {{
    background-color: {accent};
    color: {bg3};
    border: none;
    border-radius: 14px;
    padding: 7px 16px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {accent_hover};
}}
QPushButton:pressed {{
    background-color: {accent_press};
}}
QPushButton:disabled {{
    background-color: {border};
    color: {dim};
}}
QPushButton#stopBtn {{
    background-color: {danger};
    color: white;
}}
QPushButton#stopBtn:hover {{
    background-color: #ff6b81;
}}

/* ── Group box ──────────────────────────────────── */
QGroupBox {{
    border: 1px solid {border};
    border-radius: 8px;
    margin-top: 16px;
    padding: 18px 14px 14px 14px;
    font-weight: 600;
    color: {accent};
    font-size: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    background-color: {bg1};
}}

/* ── Scrollbars (slim, unobtrusive) ─────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {dim};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {border};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {dim};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Lists / labels ─────────────────────────────── */
QListWidget {{
    background-color: {bg3};
    color: {text};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-radius: 4px;
}}
QListWidget::item:selected {{
    background-color: {accent};
    color: {bg3};
}}
QListWidget::item:hover {{
    background-color: {bg2};
}}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {border};
}}
QProgressBar {{
    border: 1px solid {border};
    border-radius: 4px;
    text-align: center;
    color: white;
    background-color: {bg3};
    height: 20px;
}}
QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 3px;
}}
QLabel#statusOk {{
    color: {success};
    font-weight: bold;
}}
QLabel#statusWarn {{
    color: {warn};
    font-weight: bold;
}}
QLabel#statusCrit {{
    color: {danger};
    font-weight: bold;
}}
QComboBox {{
    background-color: {bg3};
    color: {text};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 6px;
}}
QSpinBox {{
    background-color: {bg3};
    color: {text};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 6px;
}}
QScrollArea {{
    border: none;
}}
QMenu {{
    background-color: {bg1};
    color: {text};
    border: 1px solid {border};
}}
QMenu::item:selected {{
    background-color: {accent};
    color: {bg3};
}}
"""


# ─── Theme Definitions ──────────────────────────────────────────────────

THEMES = {
    "slime_blue": {
        "name": "史萊姆之藍",
        "en_name": "Slime Blue",
        "style": _build_style(
            bg1="#1a1a2e", bg2="#16213e", bg3="#0f0f23",
            text="#e0e0e0", accent="#00dcff", accent_hover="#33e5ff", accent_press="#00b8d4",
            dim="#888", border="#2a2a4a", danger="#ff4757", success="#2ed573", warn="#ffa502",
        ),
        "header_bg": "#0f0f23",
        "accent": "#00dcff",
    },
    "tempest_purple": {
        "name": "暴風龍・紫",
        "en_name": "Tempest Purple",
        "style": _build_style(
            bg1="#1a1028", bg2="#241540", bg3="#120a20",
            text="#e0d8f0", accent="#b388ff", accent_hover="#ce9cff", accent_press="#9c6aff",
            dim="#887aaa", border="#3a2860", danger="#ff4081", success="#69f0ae", warn="#ffd740",
        ),
        "header_bg": "#120a20",
        "accent": "#b388ff",
    },
    "predator_dark": {
        "name": "捕食者・闇",
        "en_name": "Predator Dark",
        "style": _build_style(
            bg1="#0d0d0d", bg2="#1a1a1a", bg3="#0a0a0a",
            text="#c8c8c8", accent="#ff3d3d", accent_hover="#ff6b6b", accent_press="#cc3030",
            dim="#666", border="#333", danger="#ff1744", success="#00e676", warn="#ff9100",
        ),
        "header_bg": "#0a0a0a",
        "accent": "#ff3d3d",
    },
    "great_sage_green": {
        "name": "大賢者・翠",
        "en_name": "Great Sage Green",
        "style": _build_style(
            bg1="#0a1a14", bg2="#0e2a1e", bg3="#06120c",
            text="#d0f0e0", accent="#00e676", accent_hover="#33ff99", accent_press="#00c860",
            dim="#5a8a6a", border="#1a4030", danger="#ff5252", success="#69f0ae", warn="#ffab40",
        ),
        "header_bg": "#06120c",
        "accent": "#00e676",
    },
    "demon_lord_crimson": {
        "name": "魔王・紅蓮",
        "en_name": "Demon Lord Crimson",
        "style": _build_style(
            bg1="#1a0a0e", bg2="#2a1018", bg3="#12060a",
            text="#f0d0d8", accent="#ff6090", accent_hover="#ff80a0", accent_press="#e04070",
            dim="#aa6070", border="#4a1828", danger="#ff1744", success="#76ff03", warn="#ffc400",
        ),
        "header_bg": "#12060a",
        "accent": "#ff6090",
    },
    "ultimate_gold": {
        "name": "究極・黃金",
        "en_name": "Ultimate Gold",
        "style": _build_style(
            bg1="#1a1508", bg2="#2a2210", bg3="#120e04",
            text="#f0e8d0", accent="#ffd700", accent_hover="#ffe033", accent_press="#ccac00",
            dim="#aa9060", border="#4a3820", danger="#ff5252", success="#00e676", warn="#ff9100",
        ),
        "header_bg": "#120e04",
        "accent": "#ffd700",
    },
}

DEFAULT_THEME = "slime_blue"

_current_theme = DEFAULT_THEME


def set_theme(theme_id: str):
    global _current_theme
    if theme_id in THEMES:
        _current_theme = theme_id


def get_theme() -> str:
    return _current_theme


def get_theme_style() -> str:
    return THEMES[_current_theme]["style"]


def get_theme_info() -> dict:
    return THEMES[_current_theme]


def list_themes() -> list[tuple[str, str]]:
    """Return [(id, display_name), ...]"""
    return [(tid, t["name"]) for tid, t in THEMES.items()]
