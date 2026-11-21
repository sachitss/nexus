"""Binary (byte-level) comparison producing aligned hex rows."""
from __future__ import annotations

import difflib
from dataclasses import dataclass

ROW = 16


@dataclass
class HexRow:
    offset_l: int | None
    offset_r: int | None
    left: bytes
    right: bytes
    diff_cols: set   # byte columns that differ


def hex_line(b: bytes) -> str:
    h = " ".join(f"{x:02X}" for x in b).ljust(ROW * 3 - 1)
    a = "".join(chr(x) if 32 <= x < 127 else "." for x in b)
    return f"{h}  {a}"


def diff_binary(a: bytes, b: bytes, align: bool = True, max_align: int = 2_000_000) -> tuple[list[HexRow], dict]:
    """Compare two byte strings. With align=True insertions/deletions are detected
    (for files up to max_align bytes); otherwise bytes are compared by offset."""
    rows: list[HexRow] = []
    stats = {"equal_bytes": 0, "diff_bytes": 0, "left_only": 0, "right_only": 0, "first_diff": None}
    if align and len(a) <= max_align and len(b) <= max_align:
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        ops = sm.get_opcodes()
    else:
        n = min(len(a), len(b))
        ops = [("equal" if a[i:i + ROW] == b[i:i + ROW] else "replace", i, min(i + ROW, n), i, min(i + ROW, n))
               for i in range(0, n, ROW)]
        if len(a) > n:
            ops.append(("delete", n, len(a), n, n))
        if len(b) > n:
            ops.append(("insert", n, n, n, len(b)))
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            stats["equal_bytes"] += i2 - i1
            for k in range(0, i2 - i1, ROW):
                rows.append(HexRow(i1 + k, j1 + k, a[i1 + k:min(i2, i1 + k + ROW)], b[j1 + k:min(j2, j1 + k + ROW)], set()))
            continue
        if stats["first_diff"] is None:
            stats["first_diff"] = i1
        n, m = i2 - i1, j2 - j1
        for k in range(0, max(n, m), ROW):
            la = a[i1 + k:i1 + min(n, k + ROW)] if k < n else b""
            rb = b[j1 + k:j1 + min(m, k + ROW)] if k < m else b""
            cols = {c for c in range(max(len(la), len(rb))) if c >= len(la) or c >= len(rb) or la[c] != rb[c]}
            rows.append(HexRow(i1 + k if la else None, j1 + k if rb else None, la, rb, cols))
        common = min(n, m)
        stats["diff_bytes"] += sum(1 for k in range(common) if a[i1 + k] != b[j1 + k])
        stats["left_only"] += max(0, n - m)
        stats["right_only"] += max(0, m - n)
    return rows, stats
