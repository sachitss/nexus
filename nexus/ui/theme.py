"""Light and dark themes, diff colours and Pygments token colours."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

LIGHT = {
    "bg": "#ffffff", "fg": "#1e1e1e", "gutter_bg": "#f3f3f3", "gutter_fg": "#8a8a8a",
    "current_line": "#f5f8ff", "selection": "#cce0ff",
    "changed": "#fff1d6", "changed_hl": "#ffc266", "left": "#ffe3e3", "right": "#dff5df",
    "filler": "#eeeeee", "found": "#fff59d", "current_hunk": "#4a90e2",
}
DARK = {
    "bg": "#0f1a2e", "fg": "#dbe4f3", "gutter_bg": "#132038", "gutter_fg": "#5f7194",
    "current_line": "#17284a", "selection": "#264f78",
    "changed": "#3a3320", "changed_hl": "#8a6a1a", "left": "#4a2430", "right": "#1d3d2c",
    "filler": "#1a2436", "found": "#5c5a1d", "current_hunk": "#4a90e2",
}
# folder status colours requested in the specification
FOLDER_LIGHT = {"same": "#dff3e3", "different": "#fbdada", "modified": "#fff4c2", "new": "#dbe9ff",
                "excluded": "#ececec", "renamed": "#efe0ff",
                "conflict": "#ffd9b0"}
FOLDER_DARK = {"same": "#163a26", "different": "#4a1f27", "modified": "#4a4016", "new": "#173560",
               "excluded": "#262c38", "renamed": "#3a2856",
               "conflict": "#5a3412"}

TOKEN_COLORS_LIGHT = {
    "Keyword": ("#0033b3", True), "Name.Builtin": ("#7a3e9d", False), "Name.Function": ("#00627a", False),
    "Name.Class": ("#00627a", True), "Name.Decorator": ("#9e880d", False), "Name.Tag": ("#0033b3", False),
    "Name.Attribute": ("#871094", False), "String": ("#067d17", False), "Number": ("#1750eb", False),
    "Comment": ("#8c8c8c", False), "Operator": ("#444444", False), "Literal": ("#1750eb", False),
    "Generic.Heading": ("#0033b3", True), "Generic.Subheading": ("#0033b3", True),
    "Generic.Inserted": ("#067d17", False), "Generic.Deleted": ("#c0392b", False),
}
TOKEN_COLORS_DARK = {
    "Keyword": ("#cf8e6d", True), "Name.Builtin": ("#c77dbb", False), "Name.Function": ("#56a8f5", False),
    "Name.Class": ("#56a8f5", True), "Name.Decorator": ("#b3ae60", False), "Name.Tag": ("#d5b778", False),
    "Name.Attribute": ("#bababa", False), "String": ("#6aab73", False), "Number": ("#2aacb8", False),
    "Comment": ("#7a7e85", False), "Operator": ("#bcbec4", False), "Literal": ("#2aacb8", False),
    "Generic.Heading": ("#cf8e6d", True), "Generic.Subheading": ("#cf8e6d", True),
    "Generic.Inserted": ("#6aab73", False), "Generic.Deleted": ("#f75464", False),
}


class Theme:
    dark = False

    @classmethod
    def c(cls, key: str) -> QColor:
        return QColor((DARK if cls.dark else LIGHT)[key])

    @classmethod
    def folder(cls, key: str) -> QColor:
        return QColor((FOLDER_DARK if cls.dark else FOLDER_LIGHT)[key])

    @classmethod
    def token_colors(cls):
        return TOKEN_COLORS_DARK if cls.dark else TOKEN_COLORS_LIGHT


def mono_font(size: int = 10) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    for fam in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Menlo"):
        if fam in QFontDatabase.families():
            f = QFont(fam)
            break
    f.setPointSize(size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


RIBBON_QSS_DARK = """
#Ribbon::pane { border: none; background: #17233b; }
#Ribbon QTabBar::tab { background: transparent; color: #b9c6dd; padding: 5px 14px; border: none; }
#Ribbon QTabBar::tab:selected { color: #ffffff; background: #17233b; border-top: 2px solid #4c8dff; }
#Ribbon QTabBar::tab:hover { color: #ffffff; }
#Ribbon QToolButton { color: #dbe4f3; border-radius: 4px; padding: 2px 4px; }
#Ribbon QToolButton:hover { background: #22345a; }
#Ribbon QToolButton:checked { background: #2a4170; }
#RibbonGroup { border-right: 1px solid #2a3a5c; }
#RibbonGroupTitle { color: #7d8db0; font-size: 8pt; }
QTabWidget#Docs::pane { border-top: 1px solid #22345a; }
"""
RIBBON_QSS_LIGHT = """
#Ribbon::pane { border: none; background: #f4f7fc; border-bottom: 1px solid #d5ddeb; }
#Ribbon QTabBar::tab { background: transparent; color: #33435f; padding: 5px 14px; border: none; }
#Ribbon QTabBar::tab:selected { color: #0d2a5c; background: #f4f7fc; border-top: 2px solid #2f6fd6; }
#Ribbon QTabBar::tab:hover { color: #0d2a5c; }
#Ribbon QToolButton { color: #1d2a44; border-radius: 4px; padding: 2px 4px; }
#Ribbon QToolButton:hover { background: #e1e9f7; }
#Ribbon QToolButton:checked { background: #cfdcf5; }
#RibbonGroup { border-right: 1px solid #d5ddeb; }
#RibbonGroupTitle { color: #7383a0; font-size: 8pt; }
QTabWidget#Docs::pane { border-top: 1px solid #d5ddeb; }
"""

# theme modes stored in QSettings("Nexus", "Nexus") under "theme"
MODES = ("system", "light", "dark")
MODE_LABELS = {"system": "Follow Windows", "light": "Light", "dark": "Dark (navy)"}


def stored_mode(settings) -> str:
    """Theme mode from the settings; migrates the old boolean "dark" setting."""
    m = settings.value("theme", "", type=str)
    if m in MODES:
        return m
    return "dark" if settings.value("dark", True, type=bool) else "light"


def system_is_dark(app: QApplication | None = None) -> bool:
    """Windows "app mode" (Settings > Personalisation > Colours), via Qt 6.5+ style hints."""
    app = app or QApplication.instance()
    try:
        from PySide6.QtCore import Qt
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - older Qt or no platform support
        return False


def resolve(mode: str, app: QApplication | None = None) -> bool:
    """True when the mode results in the dark theme."""
    if mode == "system":
        return system_is_dark(app)
    return mode == "dark"


def _palette(colors: dict, disabled: str) -> QPalette:
    p = QPalette()
    roles = QPalette.ColorRole
    for role, c in colors.items():
        p.setColor(getattr(roles, role), QColor(c))
    for role in ("Text", "ButtonText", "WindowText"):
        p.setColor(QPalette.ColorGroup.Disabled, getattr(roles, role), QColor(disabled))
    return p


DARK_PALETTE = {
    "Window": "#14203a", "WindowText": "#dbe4f3", "Base": "#0f1a2e", "AlternateBase": "#17233b",
    "ToolTipBase": "#17233b", "ToolTipText": "#dbe4f3", "Text": "#dbe4f3", "Button": "#1c2b4a",
    "ButtonText": "#dbe4f3", "Highlight": "#2f65ca", "HighlightedText": "#ffffff", "PlaceholderText": "#6d7fa3",
    "Light": "#2a3a5c", "Midlight": "#22345a", "Mid": "#22345a", "Dark": "#0b1424", "Shadow": "#060c17",
    "Link": "#7fb2ff", "BrightText": "#ffffff",
}
# light environment: white work areas on a soft blue-grey frame, navy text, blue accent
LIGHT_PALETTE = {
    "Window": "#eef2f8", "WindowText": "#1d2a44", "Base": "#ffffff", "AlternateBase": "#f4f7fc",
    "ToolTipBase": "#ffffff", "ToolTipText": "#1d2a44", "Text": "#1d2a44", "Button": "#f7f9fd",
    "ButtonText": "#1d2a44", "Highlight": "#2f6fd6", "HighlightedText": "#ffffff", "PlaceholderText": "#8592aa",
    "Light": "#ffffff", "Midlight": "#e6ecf5", "Mid": "#c9d3e3", "Dark": "#a9b5c9", "Shadow": "#7d8aa3",
    "Link": "#2f6fd6", "BrightText": "#000000",
}


def apply(app: QApplication, dark: bool) -> None:
    Theme.dark = dark
    app.setStyle("Fusion")
    if dark:
        app.setPalette(_palette(DARK_PALETTE, "#55627d"))
        app.setStyleSheet(RIBBON_QSS_DARK)
    else:
        app.setPalette(_palette(LIGHT_PALETTE, "#a0aabb"))
        app.setStyleSheet(RIBBON_QSS_LIGHT)


def apply_mode(app: QApplication, mode: str) -> bool:
    """Apply "system" / "light" / "dark"; returns True when dark is active."""
    dark = resolve(mode, app)
    apply(app, dark)
    return dark
