"""Turn files of various formats into comparable text lines or tables."""
from __future__ import annotations

import csv
import datetime as _dt
import io
import os
import re

from .textio import read_text, is_binary

TABLE_EXT = {".csv", ".tsv", ".tab", ".xlsx", ".xlsm", ".xltx", ".xltm", ".xls"}
WORD_EXT = {".docx", ".docm", ".dotx", ".doc"}
XML_EXT = {".xml", ".xsd", ".xsl", ".xslt", ".svg", ".config", ".csproj", ".resx", ".gml", ".ifcxml"}
JSON_EXT = {".json", ".geojson"}
BINARY_EXT = {".exe", ".dll", ".bin", ".dat", ".zip", ".7z", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".dwg",
              ".rvt", ".ifc", ".stp", ".step", ".iso", ".msi", ".so", ".o", ".class"}
VDI_EXT = {".vdi"}   # VDI 3805 files are also detected by content, see is_vdi3805()


def ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


# ---------------------------------------------------------------- Word (.docx)
def _run_format(p) -> str:
    sig = []
    for r in p.runs:
        f = []
        if r.bold:
            f.append("bold")
        if r.italic:
            f.append("italic")
        if r.underline:
            f.append("underline")
        if r.font.strike:
            f.append("strike")
        if r.font.size:
            f.append(f"{r.font.size.pt:g}pt")
        if r.font.name:
            f.append(r.font.name)
        try:
            if r.font.color is not None and r.font.color.type is not None and r.font.color.rgb is not None:
                f.append(f"#{r.font.color.rgb}")
        except (AttributeError, ValueError):
            pass
        if r.font.highlight_color:
            f.append(f"highlight={r.font.highlight_color}")
        if f and r.text.strip():
            sig.append(f"«{r.text.strip()[:40]}» {'+'.join(f)}")
    if p.alignment is not None:
        sig.insert(0, f"align={p.alignment}")
    return "; ".join(sig)


def docx_to_lines(path: str, with_styles: bool = True, with_format: bool = False) -> list[str]:
    """Extract a .docx as lines: paragraphs in body order, tables as pipe rows,
    plus headers/footers. Formatting-only changes are not detected."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(path)
    lines: list[str] = []

    def para_line(p: Paragraph) -> str:
        style = p.style.name if (with_styles and p.style is not None) else ""
        txt = p.text
        if style and style not in ("Normal", "Standard", "Default Paragraph Font"):
            txt = f"[{style}] {txt}"
        if with_format:
            sig = _run_format(p)
            if sig:
                txt += "\n    ⤷ format: " + sig
        return txt

    def emit_table(t: Table, depth=0):
        lines.append(f"{'  ' * depth}┌ Table ({len(t.rows)}×{len(t.columns)})")
        for r in t.rows:
            cells = []
            for c in r.cells:
                cells.append(" ".join(c.text.split("\n")))
            lines.append(f"{'  ' * depth}| " + " | ".join(cells) + " |")
        lines.append(f"{'  ' * depth}└")

    body = d.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.extend(para_line(Paragraph(child, d)).split("\n"))
        elif tag == "tbl":
            emit_table(Table(child, d))
    # headers / footers
    for i, sec in enumerate(d.sections):
        for kind, part in (("Header", sec.header), ("Footer", sec.footer)):
            try:
                if part is None or part.is_linked_to_previous:
                    continue
                txt = [p.text for p in part.paragraphs if p.text.strip()]
            except Exception:
                continue
            if txt:
                lines.append(f"── {kind} (section {i + 1}) ──")
                lines.extend(txt)
    # core properties are often useful when comparing documents
    cp = d.core_properties
    meta = [("Title", cp.title), ("Author", cp.author), ("Last modified by", cp.last_modified_by),
            ("Revision", cp.revision)]
    meta = [f"{k}: {v}" for k, v in meta if v not in (None, "")]
    if meta:
        lines.append("── Document properties ──")
        lines.extend(meta)
    return lines


# ---------------------------------------------------------------- tables
class Sheet:
    def __init__(self, name: str, rows: list[list[str]]):
        self.name = name
        width = max((len(r) for r in rows), default=0)
        self.rows = [list(r) + [""] * (width - len(r)) for r in rows]
        self.width = width


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return repr(v)
    if isinstance(v, _dt.datetime):
        if v.time() == _dt.time(0):
            return v.date().isoformat()
        return v.isoformat(sep=" ")
    return str(v)


def load_xlsx(path: str, formulas: bool = False) -> list[Sheet]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=not formulas, read_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = [[_cell_str(v) for v in row] for row in ws.iter_rows(values_only=True)]
        # trim trailing empty rows / columns
        while rows and not any(rows[-1]):
            rows.pop()
        if rows:
            width = max(len(r) for r in rows)
            while width > 0 and not any(len(r) >= width and r[width - 1] for r in rows):
                width -= 1
            rows = [r[:width] for r in rows]
        sheets.append(Sheet(ws.title, rows))
    wb.close()
    return sheets


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in ";,\t|"}
        return max(counts, key=counts.get) if any(counts.values()) else ","


def load_csv(path: str, delimiter: str | None = None, encoding: str | None = None) -> list[Sheet]:
    tf = read_text(path, encoding)
    delim = delimiter or ("\t" if ext(path) in (".tsv", ".tab") else sniff_delimiter(tf.text[:50_000]))
    rows = list(csv.reader(io.StringIO(tf.text), delimiter=delim))
    return [Sheet(os.path.basename(path), rows)]


def load_xls(path: str) -> list[Sheet]:
    try:
        import xlrd
    except ImportError:
        from .office import convert_legacy
        return load_xlsx(convert_legacy(path))
    book = xlrd.open_workbook(path)
    out = []
    for sh in book.sheets():
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    v = _cell_str(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                else:
                    v = _cell_str(cell.value if cell.value != "" else None)
                row.append(v)
            rows.append(row)
        out.append(Sheet(sh.name, rows))
    return out


def load_table(path: str) -> list[Sheet]:
    if ext(path) == ".xls":
        return load_xls(path)
    if ext(path) in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        return load_xlsx(path)
    if is_vdi3805(path):
        return [load_vdi3805(path)]
    return load_csv(path)


# ---------------------------------------------------------------- VDI 3805
# VDI 3805 product data files are plain text: one data record per line, fields
# separated by ";" and the first field is the record type ("Satzart", e.g. 010).
# The exact field layout of each record type is defined per part of the
# standard (not freely available), so we compare generically: a configurable
# number of leading fields form the record key.
_VDI_LINE = re.compile(r"^\s*\d{1,4}(?:\.\d{1,2})?\s*;")
_VDI_SPLIT = re.compile(r"(?<!\\);")   # ';' inside text is escaped as '\;' (VDI 3805 Blatt 1)


def is_vdi3805(path: str) -> bool:
    if ext(path) in VDI_EXT:
        return True
    if ext(path) in TABLE_EXT | WORD_EXT:
        return False
    try:
        with open(path, "rb") as fh:
            data = fh.read(64_000)
    except OSError:
        return False
    if is_binary(data):
        return False
    text = data.decode("latin-1")
    lines = [l for l in text.splitlines()[:300] if l.strip()]
    if len(lines) < 2:
        return False
    hits = sum(1 for l in lines if _VDI_LINE.match(l))
    return hits / len(lines) > 0.9 and lines[0].lstrip().split(";")[0].strip().lstrip("0") in ("10", "1")


def load_vdi3805(path: str) -> Sheet:
    tf = read_text(path)
    rows = []
    for line in tf.text.split("\n"):
        if not line.strip():
            continue
        line = line.rstrip("\x1a")              # EOF marker allowed by Blatt 1
        if not line.strip():
            continue
        fields = _VDI_SPLIT.split(line) if "\\;" in line else line.split(";")
        # a trailing ';' terminates many records - drop the resulting empty field
        if len(fields) > 1 and fields[-1] == "":
            fields = fields[:-1]
        rows.append([f.strip().replace("\\;", ";") for f in fields])
    return Sheet(os.path.basename(path), rows)


def vdi_record_summary(sheet: Sheet) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in sheet.rows:
        out[r[0]] = out.get(r[0], 0) + 1
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- dispatch
def compare_kind(left: str, right: str) -> str:
    """Pick the comparison view for two paths: folder, table, vdi, word or text."""
    if os.path.isdir(left) and os.path.isdir(right):
        return "folder"
    e = {ext(left), ext(right)}
    if e & WORD_EXT:
        return "word"
    if e <= TABLE_EXT and e:
        return "table"
    if e <= XML_EXT and e:
        return "xml"
    if e <= JSON_EXT and e:
        return "json"
    if is_vdi3805(left) or is_vdi3805(right):
        return "vdi"
    if e & BINARY_EXT or _binary(left) or _binary(right):
        return "binary"
    return "text"


def _binary(path):
    try:
        with open(path, "rb") as fh:
            return is_binary(fh.read(8000))
    except OSError:
        return False


def load_as_lines(path: str, with_format: bool = False) -> list[str]:
    """Text lines for any supported file (Word is flattened to text)."""
    if ext(path) == ".doc":
        from .office import convert_legacy
        return docx_to_lines(convert_legacy(path), with_format=with_format)
    if ext(path) in WORD_EXT:
        return docx_to_lines(path, with_format=with_format)
    with open(path, "rb") as fh:
        head = fh.read(8000)
    if is_binary(head):
        return [f"<binary file, {os.path.getsize(path)} bytes>"]
    return read_text(path).text.split("\n")
