"""Folder comparison view (two-way), synchronisation dialog and N-way comparison."""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil

from PySide6.QtCore import Qt, Signal, QThread, QDate
from PySide6.QtGui import QAction, QKeySequence, QBrush
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QToolButton, QLabel, QComboBox,
                               QTreeWidget, QTreeWidgetItem, QFileDialog, QMessageBox, QToolBar, QMenu,
                               QAbstractItemView, QDialog, QFormLayout, QCheckBox, QSpinBox, QDateEdit,
                               QDialogButtonBox, QPushButton, QListWidget)

from ..core import report
from ..core.automation import app_data_dir
from ..core.folderdiff import (FolderOptions, compare_folders, summary, build_sync_plan, execute_plan, compare_many,
                               SAME, DIFFERENT, LEFT_ONLY, RIGHT_ONLY, NEWER_LEFT, NEWER_RIGHT, EXCLUDED, RENAMED)
from .common import Task, make_table, fill_table, fmt_size as _fmt_size, fmt_time as _fmt_time
from .theme import Theme

STATUS_TEXT = {SAME: "identical", DIFFERENT: "different", LEFT_ONLY: "new on left", RIGHT_ONLY: "new on right",
               NEWER_LEFT: "modified (left newer)", NEWER_RIGHT: "modified (right newer)", EXCLUDED: "excluded",
               RENAMED: "renamed / moved"}
# colour scheme from the specification: green identical, red different, yellow modified,
# blue new, grey excluded (+ purple for detected renames)
STATUS_COLOR = {SAME: "same", DIFFERENT: "different", NEWER_LEFT: "modified", NEWER_RIGHT: "modified",
                LEFT_ONLY: "new", RIGHT_ONLY: "new", EXCLUDED: "excluded", RENAMED: "renamed"}


class _Worker(QThread):
    progress = Signal(str)
    finished_ok = Signal(object)

    def __init__(self, left, right, opts):
        super().__init__()
        self.left, self.right, self.opts = left, right, opts

    def run(self):
        n = [0]

        def prog(rel):
            n[0] += 1
            if n[0] % 50 == 0:
                self.progress.emit(rel)

        self.finished_ok.emit(compare_folders(self.left, self.right, self.opts, prog))


class FilterDialog(QDialog):
    """Advanced filters: regular expressions, size and date ranges."""

    def __init__(self, opts: FolderOptions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Folder comparison filters")
        f = QFormLayout(self)
        self.inc_re = QLineEdit(opts.include_regex)
        self.inc_re.setPlaceholderText(r"e.g. ^drawings/.*\.dwg$")
        self.exc_re = QLineEdit(opts.exclude_regex)
        self.min_kb = QSpinBox()
        self.min_kb.setRange(0, 10_000_000)
        self.min_kb.setSuffix(" KB")
        self.min_kb.setValue((opts.min_size or 0) // 1024)
        self.max_kb = QSpinBox()
        self.max_kb.setRange(0, 100_000_000)
        self.max_kb.setSuffix(" KB")
        self.max_kb.setSpecialValueText("no limit")
        self.max_kb.setValue((opts.max_size or 0) // 1024)
        self.after_on, self.after = QCheckBox("Modified after"), QDateEdit(QDate.currentDate().addMonths(-1))
        self.before_on, self.before = QCheckBox("Modified before"), QDateEdit(QDate.currentDate())
        for cb, de, val in ((self.after_on, self.after, opts.modified_after), (self.before_on, self.before, opts.modified_before)):
            de.setCalendarPopup(True)
            if val:
                cb.setChecked(True)
                d = _dt.date.fromtimestamp(val)
                de.setDate(QDate(d.year, d.month, d.day))
        self.show_exc = QCheckBox("Show excluded items (grey)")
        self.show_exc.setChecked(opts.show_excluded)
        self.renames = QCheckBox("Detect renamed / moved files (same size and SHA-256)")
        self.renames.setChecked(opts.detect_renames)
        f.addRow("Include path regex:", self.inc_re)
        f.addRow("Exclude path regex:", self.exc_re)
        f.addRow("Minimum size:", self.min_kb)
        f.addRow("Maximum size:", self.max_kb)
        f.addRow(self.after_on, self.after)
        f.addRow(self.before_on, self.before)
        f.addRow(self.show_exc)
        f.addRow(self.renames)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def apply_to(self, o: FolderOptions):
        o.include_regex = self.inc_re.text().strip()
        o.exclude_regex = self.exc_re.text().strip()
        o.min_size = self.min_kb.value() * 1024 or None
        o.max_size = self.max_kb.value() * 1024 or None
        ts = lambda de, end=False: _dt.datetime.combine(de.date().toPython(), _dt.time.max if end else _dt.time.min).timestamp()  # noqa: E731
        o.modified_after = ts(self.after) if self.after_on.isChecked() else None
        o.modified_before = ts(self.before, True) if self.before_on.isChecked() else None
        o.show_excluded = self.show_exc.isChecked()
        o.detect_renames = self.renames.isChecked()


class SyncDialog(QDialog):
    """Preview a synchronisation plan, then run it with backups and an audit log.
    Nothing is written before the user presses 'Run'."""
    MODES = [("Update right (copy new and changed files left → right)", "update_right"),
             ("Update left (copy new and changed files right → left)", "update_left"),
             ("Mirror to right (right becomes identical to left, deletes extras)", "mirror_right"),
             ("Mirror to left (left becomes identical to right, deletes extras)", "mirror_left"),
             ("Two-way (newer file wins, conflicts are only reported)", "both")]

    def __init__(self, root, left, right, parent=None):
        super().__init__(parent)
        self.root, self.left, self.right = root, left, right
        self.plan = []
        self.log = []
        self.setWindowTitle("Synchronise folders")
        self.resize(980, 600)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"<b>Left:</b> {left}<br><b>Right:</b> {right}"))
        h = QHBoxLayout()
        h.addWidget(QLabel("Mode:"))
        self.mode = QComboBox()
        for t, _ in self.MODES:
            self.mode.addItem(t)
        self.mode.currentIndexChanged.connect(self.preview)
        h.addWidget(self.mode, 1)
        v.addLayout(h)
        self.table = make_table(["Action", "Relative path", "Reason", "Source", "Target"])
        v.addWidget(self.table, 1)
        h2 = QHBoxLayout()
        self.backup = QCheckBox("Back up files before overwriting or deleting")
        self.backup.setChecked(True)
        self.backup_dir = QLineEdit(os.path.join(app_data_dir(), "backups"))
        h2.addWidget(self.backup)
        h2.addWidget(self.backup_dir, 1)
        v.addLayout(h2)
        self.info = QLabel()
        v.addWidget(self.info)
        bb = QDialogButtonBox()
        self.run_btn = bb.addButton("Run synchronisation", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        self.run_btn.clicked.connect(self.run)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.preview()

    def preview(self, *_):
        self.plan = build_sync_plan(self.root, self.left, self.right, self.MODES[self.mode.currentIndex()][1])
        col = {"copy": Theme.folder("new"), "overwrite": Theme.folder("modified"), "delete": Theme.folder("different"),
               "conflict": Theme.folder("renamed"), "skip": Theme.folder("excluded")}
        fill_table(self.table, [[a.action, a.rel, a.reason, a.src or "", a.dst or ""] for a in self.plan],
                   [col.get(a.action) for a in self.plan])
        counts = {}
        for a in self.plan:
            counts[a.action] = counts.get(a.action, 0) + 1
        self.info.setText("Preview: " + (", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing to do") +
                          ". Conflicts are never resolved automatically.")
        self.run_btn.setEnabled(any(a.action in ("copy", "overwrite", "delete") for a in self.plan))

    def run(self):
        n = sum(a.action in ("copy", "overwrite", "delete") for a in self.plan)
        dels = sum(a.action == "delete" for a in self.plan)
        if QMessageBox.question(self, "Confirm synchronisation",
                                f"{n} file operation(s) will be carried out, including {dels} deletion(s).\n"
                                "Continue?") != QMessageBox.StandardButton.Yes:
            return
        bdir = self.backup_dir.text().strip() if self.backup.isChecked() else None
        logdir = os.path.join(app_data_dir(), "sync-logs")
        os.makedirs(logdir, exist_ok=True)
        logp = os.path.join(logdir, _dt.datetime.now().strftime("sync-%Y%m%d-%H%M%S.json"))
        self.log = execute_plan(self.plan, bdir, logp)
        errs = [e for e in self.log if str(e.get("result", "")).startswith("error")]
        QMessageBox.information(self, "Synchronisation", f"Done. {len(self.log) - len(errs)} item(s) processed, "
                                f"{len(errs)} error(s).\nAudit log: {logp}" + (f"\nBackups: {bdir}" if bdir else ""))
        self.accept()


class FolderCompareView(QWidget):
    titleChanged = Signal()
    openPair = Signal(str, str)     # open a file comparison
    openFile = Signal(str)
    compared = Signal(str, str, dict)   # left, right, summary (for history)

    COLS = ["Name", "Status", "Left size", "Left modified", "Right size", "Right modified"]

    def __init__(self, left=None, right=None, parent=None):
        super().__init__(parent)
        self.roots = [left or "", right or ""]
        self.opts = FolderOptions()
        self.root_entry = None
        self.worker = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        tb = QToolBar()
        a = QAction("Compare", self)
        a.setShortcut(QKeySequence("F5"))
        a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        a.triggered.connect(self.run)
        tb.addAction(a)
        self.addAction(a)
        tb.addSeparator()
        tb.addWidget(QLabel(" Show: "))
        self.filter = QComboBox()
        self.filter.addItems(["All", "Differences", "Identical", "New on left", "New on right", "Modified", "Renamed"])
        self.filter.currentIndexChanged.connect(self._populate)
        tb.addWidget(self.filter)
        tb.addWidget(QLabel("  Compare by: "))
        self.method = QComboBox()
        self.method.addItems(["Content (exact)", "Checksum (SHA-256)", "Size and date", "Size only"])
        self.method.currentIndexChanged.connect(self.run)
        tb.addWidget(self.method)
        tb.addWidget(QLabel("  Include: "))
        self.include = QLineEdit("*")
        self.include.setMaximumWidth(120)
        self.include.setToolTip("File name patterns to include, separated by ';' (e.g. *.docx;*.xlsx)")
        self.include.editingFinished.connect(self.run)
        tb.addWidget(self.include)
        tb.addWidget(QLabel("  Exclude: "))
        self.exclude = QLineEdit(";".join(self.opts.exclude))
        self.exclude.setMaximumWidth(200)
        self.exclude.editingFinished.connect(self.run)
        tb.addWidget(self.exclude)
        tb.addSeparator()
        for text, slot in (("Filters…", self.edit_filters), ("Synchronise…", self.sync),
                           ("Expand all", lambda: self.tree.expandAll()), ("Collapse all", lambda: self.tree.collapseAll()),
                           ("Export report…", self.export_report)):
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb.addAction(a)
        v.addWidget(tb)

        prow = QHBoxLayout()
        prow.setContentsMargins(4, 0, 4, 0)
        self.edits = []
        for i in range(2):
            le = QLineEdit(self.roots[i])
            le.setPlaceholderText("Left folder" if i == 0 else "Right folder")
            le.returnPressed.connect(self._paths_changed)
            b = QToolButton(text="…")
            b.clicked.connect(lambda _=False, i=i: self.browse(i))
            prow.addWidget(le, 1)
            prow.addWidget(b)
            if i == 0:
                prow.addSpacing(20)
            self.edits.append(le)
        v.addLayout(prow)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(self.COLS)
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 170)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemActivated.connect(self._activate)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        v.addWidget(self.tree, 1)
        legend = QLabel()
        legend.setObjectName("Legend")
        self.legend = legend
        v.addWidget(legend)
        self.status = QLabel()
        self.status.setContentsMargins(6, 2, 6, 2)
        v.addWidget(self.status)
        self._legend()
        if all(self.roots):
            self.run()

    def _legend(self):
        parts = [("same", "identical"), ("different", "different"), ("modified", "modified (newer)"),
                 ("new", "new (one side)"), ("excluded", "excluded"), ("renamed", "renamed")]
        self.legend.setText("  ".join(f"<span style='background:{Theme.folder(k).name()}'>&nbsp;&nbsp;&nbsp;&nbsp;</span> {t}"
                                      for k, t in parts))
        self.legend.setContentsMargins(6, 2, 6, 0)

    def title(self):
        n = [os.path.basename(os.path.normpath(r)) or r for r in self.roots]
        return f"{n[0]} ↔ {n[1]}"

    def has_unsaved(self):
        return False

    def browse(self, i):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.roots[i])
        if d:
            self.edits[i].setText(d)
            self._paths_changed()

    def _paths_changed(self):
        self.roots = [e.text().strip() for e in self.edits]
        self.titleChanged.emit()
        self.run()

    def edit_filters(self):
        d = FilterDialog(self.opts, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            d.apply_to(self.opts)
            self.run()

    def run(self):
        if not all(os.path.isdir(r) for r in self.roots):
            self.status.setText("Choose two existing folders.")
            return
        if self.worker and self.worker.isRunning():
            return
        self.opts.method = ["content", "hash", "size_time", "size"][self.method.currentIndex()]
        self.opts.include = [p.strip() for p in self.include.text().split(";") if p.strip()] or ["*"]
        self.opts.exclude = [p.strip() for p in self.exclude.text().split(";") if p.strip()]
        self.status.setText("Comparing…")
        self.worker = _Worker(self.roots[0], self.roots[1], self.opts)
        self.worker.progress.connect(lambda rel: self.status.setText(f"Comparing… {rel}"))
        self.worker.finished_ok.connect(self._done)
        self.worker.start()

    def _done(self, entry):
        self.root_entry = entry
        self._populate()
        self.compared.emit(self.roots[0], self.roots[1], summary(entry))

    def _match(self, st):
        f = self.filter.currentIndex()
        return (f == 0 or (f == 1 and st not in (SAME, EXCLUDED)) or (f == 2 and st == SAME)
                or (f == 3 and st == LEFT_ONLY) or (f == 4 and st == RIGHT_ONLY)
                or (f == 5 and st in (DIFFERENT, NEWER_LEFT, NEWER_RIGHT)) or (f == 6 and st == RENAMED))

    def _visible(self, e):
        if e.is_dir:
            if any(self._visible(c) for c in e.children):
                return True
            return not e.children and self._match(e.status)
        return self._match(e.status)

    def _populate(self, *_):
        self.tree.clear()
        self._legend()
        if not self.root_entry:
            return

        def add(parent, e):
            for c in e.children:
                if not self._visible(c):
                    continue
                st = STATUS_TEXT.get(c.status, c.status)
                if c.is_dir and c.status == DIFFERENT:
                    st = "contains differences"
                if c.status == RENAMED and c.other_rel:
                    st = f"renamed ↔ {c.other_rel}"
                it = QTreeWidgetItem([("📁 " if c.is_dir else "") + c.name, st,
                                      _fmt_size(c.left_size), _fmt_time(c.left_mtime),
                                      _fmt_size(c.right_size), _fmt_time(c.right_mtime)])
                it.setData(0, Qt.ItemDataRole.UserRole, c)
                key = STATUS_COLOR.get(c.status)
                if key and not (c.is_dir and c.status == DIFFERENT):
                    for k in range(len(self.COLS)):
                        it.setBackground(k, QBrush(Theme.folder(key)))
                if parent is None:
                    self.tree.addTopLevelItem(it)
                else:
                    parent.addChild(it)
                if c.is_dir:
                    add(it, c)
                    it.setExpanded(c.status != SAME)

        add(None, self.root_entry)
        s = summary(self.root_entry)
        mod = s.get(DIFFERENT, 0) + s.get(NEWER_LEFT, 0) + s.get(NEWER_RIGHT, 0)
        self.status.setText(f"{s.get(SAME, 0)} identical, {mod} different, {s.get(LEFT_ONLY, 0)} new on left, "
                            f"{s.get(RIGHT_ONLY, 0)} new on right, {s.get(RENAMED, 0)} renamed, "
                            f"{s.get(EXCLUDED, 0)} excluded.  Double-click a file to compare it.")

    def _paths(self, e):
        rr = e.other_rel if (e.status == RENAMED and e.other_rel and e.left_size is not None) else e.rel
        rl = e.other_rel if (e.status == RENAMED and e.other_rel and e.left_size is None) else e.rel
        return os.path.join(self.roots[0], rl), os.path.join(self.roots[1], rr)

    def _activate(self, item, _col):
        e = item.data(0, Qt.ItemDataRole.UserRole)
        if e is None or e.is_dir:
            return
        l, r = self._paths(e)
        if e.status in (LEFT_ONLY, RIGHT_ONLY, EXCLUDED):
            p = l if os.path.exists(l) else r
            self.openFile.emit(p)
        else:
            self.openPair.emit(l, r)

    def _selected(self):
        return [it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()
                if it.data(0, Qt.ItemDataRole.UserRole) is not None]

    def _menu(self, pos):
        sel = self._selected()
        if not sel:
            return
        m = QMenu(self)
        m.addAction("Compare", lambda: self._activate(self.tree.currentItem(), 0))
        m.addSeparator()
        m.addAction("Copy to right ▶", lambda: self._copy(sel, 0))
        m.addAction("◀ Copy to left", lambda: self._copy(sel, 1))
        m.addSeparator()
        m.addAction("Open left in editor", lambda: [self.openFile.emit(self._paths(e)[0]) for e in sel if not e.is_dir])
        m.addAction("Open right in editor", lambda: [self.openFile.emit(self._paths(e)[1]) for e in sel if not e.is_dir])
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _copy(self, entries, src):
        names = "\n".join(e.rel for e in entries[:15]) + ("\n…" if len(entries) > 15 else "")
        direction = "left → right" if src == 0 else "right → left"
        if QMessageBox.question(self, "Copy files", f"Copy {len(entries)} item(s) {direction}?\n"
                                f"Existing files on the other side will be overwritten.\n\n{names}") != QMessageBox.StandardButton.Yes:
            return
        for e in entries:
            a, b = self._paths(e)
            s, d = (a, b) if src == 0 else (b, a)
            if not os.path.exists(s):
                continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
        self.run()

    def sync(self):
        if not self.root_entry:
            QMessageBox.information(self, "Synchronise", "Compare two folders first.")
            return
        if SyncDialog(self.root_entry, *self.roots, parent=self).exec() == QDialog.DialogCode.Accepted:
            self.run()

    def export_report(self):
        if not self.root_entry:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Save HTML report", "folder-comparison.html", "HTML (*.html)")
        if p:
            report.save(report.folder_report(self.root_entry, *self.roots), p)


class MultiFolderView(QWidget):
    """N-way comparison: one row per relative path, one column per folder."""
    titleChanged = Signal()
    openFile = Signal(str)

    def __init__(self, folders=None, parent=None):
        super().__init__(parent)
        self.rows = []
        self.task = None
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        h = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMaximumHeight(110)
        for f in folders or []:
            self.list.addItem(f)
        h.addWidget(self.list, 1)
        bv = QVBoxLayout()
        for t, slot in (("Add folder…", self.add), ("Remove", self.remove), ("Compare", self.run), ("Export CSV…", self.export)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            bv.addWidget(b)
        h.addLayout(bv)
        v.addLayout(h)
        self.only = QCheckBox("Only show paths that differ or are missing somewhere")
        self.only.setChecked(True)
        self.only.toggled.connect(self._fill)
        v.addWidget(self.only)
        self.table = make_table(["Relative path", "Status", "Variants"])
        self.table.itemDoubleClicked.connect(self._open)
        v.addWidget(self.table, 1)
        self.status = QLabel("Add two or more folders and press Compare.")
        v.addWidget(self.status)

    def title(self):
        return f"N-way ({self.list.count()} folders)"

    def has_unsaved(self):
        return False

    def folders(self):
        return [self.list.item(i).text() for i in range(self.list.count())]

    def add(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder")
        if d:
            self.list.addItem(d)
            self.titleChanged.emit()

    def remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self.titleChanged.emit()

    def run(self):
        fs = self.folders()
        if len(fs) < 2:
            self.status.setText("Add at least two folders.")
            return
        self.status.setText("Comparing (hashing every file)…")
        self.task = Task(compare_many, fs)
        self.task.done.connect(self._done)
        self.task.failed.connect(lambda m: self.status.setText("Error: " + m))
        self.task.start()

    def _done(self, rows):
        self.rows = rows
        self._fill()

    def _fill(self, *_):
        fs = self.folders()
        self.table.setColumnCount(3 + len(fs))
        self.table.setHorizontalHeaderLabels(["Relative path", "Status", "Variants"] + [os.path.basename(f) or f for f in fs])
        rows = [r for r in self.rows if not self.only.isChecked() or r["status"] != SAME]
        out, cols = [], []
        for r in rows:
            # label each folder's version A, B, C… so identical copies share a letter
            letters, labels = {}, []
            for h in r["hashes"]:
                if h is None:
                    labels.append("— missing —")
                else:
                    letters.setdefault(h, chr(65 + len(letters)))
                    labels.append(f"version {letters[h]}")
            out.append([r["rel"], r["status"], r["variants"]] + labels)
            cols.append(Theme.folder("same" if r["status"] == SAME else "new" if r["status"] == "missing in some" else "different"))
        fill_table(self.table, out, cols, data=rows)
        self.status.setText(f"{len(self.rows)} path(s) in total, {sum(r['status'] != SAME for r in self.rows)} not identical everywhere.")

    def _open(self, item):
        r = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        col = item.column() - 3
        if r and 0 <= col < len(self.folders()) and r["hashes"][col]:
            self.openFile.emit(os.path.join(self.folders()[col], r["rel"]))

    def export(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export", "n-way-comparison.csv", "CSV (*.csv)")
        if not p:
            return
        import csv
        with open(p, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["Relative path", "Status", "Variants"] + self.folders())
            for r in self.rows:
                w.writerow([r["rel"], r["status"], r["variants"]] + [h or "" for h in r["hashes"]])
