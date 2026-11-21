"""Find / replace bar and find-in-files panel."""
from __future__ import annotations

import fnmatch
import os
import re

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QCheckBox, QLabel,
                               QGridLayout, QTreeWidget, QTreeWidgetItem, QComboBox, QFileDialog, QToolButton)

from ..core.textio import read_text, is_binary


def build_regex(text: str, case: bool, word: bool, regex: bool) -> str:
    pat = text if regex else re.escape(text)
    if word:
        pat = rf"\b{pat}\b"
    return pat


class FindBar(QWidget):
    """Search bar shown at the bottom of the window. Works on the current editor."""
    closed = Signal()

    def __init__(self, get_editor, get_all_editors, parent=None):
        super().__init__(parent)
        self.get_editor = get_editor
        self.get_all_editors = get_all_editors
        g = QGridLayout(self)
        g.setContentsMargins(6, 4, 6, 4)
        self.find = QLineEdit(placeholderText="Find")
        self.repl = QLineEdit(placeholderText="Replace with")
        self.case = QCheckBox("Match case")
        self.word = QCheckBox("Whole word")
        self.regex = QCheckBox("Regex")
        self.wrap = QCheckBox("Wrap around")
        self.wrap.setChecked(True)
        self.selection_only = QCheckBox("In selection")
        self.info = QLabel()
        b_next = QPushButton("Next")
        b_prev = QPushButton("Previous")
        b_count = QPushButton("Count")
        b_rep = QPushButton("Replace")
        b_all = QPushButton("Replace all")
        b_all_tabs = QPushButton("Replace in all tabs")
        close = QToolButton(text="✕")
        close.setAutoRaise(True)
        g.addWidget(QLabel("Find:"), 0, 0)
        g.addWidget(self.find, 0, 1)
        g.addWidget(b_next, 0, 2)
        g.addWidget(b_prev, 0, 3)
        g.addWidget(b_count, 0, 4)
        opts = QHBoxLayout()
        for w in (self.case, self.word, self.regex, self.wrap, self.selection_only):
            opts.addWidget(w)
        opts.addStretch()
        opts.addWidget(self.info)
        g.addLayout(opts, 0, 5)
        g.addWidget(close, 0, 6)
        g.addWidget(QLabel("Replace:"), 1, 0)
        g.addWidget(self.repl, 1, 1)
        g.addWidget(b_rep, 1, 2)
        g.addWidget(b_all, 1, 3)
        g.addWidget(b_all_tabs, 1, 4)
        g.setColumnStretch(1, 2)
        g.setColumnStretch(5, 3)
        b_next.clicked.connect(lambda: self.find_next(False))
        b_prev.clicked.connect(lambda: self.find_next(True))
        b_count.clicked.connect(self.count)
        b_rep.clicked.connect(self.replace_one)
        b_all.clicked.connect(lambda: self.replace_all(False))
        b_all_tabs.clicked.connect(lambda: self.replace_all(True))
        close.clicked.connect(self.hide_bar)
        self.find.returnPressed.connect(lambda: self.find_next(False))
        self.repl.returnPressed.connect(self.replace_one)
        self.find.textChanged.connect(self._live)
        for cb in (self.case, self.word, self.regex):
            cb.toggled.connect(self._live)

    def hide_bar(self):
        ed = self.get_editor()
        if ed:
            ed.set_layer("search", [])
            ed.setFocus()
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide_bar()
        else:
            super().keyPressEvent(e)

    def show_find(self, replace=False):
        ed = self.get_editor()
        if ed:
            sel = ed.textCursor().selectedText()
            if sel and " " not in sel:
                self.find.setText(sel)
        self.show()
        (self.repl if replace and self.find.text() else self.find).setFocus()
        self.find.selectAll()

    def pattern(self):
        return build_regex(self.find.text(), self.case.isChecked(), self.word.isChecked(), self.regex.isChecked())

    def flags(self):
        f = re.MULTILINE
        if not self.case.isChecked():
            f |= re.IGNORECASE
        return f

    def _rx(self):
        try:
            self.find.setStyleSheet("")
            return re.compile(self.pattern(), self.flags())
        except re.error as ex:
            self.find.setStyleSheet("background:#ffd6d6")
            self.info.setText(f"Regex error: {ex}")
            return None

    def _live(self):
        ed = self.get_editor()
        if not ed:
            return
        if not self.find.text():
            ed.set_layer("search", [])
            self.info.setText("")
            return
        if self._rx():
            n = ed.highlight_all(self.pattern(), self.flags())
            self.info.setText(f"{n} match(es)" if n <= 5000 else "5000+ matches")

    def find_next(self, backwards=False):
        ed = self.get_editor()
        rx = self._rx()
        if not ed or not rx or not self.find.text():
            return False
        text = ed.toPlainText()
        c = ed.textCursor()
        matches = [m for m in rx.finditer(text) if m.end() > m.start()]
        if not matches:
            self.info.setText("Not found")
            return False
        if backwards:
            pos = c.selectionStart()
            cand = [m for m in matches if m.start() < pos]
            m = cand[-1] if cand else (matches[-1] if self.wrap.isChecked() else None)
        else:
            pos = c.selectionEnd()
            cand = [m for m in matches if m.start() >= pos]
            m = cand[0] if cand else (matches[0] if self.wrap.isChecked() else None)
        if m is None:
            self.info.setText("No more matches")
            return False
        c.setPosition(m.start())
        c.setPosition(m.end(), QTextCursor.MoveMode.KeepAnchor)
        ed.setTextCursor(c)
        ed.centerCursor()
        self.info.setText(f"Match {matches.index(m) + 1} of {len(matches)}")
        return True

    def count(self):
        ed = self.get_editor()
        rx = self._rx()
        if ed and rx:
            self.info.setText(f"{sum(1 for _ in rx.finditer(ed.toPlainText()))} match(es)")

    def _replacement(self, m):
        if self.regex.isChecked():
            return m.expand(self.repl.text())
        return self.repl.text()

    def replace_one(self):
        ed = self.get_editor()
        rx = self._rx()
        if not ed or not rx:
            return
        c = ed.textCursor()
        sel = c.selectedText()
        if sel:
            m = rx.fullmatch(sel)
            if m:
                c.insertText(self._replacement(m))
        self.find_next(False)

    def replace_all(self, all_tabs=False):
        rx = self._rx()
        if not rx or not self.find.text():
            return
        editors = self.get_all_editors() if all_tabs else [self.get_editor()]
        total = 0
        for ed in editors:
            if ed is None or ed.isReadOnly():
                continue
            c = ed.textCursor()
            if self.selection_only.isChecked() and c.hasSelection() and not all_tabs:
                a, b = c.selectionStart(), c.selectionEnd()
            else:
                a, b = 0, len(ed.toPlainText())
            text = ed.toPlainText()
            seg = text[a:b]
            new, n = rx.subn(self._replacement, seg)
            if n:
                cur = QTextCursor(ed.document())
                cur.beginEditBlock()
                cur.setPosition(a)
                cur.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
                cur.insertText(new)
                cur.endEditBlock()
                total += n
        self.info.setText(f"Replaced {total} occurrence(s)")
        self._live()


class _SearchWorker(QThread):
    hit = Signal(str, int, str)
    done = Signal(int, int)

    def __init__(self, root, globs, rx, recursive):
        super().__init__()
        self.root, self.globs, self.rx, self.recursive = root, globs, rx, recursive
        self.stop = False

    def run(self):
        files = hits = 0
        for dirpath, dirs, names in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in (".git", ".svn", "node_modules", "__pycache__")]
            if not self.recursive:
                dirs[:] = []
            for n in names:
                if self.stop:
                    return
                if not any(fnmatch.fnmatch(n, g) for g in self.globs):
                    continue
                p = os.path.join(dirpath, n)
                try:
                    if os.path.getsize(p) > 50_000_000:
                        continue
                    with open(p, "rb") as fh:
                        if is_binary(fh.read(8000)):
                            continue
                    text = read_text(p).text
                except OSError:
                    continue
                files += 1
                for i, line in enumerate(text.split("\n")):
                    if self.rx.search(line):
                        hits += 1
                        self.hit.emit(p, i + 1, line.strip()[:300])
                        if hits >= 20000:
                            self.done.emit(files, hits)
                            return
        self.done.emit(files, hits)


class FindInFilesPanel(QWidget):
    openRequested = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.pattern = QLineEdit(placeholderText="Search text")
        self.folder = QComboBox(editable=True)
        self.folder.setMinimumWidth(250)
        browse = QPushButton("…")
        browse.setFixedWidth(30)
        self.globs = QLineEdit("*.*")
        self.globs.setToolTip("File name filters, separated by ';' e.g. *.py;*.txt")
        self.globs.setMaximumWidth(140)
        self.case = QCheckBox("Case")
        self.word = QCheckBox("Word")
        self.regex = QCheckBox("Regex")
        self.sub = QCheckBox("Subfolders")
        self.sub.setChecked(True)
        self.go = QPushButton("Search")
        for w in (self.pattern, QLabel("in"), self.folder, browse, self.globs, self.case, self.word, self.regex, self.sub, self.go):
            row.addWidget(w)
        v.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File / line", "Text"])
        self.tree.setColumnWidth(0, 380)
        v.addWidget(self.tree)
        self.status = QLabel()
        v.addWidget(self.status)
        browse.clicked.connect(self._browse)
        self.go.clicked.connect(self.start)
        self.pattern.returnPressed.connect(self.start)
        self.tree.itemActivated.connect(self._activate)
        self.worker = None
        self._file_items = {}

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Search in folder", self.folder.currentText())
        if d:
            self.folder.setCurrentText(d)

    def start(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop = True
            self.worker.wait()
        root = self.folder.currentText().strip()
        if not self.pattern.text() or not os.path.isdir(root):
            self.status.setText("Enter search text and an existing folder.")
            return
        try:
            flags = 0 if self.case.isChecked() else re.IGNORECASE
            rx = re.compile(build_regex(self.pattern.text(), self.case.isChecked(), self.word.isChecked(), self.regex.isChecked()), flags)
        except re.error as ex:
            self.status.setText(f"Regex error: {ex}")
            return
        self.tree.clear()
        self._file_items = {}
        globs = [g.strip() for g in self.globs.text().split(";") if g.strip()] or ["*"]
        globs = ["*" if g == "*.*" else g for g in globs]
        self.status.setText("Searching…")
        self.worker = _SearchWorker(root, globs, rx, self.sub.isChecked())
        self.worker.hit.connect(self._hit)
        self.worker.done.connect(lambda f, h: self.status.setText(f"{h} hit(s) in {len(self._file_items)} of {f} file(s) searched"))
        self.worker.start()

    def _hit(self, path, line, text):
        it = self._file_items.get(path)
        if it is None:
            it = QTreeWidgetItem([path, ""])
            it.setData(0, Qt.ItemDataRole.UserRole, (path, 1))
            self.tree.addTopLevelItem(it)
            it.setExpanded(True)
            self._file_items[path] = it
        ch = QTreeWidgetItem([f"Line {line}", text])
        ch.setData(0, Qt.ItemDataRole.UserRole, (path, line))
        it.addChild(ch)
        it.setText(1, f"{it.childCount()} hit(s)")

    def _activate(self, item, _col):
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if d:
            self.openRequested.emit(*d)
