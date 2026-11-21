"""Line-based diff engine producing aligned rows for a side-by-side view."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

EQUAL, CHANGED, LEFT_ONLY, RIGHT_ONLY = "equal", "changed", "left", "right"


@dataclass
class DiffOptions:
    ignore_case: bool = False
    ignore_whitespace: bool = False      # ignore all whitespace differences
    ignore_trailing_ws: bool = True
    ignore_blank_lines: bool = False
    ignore_regex: str = ""               # parts of lines matching this are ignored
    granularity: str = "char"            # intra-line highlighting: "char" or "word"
    ignore_comments: str = ""            # comment prefix to ignore, e.g. "#", "//", ";"


@dataclass
class Row:
    tag: str
    left: int | None          # line index in left (None = filler)
    right: int | None
    left_spans: list[tuple[int, int]] = field(default_factory=list)   # changed char spans
    right_spans: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class DiffResult:
    rows: list[Row]
    left_lines: list[str]
    right_lines: list[str]

    @property
    def hunks(self) -> list[tuple[int, int]]:
        """(first_row, last_row) of each contiguous block of differences."""
        out, start = [], None
        for i, r in enumerate(self.rows):
            if r.tag != EQUAL and start is None:
                start = i
            elif r.tag == EQUAL and start is not None:
                out.append((start, i - 1))
                start = None
        if start is not None:
            out.append((start, len(self.rows) - 1))
        return out

    def stats(self) -> dict:
        s = {EQUAL: 0, CHANGED: 0, LEFT_ONLY: 0, RIGHT_ONLY: 0}
        for r in self.rows:
            s[r.tag] += 1
        s["hunks"] = len(self.hunks)
        return s

    @property
    def identical(self) -> bool:
        return all(r.tag == EQUAL for r in self.rows)


def _normaliser(opts: DiffOptions):
    rx = re.compile(opts.ignore_regex) if opts.ignore_regex else None

    cpre = opts.ignore_comments

    def norm(s: str) -> str:
        if cpre and cpre in s:
            s = s[: s.index(cpre)]
        if rx:
            s = rx.sub("", s)
        if opts.ignore_whitespace:
            s = "".join(s.split())
        elif opts.ignore_trailing_ws:
            s = s.rstrip()
        if opts.ignore_case:
            s = s.lower()
        return s

    return norm


_WORD = re.compile(r"\w+|\s+|[^\w\s]")


def _word_spans(a: str, b: str, opts: DiffOptions):
    ta, tb = _WORD.findall(a), _WORD.findall(b)
    ka = [t.lower() if opts.ignore_case else t for t in ta]
    kb = [t.lower() if opts.ignore_case else t for t in tb]
    pa, pb = [0], [0]
    for t in ta:
        pa.append(pa[-1] + len(t))
    for t in tb:
        pb.append(pb[-1] + len(t))
    ls, rs = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if opts.ignore_whitespace and not "".join(ka[i1:i2]).strip() and not "".join(kb[j1:j2]).strip():
            continue
        if i2 > i1:
            ls.append((pa[i1], pa[i2]))
        if j2 > j1:
            rs.append((pb[j1], pb[j2]))
    return ls, rs


def _char_spans(a: str, b: str, opts: DiffOptions):
    if opts.granularity == "word":
        return _word_spans(a, b, opts)
    if opts.ignore_case:
        a2, b2 = a.lower(), b.lower()
    else:
        a2, b2 = a, b
    sm = difflib.SequenceMatcher(None, a2, b2, autojunk=False)
    ls, rs = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if opts.ignore_whitespace and not a2[i1:i2].strip() and not b2[j1:j2].strip():
            continue
        if i2 > i1:
            ls.append((i1, i2))
        if j2 > j1:
            rs.append((j1, j2))
    return ls, rs


def diff_lines(left: list[str], right: list[str], opts: DiffOptions | None = None) -> DiffResult:
    opts = opts or DiffOptions()
    norm = _normaliser(opts)
    nl = [norm(x) for x in left]
    nr = [norm(x) for x in right]
    rows: list[Row] = []
    sm = difflib.SequenceMatcher(None, nl, nr, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append(Row(EQUAL, i1 + k, j1 + k))
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append(Row(LEFT_ONLY, k, None))
        elif tag == "insert":
            for k in range(j1, j2):
                rows.append(Row(RIGHT_ONLY, None, k))
        else:  # replace: pair lines by best similarity order, rest one-sided
            rows.extend(_pair_replace(left, right, nl, nr, i1, i2, j1, j2, opts))
    if opts.ignore_blank_lines:
        for r in rows:
            if r.tag == LEFT_ONLY and not left[r.left].strip():
                r.tag = EQUAL
            elif r.tag == RIGHT_ONLY and not right[r.right].strip():
                r.tag = EQUAL
    return DiffResult(rows, left, right)


def _pair_replace(left, right, nl, nr, i1, i2, j1, j2, opts):
    """Align a replaced block. Lines are paired when they are reasonably similar,
    otherwise shown as removed/added. Keeps original order on both sides."""
    out: list[Row] = []
    n, m = i2 - i1, j2 - j1
    if n * m > 40_000:  # very large block: simple pairing
        for k in range(max(n, m)):
            a = i1 + k if k < n else None
            b = j1 + k if k < m else None
            out.append(_make_row(left, right, nl, nr, a, b, opts))
        return out
    # DP alignment maximising similarity (Needleman-Wunsch style, gap = 0)
    sim = [[0.0] * m for _ in range(n)]
    for a in range(n):
        for b in range(m):
            r = difflib.SequenceMatcher(None, nl[i1 + a], nr[j1 + b], autojunk=False).ratio()
            sim[a][b] = r if r >= 0.4 else -1.0
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for a in range(n - 1, -1, -1):
        for b in range(m - 1, -1, -1):
            best = max(score[a + 1][b], score[a][b + 1])
            if sim[a][b] > 0:
                best = max(best, sim[a][b] + score[a + 1][b + 1])
            score[a][b] = best
    a = b = 0
    while a < n or b < m:
        if a < n and b < m and sim[a][b] > 0 and abs(score[a][b] - (sim[a][b] + score[a + 1][b + 1])) < 1e-9:
            out.append(_make_row(left, right, nl, nr, i1 + a, j1 + b, opts))
            a += 1
            b += 1
        elif a < n and (b >= m or score[a][b] == score[a + 1][b]):
            out.append(Row(LEFT_ONLY, i1 + a, None))
            a += 1
        else:
            out.append(Row(RIGHT_ONLY, None, j1 + b))
            b += 1
    return out


def _make_row(left, right, nl, nr, a, b, opts):
    if a is None:
        return Row(RIGHT_ONLY, None, b)
    if b is None:
        return Row(LEFT_ONLY, a, None)
    if nl[a] == nr[b]:
        return Row(EQUAL, a, b)
    ls, rs = _char_spans(left[a], right[b], opts)
    return Row(CHANGED, a, b, ls, rs)


def unified_diff(left: list[str], right: list[str], lname="left", rname="right", n=3) -> str:
    return "\n".join(difflib.unified_diff(left, right, lname, rname, lineterm="", n=n))
