"""Three-way merge (diff3 style) of line lists."""
from __future__ import annotations

import difflib
from dataclasses import dataclass

UNCHANGED, LEFT, RIGHT, BOTH, CONFLICT = "unchanged", "left", "right", "both", "conflict"


@dataclass
class Region:
    kind: str
    base: list[str]
    left: list[str]
    right: list[str]
    resolution: list[str] | None = None   # chosen lines (None for unresolved conflicts)


def _sync_regions(base, a, b):
    ia = difflib.SequenceMatcher(None, base, a, autojunk=False).get_matching_blocks()
    ib = difflib.SequenceMatcher(None, base, b, autojunk=False).get_matching_blocks()
    out = []
    i = j = 0
    while i < len(ia) and j < len(ib):
        abase, amatch, alen = ia[i]
        bbase, bmatch, blen = ib[j]
        lo = max(abase, bbase)
        hi = min(abase + alen, bbase + blen)
        if lo < hi:
            out.append((lo, hi, amatch + (lo - abase), amatch + (hi - abase), bmatch + (lo - bbase), bmatch + (hi - bbase)))
        if abase + alen < bbase + blen:
            i += 1
        else:
            j += 1
    out.append((len(base), len(base), len(a), len(a), len(b), len(b)))
    return out


def merge3(base: list[str], left: list[str], right: list[str]) -> list[Region]:
    regions: list[Region] = []
    iz = ia = ib = 0
    for zmatch, zend, amatch, aend, bmatch, bend in _sync_regions(base, left, right):
        z, a, b = base[iz:zmatch], left[ia:amatch], right[ib:bmatch]
        if z or a or b:
            if a == z and b == z:
                pass
            elif a == z:
                regions.append(Region(RIGHT, z, a, b, b))
            elif b == z:
                regions.append(Region(LEFT, z, a, b, a))
            elif a == b:
                regions.append(Region(BOTH, z, a, b, a))
            else:
                regions.append(Region(CONFLICT, z, a, b, None))
        if zend > zmatch:
            same = base[zmatch:zend]
            regions.append(Region(UNCHANGED, same, same, same, same))
        iz, ia, ib = zend, aend, bend
    return regions


def merged_lines(regions: list[Region], left_name="LEFT", right_name="RIGHT") -> list[str]:
    out: list[str] = []
    for r in regions:
        if r.resolution is not None:
            out.extend(r.resolution)
        else:
            out.append(f"<<<<<<< {left_name}")
            out.extend(r.left)
            out.append("||||||| BASE")
            out.extend(r.base)
            out.append("=======")
            out.extend(r.right)
            out.append(f">>>>>>> {right_name}")
    return out


def conflicts(regions: list[Region]) -> int:
    return sum(1 for r in regions if r.resolution is None)
