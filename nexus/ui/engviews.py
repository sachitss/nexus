"""Engineering and structured-data views: VDI 3805, XML/JSON structure, binary
(hex) comparison and three-way merge."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QComboBox,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QSplitter, QMessageBox, QDoubleSpinBox,
                               QFileDialog, QPlainTextEdit, QLineEdit, QListWidget, QListWidgetItem, QGroupBox,
                               QFormLayout, QDialog, QDialogButtonBox)

from ..core import vdi3805, vdiguide, structdiff, bindiff, merge3, report
from ..core.ai import summarize_offline
from ..core.engvalue import EngRules
from ..core.textio import read_text, write_text
from .common import (PathRow, make_table, fill_table, kind_color, short, summarize_changes, Task)
from .theme import Theme, mono_font

PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "profiles")


class BuildProfileDialog(QDialog):
    """Build VDI 3805 part profiles from the guideline documents (.docx).
    The documents are only read."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Build VDI 3805 profile from guideline")
        self.resize(720, 420)
        self.result_path = ""
        v = QVBoxLayout(self)
        hint = QLabel("Choose VDI 3805 Blatt 1 (basics) and one or more part sheets as Word files (.docx). Nexus reads "
                      "the record tables (Satzart, field no., name, unit, format, remarks) and writes a JSON profile. "
                      "The documents are only read, never changed. Profiles built from licensed VDI documents are "
                      "for your own use.")
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        v.addWidget(hint)
        f = QFormLayout()
        self.base = PathRow("", placeholder="VDI 3805 Blatt 1 (Grundlagen), e.g. VDI_3805_1.docx",
                            file_filter="Word (*.docx)")
        f.addRow("Blatt 1:", self.base)
        self.parts = QListWidget()
        pb = QHBoxLayout()
        for t, slot in (("Add part sheets…", self._add), ("Remove", self._remove)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            pb.addWidget(b)
        pb.addStretch()
        pw = QWidget()
        pv = QVBoxLayout(pw)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(self.parts)
        pv.addLayout(pb)
        f.addRow("Part sheets:", pw)
        self.out = PathRow("", folder=True, text=vdi3805.user_profile_dir())
        f.addRow("Save to folder:", self.out)
        v.addLayout(f)
        self.status = QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.go = bb.addButton("Build profiles", QDialogButtonBox.ButtonRole.ActionRole)
        self.go.clicked.connect(self.build)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Part sheets", "", "Word (*.docx)")
        for f in files:
            self.parts.addItem(f)

    def _remove(self):
        for it in self.parts.selectedItems():
            self.parts.takeItem(self.parts.row(it))

    def build(self):
        base = self.base.text()
        parts = [self.parts.item(i).text() for i in range(self.parts.count())]
        if not (base or parts):
            self.status.setText("Choose Blatt 1 and/or part sheets first.")
            return
        out = self.out.text() or vdi3805.user_profile_dir()

        def work():
            done = []
            b = vdiguide.extract(base) if base else None
            if b and not parts:
                prof = vdiguide.to_profile([b])
                path = os.path.join(out, vdiguide.profile_filename(prof))
                _save(prof, path)
                done.append((prof, path))
            for p in parts:
                prof = vdiguide.to_profile(([b] if b else []) + [vdiguide.extract(p)])
                path = os.path.join(out, vdiguide.profile_filename(prof))
                _save(prof, path)
                done.append((prof, path))
            return done

        def _save(prof, path):
            import json
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(prof, fh, ensure_ascii=False, indent=1)

        self.go.setEnabled(False)
        self.status.setText("Reading the guideline documents…")
        self.task = Task(work)
        self.task.done.connect(self._done)
        self.task.failed.connect(lambda m: (self.status.setText(f"Failed: {m}"), self.go.setEnabled(True)))
        self.task.start()

    def _done(self, done):
        self.go.setEnabled(True)
        lines = [f"{os.path.basename(p)}: {prof['name']} ({len(prof['records'])} record types)" for prof, p in done]
        self.status.setText("Saved:\n" + "\n".join(lines))
        if done:
            self.result_path = done[-1][1]


class _Base(QWidget):
    titleChanged = Signal()
    compared = Signal(str, str, str, str, object)   # kind, left, right, summary, identical
    openFile = Signal(str)

    def has_unsaved(self):
        return False


# ====================================================================== VDI 3805
class VdiView(_Base):
    """VDI 3805 comparison with engineering rules, structure trees and validation."""

    def __init__(self, left="", right="", parent=None):
        super().__init__(parent)
        self.a = self.b = None
        self.changes = []
        self.issues = ([], [])
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        top = QGroupBox("Files and rules")
        f = QFormLayout(top)
        self.left = PathRow("", placeholder="Left VDI 3805 file (old version)", text=left,
                            file_filter="VDI 3805 (*.vdi *.txt *.csv *.*)")
        self.right = PathRow("", placeholder="Right VDI 3805 file (new version)", text=right,
                             file_filter="VDI 3805 (*.vdi *.txt *.csv *.*)")
        self.profile = PathRow("", placeholder="Automatic: profile for the part named in the file header (010), "
                                           "if installed. Or choose a JSON profile.",
                               file_filter="Profile (*.json)")
        f.addRow("Left:", self.left)
        f.addRow("Right:", self.right)
        f.addRow("Profile:", self.profile)
        rules = QHBoxLayout()
        self.abs_tol = QDoubleSpinBox()
        self.abs_tol.setDecimals(6)
        self.abs_tol.setMaximum(1e9)
        self.abs_tol.setToolTip("Absolute tolerance for numeric fields whose unit is defined in the profile")
        self.rel_tol = QDoubleSpinBox()
        self.rel_tol.setDecimals(8)
        self.rel_tol.setMaximum(1)
        self.rel_tol.setSingleStep(0.0001)
        self.conv = QCheckBox("Allow unit conversion")
        self.conv.setToolTip("Only conversions from the built-in unit table are used (never °C ↔ K).")
        self.fmt = QCheckBox("Show notation-only differences (e.g. 400 vs 400,0)")
        self.fmt.setChecked(True)
        rules.addWidget(QLabel("Abs. tolerance:"))
        rules.addWidget(self.abs_tol)
        rules.addWidget(QLabel("Rel. tolerance:"))
        rules.addWidget(self.rel_tol)
        rules.addWidget(self.conv)
        rules.addWidget(self.fmt)
        rules.addStretch()
        f.addRow("Rules:", rules)
        btns = QHBoxLayout()
        for t, slot in (("Compare (F5)", self.run), ("Summary…", self.summary), ("Export Excel…", self.export_xlsx),
                        ("Export HTML…", self.export_html), ("Build profile from guideline…", self.build_profile),
                        ("Profile template", self.example_profile)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch()
        f.addRow(btns)
        v.addWidget(top)
        self.note = QLabel("Without a profile, fields are compared generically: values are only treated as "
                           "equal when they are identical or differ only in notation. No units are assumed.")
        self.note.setWordWrap(True)
        self.note.setObjectName("Hint")
        v.addWidget(self.note)

        self.tabs = QTabWidget()
        # changes tab
        w = QWidget()
        cv = QVBoxLayout(w)
        cv.setContentsMargins(0, 4, 0, 0)
        fl = QHBoxLayout()
        self.kind_filter = QComboBox()
        self.kind_filter.addItems(["All changes", "Engineering changes only", "Records added", "Records removed",
                                   "Values changed", "Notation only", "Equivalent"])
        self.role_filter = QComboBox()
        self.role_filter.addItems(["All roles", "connection", "geometry", "identifier", "parameter", "text", "reference"])
        self.rt_filter = QComboBox()
        self.rt_filter.addItem("All record types")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by key or value…")
        for wdg in (self.kind_filter, self.role_filter, self.rt_filter):
            wdg.currentIndexChanged.connect(self._fill)
        self.search.textChanged.connect(self._fill)
        for wdg in (QLabel("Show:"), self.kind_filter, self.role_filter, self.rt_filter, self.search):
            fl.addWidget(wdg)
        cv.addLayout(fl)
        self.table = make_table(["Change", "Record type", "Record name", "Key", "Field", "Field name", "Role",
                                 "Left", "Right", "Left line", "Right line"])
        self.table.itemSelectionChanged.connect(self._detail)
        cv.addWidget(self.table, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(mono_font(9))
        self.detail.setMaximumHeight(90)
        cv.addWidget(self.detail)
        self.tabs.addTab(w, "Changes")
        # structure tab
        sp = QSplitter()
        self.trees = []
        for side in ("Left", "Right"):
            t = QTreeWidget()
            t.setHeaderLabels([f"{side} product structure", "Line", "Fields"])
            t.setColumnWidth(0, 300)
            self.trees.append(t)
            sp.addWidget(t)
        self.tabs.addTab(sp, "Structure")
        # validation tab
        self.val = make_table(["Side", "Severity", "Line", "Record type", "Message"])
        self.tabs.addTab(self.val, "Validation")
        v.addWidget(self.tabs, 1)
        self.status = QLabel()
        v.addWidget(self.status)
        if left and right:
            QTimer.singleShot(0, self.run)

    def title(self):
        return f"VDI {short(self.left.text()) or 'left'} ↔ {short(self.right.text()) or 'right'}"

    def example_profile(self):
        p = os.path.join(PROFILE_DIR, "vdi3805_profile_template.json")
        self.profile.setText(p)
        QMessageBox.information(self, "Profile", "Loaded the example profile. It is a synthetic template to show the "
                                "format; replace record and field definitions with those of your VDI 3805 part.")

    def build_profile(self):
        dlg = BuildProfileDialog(self)
        dlg.exec()
        if dlg.result_path:
            self.profile.setText(dlg.result_path)

    def _profile(self):
        p = self.profile.text() or vdi3805.find_profile(self.left.text()) or ""
        self.profile_used = p
        prof = vdi3805.Profile.load(p) if p else vdi3805.Profile()
        if self.abs_tol.value() or self.rel_tol.value():
            prof.rules.abs_tol = self.abs_tol.value()
            prof.rules.rel_tol = self.rel_tol.value()
        prof.rules.allow_unit_conversion = self.conv.isChecked()
        return prof

    def run(self):
        l, r = self.left.text(), self.right.text()
        if not (os.path.isfile(l) and os.path.isfile(r)):
            self.status.setText("Choose two existing VDI 3805 files.")
            return
        try:
            prof = self._profile()
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "VDI 3805", f"Could not read the profile:\n{ex}")
            return
        fmt = self.fmt.isChecked()

        def work():
            a, b = vdi3805.load(l, prof), vdi3805.load(r, prof)
            ch = vdi3805.compare(a, b, include_format=fmt)
            return a, b, ch, (vdi3805.validate(a), vdi3805.validate(b))

        if os.path.getsize(l) + os.path.getsize(r) < 2_000_000:
            try:
                self._show(l, r, prof, work())
            except Exception as ex:  # noqa: BLE001
                QMessageBox.warning(self, "VDI 3805", f"Could not compare:\n{ex}")
            return
        self.status.setText("Comparing… (large catalogue, this runs in the background)")
        self._task = Task(work)
        self._task.done.connect(lambda res: self._show(l, r, prof, res))
        self._task.failed.connect(lambda m: QMessageBox.warning(self, "VDI 3805", f"Could not compare:\n{m}"))
        self._task.start()

    def _show(self, l, r, prof, res):
        self.a, self.b, self.changes, self.issues = res
        auto = "" if self.profile.text() or not getattr(self, "profile_used", "") else " – chosen automatically from the 010 header"
        self.note.setText(f"Profile: {prof.name}" + (f" (part {prof.part})" if prof.part and prof.part not in prof.name else "") + auto +
                          f". Rules: abs tol {prof.rules.abs_tol:g}, rel tol {prof.rules.rel_tol:g}, unit conversion "
                          f"{'allowed' if prof.rules.allow_unit_conversion else 'off'}. Values are never treated as "
                          "equivalent unless unit and rule are defined.")
        self.rt_filter.blockSignals(True)
        self.rt_filter.clear()
        self.rt_filter.addItem("All record types")
        self.rt_filter.addItems(sorted({c.rtype for c in self.changes}))
        self.rt_filter.blockSignals(False)
        self._fill()
        self._trees()
        rows, cols = [], []
        sev = {"error": Theme.folder("different"), "warning": Theme.folder("modified"), "info": None}
        for side, lst in (("left", self.issues[0]), ("right", self.issues[1])):
            for i in lst:
                rows.append([side, i.severity, i.line, i.rtype, i.message])
                cols.append(sev.get(i.severity))
        fill_table(self.val, rows, cols)
        self.tabs.setTabText(2, f"Validation ({len(rows)})")
        s = vdi3805.summary(self.changes)
        txt = summarize_offline("vdi", s)
        self.status.setText(txt)
        self.titleChanged.emit()
        self.compared.emit("vdi", l, r, txt, not any(c.engineering for c in self.changes))

    def _filtered(self):
        k = self.kind_filter.currentIndex()
        kinds = {2: "record added", 3: "record removed", 4: "value changed", 5: "format only", 6: "equivalent"}
        role = self.role_filter.currentText() if self.role_filter.currentIndex() else None
        rt = self.rt_filter.currentText() if self.rt_filter.currentIndex() > 0 else None
        q = self.search.text().lower()
        out = []
        for c in self.changes:
            if k == 1 and not c.engineering:
                continue
            if k in kinds and c.kind != kinds[k]:
                continue
            if role and c.role != role:
                continue
            if rt and c.rtype != rt:
                continue
            if q and q not in f"{c.key} {c.left} {c.right} {c.field_name}".lower():
                continue
            out.append(c)
        return out

    def _fill(self, *_):
        ch = self._filtered()
        fill_table(self.table, [[c.kind, c.rtype, c.record_name, c.key, c.field_no or "", c.field_name, c.role,
                                 c.left, c.right, c.left_line or "", c.right_line or ""] for c in ch],
                   [kind_color(c.kind) for c in ch], data=ch)
        self.tabs.setTabText(0, f"Changes ({len(ch)})")

    def _detail(self):
        it = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        c = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not c or not self.a:
            return
        def rec(ds, line):
            if not line:
                return "(not present)"
            r = ds.records[line - 1] if 0 < line <= len(ds.records) and ds.records[line - 1].line == line else \
                next((x for x in ds.records if x.line == line), None)
            return ";".join(r.fields) if r else ""
        self.detail.setPlainText(f"LEFT  line {c.left_line or '-'}: {rec(self.a, c.left_line)}\n"
                                 f"RIGHT line {c.right_line or '-'}: {rec(self.b, c.right_line)}")

    def _trees(self):
        """Structure trees are filled lazily (real catalogues have 100k+ records)."""
        for t, ds in zip(self.trees, (self.a, self.b)):
            t.clear()
            if not getattr(t, "_lazy_hooked", False):
                t.itemExpanded.connect(self._expand)
                t._lazy_hooked = True
            t._ds = ds
            root = vdi3805.build_structure(ds)
            self._add_children(t, None, root, ds)
            if t.topLevelItemCount() < 50:
                for i in range(t.topLevelItemCount()):
                    t.topLevelItem(i).setExpanded(True)

    def _add_children(self, tree, parent, node, ds):
        items = []
        for ch in node.children[:5000]:
            r = ch.record
            name = ds.profile.record_name(r.rtype)
            it = QTreeWidgetItem([f"{r.rtype}  {name}  " + ";".join(r.fields[1:4]), str(r.line), ";".join(r.fields)])
            it.setData(0, Qt.ItemDataRole.UserRole, ch)
            if ch.children:
                it.addChild(QTreeWidgetItem(["…"]))     # placeholder until expanded
            items.append(it)
        if len(node.children) > 5000:
            items.append(QTreeWidgetItem([f"… {len(node.children) - 5000} more records (see the Changes tab)"]))
        if parent is None:
            tree.addTopLevelItems(items)
        else:
            parent.addChildren(items)

    def _expand(self, item):
        if item.childCount() == 1 and item.child(0).text(0) == "…" and item.child(0).data(0, Qt.ItemDataRole.UserRole) is None:
            node = item.data(0, Qt.ItemDataRole.UserRole)
            tree = item.treeWidget()
            item.takeChild(0)
            if node is not None:
                self._add_children(tree, item, node, tree._ds)

    def summary(self):
        if not self.changes and not self.a:
            return
        offline = summarize_offline("vdi", vdi3805.summary(self.changes))
        diff = "\n".join(f"{c.kind} | {c.rtype} {c.record_name} | key {c.key} | field {c.field_no} {c.field_name} "
                         f"({c.role}) | {c.left} -> {c.right}" for c in self.changes if c.kind != "format only")
        summarize_changes(self, offline, diff, "VDI 3805 product data files (building services catalogue data)")

    def export_xlsx(self):
        if not self.a:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export", "vdi3805-comparison.xlsx", "Excel (*.xlsx)")
        if p:
            vdi3805.export_changes_xlsx(self.changes, *self.issues, p, self.left.text(), self.right.text())

    def export_html(self):
        if not self.a:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export", "vdi3805-comparison.html", "HTML (*.html)")
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(vdi3805.report_html(self.changes, *self.issues, self.left.text(), self.right.text()))


# ====================================================================== XML / JSON
class StructCompareView(_Base):
    """Structure-aware XML / JSON comparison (keyed elements, engineering values)."""

    def __init__(self, left="", right="", parent=None):
        super().__init__(parent)
        self.changes = []
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.left = PathRow("Left:", text=left, file_filter="XML / JSON (*.xml *.json *.xsd *.ifcxml *.*)")
        self.right = PathRow("Right:", text=right, file_filter="XML / JSON (*.xml *.json *.xsd *.ifcxml *.*)")
        self.xsd = PathRow("XSD:", placeholder="Optional schema for validation (otherwise the one referenced in the file)",
                           file_filter="XML Schema (*.xsd)")
        for w in (self.left, self.right, self.xsd):
            v.addWidget(w)
        opts = QHBoxLayout()
        self.collapse = QCheckBox("Collapse added/removed sub-trees")
        self.collapse.setChecked(True)
        self.show_fmt = QCheckBox("Show notation-only / equivalent")
        self.ns = QCheckBox("Ignore namespaces")
        self.ns.setChecked(True)
        self.case = QCheckBox("Ignore case")
        self.tol = QDoubleSpinBox()
        self.tol.setDecimals(6)
        self.tol.setMaximum(1e9)
        self.tol.setToolTip("Absolute tolerance for numbers with the same unit")
        self.conv = QCheckBox("Allow unit conversion")
        for w in (self.collapse, self.show_fmt, self.ns, self.case, QLabel("Tolerance:"), self.tol, self.conv):
            opts.addWidget(w)
        opts.addStretch()
        v.addLayout(opts)
        btns = QHBoxLayout()
        for t, slot in (("Compare (F5)", self.run), ("Validate both", self.validate), ("Summary…", self.summary),
                        ("Export HTML…", self.export)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            btns.addWidget(b)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by path or value…")
        self.filter.textChanged.connect(self._fill)
        btns.addWidget(self.filter, 1)
        v.addLayout(btns)
        sp = QSplitter(Qt.Orientation.Vertical)
        self.table = make_table(["Change", "Category", "Path", "Left", "Right"])
        sp.addWidget(self.table)
        self.msgs = QPlainTextEdit()
        self.msgs.setReadOnly(True)
        self.msgs.setPlaceholderText("Validation messages appear here.")
        sp.addWidget(self.msgs)
        sp.setSizes([500, 120])
        v.addWidget(sp, 1)
        self.status = QLabel()
        v.addWidget(self.status)
        if left and right:
            QTimer.singleShot(0, self.run)

    def title(self):
        return f"{short(self.left.text()) or 'left'} ↔ {short(self.right.text()) or 'right'} (structure)"

    def _load(self, p):
        if p.lower().endswith(".json"):
            return structdiff.flatten_json(structdiff.load_json(p))
        return structdiff.flatten_xml(p, ignore_ns=self.ns.isChecked())

    def run(self):
        l, r = self.left.text(), self.right.text()
        if not (os.path.isfile(l) and os.path.isfile(r)):
            self.status.setText("Choose two existing files.")
            return
        try:
            rules = EngRules(abs_tol=self.tol.value(), allow_unit_conversion=self.conv.isChecked(),
                             ignore_case=self.case.isChecked())
            ch = structdiff.diff_maps(self._load(l), self._load(r), rules, show_format=self.show_fmt.isChecked())
            if self.collapse.isChecked():
                ch = structdiff.collapse_structure(ch)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "Compare", f"Could not compare:\n{ex}")
            return
        self.changes = ch
        self._fill()
        txt = summarize_offline("xml", structdiff.summarize(ch))
        self.status.setText(txt)
        self.titleChanged.emit()
        self.compared.emit("xml", l, r, txt, not any(c.kind in ("added", "removed", "changed") for c in ch))

    def _fill(self, *_):
        q = self.filter.text().lower()
        ch = [c for c in self.changes if not q or q in f"{c.path} {c.left} {c.right}".lower()]
        fill_table(self.table, [c.as_row() for c in ch], [kind_color(c.kind) for c in ch], data=ch)

    def validate(self):
        out = []
        for p in (self.left.text(), self.right.text()):
            if p and os.path.isfile(p) and not p.lower().endswith(".json"):
                m = structdiff.validate_xml(p, self.xsd.text() or None)
                out.append(f"{p}:\n  " + ("\n  ".join(m) if m else "valid"))
        self.msgs.setPlainText("\n\n".join(out) or "Nothing to validate (JSON files have no XSD).")

    def summary(self):
        offline = summarize_offline("xml", structdiff.summarize(self.changes))
        diff = "\n".join(f"{c.kind} [{c.category}] {c.path}: {c.left} -> {c.right}" for c in self.changes)
        summarize_changes(self, offline, diff, "structured XML/JSON data")

    def export(self):
        from ..core.automation import structure_report_html
        p, _ = QFileDialog.getSaveFileName(self, "Export", "structure-comparison.html", "HTML (*.html)")
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(structure_report_html(self.changes, self.left.text(), self.right.text()))


# ====================================================================== binary
class BinaryCompareView(_Base):
    def __init__(self, left="", right="", parent=None):
        super().__init__(parent)
        self.rows = []
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.left = PathRow("Left:", text=left)
        self.right = PathRow("Right:", text=right)
        v.addWidget(self.left)
        v.addWidget(self.right)
        h = QHBoxLayout()
        self.align = QCheckBox("Detect inserted / deleted bytes (files up to 2 MB)")
        self.align.setChecked(True)
        self.only = QCheckBox("Only rows with differences")
        self.only.toggled.connect(self._fill)
        b = QPushButton("Compare (F5)")
        b.clicked.connect(self.run)
        for w in (self.align, self.only, b):
            h.addWidget(w)
        h.addStretch()
        v.addLayout(h)
        self.table = make_table(["Left offset", "Left bytes", "Right offset", "Right bytes"])
        self.table.setFont(mono_font(9))
        v.addWidget(self.table, 1)
        self.status = QLabel()
        v.addWidget(self.status)
        if left and right:
            QTimer.singleShot(0, self.run)

    def title(self):
        return f"{short(self.left.text()) or 'left'} ↔ {short(self.right.text()) or 'right'} (hex)"

    def run(self):
        l, r = self.left.text(), self.right.text()
        if not (os.path.isfile(l) and os.path.isfile(r)):
            self.status.setText("Choose two existing files.")
            return
        if max(os.path.getsize(l), os.path.getsize(r)) > 64 * 1024 * 1024:
            QMessageBox.warning(self, "Binary", "Files over 64 MB are compared by checksum only in Folder Compare.")
            return
        with open(l, "rb") as fa, open(r, "rb") as fb:
            self.rows, st = bindiff.diff_binary(fa.read(), fb.read(), align=self.align.isChecked())
        self._fill()
        same = st["diff_bytes"] == st["left_only"] == st["right_only"] == 0
        txt = (f"{st['equal_bytes']:,} equal byte(s), {st['diff_bytes']:,} different, {st['left_only']:,} only left, "
               f"{st['right_only']:,} only right" + (f"; first difference at 0x{st['first_diff']:X}" if st['first_diff'] is not None else ""))
        self.status.setText("Identical. " + txt if same else txt)
        self.titleChanged.emit()
        self.compared.emit("binary", l, r, txt, same)

    def _fill(self, *_):
        rows = [x for x in self.rows if not self.only.isChecked() or x.diff_cols or not x.left or not x.right]
        out, cols = [], []
        for x in rows[:200_000]:
            out.append(["" if x.offset_l is None else f"{x.offset_l:08X}", bindiff.hex_line(x.left) if x.left else "",
                        "" if x.offset_r is None else f"{x.offset_r:08X}", bindiff.hex_line(x.right) if x.right else ""])
            cols.append(None if not x.diff_cols and x.left and x.right else
                        Theme.folder("modified" if x.left and x.right else "new"))
        fill_table(self.table, out, cols)


# ====================================================================== three-way merge
class MergeView(_Base):
    """Three-way merge: base + two changed versions. Non-conflicting changes are
    applied automatically; each conflict is resolved by the user. The result is
    only written when the user saves it."""

    def __init__(self, base="", left="", right="", parent=None):
        super().__init__(parent)
        self.regions = []
        self.dirty = False
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.base = PathRow("Base:", text=base, placeholder="Common ancestor")
        self.leftp = PathRow("Mine:", text=left, placeholder="Left / my version")
        self.rightp = PathRow("Theirs:", text=right, placeholder="Right / their version")
        self.out = PathRow("Output:", save=True, placeholder="Where to save the merged file (asked again on save)")
        for w in (self.base, self.leftp, self.rightp, self.out):
            v.addWidget(w)
        h = QHBoxLayout()
        for t, slot in (("Merge (F5)", self.run), ("Take mine for all conflicts", lambda: self.resolve_all("left")),
                        ("Take theirs for all conflicts", lambda: self.resolve_all("right")), ("Save result…", self.save)):
            b = QPushButton(t)
            b.clicked.connect(slot)
            h.addWidget(b)
        h.addStretch()
        v.addLayout(h)
        sp = QSplitter()
        lw = QWidget()
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Changes and conflicts"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        lv.addWidget(self.list, 1)
        sp.addWidget(lw)
        rw = QWidget()
        rv = QVBoxLayout(rw)
        rv.setContentsMargins(0, 0, 0, 0)
        three = QSplitter()
        self.views = []
        for name in ("Base", "Mine", "Theirs"):
            box = QWidget()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(0, 0, 0, 0)
            bv.addWidget(QLabel(name))
            ed = QPlainTextEdit()
            ed.setReadOnly(True)
            ed.setFont(mono_font(9))
            bv.addWidget(ed)
            three.addWidget(box)
            self.views.append(ed)
        rv.addWidget(three, 1)
        pick = QHBoxLayout()
        for t, how in (("Use mine", "left"), ("Use theirs", "right"), ("Mine then theirs", "both"),
                       ("Theirs then mine", "both_r"), ("Use base", "base")):
            b = QPushButton(t)
            b.clicked.connect(lambda _=False, how=how: self.resolve(how))
            pick.addWidget(b)
        rv.addLayout(pick)
        rv.addWidget(QLabel("Result for this block (editable):"))
        self.res = QPlainTextEdit()
        self.res.setFont(mono_font(9))
        self.res.textChanged.connect(self._edited)
        rv.addWidget(self.res, 1)
        sp.addWidget(rw)
        sp.setSizes([260, 900])
        v.addWidget(sp, 1)
        self.status = QLabel()
        v.addWidget(self.status)
        if base and left and right:
            QTimer.singleShot(0, self.run)

    def title(self):
        return f"Merge {short(self.leftp.text()) or ''}"

    def has_unsaved(self):
        return self.dirty

    def run(self):
        paths = [self.base.text(), self.leftp.text(), self.rightp.text()]
        if not all(os.path.isfile(p) for p in paths):
            self.status.setText("Choose base, mine and theirs.")
            return
        self.texts = [read_text(p) for p in paths]
        b, l, r = (t.text.split("\n") for t in self.texts)
        self.all_regions = merge3.merge3(b, l, r)
        self.regions = [g for g in self.all_regions if g.kind != "unchanged"]
        self.list.clear()
        for k, g in enumerate(self.regions):
            it = QListWidgetItem(f"{k + 1}. {g.kind}  ({len(g.base)} base / {len(g.left)} mine / {len(g.right)} theirs lines)")
            it.setBackground(QBrush(Theme.folder("different" if g.kind == "conflict" else "modified")))
            self.list.addItem(it)
        self.dirty = True
        self._status()
        if self.regions:
            self.list.setCurrentRow(0)
        self.titleChanged.emit()
        n = merge3.conflicts(self.all_regions)
        self.compared.emit("merge", paths[1], paths[2], f"{len(self.regions)} change block(s), {n} conflict(s)", n == 0)

    def _status(self):
        n = sum(1 for g in self.regions if g.kind == "conflict" and g.resolution is None)
        self.status.setText(f"{len(self.regions)} change block(s); {n} unresolved conflict(s)."
                            + (" Ready to save." if n == 0 else ""))

    def _show(self, i):
        if i < 0 or i >= len(self.regions):
            return
        g = self.regions[i]
        for ed, lines in zip(self.views, (g.base, g.left, g.right)):
            ed.setPlainText("\n".join(lines))
        self.res.blockSignals(True)
        self.res.setPlainText("" if g.resolution is None else "\n".join(g.resolution))
        self.res.setPlaceholderText("Unresolved conflict: choose a version above or type the result.")
        self.res.blockSignals(False)

    def _edited(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.regions):
            self.regions[i].resolution = self.res.toPlainText().split("\n")
            self._mark(i)

    def _mark(self, i):
        g = self.regions[i]
        if g.kind == "conflict":
            self.list.item(i).setBackground(QBrush(Theme.folder("same" if g.resolution is not None else "different")))
        self._status()

    def resolve(self, how, i=None):
        i = self.list.currentRow() if i is None else i
        if not 0 <= i < len(self.regions):
            return
        g = self.regions[i]
        g.resolution = {"left": g.left, "right": g.right, "both": g.left + g.right, "both_r": g.right + g.left,
                        "base": g.base}[how]
        self._mark(i)
        if i == self.list.currentRow():
            self._show(i)

    def resolve_all(self, how):
        for i, g in enumerate(self.regions):
            if g.kind == "conflict" and g.resolution is None:
                self.resolve(how, i)

    def save(self):
        if not self.regions and not getattr(self, "all_regions", None):
            return
        if any(g.kind == "conflict" and g.resolution is None for g in self.regions):
            if QMessageBox.question(self, "Save", "There are unresolved conflicts. Save with conflict markers?") != QMessageBox.StandardButton.Yes:
                return
        p = self.out.text()
        p, _ = QFileDialog.getSaveFileName(self, "Save merged file", p or self.leftp.text())
        if not p:
            return
        if os.path.abspath(p) in [os.path.abspath(x) for x in (self.base.text(), self.leftp.text(), self.rightp.text())]:
            if QMessageBox.question(self, "Overwrite input?", f"{p} is one of the input files. Overwrite it?") != QMessageBox.StandardButton.Yes:
                return
        lines = merge3.merged_lines(self.all_regions, "mine", "theirs")
        t = self.texts[1]
        write_text(p, "\n".join(lines), t.encoding, t.eol)
        self.out.setText(p)
        self.dirty = False
        self.status.setText(f"Saved {p}")
