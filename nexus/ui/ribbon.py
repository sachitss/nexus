"""A compact ribbon toolbar: tabs -> groups -> large and small buttons."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QTabWidget, QWidget, QHBoxLayout, QVBoxLayout, QToolButton, QLabel, QFrame,
                               QGridLayout, QSizePolicy)

from .icons import icon


class RibbonGroup(QFrame):
    def __init__(self, title: str, owner):
        super().__init__()
        self.owner = owner
        self.setObjectName("RibbonGroup")
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 2, 4, 0)
        v.setSpacing(0)
        self.row = QHBoxLayout()
        self.row.setSpacing(2)
        v.addLayout(self.row, 1)
        lab = QLabel(title)
        lab.setObjectName("RibbonGroupTitle")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(lab)
        self._small_grid = None
        self._small_count = 0

    def _button(self, b, a, menu):
        b.setDefaultAction(a)
        b.setAutoRaise(True)
        if menu is not None:
            a.setMenu(menu)
            b.setMenu(menu)
            b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup if a.property("menu_only")
                           else QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        b.customContextMenuRequested.connect(lambda pos, b=b, a=a: self.owner.button_menu(b, a, pos))

    def _action(self, text, icon_name, slot, shortcut=None, checkable=False, checked=False, tip=None):
        a = QAction(text.replace("&", "&&"), self.owner.window_ref)
        if icon_name:
            a.setIcon(icon(icon_name, self.owner.icon_color()))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setToolTip((tip or text) + (f"  ({shortcut})" if shortcut else ""))
        if slot is None:
            a.setProperty("menu_only", True)
        elif checkable:
            a.setCheckable(True)
            a.setChecked(checked)
            a.toggled.connect(slot)
        else:
            a.triggered.connect(lambda _=False: slot())
        self.owner.window_ref.addAction(a)   # shortcuts work everywhere
        a.setData(icon_name)
        self.owner.all_actions.append(a)
        return a

    def large(self, text, icon_name, slot, menu=None, **kw) -> QAction:
        a = self._action(text, icon_name, slot, **kw)
        b = QToolButton()
        self._button(b, a, menu)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        b.setIconSize(QSize(26, 26))
        b.setMinimumWidth(58)
        b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.row.addWidget(b)
        self._small_grid = None
        return a

    def small(self, text, icon_name, slot, menu=None, **kw) -> QAction:
        a = self._action(text, icon_name, slot, **kw)
        if self._small_grid is None or self._small_count >= 3:
            w = QWidget()
            self._small_grid = QVBoxLayout(w)
            self._small_grid.setContentsMargins(0, 0, 0, 0)
            self._small_grid.setSpacing(0)
            self.row.addWidget(w)
            self._small_count = 0
        b = QToolButton()
        self._button(b, a, menu)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setIconSize(QSize(16, 16))
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._small_grid.addWidget(b)
        self._small_count += 1
        return a

    def widget(self, w):
        self.row.addWidget(w)
        self._small_grid = None


class RibbonTab(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.h = QHBoxLayout(self)
        self.h.setContentsMargins(2, 2, 2, 2)
        self.h.setSpacing(4)
        self.h.addStretch()

    def group(self, title) -> RibbonGroup:
        g = RibbonGroup(title, self.owner)
        self.h.insertWidget(self.h.count() - 1, g)
        return g


class Ribbon(QTabWidget):
    collapsedChanged = Signal(bool)
    addToQuickAccess = Signal(object)    # QAction

    def __init__(self, window):
        super().__init__()
        self.setObjectName("Ribbon")
        self.window_ref = window
        self.all_actions: list[QAction] = []
        self.collapsed = False
        self.setDocumentMode(True)
        self.tabBarDoubleClicked.connect(lambda _: self.set_collapsed(not self.collapsed))
        self.tabBarClicked.connect(self._clicked)
        self.setFixedHeight(118)

    def button_menu(self, button, action, pos):
        from PySide6.QtWidgets import QMenu
        m = QMenu(button)
        m.addAction("Add to Quick Access Toolbar", lambda: self.addToQuickAccess.emit(action))
        m.addAction("Collapse the ribbon" if not self.collapsed else "Expand the ribbon",
                    lambda: self.set_collapsed(not self.collapsed))
        m.exec(button.mapToGlobal(pos))

    def find_action(self, text):
        return next((a for a in self.all_actions if a.text() in (text, text.replace("&", "&&"))), None)

    def icon_color(self):
        from .theme import Theme
        return "#7fb2ff" if Theme.dark else "#2f6fd6"

    def tab(self, title) -> RibbonTab:
        t = RibbonTab(self)
        self.addTab(t, title)
        return t

    def set_collapsed(self, on):
        self.collapsed = on
        self.setFixedHeight(self.tabBar().sizeHint().height() + 2 if on else 118)
        self.collapsedChanged.emit(on)

    def _clicked(self, _i):
        if self.collapsed:
            self.set_collapsed(False)

    def retint(self):
        for a in self.all_actions:
            name = a.data()
            if name:
                a.setIcon(icon(name, self.icon_color()))
