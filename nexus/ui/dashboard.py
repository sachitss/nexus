"""Home / dashboard page in the Nexus concept style."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QToolButton, QFrame,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QScrollArea)

from .. import __version__, POWERED_BY, BRAND, COPYRIGHT
from PySide6.QtGui import QPixmap

LOGO = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "brand-logo.png")
from .icons import icon
from .theme import Theme


class Tile(QToolButton):
    def __init__(self, title, subtitle, icon_name, slot):
        super().__init__()
        self.setObjectName("Tile")
        self.setText(f"{title}\n{subtitle}".replace("&", "&&"))
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setIcon(icon(icon_name, "#7fb2ff" if Theme.dark else "#2f6fd6", 40))
        self.setIconSize(QSize(30, 30))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setMinimumSize(250, 64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(slot)


class Dashboard(QWidget):
    openHistory = Signal(dict)

    def __init__(self, win):
        super().__init__()
        self.win = win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("DashBody")
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 22, 28, 22)
        v.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("Hero")
        hv = QVBoxLayout(hero)
        hv.setContentsMargins(22, 16, 22, 16)
        top = QHBoxLayout()
        name = QLabel("NEXUS")
        name.setObjectName("HeroName")
        badge = QLabel(f"v{__version__}")
        badge.setObjectName("HeroBadge")
        top.addWidget(name)
        top.addStretch()
        top.addWidget(badge)
        hv.addLayout(top)
        tag = QLabel("COMPARE · EDIT · VALIDATE")
        tag.setObjectName("HeroTag")
        sub = QLabel("Advanced File Intelligence & Engineering Data Platform")
        sub.setObjectName("HeroSub")
        hv.addWidget(tag)
        subrow = QHBoxLayout()
        subrow.addWidget(sub)
        subrow.addStretch()
        powered = QLabel(POWERED_BY)
        powered.setObjectName("HeroPowered")
        powered.setToolTip(f"{BRAND} · {COPYRIGHT}")
        subrow.addWidget(powered)
        hv.addLayout(subrow)
        line = QFrame()
        line.setObjectName("HeroLine")
        line.setFixedHeight(1)
        hv.addWidget(line)
        grid = QGridLayout()
        grid.setSpacing(8)
        main = [
            ("Folder Compare", "Directories, sync, renames", "folder-compare", lambda: win.compare_dialog(folders=True)),
            ("Text Editor", "Tabs, highlighting, regex", "editor", win.new_tab),
            ("Smart Diff", "Any two files, auto-detected", "smart-diff", win.compare_dialog),
            ("Excel / CSV", "Cells, keys, formulas", "table", lambda: win.compare_dialog(hint="table")),
            ("Word Compare", "Text, tables, formatting", "word", lambda: win.compare_dialog(hint="word")),
            ("VDI 3805", "Records, structure, validation", "vdi", lambda: win.compare_dialog(hint="vdi")),
        ]
        for k, (t, s, ic, slot) in enumerate(main):
            grid.addWidget(Tile(t, s, ic, slot), k // 3, k % 3)
        hv.addLayout(grid)
        v.addWidget(hero)

        more = QLabel("More tools")
        more.setObjectName("SectionTitle")
        v.addWidget(more)
        g2 = QGridLayout()
        g2.setSpacing(8)
        tools = [
            ("XML / JSON", "Structure + XSD", "xml", lambda: win.compare_dialog(hint="xml")),
            ("Three-way merge", "Base, mine, theirs", "merge", win.open_merge),
            ("Binary", "Hex comparison", "binary", lambda: win.compare_dialog(hint="binary")),
            ("File Explorer", "Dual pane, list, rename", "explorer", win.open_explorer),
            ("Search & Replace", "Many folders, preview", "search", win.open_search),
            ("Validation & Reports", "XSD, VDI checks", "validate", win.open_validation),
            ("Automation", "Jobs, profiles, history", "automation", win.open_automation),
            ("Settings", "Theme, AI, backups", "settings", win.open_settings),
        ]
        for k, (t, s, ic, slot) in enumerate(tools):
            g2.addWidget(Tile(t, s, ic, slot), k // 4, k % 4)
        v.addLayout(g2)

        rec = QLabel("Recent comparisons")
        rec.setObjectName("SectionTitle")
        v.addWidget(rec)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["When", "Kind", "Left / right", "Result"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(180)
        self.table.itemDoubleClicked.connect(self._open)
        v.addWidget(self.table, 1)
        hint = QLabel("Drop one file to edit it, or two files/folders to compare them. Everything runs locally; "
                      "nothing is uploaded unless you use the optional AI summary.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        # publisher footer: logo on a white card (the logo has dark lettering), "Powered by ..."
        foot = QHBoxLayout()
        foot.setSpacing(12)
        logo = QLabel()
        logo.setObjectName("BrandLogo")
        pm = QPixmap(LOGO)
        if not pm.isNull():
            logo.setPixmap(pm.scaledToHeight(54, Qt.TransformationMode.SmoothTransformation))
        logo.setToolTip(BRAND)
        foot.addWidget(logo)
        txt = QLabel(f"<b>{POWERED_BY}</b><br>{BRAND} · {COPYRIGHT.split(' · ')[0]}")
        txt.setObjectName("PoweredBy")
        foot.addWidget(txt)
        foot.addStretch()
        v.addLayout(foot)
        self.apply_style()
        self.refresh()

    def apply_style(self):
        if Theme.dark:
            qss = """
#DashBody { background: #0f1a2e; }
#Hero { background: #17233b; border-radius: 12px; }
#HeroName { color: #ffffff; font-size: 22pt; font-weight: 800; }
#HeroBadge { color: #34d17a; background: #123a2a; border-radius: 9px; padding: 2px 10px; }
#HeroTag { color: #ffffff; font-size: 14pt; font-weight: 700; }
#HeroSub { color: #c5d1e6; font-size: 10.5pt; }
#HeroPowered { color: #7fb2ff; font-size: 9.5pt; font-weight: 600; }
#HeroLine { background: #2e4066; }
#Tile { background: #1c2b4a; color: #ffffff; border: 1px solid #263a62; border-radius: 8px; padding: 8px 12px;
        text-align: left; font-weight: 600; }
#Tile:hover { background: #24375e; border-color: #4c8dff; }
#SectionTitle { color: #c5d1e6; font-size: 11pt; font-weight: 700; }
#Hint { color: #7d8db0; }
#BrandLogo { background: #ffffff; border-radius: 8px; padding: 4px 8px; }
#PoweredBy { color: #c5d1e6; }
"""
        else:
            qss = """
#DashBody { background: #f2f5fb; }
#Hero { background: #17233b; border-radius: 12px; }
#HeroName { color: #ffffff; font-size: 22pt; font-weight: 800; }
#HeroBadge { color: #34d17a; background: #123a2a; border-radius: 9px; padding: 2px 10px; }
#HeroTag { color: #ffffff; font-size: 14pt; font-weight: 700; }
#HeroSub { color: #c5d1e6; font-size: 10.5pt; }
#HeroPowered { color: #7fb2ff; font-size: 9.5pt; font-weight: 600; }
#HeroLine { background: #2e4066; }
#Hero #Tile { background: #1c2b4a; color: #ffffff; border: 1px solid #263a62; }
#Tile { background: #ffffff; color: #17233b; border: 1px solid #d5ddeb; border-radius: 8px; padding: 8px 12px;
        text-align: left; font-weight: 600; }
#Tile:hover { border-color: #2f6fd6; }
#SectionTitle { color: #17233b; font-size: 11pt; font-weight: 700; }
#Hint { color: #6a7a96; }
#BrandLogo { background: #ffffff; border: 1px solid #d5ddeb; border-radius: 8px; padding: 4px 8px; }
#PoweredBy { color: #17233b; }
"""
        self.setStyleSheet(qss)

    def refresh(self):
        try:
            rows = self.win.history.recent(30)
        except Exception:  # noqa: BLE001
            rows = []
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["time"].replace("T", " "), r["kind"],
                    f"{os.path.basename(r['left'] or '')}  ↔  {os.path.basename(r['right'] or '')}", r["summary"] or ""]
            for k, val in enumerate(vals):
                it = QTableWidgetItem(val)
                it.setData(Qt.ItemDataRole.UserRole, r)
                it.setToolTip(f"{r['left']}\n{r['right']}")
                self.table.setItem(i, k, it)
        self.table.resizeColumnsToContents()

    def _open(self, item):
        self.openHistory.emit(item.data(Qt.ItemDataRole.UserRole))

    def title(self):
        return "Home"

    def has_unsaved(self):
        return False
