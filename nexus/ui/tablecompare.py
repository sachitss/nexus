"""Side-by-side table comparison for CSV, Excel and VDI 3805 files."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtGui import QColor, QKeySequence, QAction
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QToolButton, QLabel, QComboBox,
                               QCheckBox, QTableView, QSplitter, QFileDialog, QMessageBox, QSpinBox,
                               QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                               QToolBar, QPushButton)

from ..core import loaders, report
from ..core.tablediff import TableOptions, diff_tables, EQUAL, CHANGED, LEFT_ONLY, RIGHT_ONLY
from .theme import Theme


class SideModel(QAbstractTableModel):
    def __init__(self, side: str):
        super().__init__()
        self.side = side
        self.res = None
        self.rows = []          # filtered TRow list
        self.vdi = False

    def set(self, res, rows):
        self.beginResetModel()
        self.res, self.rows = res, rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.res.columns) if self.res else 0

    def _idx(self, r):
        return r.left if self.side == "left" else r.right

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        r = self.rows[index.row()]
        idx = self._idx(r)
        u = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self.res.cell(self.side, idx, u)
        if role == Qt.ItemDataRole.BackgroundRole:
            if idx is None:
                return Theme.c("filler")
            if r.tag == CHANGED:
                return Theme.c("changed_hl") if u in r.changed_cols else Theme.c("changed")
            if r.tag == LEFT_ONLY:
                return Theme.c("left")
            if r.tag == RIGHT_ONLY:
                return Theme.c("right")
            m = self.res.lmap if self.side == "left" else self.res.rmap
            if m[u] is None:
                return Theme.c("filler")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or not self.res:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if self.vdi:
                return f"F{section + 1}" if section else "Record"
            m = self.res.lmap if self.side == "left" else self.res.rmap
            name = self.res.columns[section]
            return name if m[section] is not None else f"({name})"
        idx = self._idx(self.rows[section])
        if idx is None:
            return ""
        return str(idx + 1 + (1 if self.res.header_left else 0))


class TableCompareView(QWidget):
    titleChanged = Signal()

    def __init__(self, left: str | None = None, right: str | None = None, vdi: bool = False, parent=None):
        super().__init__(parent)
        self.paths = [left, right]
        self.sheets = [[], []]
        self.vdi = vdi
        self.res = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        tb = QToolBar()
        a = QAction("Recompare", self)
        a.setShortcut(QKeySequence("F5"))
        a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        a.triggered.connect(self.reload)
        tb.addAction(a)
        self.addAction(a)
        tb.addSeparator()
        for text, step, sc in (("◀ Prev", -1, "Shift+F7"), ("Next ▶", 1, "F7")):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(sc))
            a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            a.triggered.connect(lambda _=False, s=step: self.goto_diff(s))
            tb.addAction(a)
            self.addAction(a)
        tb.addSeparator()
        self.only = QCheckBox("Only differences")
        self.only.toggled.connect(self._fill)
        tb.addWidget(self.only)
        self.header = QCheckBox("First row is header")
        self.header.setChecked(not vdi)
        self.header.toggled.connect(self.compare)
        self._a_header = tb.addWidget(self.header)
        self._a_kl = tb.addWidget(QLabel("  Key:"))
        self.keys = QLineEdit()
        self.keys.setMaximumWidth(180)
        self.keys.setPlaceholderText("column names/letters, e.g. A or ID")
        self.keys.setToolTip("Rows are matched by these key columns (comma separated column letters, numbers or\n"
                             "header names). Leave empty to align rows by position/content.")
        self.keys.editingFinished.connect(self.compare)
        self._a_keys = tb.addWidget(self.keys)
        self.vdi_keys = QSpinBox()
        self.vdi_keys.setRange(0, 12)
        self.vdi_keys.setSpecialValueText("auto")
        self.vdi_keys.setPrefix("first ")
        self.vdi_keys.setSuffix(" fields")
        self.vdi_keys.setToolTip("VDI 3805: number of leading fields (record type + index fields) that identify a record.\n"
                                 "'auto' picks the smallest number that makes records unique.")
        self.vdi_keys.valueChanged.connect(self.compare)
        self._a_vdi = tb.addWidget(self.vdi_keys)
        self.case = QCheckBox("Ignore case")
        self.case.toggled.connect(self.compare)
        tb.addWidget(self.case)
        self.tol = QDoubleSpinBox()
        self.tol.setDecimals(6)
        self.tol.setRange(0, 1e9)
        self.tol.setSpecialValueText("exact")
        self.tol.setPrefix("± ")
        self.tol.setToolTip("Numeric tolerance: numbers within this distance are treated as equal (accepts 1,5 and 1.5)")
        self.tol.valueChanged.connect(self.compare)
        tb.addWidget(QLabel(" Numbers:"))
        tb.addWidget(self.tol)
        self.rectype = QComboBox()
        self.rectype.currentIndexChanged.connect(self._fill)
        self.rectype_label = QLabel("  Record type:")
        self._a_rl = tb.addWidget(self.rectype_label)
        self._a_rt = tb.addWidget(self.rectype)
        tb.addSeparator()
        a = QAction("Export report…", self)
        a.triggered.connect(self.export_report)
        tb.addAction(a)
        v.addWidget(tb)
        for a in (self._a_kl, self._a_keys, self._a_header):
            a.setVisible(not vdi)
        for a in (self._a_vdi, self._a_rl, self._a_rt):
            a.setVisible(vdi)

        prow = QHBoxLayout()
        prow.setContentsMargins(4, 0, 4, 0)
        self.path_edits, self.sheet_boxes = [], []
        for i in range(2):
            le = QLineEdit(self.paths[i] or "")
            le.returnPressed.connect(lambda i=i: self.set_path(i, self.path_edits[i].text()))
            b = QToolButton(text="…")
            b.clicked.connect(lambda _=False, i=i: self.browse(i))
            cb = QComboBox()
            cb.setToolTip("Worksheet")
            cb.currentIndexChanged.connect(self.compare)
            prow.addWidget(le, 1)
            prow.addWidget(b)
            prow.addWidget(cb)
            if i == 0:
                prow.addSpacing(20)
            self.path_edits.append(le)
            self.sheet_boxes.append(cb)
        v.addLayout(prow)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.models = [SideModel("left"), SideModel("right")]
        self.views = []
        for m in self.models:
            m.vdi = vdi
            t = QTableView()
            t.setModel(m)
            t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            t.verticalHeader().setDefaultSectionSize(22)
            t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            split.addWidget(t)
            self.views.append(t)
        for a, b in ((0, 1), (1, 0)):
            self.views[a].verticalScrollBar().valueChanged.connect(self.views[b].verticalScrollBar().setValue)
            self.views[a].horizontalScrollBar().valueChanged.connect(self.views[b].horizontalScrollBar().setValue)
            self.views[a].clicked.connect(lambda idx, b=b: self._select(idx.row(), b))
        vsplit.addWidget(split)
        # detail pane: one record field-by-field
        self.detail = QTableWidget(0, 3)
        self.detail.setHorizontalHeaderLabels(["Column", "Left", "Right"])
        self.detail.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.detail.verticalHeader().setVisible(False)
        self.detail.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        vsplit.addWidget(self.detail)
        vsplit.setSizes([500, 160])
        v.addWidget(vsplit, 1)
        self.status = QLabel()
        self.status.setContentsMargins(6, 2, 6, 2)
        v.addWidget(self.status)
        self.load()

    def title(self):
        n = [os.path.basename(p) if p else "?" for p in self.paths]
        return f"{n[0]} ↔ {n[1]}"

    def has_unsaved(self):
        return False

    # ---------------------------------------------------------------- loading
    def browse(self, i):
        p, _ = QFileDialog.getOpenFileName(self, "Open table", self.paths[i] or "",
                                           "Tables (*.csv *.tsv *.txt *.xlsx *.xlsm *.vdi *.0* *.*)")
        if p:
            self.set_path(i, p)

    def set_path(self, i, p):
        self.paths[i] = p
        self.load()
        self.titleChanged.emit()

    def reload(self):
        self.load()

    def load(self):
        for i, p in enumerate(self.paths):
            box = self.sheet_boxes[i]
            box.blockSignals(True)
            prev = box.currentText()
            box.clear()
            self.sheets[i] = []
            if p and os.path.isfile(p):
                try:
                    self.sheets[i] = [loaders.load_vdi3805(p)] if self.vdi else loaders.load_table(p)
                except Exception as ex:  # noqa: BLE001 - show any loader problem to the user
                    QMessageBox.warning(self, "Load error", f"Could not read {p}:\n{ex}")
            box.addItems([s.name for s in self.sheets[i]])
            if prev and box.findText(prev) >= 0:
                box.setCurrentIndex(box.findText(prev))
            box.setVisible(len(self.sheets[i]) > 1)
            box.blockSignals(False)
            self.path_edits[i].setText(p or "")
        if not self.vdi and not self.keys.text().strip() and not getattr(self, "_key_guessed", False):
            self._key_guessed = True
            l, r = self._rows_of(0), self._rows_of(1)
            if l and r and l[0] and r[0] and l[0][0] == r[0][0]:
                cl = [x[0] for x in l[1:] if x]
                cr = [x[0] for x in r[1:] if x]
                if cl and len(set(cl)) == len(cl) and len(set(cr)) == len(cr):
                    self.keys.setText(l[0][0] or "A")   # first column looks like an ID
        # pair sheets by name where possible
        if self.sheets[0] and self.sheets[1]:
            names = [s.name for s in self.sheets[1]]
            ln = self.sheet_boxes[0].currentText()
            if ln in names:
                self.sheet_boxes[1].blockSignals(True)
                self.sheet_boxes[1].setCurrentIndex(names.index(ln))
                self.sheet_boxes[1].blockSignals(False)
        self.compare()

    def _rows_of(self, i):
        return self.sheets[i][0].rows if self.sheets[i] else []

    def _rows(self, i):
        sh = self.sheets[i]
        if not sh:
            return []
        k = max(0, self.sheet_boxes[i].currentIndex())
        return sh[min(k, len(sh) - 1)].rows

    def _parse_keys(self, header):
        out = []
        for tok in [t.strip() for t in self.keys.text().split(",") if t.strip()]:
            if tok.isdigit():
                out.append(int(tok) - 1)
            elif header and tok in header:
                out.append(header.index(tok))
            elif tok.isalpha() and tok.isupper() and len(tok) <= 3:
                n = 0
                for ch in tok:
                    n = n * 26 + (ord(ch) - 64)
                out.append(n - 1)
            elif header:
                low = [h.lower() for h in header]
                if tok.lower() in low:
                    out.append(low.index(tok.lower()))
        return out

    def _auto_vdi_keys(self, left, right):
        for n in range(1, 9):
            ok = True
            for rows in (left, right):
                seen = set()
                for r in rows:
                    k = tuple(r[:n])
                    if k in seen:
                        ok = False
                        break
                    seen.add(k)
                if not ok:
                    break
            if ok:
                return n
        return 2

    def compare(self, *_):
        left, right = self._rows(0), self._rows(1)
        opts = TableOptions(ignore_case=self.case.isChecked(),
                            numeric_tolerance=self.tol.value() if self.tol.value() > 0 else None)
        if self.vdi:
            opts.header_row = False
            n = self.vdi_keys.value() or self._auto_vdi_keys(left, right)
            self.vdi_key_count = n
            opts.key_columns = list(range(n))
        else:
            opts.header_row = self.header.isChecked()
            opts.key_columns = self._parse_keys(left[0] if (left and opts.header_row) else None)
        self.res = diff_tables(left, right, opts)
        for m in self.models:
            m.set(self.res, [])
        if self.vdi:
            types = sorted({r[0] for r in left + right if r})
            self.rectype.blockSignals(True)
            cur = self.rectype.currentText()
            self.rectype.clear()
            self.rectype.addItem("All")
            self.rectype.addItems(types)
            if cur and self.rectype.findText(cur) >= 0:
                self.rectype.setCurrentIndex(self.rectype.findText(cur))
            self.rectype.blockSignals(False)
        self._fill()

    def _fill(self, *_):
        if not self.res:
            return
        rows = self.res.rows
        if self.only.isChecked():
            rows = [r for r in rows if r.tag != EQUAL]
        if self.vdi and self.rectype.currentIndex() > 0:
            t = self.rectype.currentText()
            rows = [r for r in rows if (self.res.cell("left", r.left, 0) or self.res.cell("right", r.right, 0)) == t]
        for m in self.models:
            m.set(self.res, rows)
        for v in self.views:
            v.resizeColumnsToContents()
            for c in range(v.model().columnCount()):
                if v.columnWidth(c) > 320:
                    v.setColumnWidth(c, 320)
        s = self.res.stats()
        if self.res.identical:
            msg = "✔ No differences"
        else:
            msg = (f"{s['changed']} changed row(s) with {s['cells']} changed cell(s), {s['left']} only left, "
                   f"{s['right']} only right, {s['equal']} identical")
        if self.vdi:
            msg += f"   |  VDI 3805 records keyed by first {self.vdi_key_count} field(s)"
            msg += f"   |  record types: {self.rectype.count() - 1}"
        elif self.keys.text().strip():
            msg += "   |  rows matched by key"
        else:
            msg += "   |  rows aligned by content (set a key column for better matching)"
        self.status.setText(msg)

    # ---------------------------------------------------------------- navigation
    def _select(self, row, other):
        v = self.views[other]
        v.selectRow(row)
        self._show_detail(row)

    def _show_detail(self, row):
        rows = self.models[0].rows
        if not (0 <= row < len(rows)):
            return
        r = rows[row]
        self.detail.setRowCount(len(self.res.columns))
        for u, name in enumerate(self.res.columns):
            if self.vdi:
                name = "Record type" if u == 0 else f"Field {u + 1}"
            items = [QTableWidgetItem(name), QTableWidgetItem(self.res.cell("left", r.left, u)),
                     QTableWidgetItem(self.res.cell("right", r.right, u))]
            if u in r.changed_cols:
                for it in items[1:]:
                    it.setBackground(Theme.c("changed_hl"))
            for k, it in enumerate(items):
                self.detail.setItem(u, k, it)

    def goto_diff(self, step):
        rows = self.models[0].rows
        if not rows:
            return
        cur = self.views[0].currentIndex().row()
        rng = range(cur + 1, len(rows)) if step > 0 else range(cur - 1, -1, -1)
        for i in rng:
            if rows[i].tag != EQUAL:
                for v in self.views:
                    v.selectRow(i)
                    v.scrollTo(self.models[0].index(i, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
                self._show_detail(i)
                return

    def export_report(self):
        if not self.res:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Save HTML report", "table-comparison.html", "HTML (*.html)")
        if p:
            report.save(report.table_report(self.res, self.paths[0] or "", self.paths[1] or ""), p)
