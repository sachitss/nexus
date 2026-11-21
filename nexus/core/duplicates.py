"""Duplicate file and version analysis.

Finds identical files (size, then quick hash of the first 64 KB, then full
SHA-256), duplicate file names, identical content under different names,
near-duplicate text files, version series (draft_v1/v2, "(1)", "- Kopie",
dates in names) and groups files by extension, size and date.

Nothing here deletes, moves or changes files. The results are lists for the
user to review, open, compare and export.
"""
from __future__ import annotations

import csv
import datetime as _dt
import difflib
import hashlib
import os
import re
from dataclasses import dataclass, field

from .fileops import FileInfo, list_directory

TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".xml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".log", ".py", ".js",
            ".ts", ".cs", ".java", ".c", ".h", ".cpp", ".hpp", ".sql", ".html", ".htm", ".css", ".xsd", ".xsl",
            ".xslt", ".svg", ".vdi", ".bat", ".ps1", ".php", ".rst", ".tex", ".ifc"}


def _hash(path: str, limit: int | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        if limit:
            h.update(fh.read(limit))
        else:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


@dataclass
class Result:
    root: list
    files: int = 0
    identical: list = field(default_factory=list)       # [{hash, size, paths}]
    same_name: list = field(default_factory=list)       # [{name, paths}]
    renamed_copies: list = field(default_factory=list)  # identical groups whose names differ
    near: list = field(default_factory=list)            # [{a, b, similarity}]
    versions: list = field(default_factory=list)        # [{series, files: [{path, modified, newest}]}]
    by_ext: list = field(default_factory=list)          # [[ext, count, bytes]]
    by_size: list = field(default_factory=list)         # [[bucket, count, bytes]]
    by_month: list = field(default_factory=list)        # [[YYYY-MM, count, bytes]]
    errors: list = field(default_factory=list)

    @property
    def wasted(self) -> int:
        return sum(g["size"] * (len(g["paths"]) - 1) for g in self.identical)


def identical_groups(files: list[FileInfo], min_size: int = 1, errors=None, cancel=None) -> list[dict]:
    by_size: dict[int, list[FileInfo]] = {}
    for f in files:
        if not f.is_dir and f.size >= min_size:
            by_size.setdefault(f.size, []).append(f)
    out = []
    for size, group in by_size.items():
        if cancel and cancel():
            break
        if len(group) < 2:
            continue
        quick: dict[str, list[FileInfo]] = {}
        for f in group:
            try:
                quick.setdefault(_hash(f.path, 65536), []).append(f)
            except OSError as ex:
                if errors is not None:
                    errors.append(f"{f.path}: {ex}")
        for qg in quick.values():
            if len(qg) < 2:
                continue
            full: dict[str, list[str]] = {}
            for f in qg:
                try:
                    h = _hash(f.path) if size > 65536 else _hash(f.path, 65536)
                    full.setdefault(h, []).append(f.path)
                except OSError as ex:
                    if errors is not None:
                        errors.append(f"{f.path}: {ex}")
            for h, paths in full.items():
                if len(paths) > 1:
                    out.append({"hash": h, "size": size, "paths": sorted(paths)})
    out.sort(key=lambda g: -g["size"] * (len(g["paths"]) - 1))
    return out


def same_name_groups(files: list[FileInfo]) -> list[dict]:
    by: dict[str, list[str]] = {}
    for f in files:
        if not f.is_dir:
            by.setdefault(f.name.lower(), []).append(f.path)
    return [{"name": os.path.basename(p[0]), "paths": sorted(p)} for n, p in sorted(by.items()) if len(p) > 1]


def renamed_copies(identical: list[dict]) -> list[dict]:
    return [g for g in identical if len({os.path.basename(p).lower() for p in g["paths"]}) > 1]


# ---------------------------------------------------------------- near duplicates
def _read_text(path: str, limit: int) -> str | None:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def near_duplicates(files: list[FileInfo], threshold: float = 0.9, max_size: int = 2_000_000,
                    max_files: int = 3000, cancel=None) -> list[dict]:
    """Pairs of text files (same extension, comparable size) whose line content is
    at least `threshold` similar but not identical."""
    cands = [f for f in files if not f.is_dir and f.ext in TEXT_EXT and 0 < f.size <= max_size][:max_files]
    by_ext: dict[str, list[FileInfo]] = {}
    for f in cands:
        by_ext.setdefault(f.ext, []).append(f)
    cache: dict[str, list[str] | None] = {}

    def lines(f):
        if f.path not in cache:
            t = _read_text(f.path, max_size)
            cache[f.path] = None if t is None else [ln.strip() for ln in t.splitlines() if ln.strip()]
        return cache[f.path]

    out = []
    for ext, group in by_ext.items():
        group.sort(key=lambda f: f.size)
        for i, a in enumerate(group):
            if cancel and cancel():
                return out
            for b in group[i + 1:]:
                if b.size > a.size / threshold + 64:      # sizes too far apart: sorted, so stop here
                    break
                la, lb = lines(a), lines(b)
                if not la or not lb or la == lb:
                    continue
                sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
                if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                    continue
                r = sm.ratio()
                if r >= threshold:
                    out.append({"a": a.path, "b": b.path, "similarity": round(r, 4)})
    out.sort(key=lambda x: -x["similarity"])
    return out


# ---------------------------------------------------------------- versions
_VERSION_PATTERNS = [
    r"[\s_\-.]*\(\d+\)$",                                   # name (1)
    r"[\s_\-]*-?\s*(kopie|copy|copia|copie)(\s*\(\d+\))?$",  # name - Kopie, name_copy
    r"[\s_\-.]*(?<![a-z])(v|ver|version|rev|r|stand|index|idx)[\s_\-.]?\d+([._]\d+)*[a-z]?$",
    r"[\s_\-.]*(19|20)\d{2}[\-_.]?(0[1-9]|1[0-2])[\-_.]?(0[1-9]|[12]\d|3[01])$",   # 2025-03-01, 20250301
    r"[\s_\-.]*(0[1-9]|[12]\d|3[01])[\-_.](0[1-9]|1[0-2])[\-_.](19|20)?\d{2}$",     # 01.03.2025
    r"[\s_\-.]*(old|alt|neu|new|final|draft|entwurf|backup|bak|orig|original)$",
    r"[\s_\-.]+(19|20)\d{2}$",                               # name_2025
    r"[\s_\-.]+\d{1,3}$",                                    # name_2
]
_VRX = [re.compile(p, re.IGNORECASE) for p in _VERSION_PATTERNS]


def series_key(name: str) -> str:
    """Base name of a file with version markers removed: 'Plan_v3 - Kopie.pdf' -> 'plan.pdf'."""
    stem, ext = os.path.splitext(name)
    s = stem.strip()
    for _ in range(8):                 # strip one marker at a time, most specific pattern first
        for rx in _VRX:
            t = rx.sub("", s).strip()
            if t != s:
                s = t
                break
        else:
            break
    return (s or stem).lower() + ext.lower()


def version_series(files: list[FileInfo], same_folder: bool = False) -> list[dict]:
    groups: dict[tuple, list[FileInfo]] = {}
    for f in files:
        if f.is_dir:
            continue
        key = (os.path.dirname(f.path) if same_folder else "", series_key(f.name))
        groups.setdefault(key, []).append(f)
    out = []
    for (_folder, key), fs in sorted(groups.items(), key=lambda kv: kv[0][1]):
        if len(fs) < 2 or len({f.name.lower() for f in fs}) < 2:
            continue                   # same name only: that is the "same name" list
        fs.sort(key=lambda f: f.modified, reverse=True)
        out.append({"series": key, "files": [{"path": f.path, "name": f.name, "size": f.size,
                                              "modified": _dt.datetime.fromtimestamp(f.modified).strftime("%Y-%m-%d %H:%M"),
                                              "newest": i == 0} for i, f in enumerate(fs)]})
    return out


# ---------------------------------------------------------------- grouping
SIZE_BUCKETS = [(0, "0 B"), (1, "1 B – 10 KB"), (10 << 10, "10 KB – 1 MB"), (1 << 20, "1 – 100 MB"),
                (100 << 20, "100 MB – 1 GB"), (1 << 30, "over 1 GB")]


def grouping(files: list[FileInfo]):
    ext: dict[str, list[int]] = {}
    size: dict[str, list[int]] = {}
    month: dict[str, list[int]] = {}
    for f in files:
        if f.is_dir:
            continue
        for d, k in ((ext, f.ext or "(none)"),
                     (size, next(lbl for lo, lbl in reversed(SIZE_BUCKETS) if f.size >= lo)),
                     (month, _dt.datetime.fromtimestamp(f.modified).strftime("%Y-%m"))):
            a = d.setdefault(k, [0, 0])
            a[0] += 1
            a[1] += f.size
    order = [lbl for _lo, lbl in SIZE_BUCKETS]
    return (sorted(([k, c, s] for k, (c, s) in ext.items()), key=lambda x: -x[2]),
            sorted(([k, c, s] for k, (c, s) in size.items()), key=lambda x: order.index(x[0])),
            sorted(([k, c, s] for k, (c, s) in month.items()), reverse=True))


# ---------------------------------------------------------------- all in one
def analyse(roots: list[str], include="*", exclude="", min_size: int = 1, near_threshold: float = 0.9,
            do_near: bool = True, do_versions: bool = True, versions_same_folder: bool = False,
            cancel=None, progress=None) -> Result:
    files: list[FileInfo] = []
    for r in roots:
        if os.path.isdir(r):
            files += list_directory(r, recursive=True, include=include, exclude=exclude, include_dirs=False,
                                    cancel=cancel)
    if progress:
        progress(f"{len(files):,} files scanned, comparing content…")
    res = Result(roots, len(files))
    res.identical = identical_groups(files, min_size, res.errors, cancel)
    res.same_name = same_name_groups(files)
    res.renamed_copies = renamed_copies(res.identical)
    if do_near:
        if progress:
            progress("Looking for near-duplicate text files…")
        extra = {p for g in res.identical for p in g["paths"][1:]}     # one representative per identical group
        res.near = near_duplicates([f for f in files if f.path not in extra], near_threshold, cancel=cancel)
    if do_versions:
        res.versions = version_series(files, versions_same_folder)
    res.by_ext, res.by_size, res.by_month = grouping(files)
    return res


def export(res: Result, path: str) -> str:
    """CSV (one table with a 'List' column) or XLSX (one sheet per list)."""
    tables = {
        "Identical content": (["Group", "Size", "SHA-256", "Path"],
                              [[i, g["size"], g["hash"], p] for i, g in enumerate(res.identical, 1) for p in g["paths"]]),
        "Same name": (["Group", "Name", "Path"],
                      [[i, g["name"], p] for i, g in enumerate(res.same_name, 1) for p in g["paths"]]),
        "Different names": (["Group", "Size", "Path"],
                            [[i, g["size"], p] for i, g in enumerate(res.renamed_copies, 1) for p in g["paths"]]),
        "Near duplicates": (["Similarity", "File A", "File B"], [[n["similarity"], n["a"], n["b"]] for n in res.near]),
        "Versions": (["Series", "Newest", "Modified", "Size", "Path"],
                     [[v["series"], "newest" if f["newest"] else "older", f["modified"], f["size"], f["path"]]
                      for v in res.versions for f in v["files"]]),
        "By extension": (["Extension", "Files", "Bytes"], res.by_ext),
        "By size": (["Size", "Files", "Bytes"], res.by_size),
        "By month": (["Month", "Files", "Bytes"], res.by_month),
    }
    if path.lower().endswith(".xlsx"):
        import openpyxl
        from openpyxl.styles import Font
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, (head, rows) in tables.items():
            ws = wb.create_sheet(name[:31])
            ws.append(head)
            for c in ws[1]:
                c.font = Font(bold=True)
            for r in rows:
                ws.append(r)
            ws.freeze_panes = "A2"
            ws.column_dimensions[openpyxl.utils.get_column_letter(len(head))].width = 90
        wb.save(path)
    else:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            for name, (head, rows) in tables.items():
                w.writerow([f"# {name}"])
                w.writerow(head)
                w.writerows(rows)
                w.writerow([])
    return path
