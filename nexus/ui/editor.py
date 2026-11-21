"""Code editor widget: line numbers, Pygments highlighting, editing helpers."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, QRect, QSize, QTimer, Signal
from PySide6.QtGui import (QColor, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QFont,
                           QTextFormat, QTextOption, QKeySequence)
from PySide6.QtWidgets import QPlainTextEdit, QWidget, QTextEdit

from pygments import lex
from pygments.lexers import get_lexer_for_filename, get_lexer_by_name, TextLexer
from pygments.util import ClassNotFound

from ..core.textio import read_text, write_text
from .theme import Theme, mono_font

MAX_HIGHLIGHT_CHARS = 3_000_000

COMMENT_PREFIX = {
    "python": "#", "bash": "#", "yaml": "#", "toml": "#", "ini": ";", "powershell": "#", "perl": "#",
    "ruby": "#", "r": "#", "make": "#", "docker": "#", "cmake": "#", "properties": "#",
    "c": "//", "cpp": "//", "csharp": "//", "java": "//", "javascript": "//", "typescript": "//",
    "go": "//", "rust": "//", "kotlin": "//", "swift": "//", "php": "//", "scss": "//", "json": "//",
    "sql": "--", "tsql": "--", "lua": "--", "haskell": "--", "vbnet": "'", "vb.net": "'",
    "batch": "REM ", "latex": "%", "tex": "%", "matlab": "%", "fortran": "!",
}


def lexer_for(path: str | None, text: str = ""):
    if path:
        try:
            return get_lexer_for_filename(os.path.basename(path), text[:2000], stripnl=False, ensurenl=False)
        except ClassNotFound:
            pass
    return TextLexer(stripnl=False, ensurenl=False)


class PygmentsHighlighter(QSyntaxHighlighter):
    """Lexes the whole document (debounced) and applies cached per-line formats,
    so multi-line strings and comments are coloured correctly."""

    def __init__(self, doc, lexer=None):
        super().__init__(doc)
        self.lexer = lexer or TextLexer(stripnl=False, ensurenl=False)
        self._formats: dict[int, list[tuple[int, int, QTextCharFormat]]] = {}
        self._fmt_cache: dict = {}
        self._timer = QTimer(singleShot=True, interval=250)
        self._timer.timeout.connect(self.relex)
        doc.contentsChange.connect(lambda *_: self._timer.start())
        self.relex()

    def set_lexer(self, lexer):
        self.lexer = lexer
        self.relex()

    def _fmt(self, ttype):
        f = self._fmt_cache.get(ttype)
        if f is not None:
            return f
        colors = Theme.token_colors()
        spec = None
        for t in reversed(ttype.split()):   # Keyword.Constant -> Keyword -> Token
            key = str(t)[len("Token."):]
            if key in colors:
                spec = colors[key]
                break
        f = None
        if spec:
            f = QTextCharFormat()
            f.setForeground(QColor(spec[0]))
            if spec[1]:
                f.setFontWeight(QFont.Weight.Bold)
            if "Comment" in str(ttype):
                f.setFontItalic(True)
        self._fmt_cache[ttype] = f
        return f

    def relex(self):
        doc = self.document()
        self._fmt_cache.clear()
        self._formats = {}
        text = doc.toPlainText()
        if isinstance(self.lexer, TextLexer) or len(text) > MAX_HIGHLIGHT_CHARS:
            self.rehighlight()
            return
        line, col = 0, 0
        for ttype, value in lex(text, self.lexer):
            fmt = self._fmt(ttype)
            parts = value.split("\n")
            for k, part in enumerate(parts):
                if k > 0:
                    line += 1
                    col = 0
                if part and fmt is not None:
                    self._formats.setdefault(line, []).append((col, len(part), fmt))
                col += len(part)
        self.rehighlight()

    def highlightBlock(self, text):
        for start, length, fmt in self._formats.get(self.currentBlock().blockNumber(), ()):
            self.setFormat(start, length, fmt)


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)

    def mousePressEvent(self, e):
        self.editor.gutter_click(e.position().toPoint())


class CodeEditor(QPlainTextEdit):
    zoomChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fold_enabled = True        # compare views switch this off (rows must stay aligned)
        self.bookmarks: list[QTextCursor] = []   # cursors follow edits automatically
        self.setFont(mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.gutter = LineNumberArea(self)
        self.line_label = None          # optional callable(blockNumber) -> str for custom numbers
        self.line_colors = {}           # blockNumber -> QColor, for the gutter (diff views)
        self.layers: dict[str, list] = {}
        self.highlighter = PygmentsHighlighter(self.document())
        self.comment_prefix = "#"
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._cursor_moved)
        self._update_gutter_width()
        self._cursor_moved()
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.indent_text = "    "

    # ---------------------------------------------------------------- gutter
    def gutter_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * max(3, digits) + (24 if self.fold_enabled else 0)

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        p = QPainter(self.gutter)
        p.fillRect(event.rect(), Theme.c("gutter_bg"))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        cur = self.textCursor().blockNumber()
        fh = self.fontMetrics().height()
        marks = {c.blockNumber() for c in self.bookmarks}
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                col = self.line_colors.get(num)
                if col is not None:
                    p.fillRect(0, top, self.gutter.width(), bottom - top, col)
                label = self.line_label(num) if self.line_label else str(num + 1)
                p.setPen(Theme.c("fg") if num == cur else Theme.c("gutter_fg"))
                right = self.gutter.width() - (16 if self.fold_enabled else 6)
                p.drawText(0, top, right, fh, Qt.AlignmentFlag.AlignRight, label)
                if self.fold_enabled:
                    if num in marks:
                        p.setBrush(QColor("#4c8dff"))
                        p.setPen(Qt.PenStyle.NoPen)
                        p.drawEllipse(3, top + fh // 2 - 4, 8, 8)
                    if self._foldable(block):
                        folded = block.next().isValid() and not block.next().isVisible()
                        p.setPen(Theme.c("gutter_fg"))
                        x, y = self.gutter.width() - 12, top + fh // 2
                        p.drawText(x - 2, top, 12, fh, Qt.AlignmentFlag.AlignCenter, "▸" if folded else "▾")
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1

    # ---------------------------------------------------------------- bookmarks & folding
    def gutter_click(self, pt):
        if not self.fold_enabled:
            return
        block = self.cursorForPosition(pt.__class__(0, pt.y())).block()
        if pt.x() < 14:
            self.toggle_bookmark(block.blockNumber())
        elif pt.x() > self.gutter.width() - 16:
            self.toggle_fold(block)

    def toggle_bookmark(self, line: int | None = None):
        line = self.textCursor().blockNumber() if line is None else line
        hit = [c for c in self.bookmarks if c.blockNumber() == line]
        if hit:
            for c in hit:
                self.bookmarks.remove(c)
        else:
            self.bookmarks.append(QTextCursor(self.document().findBlockByNumber(line)))
        self.gutter.update()

    def next_bookmark(self, step: int = 1):
        lines = sorted({c.blockNumber() for c in self.bookmarks})
        if not lines:
            return
        cur = self.textCursor().blockNumber()
        cand = [l for l in lines if (l > cur if step > 0 else l < cur)]
        target = (cand[0] if step > 0 else cand[-1]) if cand else (lines[0] if step > 0 else lines[-1])
        self.go_to_line(target + 1)

    def clear_bookmarks(self):
        self.bookmarks.clear()
        self.gutter.update()

    @staticmethod
    def _indent(text: str) -> int:
        return len(text.expandtabs(4)) - len(text.expandtabs(4).lstrip())

    def _fold_end(self, block):
        """Last block of the region that folds under `block` (by indentation), or None."""
        if not block.text().strip():
            return None
        base = self._indent(block.text())
        b, last = block.next(), None
        while b.isValid():
            t = b.text()
            if t.strip():
                if self._indent(t) <= base:
                    # include a closing bracket line at the same level (C, JSON, XML end tags)
                    if t.strip()[:1] in "}])" or t.strip().startswith("</"):
                        last = b
                    break
                last = b
            b = b.next()
        return last

    def _foldable(self, block) -> bool:
        nxt = block.next()
        while nxt.isValid() and not nxt.text().strip():
            nxt = nxt.next()
        return bool(block.text().strip()) and nxt.isValid() and self._indent(nxt.text()) > self._indent(block.text())

    def toggle_fold(self, block=None):
        block = block or self.textCursor().block()
        end = self._fold_end(block)
        if end is None or not self._foldable(block):
            return
        show = not block.next().isVisible()
        b = block.next()
        while b.isValid() and b.blockNumber() <= end.blockNumber():
            b.setVisible(show)
            b = b.next()
        self._relayout(block, end)

    def _relayout(self, start, end):
        doc = self.document()
        doc.markContentsDirty(start.position(), end.position() + end.length() - start.position())
        self.viewport().update()
        self.gutter.update()
        self._update_gutter_width()

    def fold_all(self, fold=True):
        b = self.document().firstBlock()
        while b.isValid():
            if fold and self._indent(b.text()) == 0 and self._foldable(b) and b.next().isVisible():
                self.toggle_fold(b)
            b = b.next()
        if not fold:
            b = self.document().firstBlock()
            while b.isValid():
                b.setVisible(True)
                b = b.next()
            self._relayout(self.document().firstBlock(), self.document().lastBlock())

    # ---------------------------------------------------------------- selections
    def set_layer(self, name: str, selections: list):
        self.layers[name] = selections
        self._apply_layers()

    def _apply_layers(self):
        sels = []
        for name in ("diff", "current", "brace", "search"):
            sels.extend(self.layers.get(name, []))
        self.setExtraSelections(sels)

    def _cursor_moved(self):
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(Theme.c("current_line"))
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.layers["current"] = [] if "diff" in self.layers and self.layers["diff"] else [sel]
        self.layers["brace"] = self._brace_match()
        self._apply_layers()
        self.gutter.update()

    def _brace_match(self):
        pairs = {"(": ")", "[": "]", "{": "}"}
        rev = {v: k for k, v in pairs.items()}
        doc = self.document()
        pos = self.textCursor().position()
        for p in (pos, pos - 1):
            ch = doc.characterAt(p)
            if ch in pairs or ch in rev:
                fwd = ch in pairs
                other = pairs[ch] if fwd else rev[ch]
                depth, q = 0, p
                limit = 200_000
                while 0 <= q < doc.characterCount() and limit:
                    c = doc.characterAt(q)
                    if c == ch:
                        depth += 1
                    elif c == other:
                        depth -= 1
                        if depth == 0:
                            return [self._char_sel(p), self._char_sel(q)]
                    q += 1 if fwd else -1
                    limit -= 1
                return []
        return []

    def _char_sel(self, pos):
        s = QTextEdit.ExtraSelection()
        s.format.setBackground(Theme.c("found"))
        c = QTextCursor(self.document())
        c.setPosition(pos)
        c.setPosition(pos + 1, QTextCursor.MoveMode.KeepAnchor)
        s.cursor = c
        return s

    def highlight_all(self, pattern: str, flags):
        """Mark all matches of a regex (used by search)."""
        out = []
        if pattern:
            try:
                rx = re.compile(pattern, flags)
            except re.error:
                rx = None
            if rx:
                text = self.toPlainText()
                fmt_bg = Theme.c("found")
                for k, m in enumerate(rx.finditer(text)):
                    if k > 5000 or m.end() == m.start():
                        break
                    s = QTextEdit.ExtraSelection()
                    s.format.setBackground(fmt_bg)
                    c = QTextCursor(self.document())
                    c.setPosition(m.start())
                    c.setPosition(m.end(), QTextCursor.MoveMode.KeepAnchor)
                    s.cursor = c
                    out.append(s)
        self.set_layer("search", out)
        return len(out)

    # ---------------------------------------------------------------- options
    def set_show_whitespace(self, on: bool):
        opt = self.document().defaultTextOption()
        flags = opt.flags()
        if on:
            flags |= QTextOption.Flag.ShowTabsAndSpaces | QTextOption.Flag.ShowLineAndParagraphSeparators
        else:
            flags &= ~(QTextOption.Flag.ShowTabsAndSpaces | QTextOption.Flag.ShowLineAndParagraphSeparators)
        opt.setFlags(flags)
        self.document().setDefaultTextOption(opt)

    def set_wrap(self, on: bool):
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if on else QPlainTextEdit.LineWrapMode.NoWrap)

    def set_lexer(self, lexer):
        self.highlighter.set_lexer(lexer)
        aliases = getattr(lexer, "aliases", []) or []
        self.comment_prefix = next((COMMENT_PREFIX[a] for a in aliases if a in COMMENT_PREFIX), "#")

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1 if e.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(e)

    def zoom(self, delta: int):
        f = self.font()
        f.setPointSize(max(6, min(40, f.pointSize() + delta)))
        self.setFont(f)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._update_gutter_width()
        self.zoomChanged.emit(f.pointSize())

    # ---------------------------------------------------------------- editing helpers
    def _selected_block_range(self):
        c = self.textCursor()
        doc = self.document()
        start = doc.findBlock(c.selectionStart()).blockNumber()
        end_block = doc.findBlock(c.selectionEnd())
        end = end_block.blockNumber()
        if c.hasSelection() and c.selectionEnd() == end_block.position() and end > start:
            end -= 1
        return start, end

    def _replace_lines(self, start, end, new_lines, keep_selection=True):
        doc = self.document()
        c = QTextCursor(doc.findBlockByNumber(start))
        last = doc.findBlockByNumber(end)
        c.beginEditBlock()
        c.setPosition(last.position() + last.length() - 1, QTextCursor.MoveMode.KeepAnchor)
        c.insertText("\n".join(new_lines))
        c.endEditBlock()
        if keep_selection:
            s = QTextCursor(doc.findBlockByNumber(start))
            e = doc.findBlockByNumber(start + len(new_lines) - 1)
            s.setPosition(e.position() + e.length() - 1, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(s)

    def lines(self, start, end):
        doc = self.document()
        return [doc.findBlockByNumber(i).text() for i in range(start, end + 1)]

    def indent(self, back=False):
        s, e = self._selected_block_range()
        out = []
        for ln in self.lines(s, e):
            if back:
                if ln.startswith(self.indent_text):
                    ln = ln[len(self.indent_text):]
                elif ln.startswith("\t"):
                    ln = ln[1:]
                else:
                    ln = ln.lstrip(" ")
            else:
                ln = self.indent_text + ln
            out.append(ln)
        self._replace_lines(s, e, out)

    def toggle_comment(self):
        s, e = self._selected_block_range()
        lines = self.lines(s, e)
        p = self.comment_prefix
        nonblank = [l for l in lines if l.strip()]
        commented = nonblank and all(l.lstrip().startswith(p.strip()) for l in nonblank)
        out = []
        for l in lines:
            if not l.strip():
                out.append(l)
            elif commented:
                i = l.index(p.strip())
                rest = l[i + len(p.strip()):]
                out.append(l[:i] + (rest[1:] if rest.startswith(" ") else rest))
            else:
                ind = len(l) - len(l.lstrip())
                out.append(l[:ind] + p.strip() + " " + l[ind:])
        self._replace_lines(s, e, out)

    def duplicate_line(self):
        s, e = self._selected_block_range()
        lines = self.lines(s, e)
        self._replace_lines(s, e, lines + lines, keep_selection=False)

    def delete_line(self):
        s, e = self._selected_block_range()
        c = QTextCursor(self.document().findBlockByNumber(s))
        last = self.document().findBlockByNumber(e)
        c.beginEditBlock()
        c.setPosition(min(last.position() + last.length(), self.document().characterCount() - 1),
                      QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        c.endEditBlock()

    def move_lines(self, direction: int):
        s, e = self._selected_block_range()
        if (direction < 0 and s == 0) or (direction > 0 and e >= self.blockCount() - 1):
            return
        if direction < 0:
            seg = self.lines(s - 1, e)
            new = seg[1:] + seg[:1]
            self._replace_lines(s - 1, e, new, keep_selection=False)
            s2, e2 = s - 1, e - 1
        else:
            seg = self.lines(s, e + 1)
            new = seg[-1:] + seg[:-1]
            self._replace_lines(s, e + 1, new, keep_selection=False)
            s2, e2 = s + 1, e + 1
        doc = self.document()
        c = QTextCursor(doc.findBlockByNumber(s2))
        eb = doc.findBlockByNumber(e2)
        c.setPosition(eb.position() + eb.length() - 1, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(c)

    def transform_selection(self, fn):
        c = self.textCursor()
        if not c.hasSelection():
            c.select(QTextCursor.SelectionType.WordUnderCursor)
        t = c.selectedText().replace(" ", "\n")
        a = c.selectionStart()
        new = fn(t)
        c.insertText(new)
        c.setPosition(a)
        c.setPosition(a + len(new), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(c)

    def transform_lines(self, fn):
        """Apply fn(list[str]) -> list[str] to selected lines (or the whole document)."""
        if self.textCursor().hasSelection():
            s, e = self._selected_block_range()
        else:
            s, e = 0, self.blockCount() - 1
        self._replace_lines(s, e, fn(self.lines(s, e)))

    def go_to_line(self, line: int, col: int = 0):
        b = self.document().findBlockByNumber(max(0, line - 1))
        c = QTextCursor(b)
        c.movePosition(QTextCursor.MoveOperation.Right, n=min(col, b.length() - 1))
        self.setTextCursor(c)
        self.centerCursor()

    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        ctrl = mods & Qt.KeyboardModifier.ControlModifier
        alt = mods & Qt.KeyboardModifier.AltModifier
        if self.isReadOnly():
            return super().keyPressEvent(e)
        if k == Qt.Key.Key_Tab and self.textCursor().hasSelection() and " " in self.textCursor().selectedText():
            return self.indent()
        if k == Qt.Key.Key_Backtab:
            return self.indent(back=True)
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods & Qt.KeyboardModifier.ShiftModifier:
            line = self.textCursor().block().text()
            ind = line[: len(line) - len(line.lstrip())]
            before = line[: self.textCursor().positionInBlock()].rstrip()
            if before.endswith((":", "{", "(", "[")):
                ind += self.indent_text
            super().keyPressEvent(e)
            self.insertPlainText(ind)
            return
        if alt and k == Qt.Key.Key_Up:
            return self.move_lines(-1)
        if alt and k == Qt.Key.Key_Down:
            return self.move_lines(1)
        if ctrl and k == Qt.Key.Key_D:
            return self.duplicate_line()
        super().keyPressEvent(e)


class EditorTab(QWidget):
    """A file opened in the editor."""
    titleChanged = Signal()

    def __init__(self, path: str | None = None, parent=None, text: str = ""):
        from PySide6.QtWidgets import QVBoxLayout, QSplitter
        super().__init__(parent)
        self.editor = CodeEditor(self)
        self.second = None               # split view: a second view of the same document
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.editor)
        lay.addWidget(self.splitter)
        self.path = path
        self.encoding = "utf-8"
        self.eol = "\r\n" if os.name == "nt" else "\n"
        self.disk_mtime = None
        self.untitled_name = "new"
        if path:
            self.load(path)
        else:
            self.editor.setPlainText(text)
        self.editor.document().modificationChanged.connect(lambda *_: self.titleChanged.emit())

    def toggle_split(self, orientation=Qt.Orientation.Vertical):
        if self.second is not None:
            self.second.setParent(None)
            self.second.deleteLater()
            self.second = None
            return False
        self.second = CodeEditor()
        self.second.setDocument(self.editor.document())
        self.second.fold_enabled = False
        self.second._update_gutter_width()
        self.second.setFont(self.editor.font())
        self.splitter.setOrientation(orientation)
        self.splitter.addWidget(self.second)
        self.second.go_to_line(self.editor.textCursor().blockNumber() + 1)
        return True

    def load(self, path, encoding=None):
        tf = read_text(path, encoding)
        self.editor.setPlainText(tf.text)
        self.encoding, self.eol, self.path = tf.encoding, tf.eol, path
        self.editor.set_lexer(lexer_for(path, tf.text))
        self.editor.document().setModified(False)
        self.disk_mtime = os.path.getmtime(path)
        self.titleChanged.emit()

    def save(self, path=None):
        path = path or self.path
        write_text(path, self.editor.toPlainText(), self.encoding, self.eol)
        if path != self.path:
            self.path = path
            self.editor.set_lexer(lexer_for(path, self.editor.toPlainText()[:2000]))
        self.disk_mtime = os.path.getmtime(path)
        self.editor.document().setModified(False)
        self.titleChanged.emit()

    @property
    def modified(self) -> bool:
        return self.editor.document().isModified()

    def title(self) -> str:
        name = os.path.basename(self.path) if self.path else self.untitled_name
        return ("● " if self.modified else "") + name

    def language_name(self) -> str:
        return getattr(self.editor.highlighter.lexer, "name", "Text")

    def set_language(self, alias: str):
        try:
            self.editor.set_lexer(get_lexer_by_name(alias, stripnl=False, ensurenl=False))
        except ClassNotFound:
            pass
