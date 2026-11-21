"""HTML reports for text, table and folder comparisons."""
from __future__ import annotations

import datetime as _dt
import html
import os

from .textdiff import DiffResult, EQUAL as T_EQUAL
from .tablediff import TableResult, EQUAL as TB_EQUAL
from .folderdiff import Entry, walk, summary

CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:16px;color:#222}
h1{font-size:18px} .meta{color:#666;font-size:12px;margin-bottom:12px}
table{border-collapse:collapse;font-family:Consolas,monospace;font-size:12px;width:100%}
td,th{border:1px solid #ddd;padding:1px 6px;vertical-align:top;white-space:pre-wrap}
th{background:#f0f0f0;position:sticky;top:0}
.ln{color:#999;text-align:right;width:1%}
.changed{background:#fff1d6}.left{background:#ffe0e0}.right{background:#dcf5dc}
.filler{background:#f3f3f3}.hl{background:#ffb347}.cell{background:#ffd27f}
"""


def _page(title, body, left, right):
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body><h1>{html.escape(title)}</h1>"
            f"<div class='meta'>Left: {html.escape(left)}<br>Right: {html.escape(right)}<br>Created {now}</div>"
            f"{body}</body></html>")


def _spanned(text, spans):
    if not spans:
        return html.escape(text)
    out, pos = [], 0
    for a, b in spans:
        out.append(html.escape(text[pos:a]))
        out.append(f"<span class='hl'>{html.escape(text[a:b])}</span>")
        pos = b
    out.append(html.escape(text[pos:]))
    return "".join(out)


def text_report(res: DiffResult, left: str, right: str, only_diffs: bool = False, context: int = 3) -> str:
    keep = set(range(len(res.rows)))
    if only_diffs:
        keep = set()
        for a, b in res.hunks:
            keep.update(range(max(0, a - context), min(len(res.rows), b + context + 1)))
    rows = []
    last = -1
    for i, r in enumerate(res.rows):
        if i not in keep:
            continue
        if last != -1 and i != last + 1:
            rows.append("<tr><td colspan=4 class='filler'>⋯</td></tr>")
        last = i
        cls = "" if r.tag == T_EQUAL else r.tag
        lt = _spanned(res.left_lines[r.left], r.left_spans) if r.left is not None else ""
        rt = _spanned(res.right_lines[r.right], r.right_spans) if r.right is not None else ""
        lc = cls if r.left is not None else "filler"
        rc = cls if r.right is not None else "filler"
        rows.append(f"<tr><td class='ln'>{'' if r.left is None else r.left + 1}</td><td class='{lc}'>{lt}</td>"
                    f"<td class='ln'>{'' if r.right is None else r.right + 1}</td><td class='{rc}'>{rt}</td></tr>")
    s = res.stats()
    head = (f"<p>{s['hunks']} difference block(s): {s['changed']} changed, {s['left']} only left, "
            f"{s['right']} only right line(s).</p>")
    body = head + "<table><tr><th>#</th><th>Left</th><th>#</th><th>Right</th></tr>" + "".join(rows) + "</table>"
    return _page("Text comparison", body, left, right)


def table_report(res: TableResult, left: str, right: str, only_diffs: bool = True) -> str:
    cols = "".join(f"<th>{html.escape(c)}</th>" for c in res.columns)
    out = []
    for r in res.rows:
        if only_diffs and r.tag == TB_EQUAL:
            continue
        for side, idx in (("left", r.left), ("right", r.right)):
            if r.tag in ("left", "right") and r.tag != side:
                continue
            cells = []
            for u in range(len(res.columns)):
                v = html.escape(res.cell(side, idx, u))
                cls = "cell" if u in r.changed_cols else ""
                cells.append(f"<td class='{cls}'>{v}</td>")
            label = "L" if side == "left" else "R"
            rowno = "" if idx is None else idx + 1 + (1 if res.header_left else 0)
            cls = r.tag if r.tag != "changed" else "changed"
            out.append(f"<tr class='{cls}'><td class='ln'>{label}{rowno}</td>{''.join(cells)}</tr>")
    s = res.stats()
    head = (f"<p>{s['changed']} changed row(s) with {s['cells']} changed cell(s), {s['left']} row(s) only left, "
            f"{s['right']} row(s) only right, {s['equal']} identical.</p>")
    return _page("Table comparison", head + f"<table><tr><th>Row</th>{cols}</tr>{''.join(out)}</table>", left, right)


def folder_report(root: Entry, left: str, right: str, only_diffs: bool = True) -> str:
    out = []
    for e in walk(root):
        if e.is_dir or (only_diffs and e.status == "same"):
            continue
        cls = {"left": "left", "right": "right", "same": ""}.get(e.status, "changed")
        out.append(f"<tr class='{cls}'><td>{html.escape(e.rel)}</td><td>{e.status}</td>"
                   f"<td>{'' if e.left_size is None else e.left_size}</td>"
                   f"<td>{'' if e.right_size is None else e.right_size}</td></tr>")
    s = summary(root)
    head = "<p>" + ", ".join(f"{v} {k}" for k, v in sorted(s.items())) + "</p>"
    body = head + "<table><tr><th>File</th><th>Status</th><th>Left size</th><th>Right size</th></tr>" + "".join(out) + "</table>"
    return _page("Folder comparison", body, left, right)


def save(html_text: str, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
