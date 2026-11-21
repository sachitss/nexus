"""Small shared widgets and helpers for the views."""
from __future__ import annotations

import datetime as _dt
import os
import traceback

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QLineEdit, QToolButton, QLabel, QFileDialog, QTableWidget,
                               QTableWidgetItem, QAbstractItemView, QHeaderView)

from .theme import Theme


class Task(QThread):
    """Runs fn(*args) in a background thread; emits done(result) or failed(message)."""
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        try:
            self.done.emit(self.fn(*self.args, **self.kw))
        except Exception as ex:  # noqa: BLE001 - shown to the user
            traceback.print_exc()
            self.failed.emit(str(ex))


class PathRow(QWidget):
    """Label + line edit + browse button."""
    changed = Signal(str)

    def __init__(self, label: str, folder=False, placeholder="", file_filter="All files (*)", save=False, text=""):
        super().__init__()
        self.folder, self.filter, self.save = folder, file_filter, save
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        if label:
            lab = QLabel(label)
            lab.setMinimumWidth(60)
            h.addWidget(lab)
        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.text()))
        h.addWidget(self.edit, 1)
        b = QToolButton(text="…")
        b.setToolTip("Browse")
        b.clicked.connect(self.browse)
        h.addWidget(b)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, t):
        self.edit.setText(t)

    def browse(self):
        cur = self.text()
        if self.folder:
            p = QFileDialog.getExistingDirectory(self, "Choose folder", cur)
        elif self.save:
            p, _ = QFileDialog.getSaveFileName(self, "Save as", cur, self.filter)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Choose file", cur, self.filter)
        if p:
            self.edit.setText(p)
            self.changed.emit(p)


def fmt_size(n):
    if n is None:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024


def fmt_time(t):
    return "" if t is None else _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def make_table(headers, stretch_last=True) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(22)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(False)
    t.setSortingEnabled(False)
    if stretch_last:
        t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    return t


def fill_table(t: QTableWidget, rows, colors=None, data=None, max_rows=200_000):
    """rows: list of lists; colors: list of QColor|None per row; data: payload per row."""
    t.setSortingEnabled(False)
    t.setRowCount(0)
    rows = rows[:max_rows]
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        brush = QBrush(colors[i]) if colors and colors[i] is not None else None
        for k, v in enumerate(r):
            it = QTableWidgetItem("" if v is None else str(v))
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                it.setData(Qt.ItemDataRole.DisplayRole, v)
            if brush is not None:
                it.setBackground(brush)
            if data is not None and k == 0:
                it.setData(Qt.ItemDataRole.UserRole, data[i])
            t.setItem(i, k, it)
    t.resizeColumnsToContents()
    for k in range(t.columnCount()):
        if t.columnWidth(k) > 420:
            t.setColumnWidth(k, 420)
    t.setSortingEnabled(True)


KIND_COLOR = {"added": "new", "right": "new", "record added": "new", "only right": "new",
              "removed": "different", "left": "different", "record removed": "different", "only left": "different",
              "changed": "modified", "value": "modified", "value changed": "modified", "formula": "modified",
              "format": "excluded", "format only": "excluded", "equivalent": "same", "same": "same",
              "conflict": "different", "renamed": "renamed"}


def kind_color(kind: str) -> QColor | None:
    k = KIND_COLOR.get(kind)
    return Theme.folder(k) if k else None


def save_dialog(parent, title, default_name, filters):
    p, _ = QFileDialog.getSaveFileName(parent, title, default_name, filters)
    return p


def short(path: str) -> str:
    return os.path.basename(os.path.normpath(path)) if path else ""


# ---------------------------------------------------------------- summaries (offline + optional Claude)
def show_text_dialog(parent, title, text):
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox
    d = QDialog(parent)
    d.setWindowTitle(title)
    d.resize(760, 520)
    v = QVBoxLayout(d)
    t = QPlainTextEdit(text)
    t.setReadOnly(True)
    v.addWidget(t)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    bb.rejected.connect(d.reject)
    v.addWidget(bb)
    d.exec()


def summarize_changes(parent, offline_text: str, diff_text: str, context: str):
    """Always shows the offline summary. If the user enabled the optional AI summary
    in Settings, asks for confirmation before sending the difference text (never
    whole files) to the Anthropic API."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMessageBox
    from ..core import ai

    s = QSettings("Nexus", "Nexus")
    if not s.value("ai_enabled", False, type=bool):
        show_text_dialog(parent, "Change summary (offline)", offline_text +
                         "\n\n(An optional AI summary can be enabled in Settings. It is off by default.)")
        return
    if not ai.claude_available():
        show_text_dialog(parent, "Change summary (offline)", offline_text +
                         "\n\nAI summary is enabled but the 'anthropic' package is not installed.")
        return
    if QMessageBox.question(parent, "Send differences to Claude?",
                            f"{len(diff_text):,} characters of difference text will be sent to the Anthropic API "
                            "to write a summary. Whole files are not sent.\n\nContinue?") != QMessageBox.StandardButton.Yes:
        show_text_dialog(parent, "Change summary (offline)", offline_text)
        return
    key = s.value("api_key", "")
    if key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = key
    task = Task(ai.summarize_with_claude, diff_text, context)
    parent._ai_task = task   # keep a reference while running
    task.done.connect(lambda txt: show_text_dialog(parent, "Change summary (Claude)", f"{txt}\n\n---\nOffline summary: {offline_text}"))
    task.failed.connect(lambda m: show_text_dialog(parent, "Change summary (offline)", f"{offline_text}\n\nAI summary failed: {m}"))
    task.start()
