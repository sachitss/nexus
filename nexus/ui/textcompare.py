"""Side-by-side text comparison with editing, merging and navigation."""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QTextCursor, QTextFormat, QKeySequence
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLineEdit, QToolButton, QLabel,
                               QFileDialog, QMenu, QMessageBox, QInputDialog, QTextEdit, QToolBar, QStyle)

from ..core import loaders, report
from ..core.textdiff import DiffOptions, diff_lines, EQUAL, CHANGED, LEFT_ONLY, RIGHT_ONLY
from ..core.textio import read_text, write_text
from .editor import CodeEditor, lexer_for
from .theme import Theme

FILLER = 1


@dataclass
class Side:
    path: str | None = None
    name: str = ""
    lines: list[str] = None
    encoding: str = "utf-8"
    eol: str = "\n"
    readonly: bool = False
    modified: bool = False

    @classmethod
    def from_path(cls, path: str, with_format: bool = False) -> "Side":
        if loaders.ext(path) in loaders.WORD_EXT:
            return cls(path, os.path.basename(path), loaders.load_as_lines(path, with_format=with_format), readonly=True)
        with open(path, "rb") as fh:
            head = fh.read(8000)
        if loaders.is_binary(head):
            return cls(path, os.path.basename(path), [f"<binary file, {os.path.getsize(path):,} bytes>"], readonly=True)
        tf = read_text(path)
        return cls(path, os.path.basename(path), tf.text.split("\n"), tf.encoding, tf.eol)


class OverviewBar(QWidget):
    """Thin vertical map of all differences; click to jump."""
    jump = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(16)
        self.rows = []
        self.view_frac = (0.0, 1.0)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), Theme.c("gutter_bg"))
        n = len(self.rows)
        if not n:
            return
        h = self.height()
        colors = {CHANGED: Theme.c("changed_hl"), LEFT_ONLY: QColor("#e06666"), RIGHT_ONLY: QColor("#5cb85c")}
        for i, r in enumerate(self.rows):
            if r.tag != EQUAL:
                y = int(i / n * h)
                p.fillRect(2, y, self.width() - 4, max(2, int(h / n)), colors[r.tag])
        a, b = self.view_frac
        p.setPen(Theme.c("gutter_fg"))
        p.drawRect(0, int(a * h), self.width() - 1, max(4, int((b - a) * h)))

    def mousePressEvent(self, e):
        self.jump.emit(e.position().y() / max(1, self.height()))

    mouseMoveEvent = mousePressEvent


class TextCompareView(QWidget):
    titleChanged = Signal()
    openInEditor = Signal(str)

    def __init__(self, left: Side | None = None, right: Side | None = None, parent=None):
        super().__init__(parent)
        self.opts = DiffOptions()
        self.sides = [left or Side(name="left", lines=[""]), right or Side(name="right", lines=[""])]
        self.result = None
        self.hunk = -1
        self.only_diffs = False
        self._rebuilding = False

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(self._make_toolbar())

        # path rows
        paths = QHBoxLayout()
        paths.setContentsMargins(4, 0, 4, 0)
        self.path_edits = []
        for i in range(2):
            le = QLineEdit()
            le.setPlaceholderText("Left file" if i == 0 else "Right file")
            le.returnPressed.connect(lambda i=i: self.load_side(i, self.path_edits[i].text()))
            b = QToolButton(text="…")
            b.setToolTip("Browse")
            b.clicked.connect(lambda _=False, i=i: self.browse(i))
            s = QToolButton(text="💾")
            s.setToolTip("Save this side")
            s.clicked.connect(lambda _=False, i=i: self.save_side(i))
            paths.addWidget(le, 1)
            paths.addWidget(b)
            paths.addWidget(s)
            self.path_edits.append(le)
            if i == 0:
                paths.addSpacing(20)
        v.addLayout(paths)

        body = QHBoxLayout()
        body.setSpacing(0)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.eds = [CodeEditor(), CodeEditor()]
        for _e in self.eds:
            _e.fold_enabled = False
            _e._update_gutter_width()
        for i, ed in enumerate(self.eds):
            ed.line_label = lambda n, i=i: self._label(i, n)
            ed.textChanged.connect(lambda i=i: self._edited(i))
            ed.setUndoRedoEnabled(True)
            self.split.addWidget(ed)
        body.addWidget(self.split, 1)
        self.overview = OverviewBar()
        self.overview.jump.connect(self._jump_frac)
        body.addWidget(self.overview)
        v.addLayout(body, 1)

        self.status = QLabel()
        self.status.setContentsMargins(6, 2, 6, 2)
        v.addWidget(self.status)

        # scrolling sync
        for a, b in ((0, 1), (1, 0)):
            self.eds[a].verticalScrollBar().valueChanged.connect(lambda val, b=b: self._sync_v(b, val))
            self.eds[a].horizontalScrollBar().valueChanged.connect(lambda val, b=b: self.eds[b].horizontalScrollBar().setValue(val))
        self.eds[0].verticalScrollBar().valueChanged.connect(self._update_overview_view)
        self.recompare_timer = QTimer(singleShot=True, interval=900)
        self.recompare_timer.timeout.connect(self.recompare_from_editors)
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _make_toolbar(self):
        tb = QToolBar()
        tb.setIconSize(tb.iconSize() * 0.8)
        st = self.style()

        def act(text, slot, shortcut=None, tip=None, icon=None):
            a = QAction(text, self)
            if icon is not None:
                a.setIcon(st.standardIcon(icon))
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            a.setToolTip(tip or text + (f" ({shortcut})" if shortcut else ""))
            a.triggered.connect(slot)
            tb.addAction(a)
            self.addAction(a)
            return a

        act("Recompare", self.reload_and_compare, "F5", "Reload files from disk and compare (F5)", QStyle.StandardPixmap.SP_BrowserReload)
        tb.addSeparator()
        act("◀ Prev", lambda: self.goto_hunk(-1), "Shift+F7", "Previous difference (Shift+F7)")
        act("Next ▶", lambda: self.goto_hunk(1), "F7", "Next difference (F7)")
        tb.addSeparator()
        act("Copy to right ▶", lambda: self.copy_hunk(0), "Alt+Right", "Copy current difference to the right (Alt+Right)")
        act("◀ Copy to left", lambda: self.copy_hunk(1), "Alt+Left", "Copy current difference to the left (Alt+Left)")
        act("Copy all to right", lambda: self.copy_all(0), None, "Make the right side identical to the left side")
        act("Copy all to left", lambda: self.copy_all(1), None, "Make the left side identical to the right side")
        tb.addSeparator()
        self.act_only = act("Only differences", self.toggle_only_diffs, "Ctrl+Shift+D", "Hide identical lines (Ctrl+Shift+D)")
        self.act_only.setCheckable(True)
        act("Swap sides", self.swap, None, "Swap left and right")
        opt_btn = QToolButton(text="Options ▾")
        opt_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(opt_btn)
        for label, attr in (("Ignore case", "ignore_case"), ("Ignore all whitespace", "ignore_whitespace"),
                            ("Ignore trailing whitespace", "ignore_trailing_ws"), ("Ignore blank lines", "ignore_blank_lines")):
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(getattr(self.opts, attr))
            a.toggled.connect(lambda on, attr=attr: (setattr(self.opts, attr, on), self.refresh()))
        m.addSeparator()
        a = m.addAction("Highlight whole words (instead of characters)")
        a.setCheckable(True)
        a.setChecked(self.opts.granularity == "word")
        a.toggled.connect(lambda on: (setattr(self.opts, "granularity", "word" if on else "char"), self.refresh()))
        a = m.addAction("Word documents: compare formatting too")
        a.setCheckable(True)
        a.toggled.connect(self._set_with_format)
        m.addSeparator()
        m.addAction("Ignore comments starting with…", self._ask_comment)
        m.addAction("Ignore text matching regex…", self._ask_regex)
        opt_btn.setMenu(m)
        tb.addWidget(opt_btn)
        tb.addSeparator()
        act("Export report…", self.export_report, None, "Save an HTML report of the comparison")
        act("Unified diff…", self.export_patch, None, "Save differences as a unified diff / patch file")
        return tb

    def _set_with_format(self, on):
        self.with_format = on
        for i, sd in enumerate(self.sides):
            if sd.path and loaders.ext(sd.path) in loaders.WORD_EXT:
                self.sides[i] = Side.from_path(sd.path, with_format=on)
        self.refresh()

    def _ask_comment(self):
        t, ok = QInputDialog.getText(self, "Ignore comments", "Ignore lines / line endings starting with this comment "
                                     "prefix (e.g. #, //, ;). Leave empty to disable:", text=self.opts.ignore_comments)
        if ok:
            self.opts.ignore_comments = t.strip()
            self.refresh()

    def _ask_regex(self):
        t, ok = QInputDialog.getText(self, "Ignore regex", "Ignore parts of lines matching this regular expression\n"
                                     "(e.g. timestamps: \\d{2}:\\d{2}:\\d{2}). Leave empty to disable:", text=self.opts.ignore_regex)
        if ok:
            self.opts.ignore_regex = t
            self.refresh()

    def title(self):
        names = [("● " if s.modified else "") + (s.name or "untitled") for s in self.sides]
        return f"{names[0]} ↔ {names[1]}"

    # ------------------------------------------------------------------ loading/saving
    def browse(self, i):
        p, _ = QFileDialog.getOpenFileName(self, "Open file", self.sides[i].path or "")
        if p:
            self.load_side(i, p)

    def load_side(self, i, path):
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Compare", f"File not found:\n{path}")
            return
        if self.sides[i].modified and not self._confirm_discard(i):
            return
        self.sides[i] = Side.from_path(path)
        self.refresh()
        self.titleChanged.emit()

    def reload_and_compare(self):
        for i, s in enumerate(self.sides):
            if s.path and os.path.isfile(s.path):
                if s.modified:
                    if not self._confirm_discard(i):
                        continue
                self.sides[i] = Side.from_path(s.path, getattr(self, "with_format", False))
        self.refresh()
        self.titleChanged.emit()

    def _confirm_discard(self, i):
        r = QMessageBox.question(self, "Unsaved changes", f"Discard unsaved changes in {self.sides[i].name}?")
        return r == QMessageBox.StandardButton.Yes

    def save_side(self, i) -> bool:
        s = self.sides[i]
        if s.readonly:
            QMessageBox.information(self, "Save", "This side is read-only (e.g. a Word document shown as text).")
            return False
        self.recompare_from_editors()
        path = s.path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save as")
            if not path:
                return False
            s.path, s.name = path, os.path.basename(path)
        write_text(path, "\n".join(s.lines), s.encoding, s.eol)
        s.modified = False
        self.path_edits[i].setText(path)
        self.titleChanged.emit()
        return True

    def has_unsaved(self):
        return any(s.modified for s in self.sides)

    def swap(self):
        self.recompare_from_editors()
        self.sides.reverse()
        self.refresh()
        self.titleChanged.emit()

    # ------------------------------------------------------------------ compare & render
    def refresh(self):
        self.result = diff_lines(self.sides[0].lines, self.sides[1].lines, self.opts)
        self._render()

    def _label(self, i, block_no):
        if not self.result or block_no >= len(self.result.rows):
            return ""
        r = self.result.rows[block_no]
        idx = r.left if i == 0 else r.right
        return "" if idx is None else str(idx + 1)

    def _render(self):
        res = self.result
        self._rebuilding = True
        focus = next((i for i, e in enumerate(self.eds) if e.hasFocus()), None)
        cur_block = self.eds[focus].textCursor().blockNumber() if focus is not None else None
        cur_col = self.eds[focus].textCursor().positionInBlock() if focus is not None else 0
        vpos = self.eds[0].verticalScrollBar().value()
        for i, ed in enumerate(self.eds):
            side = self.sides[i]
            lines = []
            for r in res.rows:
                idx = r.left if i == 0 else r.right
                lines.append("" if idx is None else side.lines[idx])
            ed.blockSignals(True)
            ed.setPlainText("\n".join(lines))
            ed.blockSignals(False)
            ed.setReadOnly(side.readonly)
            ed.document().setModified(False)
            ed.set_lexer(lexer_for(side.path or side.name))
            doc = ed.document()
            b = doc.begin()
            for r in res.rows:
                idx = r.left if i == 0 else r.right
                b.setUserState(FILLER if idx is None else 0)
                b = b.next()
            self.path_edits[i].setText(side.path or "")
            self._paint_diff(i)
        self._apply_visibility()
        for ed in self.eds:
            ed.verticalScrollBar().setValue(vpos)
        if focus is not None and cur_block is not None:
            ed = self.eds[focus]
            b = ed.document().findBlockByNumber(min(cur_block, ed.blockCount() - 1))
            c = QTextCursor(b)
            c.setPosition(b.position() + min(cur_col, b.length() - 1))
            ed.blockSignals(True)
            ed.setTextCursor(c)
            ed.blockSignals(False)
            ed.verticalScrollBar().setValue(vpos)
        self._rebuilding = False
        self.overview.rows = res.rows
        self.overview.update()
        self._update_status()

    def _paint_diff(self, i):
        ed = self.eds[i]
        res = self.result
        doc = ed.document()
        sels = []
        colors = {CHANGED: Theme.c("changed"), LEFT_ONLY: Theme.c("left"), RIGHT_ONLY: Theme.c("right")}
        gutter = {}
        hl = Theme.c("changed_hl")
        block = doc.begin()
        for n, r in enumerate(res.rows):
            if r.tag != EQUAL:
                idx = r.left if i == 0 else r.right
                col = Theme.c("filler") if idx is None else colors[r.tag]
                gutter[n] = col
                s = QTextEdit.ExtraSelection()
                s.format.setBackground(col)
                s.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                s.cursor = QTextCursor(block)
                sels.append(s)
                if r.tag == CHANGED:
                    for a, b in (r.left_spans if i == 0 else r.right_spans):
                        s2 = QTextEdit.ExtraSelection()
                        s2.format.setBackground(hl)
                        c = QTextCursor(block)
                        c.setPosition(block.position() + a)
                        c.setPosition(block.position() + min(b, block.length() - 1), QTextCursor.MoveMode.KeepAnchor)
                        s2.cursor = c
                        sels.append(s2)
            block = block.next()
        ed.line_colors = gutter
        ed.set_layer("diff", sels)
        ed.gutter.update()

    def _apply_visibility(self, context=3):
        rows = self.result.rows
        keep = None
        if self.only_diffs:
            keep = set()
            for a, b in self.result.hunks:
                keep.update(range(max(0, a - context), min(len(rows), b + context + 1)))
        for ed in self.eds:
            doc = ed.document()
            b = doc.begin()
            n = 0
            while b.isValid():
                b.setVisible(keep is None or n in keep)
                b = b.next()
                n += 1
            doc.markContentsDirty(0, doc.characterCount())
            ed.viewport().update()
            ed.gutter.update()

    def toggle_only_diffs(self):
        self.only_diffs = self.act_only.isChecked()
        self._apply_visibility()

    def _update_status(self):
        s = self.result.stats()
        info = []
        for side in self.sides:
            info.append(f"{side.encoding}, {'CRLF' if side.eol == chr(13) + chr(10) else 'LF'}" + (" · read-only" if side.readonly else ""))
        if self.result.identical:
            msg = "✔ No differences" + (" (with current ignore options)" if self.sides[0].lines != self.sides[1].lines else "")
        else:
            msg = (f"{s['hunks']} difference block(s): {s['changed']} changed, {s['left']} only left, "
                   f"{s['right']} only right line(s)")
        self.status.setText(f"{msg}     |  Left: {info[0]}   Right: {info[1]}")

    # ------------------------------------------------------------------ editing
    def _edited(self, i):
        # textChanged also fires on re-highlighting; only real edits mark the document modified
        if self._rebuilding or not self.eds[i].document().isModified():
            return
        self.sides[i].modified = True
        self.titleChanged.emit()
        self.recompare_timer.start()

    def _lines_from_editor(self, i):
        out = []
        b = self.eds[i].document().begin()
        while b.isValid():
            if not (b.userState() == FILLER and b.text() == ""):
                out.append(b.text())
            b = b.next()
        return out

    def recompare_from_editors(self):
        self.recompare_timer.stop()
        changed = False
        for i, s in enumerate(self.sides):
            if s.modified and not s.readonly:
                new = self._lines_from_editor(i)
                if new != s.lines:
                    s.lines = new
                    changed = True
        if changed:
            self.refresh()

    # ------------------------------------------------------------------ navigation & merge
    def _current_row(self):
        ed = next((e for e in self.eds if e.hasFocus()), self.eds[0])
        return ed.textCursor().blockNumber()

    def goto_hunk(self, step):
        self.recompare_from_editors()
        hunks = self.result.hunks
        if not hunks:
            return
        row = self._current_row()
        if step > 0:
            nxt = [k for k, (a, _) in enumerate(hunks) if a > row]
            k = nxt[0] if nxt else 0
        else:
            prv = [k for k, (_, b) in enumerate(hunks) if b < row]
            k = prv[-1] if prv else len(hunks) - 1
        self.hunk = k
        a, b = hunks[k]
        for ed in self.eds:
            blk = ed.document().findBlockByNumber(a)
            ed.setTextCursor(QTextCursor(blk))
            ed.centerCursor()
        self.status.setText(f"Difference {k + 1} of {len(hunks)}  ·  " + self.status.text().split("     |  ")[-1])

    def _hunk_at_cursor(self):
        row = self._current_row()
        for a, b in self.result.hunks:
            if a <= row <= b:
                return a, b
        return None

    def copy_hunk(self, src):
        self.recompare_from_editors()
        dst = 1 - src
        if self.sides[dst].readonly:
            return
        h = self._hunk_at_cursor()
        if not h:
            self.goto_hunk(1)
            h = self._hunk_at_cursor()
            if not h:
                return
        a, b = h
        rows = self.result.rows
        get = (lambda r: r.left) if src == 0 else (lambda r: r.right)
        getd = (lambda r: r.right) if src == 0 else (lambda r: r.left)
        src_lines = [self.sides[src].lines[get(r)] for r in rows[a:b + 1] if get(r) is not None]
        dst_idx = [getd(r) for r in rows[a:b + 1] if getd(r) is not None]
        if dst_idx:
            start, end = dst_idx[0], dst_idx[-1] + 1
        else:
            start = next((getd(r) for r in rows[b + 1:] if getd(r) is not None), len(self.sides[dst].lines))
            end = start
        lines = self.sides[dst].lines
        self.sides[dst].lines = lines[:start] + src_lines + lines[end:]
        self.sides[dst].modified = True
        self.refresh()
        self.titleChanged.emit()
        # move cursor to the next difference
        self.eds[dst].setFocus()
        self.goto_hunk(1)

    def copy_all(self, src):
        self.recompare_from_editors()
        dst = 1 - src
        if self.sides[dst].readonly:
            return
        self.sides[dst].lines = list(self.sides[src].lines)
        self.sides[dst].modified = True
        self.refresh()
        self.titleChanged.emit()

    # ------------------------------------------------------------------ scrolling
    def _sync_v(self, target, val):
        sb = self.eds[target].verticalScrollBar()
        if sb.value() != val:
            sb.setValue(val)

    def _update_overview_view(self, *_):
        sb = self.eds[0].verticalScrollBar()
        total = sb.maximum() + sb.pageStep()
        if total:
            self.overview.view_frac = (sb.value() / total, (sb.value() + sb.pageStep()) / total)
            self.overview.update()

    def _jump_frac(self, f):
        rows = len(self.result.rows)
        row = int(max(0, min(1, f)) * rows)
        for ed in self.eds:
            ed.setTextCursor(QTextCursor(ed.document().findBlockByNumber(row)))
            ed.centerCursor()

    # ------------------------------------------------------------------ export
    def export_report(self):
        self.recompare_from_editors()
        p, _ = QFileDialog.getSaveFileName(self, "Save HTML report", "comparison.html", "HTML (*.html)")
        if p:
            report.save(report.text_report(self.result, self.sides[0].path or self.sides[0].name,
                                           self.sides[1].path or self.sides[1].name, only_diffs=self.only_diffs), p)

    def export_patch(self):
        from ..core.textdiff import unified_diff
        self.recompare_from_editors()
        p, _ = QFileDialog.getSaveFileName(self, "Save unified diff", "changes.patch", "Patch (*.patch *.diff)")
        if p:
            txt = unified_diff(self.sides[0].lines, self.sides[1].lines, self.sides[0].name, self.sides[1].name)
            write_text(p, txt + "\n")
