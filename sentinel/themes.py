"""Theme System - Multiple color schemes for AI Slime's GUI.

Each theme changes the overall look. Users can switch in Settings.
Themes are inspired by isekai/anime aesthetics.
"""


def _build_style(bg1: str, bg2: str, bg3: str, text: str, accent: str,
                  accent_hover: str, accent_press: str, dim: str,
                  border: str, danger: str, success: str, warn: str) -> str:
    """Build a full QSS stylesheet from color parameters."""
    return f"""
QMainWindow, QWidget {{
    background-color: {bg1};
    color: {text};
    font-family: "Segoe UI", "Microsoft JhengHei", sans-serif;
    font-size: 13px;
}}
QTabWidget::pane {{
    border: 1px solid {border};
    background-color: {bg2};
    border-radius: 4px;
}}
QTabBar::tab {{
    background-color: {bg1};
    color: {dim};
    padding: 8px 20px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{
    background-color: {bg2};
    color: {accent};
    border-bottom: 2px solid {accent};
}}
QTextEdit, QPlainTextEdit {{
    background-color: {bg3};
    color: {text};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 8px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 13px;
}}
QLineEdit {{
    background-color: {bg3};
    color: {text};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 8px;
    font-size: 13px;
}}
QLineEdit:focus {{
    border: 1px solid {accent};
}}
QPushButton {{
    background-color: {accent};
    color: {bg3};
    border: none;
    border-radius: 4px;
    padding: 8px 16px;
    font-weight: bold;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {accent_hover};
}}
QPushButton:pressed {{
    background-color: {accent_press};
}}
QPushButton#stopBtn {{
    background-color: {danger};
    color: white;
}}
QPushButton#stopBtn:hover {{
    background-color: #ff6b81;
}}
QGroupBox {{
    border: 1px solid {border};
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: bold;
    color: {accent};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
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
