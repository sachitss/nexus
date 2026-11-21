"""Main window: ribbon, dashboard, tabbed editor and all comparison / tool views."""
from __future__ import annotations

import base64
import json
import os
import xml.dom.minidom

from PySide6.QtCore import Qt, QSettings, QFileSystemWatcher, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QGuiApplication, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QMainWindow, QTabWidget, QFileDialog, QMessageBox, QLabel, QComboBox, QDialog,
                               QDialogButtonBox, QGridLayout, QLineEdit, QPushButton, QInputDialog, QWidget,
                               QVBoxLayout, QDockWidget, QApplication, QMenu, QToolBar, QToolButton)

from pygments.lexers import get_all_lexers

from .. import __version__, APP_NAME
from ..core import loaders, automation
from ..core.ai import summarize_offline
from ..core.textio import EOL_NAMES
from . import theme
from .icons import icon, app_icon
from .ribbon import Ribbon
from .dashboard import Dashboard
from .editor import EditorTab, CodeEditor
from .findreplace import FindBar, FindInFilesPanel
from .textcompare import TextCompareView, Side
from .tablecompare import TableCompareView
from .foldercompare import FolderCompareView, MultiFolderView
from .engviews import VdiView, StructCompareView, BinaryCompareView, MergeView
from .toolviews import (ExplorerView, SearchView, WorkbookView, ValidationView, AutomationView, SettingsView)

ENCODINGS = [("UTF-8", "utf-8"), ("UTF-8 with BOM", "utf-8-sig"), ("ANSI / Windows-1252", "cp1252"),
             ("ISO-8859-1 (Latin-1)", "latin-1"), ("ISO-8859-15", "iso-8859-15"), ("UTF-16 LE", "utf-16-le"),
             ("UTF-16 BE", "utf-16-be"), ("OEM 850 (DOS)", "cp850")]

# compare kinds offered in the dialog: (label, kind)
KINDS = [("Auto-detect", None), ("Text / source code", "text"), ("Word document", "word"),
         ("Excel / CSV table (key columns)", "table"), ("Excel workbook (cells, formulas, formats)", "workbook"),
         ("XML / JSON structure", "xml"), ("VDI 3805", "vdi"), ("Binary (hex)", "binary"), ("Folders", "folder")]
HINT_INDEX = {k: i for i, (_, k) in enumerate(KINDS)}


class CompareDialog(QDialog):
    def __init__(self, parent=None, folders=False, hint=None, left="", right=""):
        super().__init__(parent)
        self.setWindowTitle("Compare folders" if folders else "Compare")
        self.resize(760, 160)
        g = QGridLayout(self)
        self.kind = QComboBox()
        for label, _ in KINDS:
            self.kind.addItem(label)
        self.kind.setCurrentIndex(HINT_INDEX.get("folder" if folders else hint, 0))
        g.addWidget(QLabel("Type:"), 0, 0)
        g.addWidget(self.kind, 0, 1, 1, 3)
        self.edits = []
        for i, label in enumerate(("Left:", "Right:"), start=1):
            le = QLineEdit(left if i == 1 else right)
            le.setPlaceholderText("file or folder (Word, Excel, CSV, XML, JSON, VDI 3805, text, binary…)")
            bf = QPushButton("File…")
            bd = QPushButton("Folder…")
            bf.clicked.connect(lambda _=False, le=le: self._pick(le, False))
            bd.clicked.connect(lambda _=False, le=le: self._pick(le, True))
            g.addWidget(QLabel(label), i, 0)
            g.addWidget(le, i, 1)
            g.addWidget(bf, i, 2)
            g.addWidget(bd, i, 3)
            self.edits.append(le)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        g.addWidget(bb, 3, 0, 1, 4)

    def _pick(self, le, folder):
        if folder:
            p = QFileDialog.getExistingDirectory(self, "Choose folder", le.text())
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Choose file", le.text())
        if p:
            le.setText(p)

    def paths(self):
        return [e.text().strip() for e in self.edits]

    def chosen_kind(self):
        return KINDS[self.kind.currentIndex()][1]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("Nexus", "Nexus")
        self.history = automation.History()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self.setAcceptDrops(True)
        self.word_wrap = False
        self.show_ws = False
        self._untitled = 0

        self.tabs = QTabWidget()
        self.tabs.setObjectName("Docs")
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)

        self.ribbon = Ribbon(self)
        self.ribbon.addToQuickAccess.connect(self._add_quick)
        self.qat = QToolBar("Quick access")
        self.qat.setObjectName("QuickAccess")
        self.qat.setIconSize(self.qat.iconSize() * 0.7)
        self.qat.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.qat.customContextMenuRequested.connect(self._qat_menu)

        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self.qat)
        cv.addWidget(self.ribbon)
        cv.addWidget(self.tabs, 1)
        self.findbar = FindBar(self.current_editor, self.all_editors)
        self.findbar.hide()
        cv.addWidget(self.findbar)
        self.setCentralWidget(central)

        self.fif = FindInFilesPanel()
        self.fif.openRequested.connect(lambda p, ln: self.open_file(p, ln))
        self.fif_dock = QDockWidget("Find in files", self)
        self.fif_dock.setObjectName("FindInFiles")
        self.fif_dock.setWidget(self.fif)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.fif_dock)
        self.fif_dock.hide()

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._file_changed_on_disk)
        self._build_status()
        self._build_ribbon()
        self._load_quick()
        self.dashboard = None
        self.open_home()
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        # auto-save of unsaved editor tabs into a recovery folder (never over the user's files)
        self.recovery_dir = os.path.join(automation.app_data_dir(), "recovery")
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.write_recovery)
        self._apply_settings()
        QTimer.singleShot(300, self._offer_recovery)

    # ================================================================ helpers
    def current(self):
        return self.tabs.currentWidget()

    def current_tab(self) -> EditorTab | None:
        w = self.current()
        return w if isinstance(w, EditorTab) else None

    def current_editor(self) -> CodeEditor | None:
        w = self.current()
        if isinstance(w, EditorTab):
            if w.second is not None and w.second.hasFocus():
                return w.second
            return w.editor
        if isinstance(w, TextCompareView):
            return next((e for e in w.eds if e.hasFocus()), w.eds[0])
        return None

    def editor_tabs(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count()) if isinstance(self.tabs.widget(i), EditorTab)]

    def all_editors(self):
        return [t.editor for t in self.editor_tabs()]

    def _add(self, w, focus=True):
        i = self.tabs.addTab(w, w.title().replace("&", "&&"))
        self.tabs.setTabToolTip(i, getattr(w, "path", None) or w.title())
        if hasattr(w, "titleChanged"):
            w.titleChanged.connect(lambda w=w: self._retitle(w))
        if isinstance(w, EditorTab):
            ed = w.editor
            ed.cursorPositionChanged.connect(self._update_status)
            ed.set_wrap(self.word_wrap)
            ed.set_show_whitespace(self.show_ws)
            f = ed.font()
            f.setPointSize(self.settings.value("font_size", 10, type=int))
            ed.setFont(f)
            if w.path:
                self.watcher.addPath(w.path)
                self._add_recent(w.path)
        for sig, slot in (("openPair", self.open_compare), ("openFile", self.open_file), ("openTable", self._open_table),
                          ("compared", self._record)):
            if hasattr(w, sig):
                getattr(w, sig).connect(slot)
        if isinstance(w, FolderCompareView):
            w.compared.disconnect(self._record)
            w.compared.connect(lambda l, r, s: self._record("folder", l, r, ", ".join(f"{v} {k}" for k, v in sorted(s.items())),
                                                            set(s) <= {"same", "excluded"}))
        if focus:
            self.tabs.setCurrentWidget(w)
        return w

    def _retitle(self, w):
        i = self.tabs.indexOf(w)
        if i >= 0:
            self.tabs.setTabText(i, w.title().replace("&", "&&"))
            p = getattr(w, "path", None)
            self.tabs.setTabToolTip(i, p or w.title())
        self._update_status()

    def _record(self, kind, left, right, summary, identical):
        try:
            self.history.add(kind, left, right, {}, summary, "", identical)
        except Exception:  # noqa: BLE001 - history is best effort
            return
        if self.dashboard is not None:
            self.dashboard.refresh()

    def _ed(self, fn):
        def run():
            ed = self.current_editor()
            if ed is not None and not ed.isReadOnly():
                fn(ed)
        return run

    def _any_ed(self, fn):
        def run():
            ed = self.current_editor()
            if ed is not None:
                fn(ed)
        return run

    def _cv(self, name, *args):
        """Call a method on the current comparison view, if it has it."""
        def run():
            w = self.current()
            f = getattr(w, name, None)
            if callable(f):
                f(*args)
        return run

    # ================================================================ ribbon
    def _menu(self, items):
        m = QMenu(self)
        for it in items:
            if it is None:
                m.addSeparator()
            else:
                text, slot = it[0], it[1]
                a = m.addAction(text, slot)
                if len(it) > 2:
                    a.setShortcut(QKeySequence(it[2]))
        return m

    def _build_ribbon(self):
        r = self.ribbon
        # ---------------------------------------------------------- Home
        t = r.tab("Home")
        g = t.group("Start")
        g.large("Dashboard", "home", self.open_home, tip="Show the Nexus dashboard")
        g = t.group("File")
        g.large("New", "new", self.new_tab, shortcut="Ctrl+N")
        self.recent_menu = QMenu(self)
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        g.large("Open", "open", self.open_dialog, shortcut="Ctrl+O", menu=self.recent_menu)
        g.small("Save", "save", self.save_current, shortcut="Ctrl+S")
        g.small("Save as…", "save", self.save_as, shortcut="Ctrl+Shift+S")
        g.small("Save all", "save-all", self.save_all, shortcut="Ctrl+Alt+S")
        g.small("Reload", "refresh", self.reload_current, shortcut="Ctrl+R")
        g.small("Close tab", "prev", lambda: self.close_tab(self.tabs.currentIndex()), shortcut="Ctrl+W")
        g.small("Close all", "prev", self.close_all)
        g = t.group("Compare")
        g.large("Smart compare", "smart-diff", self.compare_dialog, shortcut="Ctrl+K",
                tip="Compare two files; the right comparison is chosen from the file type")
        g.large("Folders", "folder-compare", lambda: self.compare_dialog(folders=True), shortcut="Ctrl+Alt+K")
        g.large("Three-way merge", "merge", self.open_merge)
        g = t.group("Edit")
        g.small("Undo", "undo", self._ed(lambda ed: ed.undo()), shortcut="Ctrl+Z")
        g.small("Redo", "redo", self._ed(lambda ed: ed.redo()), shortcut="Ctrl+Y")
        g.small("Find", "search", lambda: self.findbar.show_find(False), shortcut="Ctrl+F")
        g.small("Replace", "replace", lambda: self.findbar.show_find(True), shortcut="Ctrl+H")
        g.small("Go to line", "goto", self.goto_line, shortcut="Ctrl+G")
        g.small("Find in files", "find-files", self.show_find_in_files, shortcut="Ctrl+Shift+F")
        g = t.group("Tools")
        g.large("Explorer", "explorer", self.open_explorer)
        g.large("Search & Replace", "search", self.open_search)
        g.large("Validate", "validate", self.open_validation)

        # ---------------------------------------------------------- Compare
        t = r.tab("Compare")
        g = t.group("New comparison")
        g.large("Smart", "smart-diff", self.compare_dialog)
        g.large("Folders", "folder-compare", lambda: self.compare_dialog(folders=True))
        g.large("N-way folders", "structure", self.open_nway)
        g.small("Text / code", "editor", lambda: self.compare_dialog(hint="text"))
        g.small("Word", "word", lambda: self.compare_dialog(hint="word"))
        g.small("Excel / CSV table", "table", lambda: self.compare_dialog(hint="table"))
        g.small("Excel workbook", "table", lambda: self.compare_dialog(hint="workbook"))
        g.small("XML / JSON", "xml", lambda: self.compare_dialog(hint="xml"))
        g.small("Binary (hex)", "binary", lambda: self.compare_dialog(hint="binary"))
        g.large("Three-way merge", "merge", self.open_merge)
        g = t.group("Current document")
        g.small("With another tab…", "duplicate", self.compare_with_tab, shortcut="Ctrl+Alt+C")
        g.small("With saved file", "save", self.compare_with_disk)
        g.small("With clipboard", "copy-right", self.compare_with_clipboard)
        g.small("Empty text comparison", "split", self.new_text_compare)
        g.small("Open sides in editor", "editor", self.open_compare_sides)
        g = t.group("Differences")
        g.large("Previous", "prev", lambda: (self._cv("goto_hunk", -1)(), self._cv("goto_diff", -1)()))
        g.large("Next", "next", lambda: (self._cv("goto_hunk", 1)(), self._cv("goto_diff", 1)()))
        g.small("Copy to right", "copy-right", self._cv("copy_hunk", 0))
        g.small("Copy to left", "copy-left", self._cv("copy_hunk", 1))
        g.small("Only differences", "filter", self._cv("toggle_only_diffs"))
        g = t.group("Results")
        g.large("Recompare", "refresh", self.recompare)
        g.small("Change summary", "ai", self.summarize_current, tip="Plain-language change summary (offline; optional AI)")
        g.small("Export report", "report", self._cv("export_report"))
        g.small("Synchronise folders", "sync", self._cv("sync"))

        # ---------------------------------------------------------- Edit
        t = r.tab("Edit")
        g = t.group("Clipboard")
        g.small("Cut", "copy-left", self._ed(lambda ed: ed.cut()), shortcut="Ctrl+X")
        g.small("Copy", "duplicate", self._any_ed(lambda ed: ed.copy()), shortcut="Ctrl+C")
        g.small("Paste", "copy-right", self._ed(lambda ed: ed.paste()), shortcut="Ctrl+V")
        g = t.group("Lines")
        g.small("Duplicate", "duplicate", self._ed(lambda ed: ed.duplicate_line()), shortcut="Ctrl+D")
        g.small("Delete", "prev", self._ed(lambda ed: ed.delete_line()), shortcut="Ctrl+Shift+K")
        g.small("Move up", "prev", self._ed(lambda ed: ed.move_lines(-1)))
        g.small("Move down", "next", self._ed(lambda ed: ed.move_lines(1)))
        g.large("Sort / clean", "list", None, menu=self._menu([
            ("Sort ascending", self._ed(lambda ed: ed.transform_lines(sorted))),
            ("Sort descending", self._ed(lambda ed: ed.transform_lines(lambda l: sorted(l, reverse=True)))),
            ("Sort (case-insensitive)", self._ed(lambda ed: ed.transform_lines(lambda l: sorted(l, key=str.lower)))),
            ("Remove duplicate lines", self._ed(lambda ed: ed.transform_lines(lambda l: list(dict.fromkeys(l))))),
            ("Remove empty lines", self._ed(lambda ed: ed.transform_lines(lambda l: [x for x in l if x.strip()] or [""]))),
            ("Trim trailing whitespace", self._ed(lambda ed: ed.transform_lines(lambda l: [x.rstrip() for x in l]))),
            ("Reverse line order", self._ed(lambda ed: ed.transform_lines(lambda l: l[::-1]))),
            None,
            ("Tabs to spaces", self._ed(lambda ed: ed.transform_lines(lambda l: [x.expandtabs(4) for x in l]))),
            ("Leading spaces to tabs", self._ed(lambda ed: ed.transform_lines(_spaces_to_tabs))),
        ]))
        g = t.group("Text")
        g.small("Toggle comment", "editor", self._ed(lambda ed: ed.toggle_comment()), shortcut="Ctrl+/")
        g.small("Indent", "next", self._ed(lambda ed: ed.indent()), shortcut="Ctrl+]")
        g.small("Unindent", "prev", self._ed(lambda ed: ed.indent(back=True)), shortcut="Ctrl+[")
        g.large("Case", "rename", None, menu=self._menu([
            ("UPPERCASE", self._ed(lambda ed: ed.transform_selection(str.upper)), "Ctrl+Shift+U"),
            ("lowercase", self._ed(lambda ed: ed.transform_selection(str.lower)), "Ctrl+U"),
            ("Title Case", self._ed(lambda ed: ed.transform_selection(str.title))),
            ("iNVERT cASE", self._ed(lambda ed: ed.transform_selection(str.swapcase))),
        ]))
        g.large("Format", "structure", None, menu=self._menu([
            ("Format JSON", self._ed(lambda ed: self._whole(ed, lambda t: json.dumps(json.loads(t), indent=4, ensure_ascii=False)))),
            ("Minify JSON", self._ed(lambda ed: self._whole(ed, lambda t: json.dumps(json.loads(t), separators=(",", ":"), ensure_ascii=False)))),
            ("Format XML", self._ed(lambda ed: self._whole(ed, _pretty_xml))),
            None,
            ("Base64 encode selection", self._ed(lambda ed: ed.transform_selection(lambda s: base64.b64encode(s.encode()).decode()))),
            ("Base64 decode selection", self._ed(lambda ed: ed.transform_selection(lambda s: base64.b64decode(s.encode()).decode("utf-8", "replace")))),
        ]))
        g = t.group("Bookmarks")
        g.small("Toggle bookmark", "bookmark", self._any_ed(lambda ed: ed.toggle_bookmark()), shortcut="Ctrl+F2")
        g.small("Next bookmark", "next", self._any_ed(lambda ed: ed.next_bookmark(1)), shortcut="F2")
        g.small("Previous bookmark", "prev", self._any_ed(lambda ed: ed.next_bookmark(-1)), shortcut="Shift+F2")
        g.small("Clear bookmarks", "bookmark", self._any_ed(lambda ed: ed.clear_bookmarks()))
        g = t.group("Folding")
        g.small("Fold / unfold here", "fold", self._any_ed(lambda ed: ed.toggle_fold()), shortcut="Ctrl+Shift+[")
        g.small("Fold all", "fold", self._any_ed(lambda ed: ed.fold_all(True)), shortcut="Ctrl+Alt+[")
        g.small("Unfold all", "fold", self._any_ed(lambda ed: ed.fold_all(False)), shortcut="Ctrl+Alt+]")

        # ---------------------------------------------------------- Search
        t = r.tab("Search")
        g = t.group("In document")
        g.large("Find", "search", lambda: self.findbar.show_find(False))
        g.large("Replace", "replace", lambda: self.findbar.show_find(True))
        g.small("Find next", "next", lambda: self.findbar.find_next(False), shortcut="F3")
        g.small("Find previous", "prev", lambda: self.findbar.find_next(True), shortcut="Shift+F3")
        g.small("Go to line", "goto", self.goto_line)
        g = t.group("Across files")
        g.large("Find in files", "find-files", self.show_find_in_files)
        g.large("Search & Replace", "replace", self.open_search, tip="Search and replace across folders with preview, history and export")

        # ---------------------------------------------------------- Files
        t = r.tab("Files")
        g = t.group("Explorer")
        g.large("Dual pane", "explorer", self.open_explorer)
        g.large("Directory list", "list", lambda: self.open_explorer(1))
        g.large("Batch rename", "rename", lambda: self.open_explorer(2))
        g.large("Attributes", "attrs", lambda: self.open_explorer(3))
        g = t.group("Analyse")
        g.small("Duplicates & sizes", "duplicate", lambda: self.open_explorer(1))
        g.small("Folder compare", "folder-compare", lambda: self.compare_dialog(folders=True))
        g.small("N-way compare", "structure", self.open_nway)

        # ---------------------------------------------------------- Engineering
        t = r.tab("Engineering")
        g = t.group("VDI 3805")
        g.large("Compare VDI 3805", "vdi", lambda: self.compare_dialog(hint="vdi"))
        g.small("Validate file", "validate", self.open_validation)
        g.small("Build profile from guideline…", "settings", self.build_vdi_profile)
        g.small("Open profiles folder", "open", self.open_profile_folder)
        g.small("Edit profile template", "settings", self.open_profile_template)
        g = t.group("XML / JSON")
        g.large("Structure compare", "xml", lambda: self.compare_dialog(hint="xml"))
        g.small("XSD validation", "validate", self.open_validation)
        g = t.group("Office")
        g.large("Excel workbook", "table", lambda: self.compare_dialog(hint="workbook"))
        g.large("Word", "word", lambda: self.compare_dialog(hint="word"))

        # ---------------------------------------------------------- Automation
        t = r.tab("Automation")
        g = t.group("Jobs")
        g.large("Jobs", "automation", self.open_automation)
        g.large("Run job file…", "play", self.run_job_file)
        g = t.group("History")
        g.large("History", "history", lambda: self.open_automation(1))

        # ---------------------------------------------------------- View
        t = r.tab("View")
        g = t.group("Theme")
        mode = theme.stored_mode(self.settings)
        self.theme_actions = {}
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for m, ic in (("light", "sun"), ("dark", "moon"), ("system", "monitor")):
            a = g.large(theme.MODE_LABELS[m], ic, lambda on, m=m: on and self.set_theme(m), checkable=True,
                        checked=(m == mode), tip=f"{theme.MODE_LABELS[m]} environment")
            grp.addAction(a)
            self.theme_actions[m] = a
        self.act_dark = g.small("Toggle light / dark", "theme", self.toggle_theme, shortcut="Ctrl+Shift+D",
                                tip="Switch between the light and the dark environment")
        g = t.group("Editor")
        g.small("Word wrap", "wrap", self.set_wrap, shortcut="Alt+Z", checkable=True)
        g.small("Show whitespace", "list", self.set_ws, checkable=True)
        g.small("Split view", "split", self.toggle_split, shortcut="Ctrl+Alt+V")
        g.small("Zoom in", "search", self._any_ed(lambda ed: ed.zoom(1)), shortcut="Ctrl++")
        g.small("Zoom out", "search", self._any_ed(lambda ed: ed.zoom(-1)), shortcut="Ctrl+-")
        g.small("Find-in-files panel", "find-files", lambda: self.fif_dock.setVisible(not self.fif_dock.isVisible()))
        enc_menu = QMenu(self)
        reo, conv = enc_menu.addMenu("Reopen with encoding"), enc_menu.addMenu("Convert to (on save)")
        for label, code in ENCODINGS:
            reo.addAction(label, lambda code=code: self.reopen_with(code))
            conv.addAction(label, lambda code=code: self.convert_to(code))
        enc_menu.addSeparator()
        for eol, name in EOL_NAMES.items():
            enc_menu.addAction(f"Line endings: {name}", lambda eol=eol: self.set_eol(eol))
        g = t.group("Document")
        g.large("Encoding", "binary", None, menu=enc_menu)
        g = t.group("Window")
        g.small("Collapse ribbon", "fold", lambda: r.set_collapsed(not r.collapsed), shortcut="Ctrl+F1")
        g.small("Settings", "settings", self.open_settings)
        g.small("Keyboard shortcuts", "list", self.show_shortcuts, shortcut="F1")
        g.small("About", "home", self.about)

    def _whole(self, ed, fn):
        try:
            if not ed.textCursor().hasSelection():
                ed.selectAll()
            ed.transform_selection(fn)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "Format", f"Could not format: {ex}")

    # ---------------------------------------------------------------- quick access toolbar
    DEFAULT_QAT = ["Save", "Undo", "Redo", "Smart compare", "Toggle light / dark"]

    def _load_quick(self):
        self.qat.clear()
        items = list(self.settings.value("qat", self.DEFAULT_QAT) or [])
        if not self.settings.value("qat_theme_added", False, type=bool):   # one-time: offer the theme toggle
            if "Toggle light / dark" not in items:
                items.append("Toggle light / dark")
                self.settings.setValue("qat", items)
            self.settings.setValue("qat_theme_added", True)
        for text in items:
            a = self.ribbon.find_action(text)
            if a is not None:
                self.qat.addAction(a)

    def _add_quick(self, action):
        names = list(self.settings.value("qat", self.DEFAULT_QAT) or [])
        if action.text() not in names:
            names.append(action.text())
            self.settings.setValue("qat", names)
            self._load_quick()

    def _qat_menu(self, pos):
        a = self.qat.actionAt(pos)
        m = QMenu(self)
        if a is not None:
            m.addAction(f"Remove '{a.text()}'", lambda: self._remove_quick(a.text()))
        m.addAction("Reset Quick Access Toolbar", lambda: (self.settings.setValue("qat", self.DEFAULT_QAT), self._load_quick()))
        m.addAction("Tip: right-click any ribbon button to add it here").setEnabled(False)
        m.exec(self.qat.mapToGlobal(pos))

    def _remove_quick(self, text):
        names = [n for n in (self.settings.value("qat", self.DEFAULT_QAT) or []) if n != text]
        self.settings.setValue("qat", names)
        self._load_quick()

    # ================================================================ status bar
    def _build_status(self):
        sb = self.statusBar()
        self.st_pos, self.st_len, self.st_enc, self.st_eol = QLabel(), QLabel(), QLabel(), QLabel()
        self.st_lang = QComboBox()
        self.st_lang.setMinimumWidth(160)
        self._lexers = sorted(((n, a[0]) for n, a, *_ in get_all_lexers() if a), key=lambda x: x[0].lower())
        self.st_lang.addItems([n for n, _ in self._lexers])
        self.st_lang.setToolTip("Syntax highlighting language")
        self.st_lang.activated.connect(self._lang_chosen)
        for w in (self.st_pos, self.st_len, self.st_enc, self.st_eol):
            w.setContentsMargins(8, 0, 8, 0)
            sb.addPermanentWidget(w)
        sb.addPermanentWidget(self.st_lang)
        # light / dark environment switch, always visible in the status bar
        self.theme_label = QToolButton()
        self.theme_label.setAutoRaise(True)
        self.theme_label.setToolTip("Environment: light, dark or follow Windows (Ctrl+Shift+D toggles)")
        self.theme_label.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(self.theme_label)
        for mode in ("light", "dark", "system"):
            m.addAction(theme.MODE_LABELS[mode], lambda mode=mode: self.set_theme(mode))
        self.theme_label.setMenu(m)
        sb.addPermanentWidget(self.theme_label)
        sb.showMessage("Local-first: nothing leaves this computer unless you use the optional AI summary.", 8000)

    def _lang_chosen(self, i):
        t = self.current_tab()
        if t:
            t.set_language(self._lexers[i][1])

    def _tab_changed(self, *_):
        t = self.current_tab()
        self.st_lang.setVisible(t is not None)
        self._update_status()
        w = self.current()
        self.setWindowTitle(f"{w.title()} – {APP_NAME}" if w else APP_NAME)
        if isinstance(w, Dashboard):
            w.refresh()

    def _update_status(self):
        t = self.current_tab()
        if not t:
            for w in (self.st_pos, self.st_len, self.st_enc, self.st_eol):
                w.setText("")
            return
        c = t.editor.textCursor()
        sel = len(c.selectedText())
        self.st_pos.setText(f"Ln {c.blockNumber() + 1}, Col {c.positionInBlock() + 1}" + (f"  (Sel {sel})" if sel else ""))
        self.st_len.setText(f"{t.editor.blockCount():,} lines")
        self.st_enc.setText(t.encoding.upper())
        self.st_eol.setText({"\r\n": "CRLF", "\n": "LF", "\r": "CR"}[t.eol])
        k = self.st_lang.findText(t.language_name())
        if k >= 0:
            self.st_lang.setCurrentIndex(k)

    # ================================================================ singleton tool tabs
    def _single(self, cls, factory, sub=None):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if type(w) is cls:
                self.tabs.setCurrentIndex(i)
                break
        else:
            w = self._add(factory())
        if sub is not None and hasattr(w, "tabs") and isinstance(w.tabs, QTabWidget):
            w.tabs.setCurrentIndex(sub)
        return w

    def open_home(self):
        if self.dashboard is None or self.tabs.indexOf(self.dashboard) < 0:
            self.dashboard = Dashboard(self)
            self.dashboard.openHistory.connect(self._open_history_entry)
            self.tabs.insertTab(0, self.dashboard, icon("home", self.ribbon.icon_color()), "Home")
        self.tabs.setCurrentWidget(self.dashboard)
        self.dashboard.refresh()

    def open_explorer(self, sub=None):
        t = self.current_tab()
        start = os.path.dirname(t.path) if t and t.path else None
        return self._single(ExplorerView, lambda: ExplorerView(start), sub if isinstance(sub, int) else None)

    def open_search(self):
        t = self.current_tab()
        return self._single(SearchView, lambda: SearchView(os.path.dirname(t.path) if t and t.path else ""))

    def open_validation(self):
        return self._single(ValidationView, ValidationView)

    def open_automation(self, sub=None):
        return self._single(AutomationView, lambda: AutomationView(self.history), sub if isinstance(sub, int) else None)

    def open_settings(self):
        w = self._single(SettingsView, SettingsView)
        if not getattr(w, "_hooked", False):
            w.changed.connect(self._apply_settings)
            w._hooked = True
        return w

    def open_merge(self):
        return self._add(MergeView())

    def open_nway(self):
        return self._add(MultiFolderView())

    def build_vdi_profile(self):
        from .engviews import BuildProfileDialog
        BuildProfileDialog(self).exec()

    def open_profile_folder(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        from ..core import vdi3805
        d = vdi3805.user_profile_dir()
        os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def open_profile_template(self):
        from .engviews import PROFILE_DIR
        self.open_file(os.path.join(PROFILE_DIR, "vdi3805_profile_template.json"))

    def _open_history_entry(self, r):
        if r.get("report") and os.path.exists(r["report"]):
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(r["report"]))
        elif r.get("left") and r.get("right") and os.path.exists(r["left"]) and os.path.exists(r["right"]):
            kind = r.get("kind", "")
            self.open_compare(r["left"], r["right"], kind if kind in dict((k, 1) for _, k in KINDS) else None)

    def _apply_settings(self):
        mode = theme.stored_mode(self.settings)
        if mode != getattr(self, "theme_mode", None) or theme.resolve(mode) != theme.Theme.dark:
            self.set_theme(mode)
        size = self.settings.value("font_size", 10, type=int)
        for ed in self.all_editors():
            f = ed.font()
            f.setPointSize(size)
            ed.setFont(f)
        mins = self.settings.value("autosave_min", 2, type=int)
        if mins:
            self.autosave_timer.start(mins * 60_000)
        else:
            self.autosave_timer.stop()

    # ================================================================ files
    def new_tab(self, text=""):
        self._untitled += 1
        t = EditorTab(text=text if isinstance(text, str) else "")
        t.untitled_name = f"new {self._untitled}"
        self._add(t)
        t.editor.setFocus()
        return t

    def open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Open files", self.settings.value("last_dir", ""))
        for p in paths:
            self.open_file(p)

    def open_file(self, path, line=None):
        path = os.path.abspath(path)
        for t in self.editor_tabs():
            if t.path and os.path.normcase(t.path) == os.path.normcase(path):
                self.tabs.setCurrentWidget(t)
                if line:
                    t.editor.go_to_line(line)
                return t
        if os.path.isdir(path):
            return self.open_explorer()
        ext = loaders.ext(path)
        if ext in loaders.WORD_EXT or ext in (".xlsx", ".xlsm", ".xls"):
            QMessageBox.information(self, "Open", "Word and Excel files cannot be edited here. Use Compare to view "
                                    "their content and differences.")
            return None
        if os.path.getsize(path) > 500 * 1024 * 1024:
            QMessageBox.warning(self, "Open", "This file is larger than 500 MB and is not opened in the editor.")
            return None
        try:
            t = EditorTab(path)
        except OSError as ex:
            QMessageBox.warning(self, "Open", str(ex))
            return None
        self.settings.setValue("last_dir", os.path.dirname(path))
        self._add(t)
        if line:
            t.editor.go_to_line(line)
        return t

    def save_current(self):
        w = self.current()
        if isinstance(w, EditorTab):
            return self._save_tab(w)
        if isinstance(w, TextCompareView):
            for i, s in enumerate(w.sides):
                if s.modified:
                    w.save_side(i)
        elif hasattr(w, "save") and callable(w.save):
            w.save()
        return True

    def _save_tab(self, t: EditorTab, ask=False):
        if t.path and not ask:
            try:
                self.watcher.removePath(t.path)
                t.save()
            except OSError as ex:
                QMessageBox.warning(self, "Save", str(ex))
                return False
            finally:
                if t.path:
                    self.watcher.addPath(t.path)
            return True
        p, _ = QFileDialog.getSaveFileName(self, "Save as", t.path or self.settings.value("last_dir", ""))
        if not p:
            return False
        if t.path:
            self.watcher.removePath(t.path)
        t.save(p)
        self.watcher.addPath(p)
        self._add_recent(p)
        return True

    def save_as(self):
        t = self.current_tab()
        if t:
            self._save_tab(t, ask=True)

    def save_all(self):
        for t in self.editor_tabs():
            if t.modified:
                self.tabs.setCurrentWidget(t)
                self._save_tab(t)

    def reload_current(self):
        t = self.current_tab()
        if t and t.path:
            if t.modified and QMessageBox.question(self, "Reload", "Discard your changes and reload?") != QMessageBox.StandardButton.Yes:
                return
            t.load(t.path, t.encoding)
        else:
            self.recompare()

    def recompare(self):
        w = self.current()
        for name in ("reload_and_compare", "run", "compare"):
            f = getattr(w, name, None)
            if callable(f):
                f()
                return

    def close_tab(self, i):
        w = self.tabs.widget(i)
        if w is None:
            return False
        if isinstance(w, EditorTab) and w.modified:
            self.tabs.setCurrentIndex(i)
            r = QMessageBox.question(self, "Save changes", f"Save changes to {w.title().lstrip('● ')}?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel or (r == QMessageBox.StandardButton.Save and not self._save_tab(w)):
                return False
        elif getattr(w, "has_unsaved", lambda: False)():
            r = QMessageBox.question(self, "Unsaved changes", "This tab has unsaved changes. Close anyway?")
            if r != QMessageBox.StandardButton.Yes:
                return False
        if isinstance(w, EditorTab) and w.path:
            self.watcher.removePath(w.path)
        self.tabs.removeTab(i)
        if w is self.dashboard:
            self.dashboard = None
        w.deleteLater()
        if self.tabs.count() == 0:
            self.open_home()
        return True

    def close_all(self):
        for i in range(self.tabs.count() - 1, -1, -1):
            if not self.close_tab(i):
                return False
        return True

    def _file_changed_on_disk(self, path):
        for t in self.editor_tabs():
            if t.path == path and os.path.exists(path):
                if t.disk_mtime and abs(os.path.getmtime(path) - t.disk_mtime) < 0.01:
                    continue
                if not t.modified:
                    t.load(path, t.encoding)
                    self.statusBar().showMessage(f"Reloaded {os.path.basename(path)} (changed on disk)", 5000)
                else:
                    t.disk_mtime = os.path.getmtime(path)
                    if QMessageBox.question(self, "File changed", f"{path}\nwas changed by another program. Reload and lose your edits?") == QMessageBox.StandardButton.Yes:
                        t.load(path, t.encoding)
                if path not in self.watcher.files():
                    self.watcher.addPath(path)

    def _add_recent(self, p):
        rec = [x for x in (self.settings.value("recent", []) or []) if x != p]
        self.settings.setValue("recent", ([p] + rec)[:15])

    def _fill_recent(self):
        self.recent_menu.clear()
        for p in self.settings.value("recent", []) or []:
            self.recent_menu.addAction(p, lambda p=p: self.open_file(p) if os.path.exists(p) else None)

    # ================================================================ recovery (auto-save)
    def write_recovery(self):
        os.makedirs(self.recovery_dir, exist_ok=True)
        index = []
        for k, t in enumerate(self.editor_tabs()):
            if not t.modified:
                continue
            fn = os.path.join(self.recovery_dir, f"tab{k}.txt")
            with open(fn, "w", encoding="utf-8", newline="") as fh:
                fh.write(t.editor.toPlainText())
            index.append({"file": fn, "path": t.path, "name": t.untitled_name, "encoding": t.encoding, "eol": t.eol})
        with open(os.path.join(self.recovery_dir, "index.json"), "w", encoding="utf-8") as fh:
            json.dump(index, fh, indent=2)

    def _clear_recovery(self):
        p = os.path.join(self.recovery_dir, "index.json")
        if os.path.exists(p):
            for e in json.load(open(p, encoding="utf-8")):
                if os.path.exists(e["file"]):
                    os.remove(e["file"])
            os.remove(p)

    def _offer_recovery(self):
        p = os.path.join(self.recovery_dir, "index.json")
        if not os.path.exists(p):
            return
        try:
            entries = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = [e for e in entries if os.path.exists(e["file"])]
        if not entries:
            return
        names = "\n".join(e["path"] or e["name"] for e in entries)
        if QMessageBox.question(self, "Recover unsaved work", f"Nexus was not closed normally. Restore unsaved text "
                                f"for these documents?\n\n{names}") == QMessageBox.StandardButton.Yes:
            for e in entries:
                with open(e["file"], encoding="utf-8", newline="") as fh:
                    text = fh.read()
                t = self.new_tab(text)
                t.untitled_name = f"recovered – {os.path.basename(e['path']) if e['path'] else e['name']}"
                t.encoding, t.eol = e.get("encoding", "utf-8"), e.get("eol", "\n")
                t.editor.document().setModified(True)
                self._retitle(t)
        self._clear_recovery()

    # ================================================================ encoding
    def reopen_with(self, code):
        t = self.current_tab()
        if t and t.path:
            t.load(t.path, code)
            self._update_status()

    def convert_to(self, code):
        t = self.current_tab()
        if t:
            t.encoding = code
            t.editor.document().setModified(True)
            self._update_status()

    def set_eol(self, eol):
        t = self.current_tab()
        if t:
            t.eol = eol
            t.editor.document().setModified(True)
            self._update_status()

    # ================================================================ view
    def set_wrap(self, on):
        self.word_wrap = on
        for ed in self.all_editors():
            ed.set_wrap(on)

    def set_ws(self, on):
        self.show_ws = on
        for ed in self.all_editors():
            ed.set_show_whitespace(on)

    def toggle_split(self):
        t = self.current_tab()
        if t:
            t.toggle_split()

    def set_theme(self, mode: str):
        """Light, dark or "follow Windows" environment. Applies to the whole window at once."""
        if mode not in theme.MODES:
            mode = "dark"
        self.theme_mode = mode
        self.settings.setValue("theme", mode)
        a = self.theme_actions.get(mode)
        if a is not None and not a.isChecked():
            a.blockSignals(True)
            a.setChecked(True)
            a.blockSignals(False)
        hints = QApplication.instance().styleHints()
        if not getattr(self, "_scheme_hooked", False) and hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_scheme_changed)
            self._scheme_hooked = True
        self._apply_dark(theme.resolve(mode))

    def _system_scheme_changed(self, *_):
        if getattr(self, "theme_mode", "") == "system":
            self._apply_dark(theme.resolve("system"))

    def toggle_theme(self):
        self.set_theme("light" if theme.Theme.dark else "dark")

    def set_dark(self, on):          # kept for callers of 0.2
        self.set_theme("dark" if on else "light")

    def _apply_dark(self, on):
        self.settings.setValue("dark", on)
        theme.apply(QApplication.instance(), on)
        if hasattr(self, "theme_label"):
            self.theme_label.setText(("Dark" if on else "Light") + (" (Windows)" if self.theme_mode == "system" else ""))
        self.ribbon.retint()
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, EditorTab):
                w.editor.highlighter.relex()
                w.editor._cursor_moved()
            elif isinstance(w, TextCompareView):
                w.refresh()
            elif isinstance(w, TableCompareView):
                w.compare()
            elif isinstance(w, FolderCompareView):
                w._populate()
            elif isinstance(w, Dashboard):
                w.apply_style()
        if self.dashboard is not None:
            self.tabs.setTabIcon(self.tabs.indexOf(self.dashboard), icon("home", self.ribbon.icon_color()))

    def goto_line(self):
        ed = self.current_editor()
        if not ed:
            return
        n, ok = QInputDialog.getInt(self, "Go to line", f"Line (1 – {ed.blockCount()}):", ed.textCursor().blockNumber() + 1, 1, ed.blockCount())
        if ok:
            ed.go_to_line(n)

    def show_find_in_files(self):
        t = self.current_tab()
        if t and t.path and not self.fif.folder.currentText():
            self.fif.folder.setCurrentText(os.path.dirname(t.path))
        self.fif_dock.show()
        self.fif.pattern.setFocus()

    # ================================================================ compare
    def open_compare(self, left, right, kind=None):
        if not (os.path.exists(left) and os.path.exists(right)):
            QMessageBox.warning(self, "Compare", "Both paths must exist.")
            return None
        auto = loaders.compare_kind(left, right)
        kind = kind or auto
        try:
            if kind == "folder" or (os.path.isdir(left) and os.path.isdir(right)):
                w = FolderCompareView(left, right)
            elif kind == "vdi":
                w = VdiView(left, right)
            elif kind == "workbook":
                w = WorkbookView(left, right)
            elif kind == "table":
                w = TableCompareView(left, right, vdi=(auto == "vdi"))
            elif kind in ("xml", "json"):
                w = StructCompareView(left, right)
            elif kind == "binary":
                w = BinaryCompareView(left, right)
            else:
                w = TextCompareView(Side.from_path(left), Side.from_path(right))
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "Compare", f"Could not compare:\n{ex}")
            return None
        self._add(w)
        # views that do not report themselves: record in history here
        if isinstance(w, TextCompareView) and w.result is not None:
            self._record("word" if auto == "word" else "text", left, right,
                         summarize_offline("text", w.result.stats()), w.result.identical)
        elif isinstance(w, TableCompareView) and w.res is not None:
            self._record("table", left, right, summarize_offline("table", w.res.stats()), w.res.identical)
        return w

    def _open_table(self, left, right):
        self.open_compare(left, right, "table")

    def compare_dialog(self, folders=False, hint=None):
        if hint == "merge":
            return self.open_merge()
        d = CompareDialog(self, folders, hint)
        if d.exec() != QDialog.DialogCode.Accepted:
            return None
        l, r = d.paths()
        kind = d.chosen_kind()
        if kind == "folder" or (os.path.isdir(l) and os.path.isdir(r)):
            if not (os.path.isdir(l) and os.path.isdir(r)):
                QMessageBox.warning(self, "Compare", "Please choose two existing folders.")
                return None
            return self.open_compare(l, r, "folder")
        if not (os.path.isfile(l) and os.path.isfile(r)):
            QMessageBox.warning(self, "Compare", "Please choose two existing files (or two folders).")
            return None
        if kind == "word":
            kind = "text"
        return self.open_compare(l, r, kind)

    def new_text_compare(self):
        self._add(TextCompareView(Side(name="left", lines=[""]), Side(name="right", lines=[""])))

    def _side_from_tab(self, t: EditorTab, selection_only=False):
        text = t.editor.textCursor().selectedText().replace(" ", "\n") if selection_only else t.editor.toPlainText()
        return Side(t.path, (os.path.basename(t.path) if t.path else t.untitled_name) + (" (selection)" if selection_only else ""),
                    text.split("\n"), t.encoding, t.eol, readonly=True)

    def compare_with_tab(self):
        t = self.current_tab()
        others = [x for x in self.editor_tabs() if x is not t]
        if not t or not others:
            QMessageBox.information(self, "Compare", "Open at least two documents in the editor first.")
            return
        names = [o.title() for o in others]
        name, ok = QInputDialog.getItem(self, "Compare with", "Compare current document with:", names, 0, False)
        if ok:
            o = others[names.index(name)]
            w = self._add(TextCompareView(self._side_from_tab(t), self._side_from_tab(o)))
            w.sides[0].readonly = w.sides[1].readonly = False
            w.refresh()

    def compare_with_disk(self):
        t = self.current_tab()
        if t and t.path:
            cur = self._side_from_tab(t)
            cur.name += " (unsaved)"
            cur.path = None
            self._add(TextCompareView(Side.from_path(t.path), cur))

    def compare_with_clipboard(self):
        """Compares the selection (or the whole document) with the clipboard text."""
        t = self.current_tab()
        if t:
            clip = QGuiApplication.clipboard().text().replace("\r\n", "\n").split("\n")
            sel = t.editor.textCursor().hasSelection()
            self._add(TextCompareView(self._side_from_tab(t, sel), Side(None, "clipboard", clip)))

    def open_compare_sides(self):
        w = self.current()
        if isinstance(w, TextCompareView):
            for s in w.sides:
                if s.path and loaders.ext(s.path) not in loaders.WORD_EXT:
                    self.open_file(s.path)
        elif isinstance(w, TableCompareView):
            for p in w.paths:
                if p and loaders.ext(p) not in (".xlsx", ".xlsm", ".xls"):
                    self.open_file(p)

    def summarize_current(self):
        from .common import summarize_changes
        w = self.current()
        if hasattr(w, "summary") and callable(w.summary):
            return w.summary()
        if isinstance(w, TextCompareView) and w.result:
            offline = summarize_offline("text", w.result.stats())
            from ..core.textdiff import unified_diff
            diff = unified_diff(w.sides[0].lines, w.sides[1].lines, w.sides[0].name, w.sides[1].name)
            summarize_changes(self, offline, diff, f"{w.sides[0].name} vs {w.sides[1].name}")
        elif isinstance(w, TableCompareView) and w.res:
            summarize_changes(self, summarize_offline("table", w.res.stats()), "", "table")
        else:
            QMessageBox.information(self, "Summary", "Open a comparison first.")

    def run_job_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Run job", os.path.join(automation.app_data_dir(), "jobs"), "Job (*.json)")
        if not p:
            return
        res = automation.run_job(automation.load_job(p), self.history)
        QMessageBox.information(self, "Job finished", "\n\n".join(f"{r.name}: {'OK' if r.ok else 'FAILED'}\n{r.summary}\n{r.report}"
                                                                   for r in res))
        if self.dashboard:
            self.dashboard.refresh()

    # ================================================================ misc
    def show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", """<table cellpadding=3>
<tr><td><b>Ctrl+N / Ctrl+O / Ctrl+S</b></td><td>New / open / save</td></tr>
<tr><td><b>Ctrl+F / Ctrl+H / F3</b></td><td>Find / replace / find next</td></tr>
<tr><td><b>Ctrl+Shift+F</b></td><td>Find in files</td></tr>
<tr><td><b>Ctrl+G</b></td><td>Go to line</td></tr>
<tr><td><b>Ctrl+D / Ctrl+Shift+K</b></td><td>Duplicate / delete line</td></tr>
<tr><td><b>Alt+Up / Alt+Down</b></td><td>Move line(s)</td></tr>
<tr><td><b>Ctrl+/</b></td><td>Toggle comment</td></tr>
<tr><td><b>Ctrl+F2 / F2 / Shift+F2</b></td><td>Toggle / next / previous bookmark (or click left of the line number)</td></tr>
<tr><td><b>Ctrl+Shift+[ / Ctrl+Alt+[ / Ctrl+Alt+]</b></td><td>Fold here / fold all / unfold all (or click ▾ in the margin)</td></tr>
<tr><td><b>Ctrl+Alt+V</b></td><td>Split view</td></tr>
<tr><td><b>Ctrl+K / Ctrl+Alt+K</b></td><td>Compare files / folders</td></tr>
<tr><td><b>Ctrl+Alt+C</b></td><td>Compare current tab with another tab</td></tr>
<tr><td><b>F7 / Shift+F7</b></td><td>Next / previous difference</td></tr>
<tr><td><b>Alt+Right / Alt+Left</b></td><td>Copy difference to right / left</td></tr>
<tr><td><b>Ctrl+Shift+D</b></td><td>Show only differences</td></tr>
<tr><td><b>F5</b></td><td>Recompare</td></tr>
<tr><td><b>Ctrl+F1</b></td><td>Collapse / expand the ribbon (or double-click a ribbon tab)</td></tr>
<tr><td><b>Ctrl+Wheel</b></td><td>Zoom</td></tr></table>""")

    def about(self):
        from .. import POWERED_BY, PUBLISHER, BRAND, COPYRIGHT, HOMEPAGE
        from .dashboard import LOGO
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        pm = QPixmap(LOGO)
        if not pm.isNull():                      # white backing so the dark lettering reads in the dark theme
            card = QPixmap(pm.width() + 24, pm.height() + 24)
            card.fill(QColor("#ffffff"))
            p = QPainter(card)
            p.drawPixmap(12, 12, pm)
            p.end()
            box.setIconPixmap(card.scaledToWidth(220, Qt.TransformationMode.SmoothTransformation))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<h3>{APP_NAME} {__version__}</h3><p>COMPARE · EDIT · VALIDATE</p>"
                    f"<p><b>{POWERED_BY}</b><br>{BRAND}</p>")
        box.setInformativeText(
            "<p>Advanced file intelligence &amp; engineering data platform: folder and file comparison, "
            "a code editor, VDI 3805 and XML engineering data, Office documents, search &amp; replace, "
            "file tools and automation. Everything runs locally.</p>"
            f"<p>Publisher: {PUBLISHER}<br>{COPYRIGHT}<br>"
            f'Project and updates: <a href="{HOMEPAGE}">{HOMEPAGE}</a></p>'
            "<p>Built with Python, Qt (PySide6, LGPL), Pygments, openpyxl, python-docx, xlrd and lxml.</p>")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    # ================================================================ drag & drop, session
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if len(paths) == 2:
            self.open_compare(*paths)
        else:
            for p in paths:
                if os.path.isfile(p):
                    self.open_file(p)

    def restore_session(self):
        if not self.settings.value("restore_session", True, type=bool):
            return
        for p in self.settings.value("session", []) or []:
            if os.path.isfile(p):
                self.open_file(p)

    def closeEvent(self, e):
        session = [t.path for t in self.editor_tabs() if t.path]
        for i in range(self.tabs.count() - 1, -1, -1):
            if not self.close_tab(i):
                e.ignore()
                return
        self.settings.setValue("session", session)
        self.settings.setValue("geometry", self.saveGeometry())
        self._clear_recovery()
        self.history.close()
        e.accept()


def _spaces_to_tabs(lines):
    out = []
    for l in lines:
        n = len(l) - len(l.lstrip(" "))
        out.append("\t" * (n // 4) + " " * (n % 4) + l.lstrip(" "))
    return out


def _pretty_xml(t):
    s = xml.dom.minidom.parseString(t.encode("utf-8")).toprettyxml(indent="    ")
    lines = [l for l in s.split("\n") if l.strip()]
    if not t.lstrip().startswith("<?xml"):
        lines = lines[1:]
    return "\n".join(lines)
