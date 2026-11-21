"""Deeper Excel and Word comparison: workbook structure, formulas, formatting,
named ranges, duplicates, legacy formats and difference export."""
from __future__ import annotations

import csv
import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from .tablediff import TableResult, EQUAL, CHANGED, LEFT_ONLY, RIGHT_ONLY


# ---------------------------------------------------------------- legacy formats
def convert_legacy(path: str) -> str:
    """Convert .doc/.xls to .docx/.xlsx in a temp folder using LibreOffice (any OS)
    or Microsoft Office via COM (Windows). Returns the new path or raises."""
    ext = os.path.splitext(path)[1].lower()
    target = {".doc": "docx", ".xls": "xlsx"}[ext]
    out_dir = tempfile.mkdtemp(prefix="nexus-conv-")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice and sys.platform == "win32":
        for cand in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
            if os.path.exists(cand):
                soffice = cand
    if soffice:
        subprocess.run([soffice, "--headless", "--convert-to", target, "--outdir", out_dir, path],
                       check=True, capture_output=True, timeout=180)
        res = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + "." + target)
        if os.path.exists(res):
            return res
    if sys.platform == "win32":
        try:
            import win32com.client  # pywin32, optional
            res = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + "." + target)
            if ext == ".doc":
                app = win32com.client.DispatchEx("Word.Application")
                doc = app.Documents.Open(os.path.abspath(path), ReadOnly=True)
                doc.SaveAs2(res, FileFormat=16)
                doc.Close(False)
            else:
                app = win32com.client.DispatchEx("Excel.Application")
                wb = app.Workbooks.Open(os.path.abspath(path), ReadOnly=True)
                wb.SaveAs(res, FileFormat=51)
                wb.Close(False)
            app.Quit()
            return res
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(f"Cannot open {os.path.basename(path)}: install LibreOffice (or Microsoft Office on Windows) "
                       "to compare legacy .doc/.xls files.")


# ---------------------------------------------------------------- Excel workbook level
@dataclass
class CellInfo:
    value: str
    formula: str
    fmt: str          # formatting signature


def _fmt_sig(c) -> str:
    parts = []
    f = c.font
    if f is not None:
        if f.b:
            parts.append("bold")
        if f.i:
            parts.append("italic")
        if f.u:
            parts.append("underline")
        if f.color is not None and f.color.type == "rgb" and f.color.rgb not in (None, "FF000000"):
            parts.append(f"color={f.color.rgb}")
        if f.sz:
            parts.append(f"size={f.sz:g}")
        if f.name:
            parts.append(f"font={f.name}")
    fill = c.fill
    if fill is not None and fill.fill_type == "solid" and fill.fgColor is not None and fill.fgColor.type == "rgb":
        parts.append(f"fill={fill.fgColor.rgb}")
    if c.number_format and c.number_format != "General":
        parts.append(f"numfmt={c.number_format}")
    if c.alignment is not None and c.alignment.horizontal:
        parts.append(f"align={c.alignment.horizontal}")
    return ", ".join(parts)


def read_workbook(path: str) -> dict:
    """{'sheets': {name: {(row, col): CellInfo}}, 'names': {name: ref}}"""
    import openpyxl
    from .loaders import _cell_str

    wf = openpyxl.load_workbook(path, data_only=False)
    wv = openpyxl.load_workbook(path, data_only=True)
    sheets = {}
    for ws in wf.worksheets:
        wsv = wv[ws.title]
        cells = {}
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if v is None and not c.has_style:
                    continue
                formula = v if isinstance(v, str) and v.startswith("=") else ""
                val = _cell_str(wsv.cell(c.row, c.column).value) if formula else _cell_str(v)
                if val == "" and not formula and not _fmt_sig(c):
                    continue
                cells[(c.row, c.column)] = CellInfo(val, formula, _fmt_sig(c))
        sheets[ws.title] = cells
    names = {}
    try:
        for n, dn in wf.defined_names.items():
            names[n] = str(dn.attr_text)
    except AttributeError:
        for dn in getattr(wf.defined_names, "definedName", []):
            names[dn.name] = str(dn.attr_text)
    for ws in wf.worksheets:   # sheet-scoped names
        for n, dn in getattr(ws, "defined_names", {}).items():
            names[f"{ws.title}!{n}"] = str(dn.attr_text)
    return {"sheets": sheets, "names": names}


def _sheet_sig(cells) -> list[str]:
    return [f"{k}:{v.value}" for k, v in sorted(cells.items())]


def pair_sheets(left: dict, right: dict) -> list[tuple[str | None, str | None, str]]:
    """Pair worksheets by name, then unmatched ones by content similarity (rename).
    Returns [(left_name, right_name, status)] status: same-name / renamed / only-left / only-right."""
    ls, rs = left["sheets"], right["sheets"]
    pairs = [(n, n, "same-name") for n in ls if n in rs]
    ul = [n for n in ls if n not in rs]
    ur = [n for n in rs if n not in ls]
    for n in list(ul):
        best, score = None, 0.0
        for m in ur:
            r = difflib.SequenceMatcher(None, _sheet_sig(ls[n]), _sheet_sig(rs[m]), autojunk=False).ratio()
            if r > score:
                best, score = m, r
        if best is not None and score >= 0.6:
            pairs.append((n, best, f"renamed ({score:.0%} similar)"))
            ul.remove(n)
            ur.remove(best)
    pairs += [(n, None, "only-left") for n in ul] + [(None, m, "only-right") for m in ur]
    return pairs


@dataclass
class CellChange:
    sheet: str
    cell: str
    kind: str        # value / formula / format / added / removed
    left: str
    right: str


def _a1(r, c):
    from openpyxl.utils import get_column_letter
    return f"{get_column_letter(c)}{r}"


def compare_cells(left: dict, right: dict, lname: str, rname: str, check_format=True) -> list[CellChange]:
    """Positional ('Normal mode') comparison of one sheet pair including formulas and formats."""
    a, b = left["sheets"][lname], right["sheets"][rname]
    out = []
    for key in sorted(set(a) | set(b)):
        x, y = a.get(key), b.get(key)
        ref = _a1(*key)
        if x is None:
            if y.value or y.formula:
                out.append(CellChange(rname, ref, "added", "", y.formula or y.value))
            continue
        if y is None:
            if x.value or x.formula:
                out.append(CellChange(lname, ref, "removed", x.formula or x.value, ""))
            continue
        if x.formula != y.formula:
            out.append(CellChange(lname, ref, "formula", x.formula, y.formula))
        if x.value != y.value:
            out.append(CellChange(lname, ref, "value", x.value, y.value))
        if check_format and x.fmt != y.fmt:
            out.append(CellChange(lname, ref, "format", x.fmt, y.fmt))
    return out


def compare_names(left: dict, right: dict) -> list[CellChange]:
    out = []
    for n in sorted(set(left["names"]) | set(right["names"])):
        a, b = left["names"].get(n), right["names"].get(n)
        if a != b:
            kind = "added" if a is None else "removed" if b is None else "value"
            out.append(CellChange("(named range)", n, kind, a or "", b or ""))
    return out


def find_duplicate_keys(rows: list[list[str]], key_cols: list[int], header=True) -> dict[tuple, list[int]]:
    seen: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows[1:] if header else rows, start=2 if header else 1):
        k = tuple(r[c] if c < len(r) else "" for c in key_cols)
        seen.setdefault(k, []).append(i)
    return {k: v for k, v in seen.items() if len(v) > 1}


# ---------------------------------------------------------------- export
def export_table_diff(res: TableResult, path: str, only_diffs=True) -> None:
    """Write the aligned comparison to .xlsx (coloured) or .csv."""
    rows = []
    for r in res.rows:
        if only_diffs and r.tag == EQUAL:
            continue
        status = {EQUAL: "identical", CHANGED: "changed", LEFT_ONLY: "only left", RIGHT_ONLY: "only right"}[r.tag]
        changed = ", ".join(res.columns[u] for u in sorted(r.changed_cols))
        lv = [res.cell("left", r.left, u) for u in range(len(res.columns))]
        rv = [res.cell("right", r.right, u) for u in range(len(res.columns))]
        rows.append((status, changed, r, lv, rv))
    header = ["Status", "Changed columns", "Side", "Row"] + res.columns
    if path.lower().endswith(".csv"):
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(header)
            for status, changed, r, lv, rv in rows:
                if r.left is not None:
                    w.writerow([status, changed, "left", r.left + 1] + lv)
                if r.right is not None:
                    w.writerow([status, changed, "right", r.right + 1] + rv)
        return
    import openpyxl
    from openpyxl.styles import PatternFill, Font

    fills = {"changed": "FFF2CC", "only left": "FCE4E4", "only right": "DDEBF7", "identical": "FFFFFF"}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Differences"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    hl = PatternFill("solid", fgColor="F4B183")
    for status, changed, r, lv, rv in rows:
        for side, idx, vals in (("left", r.left, lv), ("right", r.right, rv)):
            if idx is None:
                continue
            ws.append([status, changed, side, idx + 1] + vals)
            for k, cell in enumerate(ws[ws.max_row]):
                cell.fill = PatternFill("solid", fgColor=fills[status])
                if k >= 4 and (k - 4) in r.changed_cols:
                    cell.fill = hl
    ws.freeze_panes = "E2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)


# ---------------------------------------------------------------- Word with formatting
def docx_formatting_lines(path: str) -> list[str]:
    """Paragraph text plus a '⤷ format:' line per paragraph with non-default run
    formatting, so formatting-only changes show up as differences."""
    from .loaders import docx_to_lines
    return docx_to_lines(path, with_format=True)
