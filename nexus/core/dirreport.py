"""Directory listing reports: filters, selectable columns, sorting, grouping,
statistics, empty folders, tree diagrams and export to XLSX, CSV, TXT, HTML,
PDF, Markdown, XML and JSON. Report settings can be saved as templates.

Uses fileops.list_directory for the scan; everything here works on the
resulting FileInfo list, so a scan can be re-filtered and re-exported
without touching the disk again (except the optional SHA-256 column).
"""
from __future__ import annotations

import csv
import datetime as _dt
import fnmatch
import hashlib
import html
import json
import os
import re
from dataclasses import dataclass, field, asdict
from xml.sax.saxutils import escape as xesc

from .fileops import FileInfo, list_directory


def _fmt_time(t):
    return _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else ""


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


# key -> (header, getter(FileInfo, ctx) -> value)
COLUMNS: dict[str, tuple] = {
    "rel": ("Relative path", lambda f, c: f.rel),
    "path": ("Full path", lambda f, c: f.path),
    "name": ("Name", lambda f, c: f.name),
    "ext": ("Extension", lambda f, c: f.ext),
    "type": ("Type", lambda f, c: "folder" if f.is_dir else "file"),
    "folder": ("Folder", lambda f, c: os.path.dirname(f.rel)),
    "size": ("Size (bytes)", lambda f, c: c["dir_size"].get(f.path, 0) if f.is_dir else f.size),
    "size_h": ("Size", lambda f, c: human_size(c["dir_size"].get(f.path, 0) if f.is_dir else f.size)),
    "files": ("Files inside", lambda f, c: c["dir_files"].get(f.path, 0) if f.is_dir else ""),
    "created": ("Created", lambda f, c: _fmt_time(f.created)),
    "modified": ("Modified", lambda f, c: _fmt_time(f.modified)),
    "accessed": ("Accessed", lambda f, c: _fmt_time(f.accessed)),
    "attrs": ("Attributes", lambda f, c: f.attrs),
    "depth": ("Depth", lambda f, c: f.depth),
    "empty": ("Empty folder", lambda f, c: "yes" if f.is_dir and f.path in c["empty"] else ""),
    "sha256": ("SHA-256", lambda f, c: "" if f.is_dir else sha256(f.path)),
}
DEFAULT_COLUMNS = ["rel", "ext", "size_h", "modified", "attrs"]


@dataclass
class ReportOptions:
    """Everything that defines a report; saved as a JSON template."""
    title: str = "Directory listing"
    columns: list = field(default_factory=lambda: list(DEFAULT_COLUMNS))
    # filters (applied after the scan)
    name_pattern: str = ""            # wildcards separated by ';' e.g. "*.pdf;*.dwg"
    name_regex: str = ""
    exclude_pattern: str = ""
    min_size: int | None = None       # bytes
    max_size: int | None = None
    modified_from: str = ""           # "YYYY-MM-DD"
    modified_to: str = ""
    created_from: str = ""
    created_to: str = ""
    files: bool = True
    folders: bool = True
    hidden: bool = True               # include entries with the H attribute
    only_empty_folders: bool = False
    # layout
    sort_by: str = "rel"
    descending: bool = False
    group_by: str = ""                # "", "ext", "folder", "type", "year", "month"
    include_tree: bool = False
    include_stats: bool = True
    header_text: str = ""             # e.g. company / project line on top of the report


def _day(s: str, end=False):
    if not s:
        return None
    d = _dt.datetime.strptime(s[:10], "%Y-%m-%d")
    return (d + _dt.timedelta(days=1)).timestamp() if end else d.timestamp()


def context(files: list[FileInfo]) -> dict:
    """Folder totals (recursive size/file count) and empty folders."""
    dir_size: dict[str, int] = {}
    dir_files: dict[str, int] = {}
    dirs = [f.path for f in files if f.is_dir]
    dirset = set(dirs)
    for f in files:
        if f.is_dir:
            continue
        d = os.path.dirname(f.path)
        while d in dirset:                 # add to every listed folder above the file
            dir_size[d] = dir_size.get(d, 0) + f.size
            dir_files[d] = dir_files.get(d, 0) + 1
            d = os.path.dirname(d)
    # a folder is empty when nothing at all is below it on disk
    empty = set()
    for d in dirs:
        try:
            if not os.listdir(d):
                empty.add(d)
        except OSError:
            pass
    return {"dir_size": dir_size, "dir_files": dir_files, "empty": empty}


def apply_filters(files: list[FileInfo], o: ReportOptions, ctx: dict | None = None) -> list[FileInfo]:
    ctx = ctx or context(files)
    inc = [p.strip() for p in o.name_pattern.split(";") if p.strip()]
    exc = [p.strip() for p in o.exclude_pattern.split(";") if p.strip()]
    rx = re.compile(o.name_regex, re.IGNORECASE) if o.name_regex else None
    mf, mt = _day(o.modified_from), _day(o.modified_to, True)
    cf, ct = _day(o.created_from), _day(o.created_to, True)
    out = []
    for f in files:
        if f.is_dir and not o.folders or not f.is_dir and not o.files:
            continue
        if o.only_empty_folders and not (f.is_dir and f.path in ctx["empty"]):
            continue
        if not o.hidden and "H" in f.attrs:
            continue
        if not f.is_dir:
            if inc and not any(fnmatch.fnmatch(f.name.lower(), p.lower()) for p in inc):
                continue
            if o.min_size is not None and f.size < o.min_size or o.max_size is not None and f.size > o.max_size:
                continue
        if exc and any(fnmatch.fnmatch(f.name.lower(), p.lower()) for p in exc):
            continue
        if rx and not rx.search(f.name):
            continue
        if mf and f.modified < mf or mt and f.modified >= mt or cf and f.created < cf or ct and f.created >= ct:
            continue
        out.append(f)
    return out


def _sort_key(key):
    if key in ("size", "size_h"):
        return lambda f: f.size
    if key in ("created", "modified", "accessed"):
        return lambda f: getattr(f, key)
    if key == "depth":
        return lambda f: f.depth
    attr = {"rel": "rel", "path": "path", "name": "name", "ext": "ext"}.get(key, "rel")
    return lambda f: getattr(f, attr).lower()


def group_key(f: FileInfo, by: str) -> str:
    if by == "ext":
        return "(folder)" if f.is_dir else f.ext or "(none)"
    if by == "folder":
        return os.path.dirname(f.rel) or "."
    if by == "type":
        return "folders" if f.is_dir else "files"
    if by == "year":
        return _dt.datetime.fromtimestamp(f.modified).strftime("%Y")
    if by == "month":
        return _dt.datetime.fromtimestamp(f.modified).strftime("%Y-%m")
    return ""


@dataclass
class Report:
    root: str
    options: ReportOptions
    headers: list
    groups: list            # [(group name, [[cells]])]
    stats: dict
    tree: str
    created: str
    items: list = field(default_factory=list)   # FileInfo per row, same order as the rows of all groups


def statistics(files: list[FileInfo], ctx: dict) -> dict:
    fs = [f for f in files if not f.is_dir]
    by_ext: dict[str, list[int]] = {}
    for f in fs:
        a = by_ext.setdefault(f.ext or "(none)", [0, 0])
        a[0] += 1
        a[1] += f.size
    largest = sorted(fs, key=lambda f: -f.size)[:10]
    return {
        "files": len(fs), "folders": sum(f.is_dir for f in files), "bytes": sum(f.size for f in fs),
        "empty_folders": sorted(f.rel for f in files if f.is_dir and f.path in ctx["empty"]),
        "extensions": sorted(([e, c, s] for e, (c, s) in by_ext.items()), key=lambda x: -x[2]),
        "largest": [[f.rel, f.size] for f in largest],
        "newest": _fmt_time(max((f.modified for f in fs), default=0)),
        "oldest": _fmt_time(min((f.modified for f in fs), default=0)),
    }


def tree_text(files: list[FileInfo], root_name: str = ".", sizes: bool = True, ctx: dict | None = None) -> str:
    """Tree diagram with box-drawing characters."""
    ctx = ctx or {"dir_size": {}}
    children: dict[str, list[FileInfo]] = {}
    for f in files:
        children.setdefault(os.path.dirname(f.rel), []).append(f)
    lines = [root_name]

    def walk(rel, prefix):
        items = sorted(children.get(rel, []), key=lambda f: (not f.is_dir, f.name.lower()))
        for i, f in enumerate(items):
            last = i == len(items) - 1
            label = f.name + ("/" if f.is_dir else "")
            if sizes:
                label += f"  ({human_size(ctx['dir_size'].get(f.path, 0) if f.is_dir else f.size)})"
            lines.append(prefix + ("└── " if last else "├── ") + label)
            if f.is_dir:
                walk(f.rel, prefix + ("    " if last else "│   "))

    walk("", "")
    return "\n".join(lines)


def build(root: str, files: list[FileInfo], o: ReportOptions) -> Report:
    ctx = context(files)
    sel = apply_filters(files, o, ctx)
    sel.sort(key=_sort_key(o.sort_by), reverse=o.descending)
    cols = [c for c in o.columns if c in COLUMNS] or list(DEFAULT_COLUMNS)
    headers = [COLUMNS[c][0] for c in cols]
    groups: dict[str, list] = {}
    members: dict[str, list] = {}
    for f in sel:
        g = group_key(f, o.group_by)
        groups.setdefault(g, []).append([COLUMNS[c][1](f, ctx) for c in cols])
        members.setdefault(g, []).append(f)
    order = sorted(groups) if o.group_by else list(groups)
    st = statistics(sel, ctx) if o.include_stats else {}
    tr = tree_text(sel, os.path.basename(os.path.normpath(root)) or root, True, ctx) if o.include_tree else ""
    return Report(root, o, headers, [(g, groups[g]) for g in order], st, tr,
                  _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), [f for g in order for f in members[g]])


def scan_and_build(root: str, o: ReportOptions, recursive=True, max_depth=None) -> Report:
    return build(root, list_directory(root, recursive=recursive, include_dirs=True, max_depth=max_depth), o)


# ---------------------------------------------------------------- export
FORMATS = ("xlsx", "csv", "txt", "html", "pdf", "md", "xml", "json")


def _rows(rep: Report):
    for g, rows in rep.groups:
        for r in rows:
            yield g, r


def to_html(rep: Report, branding: str = "") -> str:
    e = html.escape
    o = rep.options
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{e(o.title)}</title><style>"
           "body{font-family:Segoe UI,Arial,sans-serif;font-size:10pt;color:#1d2a44;margin:24px}"
           "h1{font-size:16pt;margin:0 0 4px}h2{font-size:12pt;margin:18px 0 6px;color:#17233b}"
           ".meta{color:#5a6a86;margin-bottom:12px}table{border-collapse:collapse;width:100%}"
           "th{background:#17233b;color:#fff;text-align:left;padding:4px 6px}"
           "td{border-bottom:1px solid #dde3ee;padding:3px 6px;vertical-align:top}"
           "tr:nth-child(even) td{background:#f4f6fb}pre{font-family:Consolas,monospace;font-size:9pt}"
           ".foot{margin-top:18px;color:#7a879e;font-size:8.5pt}</style></head><body>"]
    if o.header_text:
        out.append(f"<div class='meta'>{e(o.header_text)}</div>")
    out.append(f"<h1>{e(o.title)}</h1><div class='meta'>{e(rep.root)} · created {rep.created}</div>")
    if rep.stats:
        s = rep.stats
        out.append(f"<p><b>{s['files']:,}</b> files · <b>{s['folders']:,}</b> folders · "
                   f"<b>{human_size(s['bytes'])}</b> · modified between {s['oldest']} and {s['newest']}"
                   f" · {len(s['empty_folders'])} empty folder(s)</p>")
    for g, rows in rep.groups:
        if g:
            out.append(f"<h2>{e(str(g))} <span class='meta'>({len(rows)})</span></h2>")
        out.append("<table><tr>" + "".join(f"<th>{e(h)}</th>" for h in rep.headers) + "</tr>")
        for r in rows:
            out.append("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in r) + "</tr>")
        out.append("</table>")
    if rep.stats and rep.stats["extensions"]:
        out.append("<h2>Extensions</h2><table><tr><th>Extension</th><th>Files</th><th>Size</th></tr>")
        for x, c, s in rep.stats["extensions"]:
            out.append(f"<tr><td>{e(x)}</td><td>{c:,}</td><td>{human_size(s)}</td></tr>")
        out.append("</table>")
    if rep.stats and rep.stats["empty_folders"]:
        out.append("<h2>Empty folders</h2><ul>" + "".join(f"<li>{e(p)}</li>" for p in rep.stats["empty_folders"]) + "</ul>")
    if rep.tree:
        out.append(f"<h2>Folder tree</h2><pre>{e(rep.tree)}</pre>")
    if branding:
        out.append(f"<div class='foot'>{e(branding)}</div>")
    out.append("</body></html>")
    return "".join(out)


def to_markdown(rep: Report) -> str:
    o = rep.options
    md = lambda s: str(s).replace("|", "\\|")  # noqa: E731
    out = [f"# {o.title}", "", f"`{rep.root}` · created {rep.created}", ""]
    if o.header_text:
        out[1:1] = ["", o.header_text]
    if rep.stats:
        s = rep.stats
        out += [f"**{s['files']:,}** files · **{s['folders']:,}** folders · **{human_size(s['bytes'])}**", ""]
    for g, rows in rep.groups:
        if g:
            out += [f"## {g} ({len(rows)})", ""]
        out.append("| " + " | ".join(rep.headers) + " |")
        out.append("|" + "---|" * len(rep.headers))
        out += ["| " + " | ".join(md(c) for c in r) + " |" for r in rows]
        out.append("")
    if rep.stats and rep.stats["empty_folders"]:
        out += ["## Empty folders", ""] + [f"* {p}" for p in rep.stats["empty_folders"]] + [""]
    if rep.tree:
        out += ["## Folder tree", "", "```", rep.tree, "```", ""]
    return "\n".join(out)


def to_text(rep: Report) -> str:
    widths = [len(h) for h in rep.headers]
    for _g, r in _rows(rep):
        widths = [max(w, min(len(str(c)), 80)) for w, c in zip(widths, r)]
    line = lambda cells: "  ".join(str(c)[:80].ljust(w) for c, w in zip(cells, widths)).rstrip()  # noqa: E731
    out = [rep.options.title, f"{rep.root} · created {rep.created}", ""]
    for g, rows in rep.groups:
        if g:
            out += [f"[{g}]"]
        out += [line(rep.headers), line(["-" * w for w in widths])] + [line(r) for r in rows] + [""]
    if rep.tree:
        out += ["Folder tree", rep.tree]
    return "\n".join(out)


def to_xml(rep: Report) -> str:
    tags = [re.sub(r"\W+", "_", h).strip("_").lower() or "col" for h in rep.headers]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<directoryListing root="{xesc(rep.root, {chr(34): "&quot;"})}" created="{rep.created}" '
           f'title="{xesc(rep.options.title, {chr(34): "&quot;"})}">']
    for g, rows in rep.groups:
        out.append(f'  <group name="{xesc(str(g), {chr(34): "&quot;"})}">' if g else "  <group>")
        for r in rows:
            out.append("    <entry>" + "".join(f"<{t}>{xesc(str(c))}</{t}>" for t, c in zip(tags, r)) + "</entry>")
        out.append("  </group>")
    if rep.stats:
        s = rep.stats
        out.append(f'  <statistics files="{s["files"]}" folders="{s["folders"]}" bytes="{s["bytes"]}"/>')
    out.append("</directoryListing>")
    return "\n".join(out)


def to_json(rep: Report) -> str:
    return json.dumps({"title": rep.options.title, "root": rep.root, "created": rep.created,
                       "options": asdict(rep.options), "columns": rep.headers,
                       "groups": [{"name": g, "entries": [dict(zip(rep.headers, r)) for r in rows]}
                                  for g, rows in rep.groups],
                       "statistics": rep.stats, "tree": rep.tree}, indent=2, ensure_ascii=False, default=str)


def export(rep: Report, path: str, fmt: str | None = None, branding: str = "") -> str:
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    if fmt in ("htm", "html"):
        _write(path, to_html(rep, branding))
    elif fmt == "md":
        _write(path, to_markdown(rep))
    elif fmt == "txt":
        _write(path, to_text(rep))
    elif fmt == "xml":
        _write(path, to_xml(rep))
    elif fmt == "json":
        _write(path, to_json(rep))
    elif fmt in ("csv", "tsv"):
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter="\t" if fmt == "tsv" else ";")
            grouped = bool(rep.options.group_by)
            w.writerow((["Group"] if grouped else []) + rep.headers)
            for g, r in _rows(rep):
                w.writerow(([g] if grouped else []) + r)
    elif fmt == "xlsx":
        _xlsx(rep, path)
    elif fmt == "pdf":
        from .pdf import html_to_pdf
        html_to_pdf(to_html(rep, branding), path, landscape=len(rep.headers) > 5)
    else:
        raise ValueError(f"unknown format {fmt}")
    return path


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _xlsx(rep: Report, path: str):
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Listing"
    grouped = bool(rep.options.group_by)
    head = (["Group"] if grouped else []) + rep.headers
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="17233B")
    for g, r in _rows(rep):
        ws.append(([g] if grouped else []) + list(r))
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    for i, h in enumerate(head, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(max(len(h) + 4, 12), 70 if "path" in h.lower() else 30)
    if rep.stats:
        st = wb.create_sheet("Statistics")
        s = rep.stats
        for row in (["Root", rep.root], ["Created", rep.created], ["Files", s["files"]], ["Folders", s["folders"]],
                    ["Total bytes", s["bytes"]], ["Oldest change", s["oldest"]], ["Newest change", s["newest"]]):
            st.append(row)
        st.append([])
        st.append(["Extension", "Files", "Bytes"])
        for row in s["extensions"]:
            st.append(row)
        if s["empty_folders"]:
            ef = wb.create_sheet("Empty folders")
            ef.append(["Empty folder"])
            for p in s["empty_folders"]:
                ef.append([p])
    if rep.tree:
        tr = wb.create_sheet("Tree")
        for ln in rep.tree.splitlines():
            tr.append([ln])
        tr.column_dimensions["A"].width = 100
    wb.save(path)


# ---------------------------------------------------------------- templates
def template_dir() -> str:
    from .automation import app_data_dir
    d = os.path.join(app_data_dir(), "report-templates")
    os.makedirs(d, exist_ok=True)
    return d


def save_template(o: ReportOptions, name: str, directory: str | None = None) -> str:
    p = os.path.join(directory or template_dir(), re.sub(r"[^\w\- ]", "_", name).strip() + ".json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(asdict(o), fh, indent=2, ensure_ascii=False)
    return p


def load_template(path: str) -> ReportOptions:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    known = ReportOptions.__dataclass_fields__
    return ReportOptions(**{k: v for k, v in d.items() if k in known})
