"""VDI 3805 product data: parsing, structure analysis, validation and
engineering-aware comparison.

Format facts used here (classic text exchange files): one data record per line,
fields separated by ';', field 1 = record type (e.g. 010 = file header), the next
fields are index fields that place the record in the product hierarchy.

Assumptions (the per-part record layouts are defined in the individual VDI 3805
sheets, which are not freely available): without a profile, fields have no
names or units, the record key is "record type + first N index fields" and the
parent of a record is the closest preceding record of a lower record type whose
index fields are a prefix of its own. A JSON *profile* can supply real record
names, field names, units, roles (connection, geometry, identifier, ...) and key
lengths; see profiles/vdi3805_profile_template.json.

The successor standard ISO 16757 plans an XML exchange format (Part 5, XSD).
XML catalogue files are handled by the generic XML structure compare plus XSD
validation (see structdiff.py) using the same engineering value rules.
"""
from __future__ import annotations

import json
import re
import os
from dataclasses import dataclass, field

from .engvalue import EngRules, compare_values, EQUAL, FORMAT_ONLY, EQUIVALENT
from .loaders import load_vdi3805, Sheet


# ---------------------------------------------------------------- profile
@dataclass
class FieldDef:
    name: str
    unit: str = ""
    role: str = ""           # identifier / connection / geometry / parameter / text / reference
    mandatory: bool = False
    numeric: bool = False
    format: str = ""         # VDI 3805 field format: A, A20, I, N, R, T, TA
    name_en: str = ""
    remarks: str = ""
    feature_id: str = ""


_FIELD_KEYS = set(FieldDef.__dataclass_fields__)


@dataclass
class RecordDef:
    rtype: str
    name: str = ""
    name_en: str = ""
    key_fields: int | None = None          # number of leading fields (incl. record type) forming the key
    parent: str | None = None              # parent record type
    fields: list[FieldDef] = field(default_factory=list)
    repeat_from: int | None = None         # 1-based field no. from which fields may repeat ("4 bis n")
    repeat_name: str = ""
    repeat_format: str = ""
    repeat_unit: str = ""


@dataclass
class Profile:
    name: str = "generic"
    part: str = ""
    default_key_fields: int | None = None  # None = automatic
    records: dict[str, RecordDef] = field(default_factory=dict)
    rules: EngRules = field(default_factory=lambda: EngRules(decimal_comma="auto"))
    hierarchical_keys: bool | None = None   # None = detect from the file (real VDI 3805 layout)
    title: str = ""
    path: str = ""

    @classmethod
    def load(cls, path: str) -> "Profile":
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        p = cls(d.get("name", os.path.basename(path)), str(d.get("part", "")), d.get("default_key_fields"))
        p.hierarchical_keys = d.get("hierarchical_keys")
        p.title = d.get("title", "")
        p.path = path
        r = d.get("rules", {})
        p.rules = EngRules(abs_tol=r.get("abs_tol", 0.0), rel_tol=r.get("rel_tol", 0.0),
                           allow_unit_conversion=r.get("allow_unit_conversion", False),
                           decimal_comma=r.get("decimal_comma", "comma"))
        for rt, rd in d.get("records", {}).items():
            rec = RecordDef(rt, rd.get("name", ""), rd.get("name_en", ""), rd.get("key_fields"), rd.get("parent"),
                            [FieldDef(**{k: v for k, v in f.items() if k in _FIELD_KEYS}) for f in rd.get("fields", [])])
            rec.repeat_from = rd.get("repeat_from")
            rec.repeat_name = rd.get("repeat_name", "")
            rec.repeat_format = rd.get("repeat_format", "")
            rec.repeat_unit = rd.get("repeat_unit", "")
            p.records[rt] = rec
        return p

    def record_name(self, rt: str) -> str:
        rd = self.records.get(rt)
        return rd.name if rd and rd.name else ""

    def field(self, rt: str, idx: int) -> FieldDef:
        rd = self.records.get(rt)
        if rd and idx < len(rd.fields):
            return rd.fields[idx]
        if rd and rd.repeat_from:
            base = rd.fields[rd.repeat_from - 1] if rd.repeat_from - 1 < len(rd.fields) else None
            k = idx + 2 - rd.repeat_from
            fmt = rd.repeat_format or (base.format if base else "")
            return FieldDef(f"{rd.repeat_name or (base.name if base else 'Field')} [{k}]",
                            rd.repeat_unit or (base.unit if base else ""), base.role if base else "", False,
                            fmt in ("I", "N", "R") or (base.numeric if base else False), fmt)
        return FieldDef("Record type" if idx == 0 else f"Field {idx + 1}")


# ---------------------------------------------------------------- data model
@dataclass
class Record:
    line: int                 # 1-based line number
    fields: list[str]
    parent: int | None = None  # position of the parent record in Dataset.records

    @property
    def rtype(self) -> str:
        return self.fields[0] if self.fields else ""


@dataclass
class Dataset:
    path: str
    records: list[Record]
    profile: Profile
    key_len: dict[str, int] = field(default_factory=dict)  # record type -> key length
    hierarchical: bool = False
    hkeys: list[tuple] = field(default_factory=list)       # hierarchical key per record

    def key(self, r: Record) -> tuple:
        if self.hierarchical and self.hkeys:
            return self.hkeys[self._pos(r)]
        n = self.key_len.get(r.rtype, 2)
        return tuple(r.fields[:n])

    def _pos(self, r: Record) -> int:
        i = r.line - 1
        if 0 <= i < len(self.records) and self.records[i] is r:
            return i
        return self.records.index(r)


REAL_LAYOUT_TYPES = {"700", "720", "800", "810", "960", "970"}


def is_real_layout(records: list[Record]) -> bool:
    """Real VDI 3805 files (Blatt 1 layout: field 2 = index, decimal sub-records)."""
    types = {r.rtype for r in records}
    return bool(types & REAL_LAYOUT_TYPES) or any("." in t for t in types)


def load(path: str, profile: Profile | None = None) -> Dataset:
    sh: Sheet = load_vdi3805(path)
    recs = []
    for i, row in enumerate(sh.rows):
        row = list(row)
        while len(row) > 1 and row[-1] == "":   # Sheet pads rows to equal width
            row.pop()
        recs.append(Record(i + 1, row))
    ds = Dataset(path, recs, profile or Profile())
    prepare(ds)
    return ds


def prepare(ds: Dataset) -> None:
    """Decide the key model, then compute key lengths, parents and keys."""
    hk = ds.profile.hierarchical_keys
    ds.hierarchical = is_real_layout(ds.records) if hk is None else bool(hk)
    ds.key_len = auto_key_lengths([ds], ds.profile)
    if ds.hierarchical:
        assign_parents(ds)
        ds.hkeys = hierarchical_keys(ds)


# ---------------------------------------------------------------- real-layout hierarchy
BASE_PARENTS = {
    "010": None, "100": None, "110": "100", "160": "110",
    "200": "110", "250": "200", "260": "200", "300": "110", "350": "300", "360": "300",
    "400": "110", "450": "400", "460": "400", "500": "110", "550": "500", "560": "500",
    "600": "110", "610": "600", "700": "110", "720": "700", "730": "700", "740": "700", "760": "700",
    "800": "110", "810": "800", "820": "800", "830": None, "900": None, "920": "900", "930": None,
    "940": None, "950": None, "960": None, "970": None,
}


def default_parent_type(rt: str, profile: Profile) -> str | None:
    rd = profile.records.get(rt)
    if rd is not None:
        return rd.parent
    if "." in rt:
        base, sub = rt.split(".", 1)
        if len(sub) == 2 and sub[0] != "0" and sub[1] != "0" and f"{base}.0{sub[0]}" in profile.records:
            return f"{base}.0{sub[0]}"
        if base in BASE_PARENTS or base in profile.records:
            return base
        return base[:-2] + "00" if base.endswith("10") else base
    return BASE_PARENTS.get(rt)


def assign_parents(ds: Dataset) -> None:
    """Parent = closest preceding record of the parent record type (file order
    as required by VDI 3805 Blatt 1). Unknown parents leave the record at the top."""
    last: dict[str, int] = {}
    cache: dict[str, str | None] = {}
    children: dict[str, list[str]] = {}
    for i, r in enumerate(ds.records):
        rt = r.rtype
        if rt not in cache:
            cache[rt] = default_parent_type(rt, ds.profile)
            children.setdefault(cache[rt], []).append(rt)
        pt = cache[rt]
        r.parent = last.get(pt) if pt else None
        last[rt] = i
        # a new parent closes the previous parent's children (and their children)
        stack = list(children.get(rt, ()))
        while stack:
            c = stack.pop()
            if last.pop(c, None) is not None:
                stack.extend(children.get(c, ()))


def hierarchical_keys(ds: Dataset) -> list[tuple]:
    """Key = chain of (record type, index) from the top-level ancestor down.
    Indices restart below each parent in real files (e.g. 700 below each 110)."""
    keys: list[tuple] = []
    seen: dict[tuple, int] = {}
    for i, r in enumerate(ds.records):
        own = (r.rtype, r.fields[1] if len(r.fields) > 1 else "")
        k = (keys[r.parent] if r.parent is not None else ()) + (own,)
        n = seen.get(k, 0)
        seen[k] = n + 1
        if n:                                   # duplicate index below the same parent: keep both
            k = k[:-1] + ((own[0], f"{own[1]}#{n + 1}"),)
        keys.append(k)
    return keys


def key_text(k: tuple) -> str:
    if k and isinstance(k[0], tuple):
        return " / ".join(f"{t}:{i}" for t, i in k)
    return ";".join(k)


def auto_key_lengths(datasets: list[Dataset], profile: Profile) -> dict[str, int]:
    """Per record type, the smallest number of leading fields that makes records
    unique in every dataset (profile values win)."""
    types = sorted({r.rtype for d in datasets for r in d.records})
    out = {}
    for t in types:
        rd = profile.records.get(t)
        if rd and rd.key_fields:
            out[t] = rd.key_fields
            continue
        if profile.default_key_fields:
            out[t] = profile.default_key_fields
            continue
        rows = [[r.fields for r in d.records if r.rtype == t] for d in datasets]
        width = max((len(f) for rs in rows for f in rs), default=1)
        chosen = width
        for n in range(1, width + 1):
            if all(len({tuple(f[:n]) for f in rs}) == len(rs) for rs in rows):
                chosen = n
                break
        out[t] = max(1, min(chosen, width))
    return out


# ---------------------------------------------------------------- structure
@dataclass
class Node:
    record: Record | None
    children: list["Node"] = field(default_factory=list)


def build_structure(ds: Dataset) -> Node:
    """Product structure tree (see module docstring for the parent rule)."""
    if ds.hierarchical:
        root = Node(None)
        nodes = [Node(r) for r in ds.records]
        for r, n in zip(ds.records, nodes):
            (nodes[r.parent] if r.parent is not None else root).children.append(n)
        return root
    root = Node(None)
    stack: list[tuple[Node, tuple]] = []   # (node, index fields)
    for r in ds.records:
        idx = tuple(r.fields[1:ds.key_len.get(r.rtype, 2)])
        node = Node(r)
        rd = ds.profile.records.get(r.rtype)
        parent = None
        for cand, cidx in reversed(stack):
            crt = cand.record.rtype
            if rd is not None:            # profile decides (parent None = top level)
                if rd.parent and crt == rd.parent:
                    parent = cand
                    break
                continue
            if _num(crt) <= 10:           # the 010 header is never a parent
                continue
            if _num(crt) < _num(r.rtype) and idx[:len(cidx)] == cidx:
                parent = cand
                break
        (parent or root).children.append(node)
        # keep stack small: drop entries of the same or higher type that cannot be parents anymore
        stack = [(n, i) for n, i in stack if _num(n.record.rtype) < _num(r.rtype)]
        stack.append((node, idx))
    return root


def _num(rt: str) -> float:
    try:
        return float(rt)
    except ValueError:
        return 10 ** 6


# ---------------------------------------------------------------- validation
@dataclass
class Issue:
    severity: str        # error / warning / info
    line: int
    rtype: str
    message: str


_RT_RX = re.compile(r"^\d{1,4}(\.\d{1,2})?$")
_INT_RX = re.compile(r"^[+-]?\d+$")
_REAL_RX = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$")
MAX_INFO_PER_TYPE = 20


def check_format(fmt: str, v: str) -> str | None:
    """Check a value against a VDI 3805 field format (Blatt 1, 'Format').
    Returns a message or None. Empty values are not checked here."""
    f = fmt.upper()
    if f == "I":
        return None if _INT_RX.match(v) else "should be an integer (format I)"
    if f in ("N", "R", "D"):
        return None if _REAL_RX.match(v) else f"should be a number with decimal point (format {f})"
    m = re.fullmatch(r"A(\d+)", f)
    if m and len(v) > int(m.group(1)):
        return f"is {len(v)} characters long, format {f} allows {m.group(1)}"
    if f in ("A", "T", "TA") and len(v) > 512:
        return f"is {len(v)} characters long, format {f} allows 512"
    return None


def validate(ds: Dataset) -> list[Issue]:
    issues: list[Issue] = []
    recs = ds.records
    if not recs:
        return [Issue("error", 0, "", "File contains no records")]
    if recs[0].rtype.lstrip("0") not in ("10", "1"):
        issues.append(Issue("warning", recs[0].line, recs[0].rtype, "First record is not the header record 010"))
    width: dict[str, dict[int, int]] = {}
    seen: dict[tuple, int] = {}
    prof = ds.profile
    unknown: dict[str, int] = {}
    for r in recs:
        rt = r.rtype
        if not _RT_RX.match(rt):
            issues.append(Issue("error", r.line, rt, f"Record type '{rt}' is not numeric"))
        width.setdefault(rt, {})
        width[rt][len(r.fields)] = width[rt].get(len(r.fields), 0) + 1
        k = ds.key(r)
        if ds.hierarchical:
            last = k[-1][1] if k else ""
            if "#" in str(last):
                issues.append(Issue("warning", r.line, rt, f"Duplicate index {last.split('#')[0]} below the same "
                                                            f"parent ({key_text(k[:-1]) or 'top level'})"))
        elif k in seen:
            issues.append(Issue("warning", r.line, rt, f"Duplicate record key {';'.join(k)} (first at line {seen[k]})"))
        else:
            seen[k] = r.line
        rd = prof.records.get(rt)
        if prof.records and rd is None:
            unknown[rt] = unknown.get(rt, 0) + 1
        for i, v in enumerate(r.fields):
            if any(ord(c) < 32 and c not in "\t" for c in v):
                issues.append(Issue("warning", r.line, rt, f"Control character in field {i + 1}"))
            if rd is None or (i >= len(rd.fields) and not rd.repeat_from):
                continue
            fd = rd.fields[i] if i < len(rd.fields) else prof.field(rt, i)
            if not v:
                if fd.mandatory:
                    issues.append(Issue("error", r.line, rt, f"Mandatory field {i + 1} '{fd.name}' is empty"))
                continue
            if fd.format:
                msg = check_format(fd.format, v)
                if msg:
                    issues.append(Issue("error", r.line, rt, f"Field {i + 1} '{fd.name}' {msg}: '{v[:40]}'"))
            elif fd.numeric:
                from .engvalue import parse_number
                if parse_number(v, prof.rules.decimal_comma) is None:
                    issues.append(Issue("error", r.line, rt, f"Field '{fd.name}' should be numeric: '{v}'"))
        if rd and rd.fields and len(r.fields) > len(rd.fields) and not rd.repeat_from:
            issues.append(Issue("warning", r.line, rt, f"{len(r.fields)} fields, profile defines {len(rd.fields)}"))
        if ds.hierarchical and r.parent is None:
            pt = default_parent_type(rt, prof)
            if pt:
                issues.append(Issue("error", r.line, rt, f"No parent record found: expected a preceding record "
                                                         f"of type {pt} (orphan)"))
    for rt, n in sorted(unknown.items()):
        issues.append(Issue("warning", 0, rt, f"Record type {rt} is not defined in profile '{prof.name}' ({n} records)"))
    for rt, counts in width.items():
        if len(counts) > 1 and not (prof.records.get(rt) and prof.records[rt].fields) and not ds.hierarchical:
            common = max(counts, key=counts.get)
            shown = 0
            for r in recs:
                if r.rtype == rt and len(r.fields) != common:
                    shown += 1
                    if shown <= MAX_INFO_PER_TYPE:
                        issues.append(Issue("info", r.line, rt, f"Has {len(r.fields)} fields; most records of type {rt} have {common}"))
            if shown > MAX_INFO_PER_TYPE:
                issues.append(Issue("info", 0, rt, f"... {shown - MAX_INFO_PER_TYPE} more records of type {rt} with a different field count"))
    if not ds.hierarchical:
        # orphans: records (other than 010) placed at the top level although lower types exist
        tree = build_structure(ds)
        top_types = sorted({_num(n.record.rtype) for n in tree.children})
        if top_types:
            lowest = top_types[0]
            min_after_header = min((t for t in top_types if t != lowest), default=None)
            for n in tree.children:
                t = _num(n.record.rtype)
                if t not in (lowest, min_after_header) and n.record.rtype in prof.records and prof.records[n.record.rtype].parent:
                    issues.append(Issue("error", n.record.line, n.record.rtype, "No parent record found (orphan)"))
    return sorted(issues, key=lambda i: (i.line, i.severity))


# ---------------------------------------------------------------- comparison
STRUCT_ADDED, STRUCT_REMOVED, VALUE, EQUIV, FORMAT = "record added", "record removed", "value changed", "equivalent", "format only"


@dataclass
class VdiChange:
    kind: str
    rtype: str
    record_name: str
    key: str
    field_no: int | None
    field_name: str
    role: str
    left: str
    right: str
    left_line: int | None
    right_line: int | None

    @property
    def engineering(self) -> bool:
        """True for changes that alter product data (not formatting/equivalence)."""
        return self.kind in (STRUCT_ADDED, STRUCT_REMOVED, VALUE)


def compare(a: Dataset, b: Dataset, include_format=False) -> list[VdiChange]:
    prof = a.profile
    if b.profile is not prof:
        b.profile = prof
        prepare(b)
    if a.hierarchical != b.hierarchical:          # one side looks real, the other not: use the real model
        import copy
        prof = copy.copy(prof)
        prof.hierarchical_keys = True
        a.profile = b.profile = prof
        prepare(a)
        prepare(b)
    if not a.hierarchical:
        keylen = auto_key_lengths([a, b], prof)
        a.key_len = b.key_len = keylen
    ka = {a.key(r): r for r in a.records}
    kb = {b.key(r): r for r in b.records}
    out: list[VdiChange] = []
    order = list(ka) + [k for k in kb if k not in ka]
    for k in order:
        ra, rb = ka.get(k), kb.get(k)
        rt = (ra or rb).rtype
        name = prof.record_name(rt)
        ks = key_text(k)
        if ra is None:
            out.append(VdiChange(STRUCT_ADDED, rt, name, ks, None, "", "", "", ";".join(rb.fields), None, rb.line))
            continue
        if rb is None:
            out.append(VdiChange(STRUCT_REMOVED, rt, name, ks, None, "", "", ";".join(ra.fields), "", ra.line, None))
            continue
        for i in range(max(len(ra.fields), len(rb.fields))):
            va = ra.fields[i] if i < len(ra.fields) else ""
            vb = rb.fields[i] if i < len(rb.fields) else ""
            if va == vb:
                continue
            fd = prof.field(rt, i)
            if fd.unit:   # unit defined by profile: values are plain numbers in that unit
                res = compare_values(va, vb, prof.rules) if (va and vb) else (EQUAL if va == vb else "different")
            else:
                res = compare_values(va, vb, EngRules(normalize_ws=True, decimal_comma=prof.rules.decimal_comma))
                if res == EQUAL and va != vb:
                    res = FORMAT_ONLY
            if res == EQUAL:
                continue
            kind = {FORMAT_ONLY: FORMAT, EQUIVALENT: EQUIV}.get(res, VALUE)
            if kind == FORMAT and not include_format:
                continue
            out.append(VdiChange(kind, rt, name, ks, i + 1, fd.name + (f" [{fd.unit}]" if fd.unit else ""), fd.role,
                                 va, vb, ra.line, rb.line))
    return out


def summary(changes: list[VdiChange]) -> dict:
    s: dict = {"records added": 0, "records removed": 0, "values changed": 0, "format only": 0, "equivalent": 0,
               "connection changes": 0, "geometry changes": 0, "by record type": {}}
    for c in changes:
        s[{STRUCT_ADDED: "records added", STRUCT_REMOVED: "records removed", VALUE: "values changed",
           FORMAT: "format only", EQUIV: "equivalent"}[c.kind]] += 1
        if c.role == "connection" and c.engineering:
            s["connection changes"] += 1
        if c.role == "geometry" and c.engineering:
            s["geometry changes"] += 1
        s["by record type"][c.rtype] = s["by record type"].get(c.rtype, 0) + 1
    return s


def export_changes_xlsx(changes: list[VdiChange], issues_a: list[Issue], issues_b: list[Issue], path: str,
                        left: str, right: str) -> None:
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Changes"
    ws.append(["Left", left])
    ws.append(["Right", right])
    ws.append([])
    hdr = ["Kind", "Record type", "Record name", "Key", "Field #", "Field", "Role", "Left value", "Right value",
           "Left line", "Right line", "Engineering change"]
    ws.append(hdr)
    for c in ws[4]:
        c.font = Font(bold=True)
    fills = {STRUCT_ADDED: "DDEBF7", STRUCT_REMOVED: "FCE4E4", VALUE: "FFF2CC", EQUIV: "E2EFDA", FORMAT: "EDEDED"}
    for c in changes:
        ws.append([c.kind, c.rtype, c.record_name, c.key, c.field_no, c.field_name, c.role, c.left, c.right,
                   c.left_line, c.right_line, "yes" if c.engineering else "no"])
        for cell in ws[ws.max_row]:
            cell.fill = PatternFill("solid", fgColor=fills.get(c.kind, "FFFFFF"))
    ws.auto_filter.ref = f"A4:L{ws.max_row}"
    ws.freeze_panes = "A5"
    for side, iss in (("Validation left", issues_a), ("Validation right", issues_b)):
        v = wb.create_sheet(side)
        v.append(["Severity", "Line", "Record type", "Message"])
        for i in iss:
            v.append([i.severity, i.line, i.rtype, i.message])
    wb.save(path)


def report_html(changes, issues_a, issues_b, left, right) -> str:
    import html
    import datetime as _dt
    s = summary(changes)
    rows = "".join(
        f"<tr class='{c.kind.replace(' ', '-')}'><td>{html.escape(c.kind)}</td><td>{c.rtype}</td><td>{html.escape(c.record_name)}</td>"
        f"<td>{html.escape(c.key)}</td><td>{c.field_no or ''}</td><td>{html.escape(c.field_name)}</td>"
        f"<td>{html.escape(c.left)}</td><td>{html.escape(c.right)}</td></tr>" for c in changes)

    def iss(lst):
        return "".join(f"<tr class='{i.severity}'><td>{i.severity}</td><td>{i.line}</td><td>{i.rtype}</td><td>{html.escape(i.message)}</td></tr>" for i in lst) or "<tr><td colspan=4>No issues</td></tr>"

    summ = ", ".join(f"{v} {k}" for k, v in s.items() if k != "by record type")
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>VDI 3805 comparison</title><style>
body{{font-family:Segoe UI,Arial;margin:16px}} table{{border-collapse:collapse;width:100%;font-size:12px}}
td,th{{border:1px solid #ddd;padding:2px 6px}} th{{background:#17233b;color:#fff}}
.record-added{{background:#ddebf7}}.record-removed{{background:#fce4e4}}.value-changed{{background:#fff2cc}}
.equivalent{{background:#e2efda}}.format-only{{background:#ededed}}.error{{background:#fce4e4}}.warning{{background:#fff2cc}}
</style></head><body><h1>VDI 3805 comparison</h1><p>Left: {html.escape(left)}<br>Right: {html.escape(right)}<br>
Created {_dt.datetime.now():%Y-%m-%d %H:%M}</p><p><b>Summary:</b> {summ}</p>
<h2>Changes</h2><table><tr><th>Kind</th><th>Type</th><th>Record</th><th>Key</th><th>#</th><th>Field</th><th>Left</th><th>Right</th></tr>{rows}</table>
<h2>Validation: left</h2><table><tr><th>Severity</th><th>Line</th><th>Type</th><th>Message</th></tr>{iss(issues_a)}</table>
<h2>Validation: right</h2><table><tr><th>Severity</th><th>Line</th><th>Type</th><th>Message</th></tr>{iss(issues_b)}</table>
</body></html>"""


# ---------------------------------------------------------------- profile lookup
BUNDLED_PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "profiles")


def user_profile_dir() -> str:
    from .automation import app_data_dir          # local import: automation imports this module
    return os.path.join(app_data_dir(), "profiles")


def profile_dirs() -> list[str]:
    """Where part profiles are looked up: the user's profile folder
    (%APPDATA%\\Nexus\\profiles, filled by "Build profile from guideline") first,
    then the profiles shipped with the program."""
    return [d for d in (user_profile_dir(), BUNDLED_PROFILE_DIR) if os.path.isdir(d)]


def header_part(path: str) -> str | None:
    """Part (Blatt) number from the 010 header record, field 2 (VDI 3805 Blatt 1)."""
    try:
        with open(path, "rb") as fh:
            first = fh.readline(4096).decode("latin-1")
    except OSError:
        return None
    f = first.split(";")
    if len(f) > 1 and f[0].strip().lstrip("0") == "10" and f[1].strip().isdigit():
        return str(int(f[1].strip()))
    return None


def find_profile(path: str, dirs: list[str] | None = None) -> str | None:
    """Profile file for the part named in the file header, falling back to the
    Part 1 (basics) profile. None when no generated profiles are installed."""
    part = header_part(path)
    dirs = profile_dirs() if dirs is None else dirs
    names = ([f"VDI3805-PART{int(part):02d}.json"] if part else []) + ["VDI3805-PART01.json"]
    for n in names:
        for d in dirs:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return None
