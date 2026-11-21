"""Tool views: file explorer (dual pane, directory list, batch rename, attributes),
search & replace across folders, Excel workbook comparison, validation,
automation and settings."""
from __future__ import annotations

import datetime as _dt
import difflib
import json
import os
import shutil
import sys

from PySide6.QtCore import Qt, Signal, QSettings, QDir, QDateTime, QFile, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QComboBox,
                               QTabWidget, QSplitter, QMessageBox, QFileDialog, QPlainTextEdit, QLineEdit,
                               QGroupBox, QFormLayout, QSpinBox, QTreeView, QFileSystemModel, QInputDialog,
                               QListWidget, QDateTimeEdit, QDoubleSpinBox, QAbstractItemView)

from ..core import fileops, msearch, office, structdiff, vdi3805, automation, loaders
from ..core.automation import app_data_dir
from .common import Task, PathRow, make_table, fill_table, fmt_size, kind_color, short
from .theme import Theme, mono_font
from .filetools import ReportPanel as DirectoryListPanel, RenamePanel as BatchRenamePanel  # noqa: F401


class _Base(QWidget):
    titleChanged = Signal()
    openFile = Signal(str)
    openPair = Signal(str, str)

    def has_unsaved(self):
        return False


# ====================================================================== explorer
class _Pane(QWidget):
    def __init__(self, path):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.path = QLineEdit(path)
        self.path.returnPressed.connect(lambda: self.go(self.path.text()))
        v.addWidget(self.path)
        self.model = QFileSystemModel()
        self.model.setRootPath("")
        self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setColumnWidth(0, 280)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.view.doubleClicked.connect(self._dbl)
        v.addWidget(self.view)
        self.go(path)

    def go(self, p):
        if p and os.path.isdir(p):
            self.view.setRootIndex(self.model.index(p))
            self.path.setText(p)

    def cwd(self):
        return self.path.text()

    def selected(self):
        return sorted({self.model.filePath(i) for i in self.view.selectionModel().selectedRows()})

    def _dbl(self, idx):
        p = self.model.filePath(idx)
        if os.path.isdir(p):
            self.go(p)


class ExplorerView(_Base):
    def __init__(self, path=None, parent=None):
        super().__init__(parent)
        path = path or os.path.expanduser("~")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs)
        self.tabs.addTab(self._dual(path), "Dual pane")
        self.listing = DirectoryListPanel(path)
        self.listing.openFile.connect(self.openFile)
        self.tabs.addTab(self.listing, "Directory listing && reports")
        self.rename = BatchRenamePanel()
        self.tabs.addTab(self.rename, "Bulk rename")
        self.attrs = AttributesPanel()
        self.tabs.addTab(self.attrs, "Attributes && timestamps")

    def title(self):
        return "File Explorer"

    def _dual(self, path):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        h = QHBoxLayout()
        acts = [("Up", self.up), ("Copy →", lambda: self.transfer(False)), ("Move →", lambda: self.transfer(True)),
                ("Rename…", self.rename_one), ("New folder…", self.new_folder), ("Delete (to recycle bin)", self.trash),
                ("Open in editor", self.open_sel), ("Compare selected", self.compare_sel),
                ("Batch rename selected", self.to_rename), ("Attributes of selected", self.to_attrs),
                ("List this folder", self.to_list)]
        for t, slot in acts:
            b = QPushButton(t)
            b.clicked.connect(slot)
            h.addWidget(b)
        h.addStretch()
        v.addLayout(h)
        sp = QSplitter()
        self.panes = [_Pane(path), _Pane(path)]
        for p in self.panes:
            p.view.clicked.connect(lambda _i, p=p: setattr(self, "active", p))
            sp.addWidget(p)
        self.active = self.panes[0]
        v.addWidget(sp, 1)
        v.addWidget(QLabel("The active pane is the one you clicked last. Copy/Move go to the other pane. "
                           "Every change asks for confirmation; deleted items go to the recycle bin."))
        return w

    def other(self):
        return self.panes[1] if self.active is self.panes[0] else self.panes[0]

    def up(self):
        self.active.go(os.path.dirname(self.active.cwd().rstrip("\\/")) or self.active.cwd())

    def transfer(self, move):
        src = self.active.selected()
        dst = self.other().cwd()
        if not src or not os.path.isdir(dst):
            return
        verb = "Move" if move else "Copy"
        if QMessageBox.question(self, verb, f"{verb} {len(src)} item(s) to\n{dst}?\nExisting items with the same name "
                                "will be overwritten.") != QMessageBox.StandardButton.Yes:
            return
        for s in src:
            d = os.path.join(dst, os.path.basename(s))
            try:
                if move:
                    shutil.move(s, d)
                elif os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
            except OSError as ex:
                QMessageBox.warning(self, verb, f"{s}: {ex}")

    def rename_one(self):
        sel = self.active.selected()
        if len(sel) != 1:
            return
        new, ok = QInputDialog.getText(self, "Rename", "New name:", text=os.path.basename(sel[0]))
        if ok and new and new != os.path.basename(sel[0]):
            target = os.path.join(os.path.dirname(sel[0]), new)
            if os.path.exists(target):
                QMessageBox.warning(self, "Rename", "An item with that name already exists.")
                return
            os.rename(sel[0], target)

    def new_folder(self):
        name, ok = QInputDialog.getText(self, "New folder", "Name:")
        if ok and name:
            os.makedirs(os.path.join(self.active.cwd(), name), exist_ok=True)

    def trash(self):
        sel = self.active.selected()
        if not sel or QMessageBox.question(self, "Delete", f"Move {len(sel)} item(s) to the recycle bin?") != QMessageBox.StandardButton.Yes:
            return
        for p in sel:
            if not QFile.moveToTrash(p):
                QMessageBox.warning(self, "Delete", f"Could not move to recycle bin: {p}")

    def open_sel(self):
        for p in self.active.selected():
            if os.path.isfile(p):
                self.openFile.emit(p)

    def compare_sel(self):
        a, b = self.panes[0].selected(), self.panes[1].selected()
        pair = (a + b) if len(a) + len(b) == 2 else self.active.selected()
        if len(pair) == 2:
            self.openPair.emit(*pair)
        else:
            QMessageBox.information(self, "Compare", "Select exactly two files or folders (in one or both panes).")

    def to_rename(self):
        self.rename.add_paths(self.active.selected(), folders_too=True)
        self.tabs.setCurrentWidget(self.rename)

    def to_attrs(self):
        self.attrs.set_paths(self.active.selected())
        self.tabs.setCurrentWidget(self.attrs)

    def to_list(self):
        self.listing.root.setText(self.active.cwd())
        self.tabs.setCurrentWidget(self.listing)


class AttributesPanel(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        h = QHBoxLayout()
        b = QPushButton("Add files…")
        b.clicked.connect(lambda: self.set_paths(self.paths() + QFileDialog.getOpenFileNames(self, "Files")[0]))
        h.addWidget(b)
        b2 = QPushButton("Clear")
        b2.clicked.connect(lambda: self.set_paths([]))
        h.addWidget(b2)
        h.addStretch()
        v.addLayout(h)
        self.table = make_table(["Path", "Read-only", "Hidden", "System", "Archive", "Created", "Modified", "Accessed"])
        self.table.itemSelectionChanged.connect(self._load_sel)
        v.addWidget(self.table, 1)
        g = QGroupBox("Change (unchecked = leave as is)")
        f = QFormLayout(g)
        self.flags = {}
        row = QHBoxLayout()
        for k, label in (("readonly", "Read-only"), ("hidden", "Hidden"), ("system", "System"), ("archive", "Archive")):
            cb = QCheckBox(label)
            cb.setTristate(True)
            cb.setCheckState(Qt.CheckState.PartiallyChecked)
            cb.setToolTip("Partially checked = keep. Hidden/System/Archive exist on Windows only.")
            self.flags[k] = cb
            row.addWidget(cb)
        row.addStretch()
        f.addRow("Attributes:", row)
        self.times = {}
        for k in ("created", "modified", "accessed"):
            r = QHBoxLayout()
            on = QCheckBox("set")
            de = QDateTimeEdit(QDateTime.currentDateTime())
            de.setCalendarPopup(True)
            de.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            r.addWidget(on)
            r.addWidget(de)
            r.addStretch()
            self.times[k] = (on, de)
            f.addRow(k.capitalize() + (" (Windows only):" if k == "created" else ":"), r)
        v.addWidget(g)
        ab = QPushButton("Apply to all listed files…")
        ab.clicked.connect(self.apply)
        v.addWidget(ab)
        self.status = QLabel()
        v.addWidget(self.status)
        self._paths = []

    def paths(self):
        return list(self._paths)

    def set_paths(self, ps):
        self._paths = [p for p in dict.fromkeys(ps) if os.path.exists(p)]
        rows = []
        for p in self._paths:
            a = fileops.get_attributes(p)
            yn = lambda x: "yes" if x else ""  # noqa: E731
            rows.append([p, yn(a["readonly"]), yn(a["hidden"]), yn(a["system"]), yn(a["archive"]),
                         a["created"].strftime("%Y-%m-%d %H:%M:%S"), a["modified"].strftime("%Y-%m-%d %H:%M:%S"),
                         a["accessed"].strftime("%Y-%m-%d %H:%M:%S")])
        fill_table(self.table, rows)

    def _load_sel(self):
        r = self.table.currentRow()
        if 0 <= r < self.table.rowCount():
            p = self.table.item(r, 0).text()
            a = fileops.get_attributes(p)
            for k in self.times:
                self.times[k][1].setDateTime(QDateTime(a[k]))

    def apply(self):
        if not self._paths:
            return
        ch = fileops.AttrChange()
        for k, cb in self.flags.items():
            st = cb.checkState()
            if st != Qt.CheckState.PartiallyChecked:
                setattr(ch, k, st == Qt.CheckState.Checked)
        for k, (on, de) in self.times.items():
            if on.isChecked():
                setattr(ch, k, de.dateTime().toPython())
        if QMessageBox.question(self, "Attributes", f"Change attributes/timestamps of {len(self._paths)} file(s)?") != QMessageBox.StandardButton.Yes:
            return
        notes = []
        for p in self._paths:
            try:
                notes += fileops.set_attributes(p, ch)
            except OSError as ex:
                notes.append(f"{p}: {ex}")
        self.set_paths(self._paths)
        self.status.setText("Done. " + "; ".join(dict.fromkeys(notes)))


# ====================================================================== search & replace
class SearchView(_Base):
    def __init__(self, root="", parent=None):
        super().__init__(parent)
        self.hits = []
        self.repl = []
        self.task = None
        self.settings = QSettings("Nexus", "Nexus")
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        f = QFormLayout()
        rrow = QHBoxLayout()
        self.roots = QLineEdit(root)
        self.roots.setPlaceholderText("One or more folders, separated by ';'")
        add = QPushButton("Add folder…")
        add.clicked.connect(self._add_root)
        rrow.addWidget(self.roots, 1)
        rrow.addWidget(add)
        f.addRow("Folders:", rrow)
        self.pattern = QComboBox()
        self.pattern.setEditable(True)
        self.pattern.addItems(self.settings.value("search_history", []) or [])
        self.pattern.setCurrentText("")
        self.replace = QComboBox()
        self.replace.setEditable(True)
        self.replace.addItems(self.settings.value("replace_history", []) or [])
        self.replace.setCurrentText("")
        f.addRow("Find:", self.pattern)
        f.addRow("Replace with:", self.replace)
        orow = QHBoxLayout()
        self.regex = QCheckBox("Regex")
        self.case = QCheckBox("Match case")
        self.word = QCheckBox("Whole word")
        self.multi = QCheckBox("Multi-line ('.' matches line breaks)")
        self.office = QCheckBox("Search inside Word/Excel (read-only)")
        self.office.setChecked(True)
        self.rec = QCheckBox("Subfolders")
        self.rec.setChecked(True)
        for w in (self.regex, self.case, self.word, self.multi, self.office, self.rec):
            orow.addWidget(w)
        orow.addStretch()
        f.addRow("Options:", orow)
        frow = QHBoxLayout()
        self.inc = QLineEdit("*")
        self.exc = QLineEdit(msearch.SearchSpec([], "").exclude)
        frow.addWidget(QLabel("Include:"))
        frow.addWidget(self.inc)
        frow.addWidget(QLabel("Exclude:"))
        frow.addWidget(self.exc, 1)
        f.addRow("Files:", frow)
        v.addLayout(f)
        brow = QHBoxLayout()
        for t, slot in (("Search", self.search), ("Preview replace", self.preview), ("Replace in files…", self.apply),
                        ("Export results…", self.export)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            brow.addWidget(b)
        self.backup = QCheckBox("Back up files before replacing")
        self.backup.setChecked(True)
        brow.addWidget(self.backup)
        brow.addStretch()
        v.addLayout(brow)
        self.tabs = QTabWidget()
        self.table = make_table(["File", "Line", "Col", "Text"])
        self.table.itemDoubleClicked.connect(self._open)
        self.tabs.addTab(self.table, "Results")
        sp = QSplitter()
        self.rtable = make_table(["File", "Replacements"])
        self.rtable.itemSelectionChanged.connect(self._show_diff)
        sp.addWidget(self.rtable)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFont(mono_font(9))
        sp.addWidget(self.diff)
        sp.setSizes([400, 700])
        self.tabs.addTab(sp, "Replace preview")
        v.addWidget(self.tabs, 1)
        self.status = QLabel()
        v.addWidget(self.status)

    def title(self):
        return "Search & Replace"

    def _add_root(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder")
        if d:
            cur = [x for x in self.roots.text().split(";") if x.strip()]
            self.roots.setText(";".join(cur + [d]))

    def _remember(self, key, box):
        t = box.currentText()
        if not t:
            return
        hist = [x for x in (self.settings.value(key, []) or []) if x != t]
        hist = ([t] + hist)[:25]
        self.settings.setValue(key, hist)

    def spec(self):
        roots = [r.strip() for r in self.roots.text().split(";") if r.strip()]
        return msearch.SearchSpec(roots, self.pattern.currentText(), regex=self.regex.isChecked(), case=self.case.isChecked(),
                                  word=self.word.isChecked(), multiline=self.multi.isChecked(), include=self.inc.text(),
                                  exclude=self.exc.text(), recursive=self.rec.isChecked(),
                                  search_office=self.office.isChecked())

    def _check(self):
        s = self.spec()
        if not s.pattern or not s.roots or not all(os.path.isdir(r) for r in s.roots):
            self.status.setText("Enter a search text and one or more existing folders.")
            return None
        try:
            s.compile()
        except Exception as ex:  # noqa: BLE001
            self.status.setText(f"Invalid regular expression: {ex}")
            return None
        self._remember("search_history", self.pattern)
        return s

    def search(self):
        s = self._check()
        if not s:
            return
        self.status.setText("Searching…")
        self.task = Task(msearch.search, s)
        self.task.done.connect(self._found)
        self.task.failed.connect(lambda m: self.status.setText("Error: " + m))
        self.task.start()

    def _found(self, res):
        self.hits, nfiles = res
        fill_table(self.table, [[h.path, h.line, h.col, h.text.strip()[:400]] for h in self.hits], data=self.hits)
        self.tabs.setCurrentIndex(0)
        self.status.setText(f"{len(self.hits):,} match(es) in {len({h.path for h in self.hits}):,} file(s); "
                            f"{nfiles:,} file(s) searched.")

    def _open(self, it):
        h = self.table.item(it.row(), 0).data(Qt.ItemDataRole.UserRole)
        if h:
            self.openFile.emit(h.path)

    def preview(self):
        s = self._check()
        if not s:
            return
        self._remember("replace_history", self.replace)
        self.status.setText("Computing replacements (nothing is written)…")
        self.task = Task(msearch.replace_preview, s, self.replace.currentText())
        self.task.done.connect(self._previewed)
        self.task.failed.connect(lambda m: self.status.setText("Error: " + m))
        self.task.start()

    def _previewed(self, items):
        self.repl = items
        fill_table(self.rtable, [[i.path, i.count] for i in items], data=items)
        self.tabs.setCurrentIndex(1)
        self.status.setText(f"{sum(i.count for i in items):,} replacement(s) in {len(items):,} file(s). "
                            "Word/Excel files are never modified. Review, then press 'Replace in files'.")

    def _show_diff(self):
        r = self.rtable.currentRow()
        if r < 0:
            return
        it = self.rtable.item(r, 0).data(Qt.ItemDataRole.UserRole)
        d = difflib.unified_diff(it.old_text.split("\n"), it.new_text.split("\n"), "before", "after", lineterm="", n=2)
        self.diff.setPlainText("\n".join(d))

    def apply(self):
        if not self.repl:
            QMessageBox.information(self, "Replace", "Run 'Preview replace' first.")
            return
        n = sum(i.count for i in self.repl)
        if QMessageBox.question(self, "Replace in files", f"Write {n:,} replacement(s) to {len(self.repl):,} file(s)?"
                                + ("\nOriginals are backed up first." if self.backup.isChecked() else "\nNo backup will be made!")) != QMessageBox.StandardButton.Yes:
            return
        bdir = os.path.join(app_data_dir(), "backups", "replace") if self.backup.isChecked() else None
        log = msearch.apply_replacements(self.repl, bdir)
        self.diff.setPlainText("\n".join(log))
        self.repl = []
        self.status.setText(f"Done. {sum(l.startswith('OK') for l in log)} file(s) written." + (f" Backups in {bdir}" if bdir else ""))

    def export(self):
        if not self.hits:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export results", "search-results.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if p:
            msearch.export_hits(self.hits, p)


# ====================================================================== Excel workbook
class WorkbookView(_Base):
    """'Normal mode' Excel comparison: sheets paired by name or content (renames),
    cell values, formulas and formatting compared by position; named ranges;
    duplicate keys. The key-based 'Intelligent mode' is the table compare view."""
    openTable = Signal(str, str)
    compared = Signal(str, str, str, str, object)

    def __init__(self, left="", right="", parent=None):
        super().__init__(parent)
        self.wb = (None, None)
        self.changes = []
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        flt = "Excel (*.xlsx *.xlsm *.xls)"
        self.left = PathRow("Left:", text=left, file_filter=flt)
        self.right = PathRow("Right:", text=right, file_filter=flt)
        v.addWidget(self.left)
        v.addWidget(self.right)
        h = QHBoxLayout()
        self.fmt = QCheckBox("Compare formatting")
        self.fmt.setChecked(True)
        self.kind = QComboBox()
        self.kind.addItems(["All", "Values", "Formulas", "Formatting", "Added / removed"])
        self.kind.currentIndexChanged.connect(self._fill)
        for t, slot in (("Compare (F5)", self.run), ("Intelligent mode (key columns)…", self.intelligent),
                        ("Export differences…", self.export)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            h.addWidget(b)
        h.addWidget(self.fmt)
        h.addWidget(QLabel("Show:"))
        h.addWidget(self.kind)
        h.addStretch()
        v.addLayout(h)
        self.tabs = QTabWidget()
        self.pairs = make_table(["Left sheet", "Right sheet", "Pairing", "Differences"])
        self.cells = make_table(["Sheet", "Cell", "Kind", "Left", "Right"])
        self.names = make_table(["Name", "Change", "Left", "Right"])
        dw = QWidget()
        dv = QVBoxLayout(dw)
        dr = QHBoxLayout()
        self.dside = QComboBox()
        self.dside.addItems(["Left", "Right"])
        self.dsheet = QComboBox()
        self.dkeys = QLineEdit("1")
        self.dkeys.setToolTip("Key column numbers, e.g. 1 or 1,3")
        db = QPushButton("Find duplicate keys")
        db.clicked.connect(self.duplicates)
        self.dside.currentIndexChanged.connect(self._sheets_for_dups)
        for w in (QLabel("File:"), self.dside, QLabel("Sheet:"), self.dsheet, QLabel("Key columns:"), self.dkeys, db):
            dr.addWidget(w)
        dr.addStretch()
        dv.addLayout(dr)
        self.dups = make_table(["Key", "Rows"])
        dv.addWidget(self.dups, 1)
        for w, t in ((self.cells, "Cells"), (self.pairs, "Sheets"), (self.names, "Named ranges"), (dw, "Duplicates")):
            self.tabs.addTab(w, t)
        v.addWidget(self.tabs, 1)
        self.status = QLabel()
        v.addWidget(self.status)
        if left and right:
            QTimer.singleShot(0, self.run)

    def title(self):
        return f"{short(self.left.text()) or 'left'} ↔ {short(self.right.text()) or 'right'} (workbook)"

    def _path(self, p):
        return office.convert_legacy(p) if p.lower().endswith(".xls") else p

    def run(self):
        l, r = self.left.text(), self.right.text()
        if not (os.path.isfile(l) and os.path.isfile(r)):
            self.status.setText("Choose two existing workbooks.")
            return
        try:
            a, b = office.read_workbook(self._path(l)), office.read_workbook(self._path(r))
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "Workbook", f"Could not read:\n{ex}")
            return
        self.wb = (a, b)
        self.changes = []
        prow = []
        for ls, rs, st in office.pair_sheets(a, b):
            if ls and rs:
                ch = office.compare_cells(a, b, ls, rs, self.fmt.isChecked())
                self.changes += ch
                prow.append([ls, rs, st, len(ch)])
            else:
                prow.append([ls or "", rs or "", st, "sheet missing"])
        fill_table(self.pairs, prow, [kind_color("renamed") if "renamed" in r[2] else
                                      kind_color("left" if r[2] == "only-left" else "right") if "only" in r[2] else None
                                      for r in prow])
        nm = office.compare_names(a, b)
        fill_table(self.names, [[c.cell, c.kind, c.left, c.right] for c in nm], [kind_color(c.kind) for c in nm])
        self._fill()
        self._sheets_for_dups()
        cnt = {}
        for c in self.changes:
            cnt[c.kind] = cnt.get(c.kind, 0) + 1
        txt = (", ".join(f"{v} {k}" for k, v in cnt.items()) or "No cell differences") + \
              f"; {len(nm)} named-range change(s); {sum('only' in p[2] for p in prow)} sheet(s) missing."
        self.status.setText(txt)
        self.titleChanged.emit()
        self.compared.emit("excel", l, r, txt, not self.changes and not nm)

    def _fill(self, *_):
        k = self.kind.currentIndex()
        want = {1: ("value",), 2: ("formula",), 3: ("format",), 4: ("added", "removed")}.get(k)
        ch = [c for c in self.changes if not want or c.kind in want]
        fill_table(self.cells, [[c.sheet, c.cell, c.kind, c.left, c.right] for c in ch], [kind_color(c.kind) for c in ch])
        self.tabs.setTabText(0, f"Cells ({len(ch)})")

    def _sheets_for_dups(self, *_):
        wb = self.wb[self.dside.currentIndex()]
        self.dsheet.clear()
        if wb:
            self.dsheet.addItems(list(wb["sheets"]))

    def duplicates(self):
        p = (self.left.text(), self.right.text())[self.dside.currentIndex()]
        if not p:
            return
        sheets = loaders.load_table(p)
        sh = next((s for s in sheets if s.name == self.dsheet.currentText()), sheets[0] if sheets else None)
        if not sh:
            return
        keys = [int(x) - 1 for x in self.dkeys.text().split(",") if x.strip().isdigit()]
        d = office.find_duplicate_keys(sh.rows, keys)
        fill_table(self.dups, [[" | ".join(k), ", ".join(map(str, v))] for k, v in d.items()])
        self.status.setText(f"{len(d)} duplicate key(s) in sheet {sh.name}.")

    def intelligent(self):
        self.openTable.emit(self.left.text(), self.right.text())

    def export(self):
        if not self.changes:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export", "workbook-differences.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if not p:
            return
        rows = [["Sheet", "Cell", "Kind", "Left", "Right"]] + [[c.sheet, c.cell, c.kind, c.left, c.right] for c in self.changes]
        if p.lower().endswith(".csv"):
            import csv
            with open(p, "w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh, delimiter=";").writerows(rows)
        else:
            import openpyxl
            from openpyxl.styles import PatternFill
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Differences"
            fills = {"value": "FFF2CC", "formula": "FCE4D6", "format": "EDEDED", "added": "DDEBF7", "removed": "FCE4E4"}
            for r in rows:
                ws.append(r)
                if r[2] in fills:
                    for c in ws[ws.max_row]:
                        c.fill = PatternFill("solid", fgColor=fills[r[2]])
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            wb.save(p)


# ====================================================================== validation
class ValidationView(_Base):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        g1 = QGroupBox("XML against XSD (single file or every .xml in a folder)")
        f1 = QFormLayout(g1)
        self.xml = PathRow("", placeholder="XML file or folder", file_filter="XML (*.xml *.*)")
        pick_dir = QPushButton("Folder…")
        pick_dir.clicked.connect(lambda: self.xml.setText(QFileDialog.getExistingDirectory(self, "Folder") or self.xml.text()))
        xr = QHBoxLayout()
        xr.addWidget(self.xml, 1)
        xr.addWidget(pick_dir)
        f1.addRow("XML:", xr)
        self.xsd = PathRow("", placeholder="Optional XSD (otherwise the schema referenced in each file)", file_filter="XSD (*.xsd)")
        f1.addRow("Schema:", self.xsd)
        b1 = QPushButton("Validate XML")
        b1.clicked.connect(self.validate_xml)
        f1.addRow(b1)
        v.addWidget(g1)
        g2 = QGroupBox("VDI 3805 file")
        f2 = QFormLayout(g2)
        self.vdi = PathRow("", placeholder="VDI 3805 file")
        self.prof = PathRow("", placeholder="Optional profile (JSON)", file_filter="JSON (*.json)")
        f2.addRow("File:", self.vdi)
        f2.addRow("Profile:", self.prof)
        b2 = QPushButton("Validate VDI 3805")
        b2.clicked.connect(self.validate_vdi)
        f2.addRow(b2)
        v.addWidget(g2)
        self.table = make_table(["Severity", "File", "Line", "Record / element", "Message"])
        v.addWidget(self.table, 1)
        h = QHBoxLayout()
        e = QPushButton("Export report…")
        e.clicked.connect(self.export)
        h.addWidget(e)
        h.addStretch()
        v.addLayout(h)
        self.status = QLabel()
        v.addWidget(self.status)

    def title(self):
        return "Validation & Reports"

    def _show(self):
        col = {"error": Theme.folder("different"), "warning": Theme.folder("modified"), "ok": Theme.folder("same")}
        fill_table(self.table, self.rows, [col.get(r[0]) for r in self.rows])
        err = sum(r[0] == "error" for r in self.rows)
        self.status.setText(f"{err} error(s), {sum(r[0] == 'warning' for r in self.rows)} warning(s).")

    def validate_xml(self):
        p, xsd = self.xml.text(), self.xsd.text() or None
        files = [p] if os.path.isfile(p) else [os.path.join(d, f) for d, _, fs in os.walk(p) for f in fs
                                                if f.lower().endswith(".xml")] if os.path.isdir(p) else []
        self.rows = []
        for fpath in files:
            msgs = structdiff.validate_xml(fpath, xsd)
            if not msgs:
                self.rows.append(["ok", fpath, "", "", "valid"])
            for m in msgs:
                line = ""
                if ":" in m and m.split(":")[0].strip().isdigit():
                    line = m.split(":")[0].strip()
                self.rows.append(["error", fpath, line, "", m])
        self._show()

    def validate_vdi(self):
        p = self.vdi.text()
        if not os.path.isfile(p):
            return
        prof = vdi3805.Profile.load(self.prof.text()) if self.prof.text() else vdi3805.Profile()
        ds = vdi3805.load(p, prof)
        self.rows = [[i.severity, p, i.line, i.rtype, i.message] for i in vdi3805.validate(ds)] or [["ok", p, "", "", "no issues found"]]
        self._show()

    def export(self):
        if not self.rows:
            return
        pth, _ = QFileDialog.getSaveFileName(self, "Export", "validation-report.csv", "CSV (*.csv);;Text (*.txt)")
        if not pth:
            return
        import csv
        with open(pth, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";" if pth.lower().endswith(".csv") else "\t")
            w.writerow(["Severity", "File", "Line", "Record / element", "Message"])
            w.writerows(self.rows)


# ====================================================================== automation
JOB_TEMPLATES = {
    "Folder compare": {"name": "Daily project check", "type": "folder_compare", "left": "D:/Projects/A",
                       "right": "//server/Projects/A", "options": {"method": "content", "exclude": "*.tmp;~$*"},
                       "report": ""},
    "File compare": {"name": "Price list check", "type": "file_compare", "left": "C:/data/old.xlsx",
                     "right": "C:/data/new.xlsx", "options": {"key": "1"}},
    "VDI 3805 compare": {"name": "Catalogue update", "type": "vdi_compare", "left": "C:/vdi/old.vdi",
                         "right": "C:/vdi/new.vdi", "options": {"profile": "auto"}},
    "Validate XML folder": {"name": "XML validation", "type": "validate_xml", "folder": "C:/exchange", "xsd": ""},
    "Folder snapshot": {"name": "Watch drawings", "type": "snapshot", "folder": "D:/Projects/A/Drawings"},
    "Directory report": {"name": "Delivery listing", "type": "dir_report", "folder": "D:/Projects/A",
                         "options": {"format": "xlsx", "columns": ["rel", "size_h", "modified", "sha256"],
                                     "include_tree": True}},
    "Duplicate analysis": {"name": "Duplicates", "type": "duplicates", "folders": ["D:/Projects/A"],
                           "options": {"format": "xlsx", "near": True, "versions": True}},
    "Rename preview": {"name": "Naming check", "type": "rename_preview", "folder": "D:/Projects/A/Out",
                       "options": {"profile": ""}},
    "Delivery workflow": {"name": "Delivery check", "type": "batch", "jobs": [
        {"name": "Listing", "type": "dir_report", "folder": "D:/Delivery/new", "options": {"format": "xlsx"}},
        {"name": "Changes since last delivery", "type": "folder_compare", "left": "D:/Delivery/previous",
         "right": "D:/Delivery/new", "options": {"method": "content"}},
        {"name": "XML check", "type": "validate_xml", "folder": "D:/Delivery/new", "xsd": ""},
        {"name": "Duplicates", "type": "duplicates", "folders": ["D:/Delivery/new"], "options": {"near": False}}]},
    "Batch": {"name": "Nightly", "type": "batch", "jobs": []},
}


class AutomationView(_Base):
    def __init__(self, history: automation.History, parent=None):
        super().__init__(parent)
        self.history = history
        self.task = None
        self.dir = os.path.join(app_data_dir(), "jobs")
        os.makedirs(self.dir, exist_ok=True)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        sp = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel(f"Saved jobs ({self.dir})"))
        self.jobs = QListWidget()
        self.jobs.currentTextChanged.connect(self._load)
        lv.addWidget(self.jobs, 1)
        nb = QHBoxLayout()
        self.tpl = QComboBox()
        self.tpl.addItems(list(JOB_TEMPLATES))
        b = QPushButton("New from template")
        b.clicked.connect(self.new_job)
        nb.addWidget(self.tpl)
        nb.addWidget(b)
        lv.addLayout(nb)
        sp.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.editor = QPlainTextEdit()
        self.editor.setFont(mono_font(10))
        rv.addWidget(QLabel("Job definition (JSON)"))
        rv.addWidget(self.editor, 1)
        br = QHBoxLayout()
        for t, slot in (("Save", self.save), ("Run now", self.run), ("Delete", self.delete),
                        ("Windows scheduled task…", self.schedule)):
            bb = QPushButton(t)
            bb.clicked.connect(slot)
            br.addWidget(bb)
        br.addStretch()
        rv.addLayout(br)
        self.result = make_table(["Job", "OK", "Identical", "Summary", "Report"])
        self.result.itemDoubleClicked.connect(self._open_report)
        rv.addWidget(self.result)
        sp.addWidget(right)
        sp.setSizes([260, 800])
        tabs = QTabWidget()
        tabs.addTab(sp, "Jobs")
        hw = QWidget()
        hv = QVBoxLayout(hw)
        self.hist = make_table(["#", "Time", "Kind", "Left", "Right", "Identical", "Summary", "Report"])
        self.hist.itemDoubleClicked.connect(self._open_report)
        hv.addWidget(self.hist, 1)
        rb = QPushButton("Refresh")
        rb.clicked.connect(self.refresh_history)
        hv.addWidget(rb)
        tabs.addTab(hw, "History (audit log)")
        v.addWidget(tabs, 1)
        self.status = QLabel("Jobs can also run unattended: Nexus.exe --job path\\to\\job.json")
        v.addWidget(self.status)
        self.refresh_jobs()
        self.refresh_history()

    def title(self):
        return "Automation"

    def refresh_jobs(self):
        self.jobs.clear()
        for f in sorted(os.listdir(self.dir)):
            if f.endswith(".json"):
                self.jobs.addItem(f)

    def refresh_history(self):
        rows = self.history.recent(500)
        fill_table(self.hist, [[r["id"], r["time"].replace("T", " "), r["kind"], r["left"], r["right"],
                                "" if r["identical"] is None else ("yes" if r["identical"] else "no"),
                                r["summary"], r["report"]] for r in rows],
                   [None if r["identical"] is None else Theme.folder("same" if r["identical"] else "modified") for r in rows])

    def _load(self, name):
        if name:
            with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
                self.editor.setPlainText(fh.read())

    def new_job(self):
        self.jobs.clearSelection()
        self.editor.setPlainText(json.dumps(JOB_TEMPLATES[self.tpl.currentText()], indent=2))

    def _job(self):
        try:
            return json.loads(self.editor.toPlainText())
        except json.JSONDecodeError as ex:
            QMessageBox.warning(self, "Job", f"The job is not valid JSON:\n{ex}")
            return None

    def save(self):
        job = self._job()
        if job is None:
            return None
        name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in job.get("name", "job")).strip() + ".json"
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(job, fh, indent=2)
        self.refresh_jobs()
        self.status.setText(f"Saved {p}")
        return p

    def run(self):
        job = self._job()
        if job is None:
            return
        self.status.setText("Running…")
        self.task = Task(automation.run_job, job, None)
        self.task.done.connect(self._ran)
        self.task.failed.connect(lambda m: self.status.setText("Error: " + m))
        self.task.start()

    def _ran(self, results):
        # record in history from the GUI thread (SQLite connection belongs to it)
        job = self._job() or {}
        for r in results:
            self.history.add(f"job:{job.get('type')}", job.get("left") or job.get("folder", ""), job.get("right", ""),
                             job, r.summary, r.report, r.identical)
        fill_table(self.result, [[r.name, "yes" if r.ok else "no", "" if r.identical is None else ("yes" if r.identical else "no"),
                                  r.summary, r.report] for r in results],
                   [Theme.folder("same" if r.ok and r.identical else "modified" if r.ok else "different") for r in results])
        self.status.setText(f"{len(results)} job(s) finished.")
        self.refresh_history()

    def delete(self):
        it = self.jobs.currentItem()
        if it and QMessageBox.question(self, "Delete", f"Delete job {it.text()}?") == QMessageBox.StandardButton.Yes:
            os.remove(os.path.join(self.dir, it.text()))
            self.refresh_jobs()

    def schedule(self):
        p = self.save()
        if not p:
            return
        exe = sys.executable if getattr(sys, "frozen", False) else "Nexus.exe"
        cmd = automation.schtasks_command(p, exe)
        QGuiApplication.clipboard().setText(cmd)
        QMessageBox.information(self, "Scheduled task", "This command registers a daily Windows scheduled task. It was "
                                "copied to the clipboard; run it in a Command Prompt (Nexus does not change your "
                                f"scheduler by itself):\n\n{cmd}")

    def _open_report(self, it):
        t = it.tableWidget()
        p = t.item(it.row(), t.columnCount() - 1).text()
        if p and os.path.exists(p):
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))


# ====================================================================== settings
class SettingsView(_Base):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = QSettings("Nexus", "Nexus")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        g = QGroupBox("Appearance")
        f = QFormLayout(g)
        from .theme import MODES, MODE_LABELS, stored_mode
        self.theme_modes = list(MODES)
        self.theme = QComboBox()
        self.theme.addItems([MODE_LABELS[m] for m in self.theme_modes])
        self.theme.setCurrentIndex(self.theme_modes.index(stored_mode(self.s)))
        self.theme.setToolTip("Light or dark environment. 'Follow Windows' uses the app mode set in "
                              "Windows Settings > Personalisation > Colours and switches with it.")
        self.font = QSpinBox()
        self.font.setRange(7, 28)
        self.font.setValue(self.s.value("font_size", 10, type=int))
        f.addRow("Environment:", self.theme)
        f.addRow("Editor font size:", self.font)
        v.addWidget(g)
        g2 = QGroupBox("Editor")
        f2 = QFormLayout(g2)
        self.autosave = QSpinBox()
        self.autosave.setRange(0, 120)
        self.autosave.setSuffix(" min")
        self.autosave.setSpecialValueText("off")
        self.autosave.setValue(self.s.value("autosave_min", 2, type=int))
        self.autosave.setToolTip("Unsaved editor tabs are written to a recovery folder (never over your files).")
        self.session = QCheckBox("Reopen files from the last session")
        self.session.setChecked(self.s.value("restore_session", True, type=bool))
        f2.addRow("Recovery snapshots every:", self.autosave)
        f2.addRow(self.session)
        v.addWidget(g2)
        g3 = QGroupBox("Safety")
        f3 = QFormLayout(g3)
        self.backup = QLineEdit(self.s.value("backup_dir", os.path.join(app_data_dir(), "backups")))
        f3.addRow("Backup folder:", self.backup)
        f3.addRow(QLabel("Nexus never changes original files without asking. Sync, replace and rename write "
                         "backups/undo logs here and to the app data folder."))
        v.addWidget(g3)
        g4 = QGroupBox("Optional AI summary (off by default)")
        f4 = QFormLayout(g4)
        self.ai = QCheckBox("Allow sending difference text to the Anthropic API when I click 'Summary'")
        self.ai.setChecked(self.s.value("ai_enabled", False, type=bool))
        self.key = QLineEdit(self.s.value("api_key", ""))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Uses ANTHROPIC_API_KEY if empty")
        f4.addRow(self.ai)
        f4.addRow("API key:", self.key)
        lab = QLabel("Only the list of differences is sent, never whole files, and you are asked every time. "
                     "The key is stored in your user settings (registry on Windows); prefer the environment variable "
                     "on shared machines. Without this, the offline rule-based summary is used.")
        lab.setWordWrap(True)
        f4.addRow(lab)
        v.addWidget(g4)
        h = QHBoxLayout()
        b = QPushButton("Save settings")
        b.clicked.connect(self.save)
        h.addWidget(b)
        o = QPushButton("Open app data folder")
        o.clicked.connect(self._open_data)
        h.addWidget(o)
        h.addStretch()
        v.addLayout(h)
        v.addStretch()

    def title(self):
        return "Settings"

    def save(self):
        self.s.setValue("theme", self.theme_modes[self.theme.currentIndex()])
        self.s.setValue("font_size", self.font.value())
        self.s.setValue("autosave_min", self.autosave.value())
        self.s.setValue("restore_session", self.session.isChecked())
        self.s.setValue("backup_dir", self.backup.text())
        self.s.setValue("ai_enabled", self.ai.isChecked())
        self.s.setValue("api_key", self.key.text())
        self.changed.emit()

    def _open_data(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(app_data_dir()))
