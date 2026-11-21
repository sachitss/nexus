"""Cell-level comparison of two tables (CSV, Excel sheets, VDI 3805 records)."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

EQUAL, CHANGED, LEFT_ONLY, RIGHT_ONLY = "equal", "changed", "left", "right"


@dataclass
class TableOptions:
    header_row: bool = True
    key_columns: list[int] = field(default_factory=list)  # left-side column indexes; empty = align by order
    ignore_case: bool = False
    trim: bool = True
    numeric_tolerance: float | None = None   # e.g. 1e-9; None = exact text compare
    align_columns_by_header: bool = True


@dataclass
class TRow:
    tag: str
    left: int | None
    right: int | None
    changed_cols: set[int] = field(default_factory=set)  # unified column indexes


@dataclass
class TableResult:
    columns: list[str]             # unified column titles
    lmap: list[int | None]         # unified col -> left col
    rmap: list[int | None]         # unified col -> right col
    rows: list[TRow]
    left: list[list[str]]
    right: list[list[str]]
    header_left: list[str]
    header_right: list[str]

    def cell(self, side: str, row: int | None, ucol: int) -> str:
        if row is None:
            return ""
        data, m = (self.left, self.lmap) if side == "left" else (self.right, self.rmap)
        c = m[ucol]
        if c is None or c >= len(data[row]):
            return ""
        return data[row][c]

    def stats(self) -> dict:
        s = {EQUAL: 0, CHANGED: 0, LEFT_ONLY: 0, RIGHT_ONLY: 0, "cells": 0}
        for r in self.rows:
            s[r.tag] += 1
            s["cells"] += len(r.changed_cols)
        return s

    @property
    def identical(self) -> bool:
        return all(r.tag == EQUAL for r in self.rows) and self.lmap == self.rmap


def _col_name(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def _values_equal(a: str, b: str, opts: TableOptions) -> bool:
    if opts.trim:
        a, b = a.strip(), b.strip()
    if opts.ignore_case:
        a, b = a.lower(), b.lower()
    if a == b:
        return True
    if opts.numeric_tolerance is not None:
        try:
            fa = float(a.replace(",", ".")) if a.count(",") == 1 and "." not in a else float(a)
            fb = float(b.replace(",", ".")) if b.count(",") == 1 and "." not in b else float(b)
            return abs(fa - fb) <= opts.numeric_tolerance
        except ValueError:
            return False
    return False


def diff_tables(left: list[list[str]], right: list[list[str]], opts: TableOptions | None = None) -> TableResult:
    opts = opts or TableOptions()
    hl: list[str] = []
    hr: list[str] = []
    if opts.header_row and left and right:
        hl, hr = left[0], right[0]
        left, right = left[1:], right[1:]
    wl = max([len(r) for r in left] + [len(hl), 0])
    wr = max([len(r) for r in right] + [len(hr), 0])

    # ----- columns
    lmap: list[int | None] = []
    rmap: list[int | None] = []
    cols: list[str] = []
    if opts.header_row and opts.align_columns_by_header and hl and hr and set(hl) & set(hr):
        rpos = {}
        for j, name in enumerate(hr):
            rpos.setdefault(name, j)
        used = set()
        for i in range(wl):
            name = hl[i] if i < len(hl) else ""
            j = rpos.get(name) if name else None
            if j is not None and j not in used:
                used.add(j)
            else:
                j = None
            lmap.append(i)
            rmap.append(j)
            cols.append(name or _col_name(i))
        for j in range(wr):
            if j not in used:
                lmap.append(None)
                rmap.append(j)
                cols.append((hr[j] if j < len(hr) else "") or _col_name(j))
    else:
        for i in range(max(wl, wr)):
            lmap.append(i if i < wl else None)
            rmap.append(i if i < wr else None)
            title = (hl[i] if i < len(hl) and hl[i] else _col_name(i))
            cols.append(title)

    def lv(r, u):
        c = lmap[u]
        return left[r][c] if c is not None and c < len(left[r]) else ""

    def rv(r, u):
        c = rmap[u]
        return right[r][c] if c is not None and c < len(right[r]) else ""

    ncol = len(cols)

    def compare_row(a: int, b: int) -> TRow:
        changed = {u for u in range(ncol) if not _values_equal(lv(a, u), rv(b, u), opts)}
        return TRow(CHANGED if changed else EQUAL, a, b, changed)

    rows: list[TRow] = []
    if opts.key_columns:
        # key-based matching; left key cols translated to unified columns
        ukeys = [u for u in range(ncol) if lmap[u] in opts.key_columns]

        def norm(s):
            s = s.strip() if opts.trim else s
            return s.lower() if opts.ignore_case else s

        def keys(n, getter):
            seen: dict[tuple, int] = {}
            out = []
            for r in range(n):
                k = tuple(norm(getter(r, u)) for u in ukeys)
                seen[k] = seen.get(k, 0) + 1
                out.append(k + (seen[k],))  # occurrence number handles duplicate keys
            return out

        kl = keys(len(left), lv)
        kr = keys(len(right), rv)
        rindex = {k: j for j, k in enumerate(kr)}
        common = set(kl) & set(kr)
        p = 0  # next right row not yet considered for right-only output
        for i, k in enumerate(kl):
            if k in common:
                j = rindex[k]
                while p < j:
                    if kr[p] not in common:
                        rows.append(TRow(RIGHT_ONLY, None, p))
                    p += 1
                rows.append(compare_row(i, j))
            else:
                rows.append(TRow(LEFT_ONLY, i, None))
        for j in range(p, len(right)):
            if kr[j] not in common:
                rows.append(TRow(RIGHT_ONLY, None, j))
    else:
        def sig(data, getter, n):
            return [tuple((getter(r, u).strip() if opts.trim else getter(r, u)) for u in range(ncol)) for r in range(n)]

        sl, sr = sig(left, lv, len(left)), sig(right, rv, len(right))
        if opts.ignore_case:
            sl = [tuple(x.lower() for x in t) for t in sl]
            sr = [tuple(x.lower() for x in t) for t in sr]
        sm = difflib.SequenceMatcher(None, sl, sr, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                rows.extend(TRow(EQUAL, i1 + k, j1 + k) for k in range(i2 - i1))
            elif tag == "delete":
                rows.extend(TRow(LEFT_ONLY, k, None) for k in range(i1, i2))
            elif tag == "insert":
                rows.extend(TRow(RIGHT_ONLY, None, k) for k in range(j1, j2))
            else:
                n, m = i2 - i1, j2 - j1
                for k in range(max(n, m)):
                    a = i1 + k if k < n else None
                    b = j1 + k if k < m else None
                    if a is None:
                        rows.append(TRow(RIGHT_ONLY, None, b))
                    elif b is None:
                        rows.append(TRow(LEFT_ONLY, a, None))
                    else:
                        # only pair rows sharing at least half their cells
                        tr = compare_row(a, b)
                        if ncol and len(tr.changed_cols) > ncol / 2 and ncol > 2:
                            rows.append(TRow(LEFT_ONLY, a, None))
                            rows.append(TRow(RIGHT_ONLY, None, b))
                        else:
                            rows.append(tr)
    return TableResult(cols, lmap, rmap, rows, left, right, hl, hr)
