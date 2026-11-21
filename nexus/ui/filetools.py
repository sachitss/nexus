"""File tool views: Bulk Rename (ordered rules), Directory Listing & Reports and
the Duplicate Finder."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal, QSettings
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QComboBox,
                               QTabWidget, QSplitter, QMessageBox, QFileDialog, QPlainTextEdit, QLineEdit,
                               QGroupBox, QFormLayout, QSpinBox, QListWidget, QListWidgetItem, QToolButton, QMenu,
                               QDialog, QDialogButtonBox, QAbstractItemView, QGridLayout, QDoubleSpinBox)

from ..core import renamer, dirreport, duplicates, fileops
from .common import Task, PathRow, make_table, fill_table, fmt_size
from .theme import Theme, mono_font

TOKEN_HELP = ("Tokens: {name} {ext} {orig} {parent} {grandparent} {n} {num} {num:4} {size} {size_kb} "
              "{mtime:%Y-%m-%d} {ctime:%Y%m%d} {now:%Y%m%d} {line1} {content:REGEX}")


# ================================================================ rename
class RuleDialog(QDialog):
    """Form generated from renamer.RULE_TYPES for one rule."""

    def __init__(self, rule: dict, parent=None):
        super().__init__(parent)
        self.rule = dict(rule)
        label, params = renamer.RULE_TYPES[rule["type"]]
        self.setWindowTitle(label)
        self.setMinimumWidth(520)
        f = QFormLayout(self)
        self.widgets = {}
        for p, kind, default, text in params:
            val = self.rule.get(p, default)
            if kind == "bool":
                w = QCheckBox()
                w.setChecked(bool(val))
            elif kind == "int":
                w = QSpinBox()
                w.setRange(-9999, 999999)
                w.setValue(int(val))
            elif kind.startswith("choice:"):
                w = QComboBox()
                w.addItems(kind.split(":", 1)[1].split("|"))
                w.setCurrentText(str(val))
            elif kind == "file":
                w = PathRow("", file_filter="Lists (*.csv *.txt *.tsv *.xlsx *.xlsm);;All files (*)", text=str(val))
            else:
                w = QLineEdit(str(val))
            self.widgets[p] = (kind, w)
            f.addRow(text + ":", w)
        if rule["type"] in ("insert", "add", "template"):
            hint = QLabel(TOKEN_HELP)
            hint.setWordWrap(True)
            hint.setObjectName("Hint")
            f.addRow(hint)
        if rule["type"] == "mapping":
            hint = QLabel("Column 1: current name (or stem / full path), column 2: new name. "
                          "A header row such as old;new is skipped. Unlisted files keep their name.")
            hint.setWordWrap(True)
            f.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def value(self) -> dict:
        for p, (kind, w) in self.widgets.items():
            if kind == "bool":
                self.rule[p] = w.isChecked()
            elif kind == "int":
                self.rule[p] = w.value()
            elif kind.startswith("choice:"):
                self.rule[p] = w.currentText()
            else:
                self.rule[p] = w.text()
        return self.rule


class RenamePanel(QWidget):
    """Bulk rename with an ordered rule list, a full preview and explicit confirmation."""
    openFile = Signal(str)

    def __init__(self):
        super().__init__()
        self.rules: list[dict] = []
        self.rows: list[dict] = []
        self._checked_interrupted = False
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        h = QHBoxLayout()
        for t, slot in (("Add files…", self.add_files), ("Add folder contents…", self.add_folder),
                        ("Add folder contents (with subfolders)…", lambda: self.add_folder(True)),
                        ("Add folders themselves…", self.add_dirs), ("Remove selected", self.remove_selected),
                        ("Clear", self.clear)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            h.addWidget(b)
        h.addStretch()
        self.count = QLabel()
        h.addWidget(self.count)
        v.addLayout(h)

        sp = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("<b>Rules</b> (run top to bottom; untick to skip)"))
        self.rule_list = QListWidget()
        self.rule_list.itemChanged.connect(self._rule_toggled)
        self.rule_list.itemDoubleClicked.connect(lambda _it: self.edit_rule())
        lv.addWidget(self.rule_list, 1)
        rb = QHBoxLayout()
        add = QToolButton(text="Add rule ▾")
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(add)
        for t, (label, _p) in renamer.RULE_TYPES.items():
            m.addAction(label, lambda t=t: self.add_rule(t))
        add.setMenu(m)
        rb.addWidget(add)
        for t, slot in (("Edit", self.edit_rule), ("▲", lambda: self.move_rule(-1)), ("▼", lambda: self.move_rule(1)),
                        ("Remove", self.remove_rule)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            rb.addWidget(b)
        lv.addLayout(rb)
        pb = QHBoxLayout()
        self.profiles = QComboBox()
        self.profiles.setMinimumWidth(140)
        self._fill_profiles()
        pb.addWidget(QLabel("Profile:"))
        pb.addWidget(self.profiles, 1)
        for t, slot in (("Load", self.load_profile), ("Save as…", self.save_profile)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            pb.addWidget(b)
        lv.addLayout(pb)
        sp.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Preview</b> (nothing is renamed until you confirm)"))
        top.addStretch()
        self.only_changes = QCheckBox("Only changed / problems")
        self.only_changes.toggled.connect(self._show)
        top.addWidget(self.only_changes)
        rv.addLayout(top)
        self.table = make_table(["#", "Status", "Current name", "New name", "Folder", "Note"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        rv.addWidget(self.table, 1)
        sp.addWidget(right)
        sp.setSizes([380, 900])
        v.addWidget(sp, 1)

        h2 = QHBoxLayout()
        for t, slot in (("Rename…", self.apply), ("Undo last rename…", self.undo_last), ("History…", self.show_history),
                        ("Export preview…", self.export_preview)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            h2.addWidget(b)
        h2.addStretch()
        v.addLayout(h2)
        self.status = QLabel("Add files or folders, then add rules. Nothing is renamed until you press Rename and confirm.")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.paths_list: list[str] = []

    # ---------- items
    def paths(self):
        return list(self.paths_list)

    def add_paths(self, ps, folders_too=False):
        have = {os.path.normcase(p) for p in self.paths_list}
        for p in ps:
            if (os.path.isfile(p) or folders_too and os.path.isdir(p)) and os.path.normcase(p) not in have:
                self.paths_list.append(p)
                have.add(os.path.normcase(p))
        self.preview()

    def add_files(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Add files")
        self.add_paths(ps)

    def add_folder(self, recursive=False):
        d = QFileDialog.getExistingDirectory(self, "Add the files of a folder")
        if not d:
            return
        if recursive:
            ps = [os.path.join(dp, n) for dp, _ds, ns in os.walk(d) for n in sorted(ns)]
        else:
            ps = sorted(os.path.join(d, n) for n in os.listdir(d))
        self.add_paths(ps)

    def add_dirs(self):
        d = QFileDialog.getExistingDirectory(self, "Add the subfolders of a folder (the folders get renamed)")
        if d:
            self.add_paths(sorted(os.path.join(d, n) for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))),
                           folders_too=True)

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        drop = {self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows}
        self.paths_list = [p for p in self.paths_list if p not in drop]
        self.preview()

    def clear(self):
        self.paths_list = []
        self.preview()

    # ---------- rules
    def set_rules(self, rules):
        self.rules = [dict(r) for r in rules]
        self._fill_rules()
        self.preview()

    def _fill_rules(self):
        self.rule_list.blockSignals(True)
        self.rule_list.clear()
        for i, r in enumerate(self.rules, 1):
            it = QListWidgetItem(f"{i}. {renamer.describe(r)}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if r.get("enabled", True) else Qt.CheckState.Unchecked)
            self.rule_list.addItem(it)
        self.rule_list.blockSignals(False)

    def _rule_toggled(self, it):
        i = self.rule_list.row(it)
        self.rules[i]["enabled"] = it.checkState() == Qt.CheckState.Checked
        self.preview()

    def add_rule(self, rtype):
        d = RuleDialog(renamer.new_rule(rtype), self)
        if d.exec():
            self.rules.append(d.value())
            self._fill_rules()
            self.rule_list.setCurrentRow(len(self.rules) - 1)
            self.preview()

    def edit_rule(self):
        i = self.rule_list.currentRow()
        if i < 0:
            return
        d = RuleDialog(self.rules[i], self)
        if d.exec():
            self.rules[i] = d.value()
            self._fill_rules()
            self.rule_list.setCurrentRow(i)
            self.preview()

    def move_rule(self, step):
        i = self.rule_list.currentRow()
        j = i + step
        if i < 0 or not 0 <= j < len(self.rules):
            return
        self.rules[i], self.rules[j] = self.rules[j], self.rules[i]
        self._fill_rules()
        self.rule_list.setCurrentRow(j)
        self.preview()

    def remove_rule(self):
        i = self.rule_list.currentRow()
        if i >= 0:
            del self.rules[i]
            self._fill_rules()
            self.preview()

    def _fill_profiles(self):
        self.profiles.clear()
        for p in renamer.list_profiles():
            self.profiles.addItem(os.path.splitext(os.path.basename(p))[0], p)

    def load_profile(self):
        p = self.profiles.currentData()
        if p:
            try:
                self.set_rules(renamer.load_profile(p))
            except (OSError, ValueError) as ex:
                QMessageBox.warning(self, "Profile", str(ex))

    def save_profile(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save rename profile", "Profile name:")
        if ok and name:
            renamer.save_profile(name, self.rules)
            self._fill_profiles()
            self.profiles.setCurrentText(name)

    # ---------- preview / execute
    def preview(self, *_):
        self.rows = renamer.preview(self.paths_list, self.rules)
        self._show()

    def _show(self, *_):
        col = {"ok": Theme.folder("new"), "conflict": Theme.folder("conflict"), "invalid": Theme.folder("different"),
               "error": Theme.folder("different"), "unchanged": None}
        rows = [(i, r) for i, r in enumerate(self.rows, 1)
                if not self.only_changes.isChecked() or r["status"] != "unchanged"]
        fill_table(self.table, [[i, r["status"], r["old"] + ("/" if r["is_dir"] else ""), r["new"],
                                 os.path.dirname(r["path"]), r["note"]] for i, r in rows],
                   [col.get(r["status"]) for _i, r in rows], data=[r["path"] for _i, r in rows])
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)       # keep the processing order
        s = renamer.summary(self.rows)
        bad = s["conflict"] + s["invalid"] + s["error"]
        self.count.setText(f"{len(self.paths_list):,} item(s)")
        self.status.setText(f"{s['ok']} will be renamed, {s['unchanged']} unchanged, {bad} problem(s)"
                            f" ({s['conflict']} conflict, {s['invalid']} invalid, {s['error']} error). "
                            "Nothing is renamed until you confirm.")

    def apply(self):
        self.preview()
        s = renamer.summary(self.rows)
        if not s["ok"]:
            QMessageBox.information(self, "Rename", "Nothing to rename: no name changes in the preview.")
            return
        if s["conflict"] or s["invalid"] or s["error"]:
            QMessageBox.warning(self, "Rename", "The preview has conflicts or invalid names. Fix the rules "
                                "(or remove those items) first; nothing was renamed.")
            return
        ok_rows = [r for r in self.rows if r["status"] == "ok"]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Confirm rename")
        box.setText(f"Rename {len(ok_rows)} item(s)?")
        box.setInformativeText("The complete before → after list is under 'Show Details'. A journal is written "
                               "first, and if any rename fails everything is rolled back. You can undo afterwards.")
        box.setDetailedText("\n".join(f"{r['old']}  →  {r['new']}    ({os.path.dirname(r['path'])})" for r in ok_rows))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if self.confirm(box) != QMessageBox.StandardButton.Yes:
            self.status.setText("Cancelled; nothing was renamed.")
            return
        res = renamer.execute(self.rows, rules=self.rules)
        QSettings("Nexus", "Nexus").setValue("last_rename_log", res["journal"])
        if res["error"]:
            QMessageBox.warning(self, "Rename", f"Stopped and rolled back, nothing is renamed:\n{res['error']}")
            self.preview()
            return
        moved = {os.path.normcase(d["from"]): d["to"] for d in res["done"]}
        self.paths_list = [moved.get(os.path.normcase(p), p) for p in self.paths_list]
        self.preview()
        self.status.setText(f"Renamed {len(res['done'])} item(s). Journal: {res['journal']}")

    @staticmethod
    def confirm(box):
        """Shows the confirmation dialog (separate so tests can answer it)."""
        return box.exec()

    def undo_last(self):
        log = QSettings("Nexus", "Nexus").value("last_rename_log", "")
        if not log or not os.path.exists(log):
            log, _ = QFileDialog.getOpenFileName(self, "Choose rename journal", renamer.history_dir(), "JSON (*.json)")
        self._undo(log)

    def _undo(self, log):
        if log and QMessageBox.question(self, "Undo rename", f"Undo the renames recorded in\n{log}?") == QMessageBox.StandardButton.Yes:
            res = renamer.undo(log)
            msg = f"Restored {res['restored']} name(s)."
            if res["skipped"]:
                msg += f" {len(res['skipped'])} could not be restored (changed since):\n" + "\n".join(res["skipped"][:20])
            QMessageBox.information(self, "Undo rename", msg)
            self.clear()

    def show_history(self):
        d = QDialog(self)
        d.setWindowTitle("Rename history")
        d.resize(820, 420)
        lv = QVBoxLayout(d)
        t = make_table(["Time", "Status", "Items", "First change", "Journal"])
        hist = renamer.history()
        fill_table(t, [[h["time"], h["status"], h["count"], h["first"], h["journal"]] for h in hist],
                   data=[h["journal"] for h in hist])
        lv.addWidget(t)
        bb = QHBoxLayout()
        b = QPushButton("Undo selected…")
        b.clicked.connect(lambda: t.currentRow() >= 0 and self._undo(t.item(t.currentRow(), 0).data(Qt.ItemDataRole.UserRole)))
        bb.addWidget(b)
        bb.addStretch()
        c = QPushButton("Close")
        c.clicked.connect(d.accept)
        bb.addWidget(c)
        lv.addLayout(bb)
        d.exec()

    def export_preview(self):
        if not self.rows:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export preview", "rename-preview.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if p:
            renamer.export_preview(self.rows, p)

    def showEvent(self, e):
        super().showEvent(e)
        if self._checked_interrupted:
            return
        self._checked_interrupted = True
        try:
            pending = renamer.interrupted()
        except OSError:
            pending = []
        for j in pending:
            if QMessageBox.question(self, "Interrupted rename",
                                    f"A rename run did not finish (the program was closed or crashed):\n{j}\n\n"
                                    "Restore the original names now?") == QMessageBox.StandardButton.Yes:
                renamer.undo(j)


# ================================================================ directory listing & reports
class ReportPanel(QWidget):
    """Directory listing with filters, columns, grouping, statistics, tree and export."""
    openFile = Signal(str)

    def __init__(self, path=""):
        super().__init__()
        self.files = []
        self.rep = None
        self.task = None
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        self.root = PathRow("Folder:", folder=True, text=path)
        v.addWidget(self.root)
        h = QHBoxLayout()
        self.rec = QCheckBox("Subfolders")
        self.rec.setChecked(True)
        self.depth = QSpinBox()
        self.depth.setRange(0, 99)
        self.depth.setSpecialValueText("any depth")
        self.scan_exc = QLineEdit(".git;__pycache__;node_modules")
        self.scan_exc.setToolTip("Folders/files skipped while scanning (wildcards, ';' separated)")
        b = QPushButton("Scan")
        b.clicked.connect(self.scan)
        for w in (self.rec, QLabel("Max depth:"), self.depth, QLabel("Skip:"), self.scan_exc, b):
            h.addWidget(w)
        v.addLayout(h)

        opts = QGroupBox("Filters, columns and layout (applied instantly to the last scan)")
        g = QGridLayout(opts)
        self.pattern = QLineEdit()
        self.pattern.setPlaceholderText("*.pdf;*.dwg")
        self.regex = QLineEdit()
        self.regex.setPlaceholderText("regular expression on the name")
        self.exclude = QLineEdit()
        self.exclude.setPlaceholderText("*.tmp;~$*")
        self.min_kb, self.max_kb = QSpinBox(), QSpinBox()
        for s in (self.min_kb, self.max_kb):
            s.setRange(0, 2_000_000_000)
            s.setSuffix(" KB")
            s.setSpecialValueText("–")
        self.mod_from, self.mod_to, self.cre_from, self.cre_to = (QLineEdit() for _ in range(4))
        for w in (self.mod_from, self.mod_to, self.cre_from, self.cre_to):
            w.setPlaceholderText("YYYY-MM-DD")
            w.setMaximumWidth(110)
        self.show_files, self.show_dirs, self.hidden, self.only_empty = (QCheckBox(t) for t in
                                                                         ("Files", "Folders", "Hidden", "Only empty folders"))
        for c in (self.show_files, self.show_dirs, self.hidden):
            c.setChecked(True)
        g.addWidget(QLabel("Names:"), 0, 0)
        g.addWidget(self.pattern, 0, 1)
        g.addWidget(QLabel("Regex:"), 0, 2)
        g.addWidget(self.regex, 0, 3)
        g.addWidget(QLabel("Exclude:"), 0, 4)
        g.addWidget(self.exclude, 0, 5)
        g.addWidget(QLabel("Size from/to:"), 1, 0)
        sz = QHBoxLayout()
        sz.addWidget(self.min_kb)
        sz.addWidget(self.max_kb)
        g.addLayout(sz, 1, 1)
        g.addWidget(QLabel("Modified:"), 1, 2)
        md = QHBoxLayout()
        md.addWidget(self.mod_from)
        md.addWidget(QLabel("–"))
        md.addWidget(self.mod_to)
        g.addLayout(md, 1, 3)
        g.addWidget(QLabel("Created:"), 1, 4)
        cd = QHBoxLayout()
        cd.addWidget(self.cre_from)
        cd.addWidget(QLabel("–"))
        cd.addWidget(self.cre_to)
        g.addLayout(cd, 1, 5)
        show = QHBoxLayout()
        for c in (self.show_files, self.show_dirs, self.hidden, self.only_empty):
            show.addWidget(c)
        g.addLayout(show, 2, 0, 1, 2)
        self.sort = QComboBox()
        self.sort.addItems(["rel", "name", "ext", "size", "modified", "created", "depth"])
        self.desc = QCheckBox("descending")
        self.group = QComboBox()
        self.group.addItems(["", "ext", "folder", "type", "year", "month"])
        lay = QHBoxLayout()
        for w in (QLabel("Sort:"), self.sort, self.desc, QLabel("Group:"), self.group):
            lay.addWidget(w)
        g.addLayout(lay, 2, 2, 1, 2)
        self.tree_on, self.stats_on = QCheckBox("Tree diagram"), QCheckBox("Statistics")
        self.stats_on.setChecked(True)
        lay2 = QHBoxLayout()
        lay2.addWidget(self.tree_on)
        lay2.addWidget(self.stats_on)
        g.addLayout(lay2, 2, 4, 1, 2)
        self.cols = QListWidget()
        self.cols.setFlow(QListWidget.Flow.LeftToRight)
        self.cols.setWrapping(True)
        self.cols.setMaximumHeight(56)
        self.cols.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for key in dirreport.DEFAULT_COLUMNS + [k for k in dirreport.COLUMNS if k not in dirreport.DEFAULT_COLUMNS]:
            it = QListWidgetItem(dirreport.COLUMNS[key][0])
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if key in dirreport.DEFAULT_COLUMNS else Qt.CheckState.Unchecked)
            self.cols.addItem(it)
        g.addWidget(QLabel("Columns:"), 3, 0)
        g.addWidget(self.cols, 3, 1, 1, 5)
        self.title = QLineEdit("Directory listing")
        self.header = QLineEdit()
        self.header.setPlaceholderText("optional line above the title, e.g. project / company")
        g.addWidget(QLabel("Title:"), 4, 0)
        g.addWidget(self.title, 4, 1, 1, 2)
        g.addWidget(QLabel("Header:"), 4, 3)
        g.addWidget(self.header, 4, 4, 1, 2)
        v.addWidget(opts)

        h2 = QHBoxLayout()
        self.fmt = QComboBox()
        self.fmt.addItems([f.upper() for f in dirreport.FORMATS])
        for t, slot in (("Export…", self.export), ("Print preview…", self.print_preview),
                        ("Save template…", self.save_template), ("Load template…", self.load_template)):
            if t == "Export…":
                h2.addWidget(QLabel("Format:"))
                h2.addWidget(self.fmt)
            b = QPushButton(t)
            b.clicked.connect(slot)
            h2.addWidget(b)
        h2.addStretch()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Quick filter…")
        self.filter.textChanged.connect(self._fill)
        h2.addWidget(self.filter, 1)
        v.addLayout(h2)

        sp = QSplitter()
        self.table = make_table(["Path"])
        self.table.itemDoubleClicked.connect(self._open)
        sp.addWidget(self.table)
        self.side = QTabWidget()
        self.stats_text = QPlainTextEdit()
        self.stats_text.setReadOnly(True)
        self.tree_text = QPlainTextEdit()
        self.tree_text.setReadOnly(True)
        self.tree_text.setFont(mono_font())
        self.ext = make_table(["Extension", "Files", "Size"])
        self.sizes = make_table(["Folder", "Size", "Files"])
        for w, t in ((self.stats_text, "Statistics"), (self.ext, "Extensions"), (self.sizes, "Folder sizes"),
                     (self.tree_text, "Tree")):
            self.side.addTab(w, t)
        sp.addWidget(self.side)
        sp.setSizes([860, 380])
        v.addWidget(sp, 1)
        self.status = QLabel()
        v.addWidget(self.status)
        for w in (self.pattern, self.regex, self.exclude, self.mod_from, self.mod_to, self.cre_from, self.cre_to,
                  self.title, self.header):
            w.editingFinished.connect(self.rebuild)
        for w in (self.min_kb, self.max_kb):
            w.valueChanged.connect(self.rebuild)
        for w in (self.show_files, self.show_dirs, self.hidden, self.only_empty, self.desc, self.tree_on, self.stats_on):
            w.toggled.connect(self.rebuild)
        for w in (self.sort, self.group):
            w.currentIndexChanged.connect(self.rebuild)
        self.cols.itemChanged.connect(self.rebuild)
        self.cols.model().rowsMoved.connect(self.rebuild)

    # ---------- options
    def options(self) -> dirreport.ReportOptions:
        cols = [self.cols.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.cols.count())
                if self.cols.item(i).checkState() == Qt.CheckState.Checked]
        return dirreport.ReportOptions(
            title=self.title.text() or "Directory listing", columns=cols, name_pattern=self.pattern.text(),
            name_regex=self.regex.text(), exclude_pattern=self.exclude.text(),
            min_size=self.min_kb.value() * 1024 or None, max_size=self.max_kb.value() * 1024 or None,
            modified_from=self.mod_from.text().strip(), modified_to=self.mod_to.text().strip(),
            created_from=self.cre_from.text().strip(), created_to=self.cre_to.text().strip(),
            files=self.show_files.isChecked(), folders=self.show_dirs.isChecked(), hidden=self.hidden.isChecked(),
            only_empty_folders=self.only_empty.isChecked(), sort_by=self.sort.currentText(),
            descending=self.desc.isChecked(), group_by=self.group.currentText(), include_tree=self.tree_on.isChecked(),
            include_stats=self.stats_on.isChecked(), header_text=self.header.text())

    def set_options(self, o: dirreport.ReportOptions):
        ws = [self.pattern, self.regex, self.exclude, self.mod_from, self.mod_to, self.cre_from, self.cre_to,
              self.title, self.header]
        for w, val in zip(ws, (o.name_pattern, o.name_regex, o.exclude_pattern, o.modified_from, o.modified_to,
                               o.created_from, o.created_to, o.title, o.header_text)):
            w.setText(val)
        self.min_kb.setValue((o.min_size or 0) // 1024)
        self.max_kb.setValue((o.max_size or 0) // 1024)
        for c, val in ((self.show_files, o.files), (self.show_dirs, o.folders), (self.hidden, o.hidden),
                       (self.only_empty, o.only_empty_folders), (self.desc, o.descending), (self.tree_on, o.include_tree),
                       (self.stats_on, o.include_stats)):
            c.setChecked(val)
        self.sort.setCurrentText(o.sort_by)
        self.group.setCurrentText(o.group_by)
        self.cols.blockSignals(True)
        items = [self.cols.takeItem(0) for _ in range(self.cols.count())]
        byk = {it.data(Qt.ItemDataRole.UserRole): it for it in items}
        for k in o.columns + [k for k in byk if k not in o.columns]:
            if k in byk:
                it = byk[k]
                it.setCheckState(Qt.CheckState.Checked if k in o.columns else Qt.CheckState.Unchecked)
                self.cols.addItem(it)
        self.cols.blockSignals(False)
        self.rebuild()

    # ---------- scan / build
    def scan(self):
        r = self.root.text()
        if not os.path.isdir(r):
            self.status.setText("Choose an existing folder.")
            return
        self.status.setText("Scanning…")
        self.task = Task(fileops.list_directory, r, self.rec.isChecked(), "*", self.scan_exc.text(), True,
                         self.depth.value() or None)
        self.task.done.connect(self._scanned)
        self.task.failed.connect(lambda m: self.status.setText("Error: " + m))
        self.task.start()

    def _scanned(self, files):
        self.files = files
        self.rebuild()

    def rebuild(self, *_):
        if not self.files:
            return
        try:
            self.rep = dirreport.build(self.root.text(), self.files, self.options())
        except Exception as ex:  # noqa: BLE001 - bad regex / date shown to the user
            self.status.setText(f"Filter error: {ex}")
            return
        self._fill()
        s = self.rep.stats
        if s:
            self.stats_text.setPlainText(
                f"Files: {s['files']:,}\nFolders: {s['folders']:,}\nTotal size: {fmt_size(s['bytes'])}\n"
                f"Oldest change: {s['oldest']}\nNewest change: {s['newest']}\n"
                f"Empty folders: {len(s['empty_folders'])}\n" + "".join(f"  {p}\n" for p in s["empty_folders"][:200]) +
                "\nLargest files:\n" + "".join(f"  {fmt_size(n):>10}  {p}\n" for p, n in s["largest"]))
            fill_table(self.ext, [[e, c, fmt_size(b)] for e, c, b in s["extensions"]])
        fill_table(self.sizes, [[k, fmt_size(b), c] for k, b, c in fileops.folder_sizes(
            [f for f in self.rep.items if not f.is_dir] or self.files, self.root.text())])
        self.tree_text.setPlainText(self.rep.tree or "Tick 'Tree diagram' to build the folder tree.")
        n = len(self.rep.items)
        self.status.setText(f"{n:,} of {len(self.files):,} entries shown."
                            + (f" {s['files']:,} files, {fmt_size(s['bytes'])}." if s else ""))

    def _fill(self, *_):
        if not self.rep:
            return
        q = self.filter.text().lower()
        grouped = bool(self.rep.options.group_by)
        head = (["Group"] if grouped else []) + self.rep.headers
        self.table.setColumnCount(len(head))
        self.table.setHorizontalHeaderLabels(head)
        rows, data = [], []
        k = 0
        for g, rs in self.rep.groups:
            for r in rs:
                f = self.rep.items[k]
                k += 1
                if q and q not in f.rel.lower():
                    continue
                rows.append(([g] if grouped else []) + r)
                data.append(f)
        fill_table(self.table, rows, data=data)

    def _open(self, it):
        f = self.table.item(it.row(), 0).data(Qt.ItemDataRole.UserRole)
        if f and not f.is_dir:
            self.openFile.emit(f.path)

    # ---------- output
    def _branding(self):
        from .. import APP_NAME, POWERED_BY, __version__
        return f"{APP_NAME} {__version__} · {POWERED_BY}"

    def export(self):
        if not self.rep:
            self.status.setText("Scan a folder first.")
            return
        fmt = self.fmt.currentText().lower()
        p, _ = QFileDialog.getSaveFileName(self, "Export report", f"directory-list.{fmt}", f"*.{fmt}")
        if p:
            try:
                dirreport.export(self.rep, p, fmt, self._branding())
                self.status.setText(f"Exported {len(self.rep.items):,} entries to {p}")
            except Exception as ex:  # noqa: BLE001
                QMessageBox.warning(self, "Export", str(ex))

    def print_preview(self):
        if not self.rep:
            return
        from PySide6.QtGui import QTextDocument
        from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
        doc = QTextDocument()
        doc.setHtml(dirreport.to_html(self.rep, self._branding()))
        pr = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintPreviewDialog(pr, self)
        dlg.paintRequested.connect(doc.print_)
        dlg.resize(1000, 760)
        dlg.exec()

    def save_template(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save report template", "Template name:")
        if ok and name:
            p = dirreport.save_template(self.options(), name)
            self.status.setText(f"Template saved: {p}")

    def load_template(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load report template", dirreport.template_dir(), "JSON (*.json)")
        if p:
            self.set_options(dirreport.load_template(p))


# ================================================================ duplicate finder
class DuplicateView(QWidget):
    """Module 11: duplicate files and version analysis. Never deletes anything."""
    titleChanged = Signal()
    openFile = Signal(str)
    openPair = Signal(str, str)

    def __init__(self, root="", parent=None):
        super().__init__(parent)
        self.res = None
        self.task = None
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        f = QFormLayout()
        self.roots = PathRow("", folder=True, text=root, placeholder="one or more folders, separated by ;")
        self.roots.browse = self._add_root
        f.addRow("Folders:", self.roots)
        row = QHBoxLayout()
        self.inc = QLineEdit("*")
        self.exc = QLineEdit(".git;__pycache__;~$*;Thumbs.db")
        self.min_kb = QSpinBox()
        self.min_kb.setRange(0, 10_000_000)
        self.min_kb.setSuffix(" KB")
        self.near = QCheckBox("Near-duplicate text")
        self.near.setChecked(True)
        self.thr = QDoubleSpinBox()
        self.thr.setRange(50, 99.9)
        self.thr.setValue(90)
        self.thr.setSuffix(" %")
        self.vers = QCheckBox("Version series")
        self.vers.setChecked(True)
        self.same_folder = QCheckBox("versions only within one folder")
        for w in (QLabel("Include:"), self.inc, QLabel("Exclude:"), self.exc, QLabel("Min size:"), self.min_kb,
                  self.near, self.thr, self.vers, self.same_folder):
            row.addWidget(w)
        f.addRow(row)
        v.addLayout(f)
        h = QHBoxLayout()
        for t, slot in (("Analyse", self.analyse), ("Cancel", self.cancel), ("Open", self.open_sel),
                        ("Compare two selected", self.compare_sel), ("Copy paths", self.copy_paths),
                        ("Export…", self.export)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            h.addWidget(b)
        h.addStretch()
        v.addLayout(h)
        self.tabs = QTabWidget()
        self.t_ident = make_table(["Group", "Size", "Copies", "Path"])
        self.t_names = make_table(["Group", "Name", "Size", "Modified", "Path"])
        self.t_renamed = make_table(["Group", "Size", "Name", "Path"])
        self.t_near = make_table(["Similarity", "File A", "File B"])
        self.t_vers = make_table(["Series", "Status", "Modified", "Size", "Path"])
        self.t_groups = make_table(["Grouping", "Key", "Files", "Size"])
        for w, t in ((self.t_ident, "Identical content"), (self.t_renamed, "Same content, different names"),
                     (self.t_names, "Same file name"), (self.t_near, "Near-duplicate text"),
                     (self.t_vers, "Version series"), (self.t_groups, "By extension / size / date")):
            w.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            w.itemDoubleClicked.connect(lambda it, w=w: self._open_row(w, it.row()))
            self.tabs.addTab(w, t)
        v.addWidget(self.tabs, 1)
        self.status = QLabel("Nothing is ever deleted or moved here: review, open, compare and export the lists.")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

    def title(self):
        return "Duplicate Finder"

    def has_unsaved(self):
        return False

    def _add_root(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder")
        if d:
            cur = [p for p in self.roots.text().split(";") if p.strip()]
            self.roots.setText(";".join(cur + [d]))

    def root_list(self):
        return [p.strip() for p in self.roots.text().split(";") if p.strip()]

    def analyse(self):
        roots = [r for r in self.root_list() if os.path.isdir(r)]
        if not roots:
            self.status.setText("Choose at least one existing folder.")
            return
        self.status.setText("Scanning and hashing…")
        self.task = Task(duplicates.analyse, roots, self.inc.text() or "*", self.exc.text(), max(self.min_kb.value() * 1024, 1),
                         self.thr.value() / 100, self.near.isChecked(), self.vers.isChecked(), self.same_folder.isChecked())
        self.task.kw["cancel"] = lambda: self.task is not None and self.task.cancelled
        self.task.done.connect(self._done)
        self.task.failed.connect(lambda m: self.status.setText("Error: " + m))
        self.task.start()

    def cancel(self):
        if self.task:
            self.task.cancel()
            self.status.setText("Cancelling…")

    def _done(self, res):
        self.res = res
        import datetime as _dt
        mt = lambda p: _dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M") if os.path.exists(p) else ""  # noqa: E731
        sz = lambda p: fmt_size(os.path.getsize(p)) if os.path.exists(p) else ""  # noqa: E731
        alt = [Theme.folder("different"), Theme.folder("modified")]
        rows, cols, data = [], [], []
        for i, g in enumerate(res.identical, 1):
            for p in g["paths"]:
                rows.append([i, fmt_size(g["size"]), len(g["paths"]), p])
                cols.append(alt[i % 2])
                data.append(p)
        fill_table(self.t_ident, rows, cols, data)
        rows, data = [], []
        for i, g in enumerate(res.renamed_copies, 1):
            for p in g["paths"]:
                rows.append([i, fmt_size(g["size"]), os.path.basename(p), p])
                data.append(p)
        fill_table(self.t_renamed, rows, data=data)
        rows, data = [], []
        for i, g in enumerate(res.same_name, 1):
            for p in g["paths"]:
                rows.append([i, g["name"], sz(p), mt(p), p])
                data.append(p)
        fill_table(self.t_names, rows, data=data)
        fill_table(self.t_near, [[f"{n['similarity']:.1%}", n["a"], n["b"]] for n in res.near],
                   data=[(n["a"], n["b"]) for n in res.near])
        rows, cols, data = [], [], []
        for s in res.versions:
            for fi in s["files"]:
                rows.append([s["series"], "newest" if fi["newest"] else "older", fi["modified"], fmt_size(fi["size"]), fi["path"]])
                cols.append(Theme.folder("new") if fi["newest"] else None)
                data.append(fi["path"])
        fill_table(self.t_vers, rows, cols, data)
        rows = ([["Extension", k, c, fmt_size(b)] for k, c, b in res.by_ext] +
                [["Size", k, c, fmt_size(b)] for k, c, b in res.by_size] +
                [["Month modified", k, c, fmt_size(b)] for k, c, b in res.by_month])
        fill_table(self.t_groups, rows)
        for w, n in ((self.t_ident, len(res.identical)), (self.t_renamed, len(res.renamed_copies)),
                     (self.t_names, len(res.same_name)), (self.t_near, len(res.near)), (self.t_vers, len(res.versions))):
            i = self.tabs.indexOf(w)
            self.tabs.setTabText(i, self.tabs.tabText(i).split(" (")[0] + f" ({n})")
        msg = (f"{res.files:,} files analysed. {len(res.identical)} group(s) of identical files, "
               f"{fmt_size(res.wasted)} held by extra copies. Nothing was deleted.")
        if res.errors:
            msg += f" {len(res.errors)} file(s) could not be read (locked or no access)."
        self.status.setText(msg)

    def _paths_of(self, w, row):
        d = w.item(row, 0).data(Qt.ItemDataRole.UserRole) if w.item(row, 0) else None
        return list(d) if isinstance(d, (tuple, list)) else [d] if d else []

    def _selected_paths(self):
        w = self.tabs.currentWidget()
        rows = sorted({i.row() for i in w.selectedIndexes()})
        out = []
        for r in rows:
            out += self._paths_of(w, r)
        return out

    def _open_row(self, w, row):
        ps = self._paths_of(w, row)
        if len(ps) == 2:
            self.openPair.emit(*ps)
        elif ps and os.path.isfile(ps[0]):
            self.openFile.emit(ps[0])

    def open_sel(self):
        for p in self._selected_paths()[:20]:
            if os.path.isfile(p):
                self.openFile.emit(p)

    def compare_sel(self):
        ps = self._selected_paths()
        if len(ps) == 2:
            self.openPair.emit(*ps)
        else:
            QMessageBox.information(self, "Compare", "Select two rows (or one near-duplicate pair).")

    def copy_paths(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText("\n".join(self._selected_paths()))

    def export(self):
        if not self.res:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export duplicate analysis", "duplicates.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if p:
            duplicates.export(self.res, p)
            self.status.setText(f"Exported to {p}")
