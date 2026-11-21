"""Recursive folder comparison."""
from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field, asdict

SAME, DIFFERENT, LEFT_ONLY, RIGHT_ONLY, NEWER_LEFT, NEWER_RIGHT = (
    "same", "different", "left", "right", "newer-left", "newer-right")
EXCLUDED, RENAMED = "excluded", "renamed"


@dataclass
class FolderOptions:
    method: str = "content"        # "content" (byte compare) | "hash" (SHA-256) | "size_time" | "size"
    include: list[str] = field(default_factory=lambda: ["*"])
    exclude: list[str] = field(default_factory=lambda: [".git", ".svn", "__pycache__", "*.tmp", "~$*", "Thumbs.db"])
    time_tolerance: float = 2.0     # seconds (FAT/zip timestamps)
    include_regex: str = ""         # applied to the relative path
    exclude_regex: str = ""
    min_size: int | None = None
    max_size: int | None = None
    modified_after: float | None = None    # epoch seconds
    modified_before: float | None = None
    show_excluded: bool = False
    detect_renames: bool = True


@dataclass
class Entry:
    rel: str
    is_dir: bool
    status: str
    left_size: int | None = None
    right_size: int | None = None
    left_mtime: float | None = None
    right_mtime: float | None = None
    children: list["Entry"] = field(default_factory=list)
    other_rel: str | None = None      # for renamed files: path on the other side

    @property
    def name(self) -> str:
        return os.path.basename(self.rel) or self.rel


def _excluded(name: str, opts: FolderOptions, is_dir: bool, rel: str = "", st=None) -> bool:
    if any(fnmatch.fnmatch(name, p) for p in opts.exclude):
        return True
    if opts.exclude_regex and re.search(opts.exclude_regex, rel or name):
        return True
    if is_dir:
        return False
    if not any(fnmatch.fnmatch(name, p) for p in opts.include):
        return True
    if opts.include_regex and not re.search(opts.include_regex, rel or name):
        return True
    if st is not None:
        if opts.min_size is not None and st.st_size < opts.min_size:
            return True
        if opts.max_size is not None and st.st_size > opts.max_size:
            return True
        if opts.modified_after is not None and st.st_mtime < opts.modified_after:
            return True
        if opts.modified_before is not None and st.st_mtime > opts.modified_before:
            return True
    return False


def files_equal(a: str, b: str, chunk: int = 1 << 20) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(chunk), fb.read(chunk)
            if x != y:
                return False
            if not x:
                return True


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _scan(path: str) -> dict[str, os.DirEntry]:
    try:
        return {e.name: e for e in os.scandir(path)}
    except OSError:
        return {}


def compare_folders(left: str, right: str, opts: FolderOptions | None = None, progress=None, rel: str = "") -> Entry:
    opts = opts or FolderOptions()
    root = Entry(rel, True, SAME)
    lp = os.path.join(left, rel) if rel else left
    rp = os.path.join(right, rel) if rel else right
    le, re_ = _scan(lp), _scan(rp)
    names = sorted(set(le) | set(re_), key=lambda n: (not ((le.get(n) or re_.get(n)).is_dir()), n.lower()))
    for n in names:
        a, b = le.get(n), re_.get(n)
        is_dir = (a or b).is_dir()
        child_rel = os.path.join(rel, n) if rel else n
        st0 = None if is_dir else (a or b).stat()
        if _excluded(n, opts, is_dir, child_rel, st0):
            if opts.show_excluded:
                root.children.append(Entry(child_rel, is_dir, EXCLUDED))
            continue
        if progress:
            progress(child_rel)
        if is_dir:
            if a and b and a.is_dir() and b.is_dir():
                child = compare_folders(left, right, opts, progress, child_rel)
            else:
                child = _one_side_tree(left if a else right, child_rel, LEFT_ONLY if a else RIGHT_ONLY, opts)
            root.children.append(child)
            continue
        st_a = a.stat() if a else None
        st_b = b.stat() if b else None
        e = Entry(child_rel, False, SAME,
                  st_a.st_size if st_a else None, st_b.st_size if st_b else None,
                  st_a.st_mtime if st_a else None, st_b.st_mtime if st_b else None)
        if not a:
            e.status = RIGHT_ONLY
        elif not b:
            e.status = LEFT_ONLY
        else:
            e.status = _compare_file(a.path, b.path, st_a, st_b, opts)
        root.children.append(e)
    if any(c.status not in (SAME, EXCLUDED) for c in root.children):
        root.status = DIFFERENT
    if not rel and opts.detect_renames:
        detect_renames(root, left, right)
    return root


def detect_renames(root: Entry, left: str, right: str) -> int:
    """Pair left-only and right-only files with identical content (size + SHA-256)
    and mark them as renamed/moved."""
    lefts = [e for e in walk(root) if not e.is_dir and e.status == LEFT_ONLY]
    rights = [e for e in walk(root) if not e.is_dir and e.status == RIGHT_ONLY]
    by_size: dict[int, list[Entry]] = {}
    for e in rights:
        by_size.setdefault(e.right_size, []).append(e)
    n = 0
    for e in lefts:
        cands = by_size.get(e.left_size) or []
        if not cands:
            continue
        try:
            h = file_hash(os.path.join(left, e.rel))
        except OSError:
            continue
        for c in cands:
            try:
                if file_hash(os.path.join(right, c.rel)) == h:
                    e.status = c.status = RENAMED
                    e.other_rel, c.other_rel = c.rel, e.rel
                    cands.remove(c)
                    n += 1
                    break
            except OSError:
                continue
    return n


def _compare_file(pa, pb, sa, sb, opts) -> str:
    if sa.st_size != sb.st_size:
        same = False
    elif opts.method == "size":
        same = True
    elif opts.method == "size_time":
        same = abs(sa.st_mtime - sb.st_mtime) <= opts.time_tolerance
    elif opts.method == "hash":
        try:
            same = file_hash(pa) == file_hash(pb)
        except OSError:
            same = False
    else:
        try:
            same = files_equal(pa, pb)
        except OSError:
            same = False
    if same:
        return SAME
    if opts.method == "size_time" or abs(sa.st_mtime - sb.st_mtime) > opts.time_tolerance:
        return NEWER_LEFT if sa.st_mtime > sb.st_mtime else NEWER_RIGHT
    return DIFFERENT


def _one_side_tree(base, rel, status, opts) -> Entry:
    e = Entry(rel, True, status)
    for de in sorted(_scan(os.path.join(base, rel)).values(), key=lambda d: (not d.is_dir(), d.name.lower())):
        crel = os.path.join(rel, de.name)
        if _excluded(de.name, opts, de.is_dir(), crel, None if de.is_dir() else de.stat()):
            continue
        if de.is_dir():
            e.children.append(_one_side_tree(base, crel, status, opts))
        else:
            st = de.stat()
            if status == LEFT_ONLY:
                e.children.append(Entry(crel, False, status, st.st_size, None, st.st_mtime, None))
            else:
                e.children.append(Entry(crel, False, status, None, st.st_size, None, st.st_mtime))
    return e


def walk(entry: Entry):
    for c in entry.children:
        yield c
        if c.is_dir:
            yield from walk(c)


def summary(root: Entry) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in walk(root):
        if not e.is_dir:
            out[e.status] = out.get(e.status, 0) + 1
    return out


# ---------------------------------------------------------------- synchronisation
@dataclass
class SyncAction:
    action: str          # copy / overwrite / delete / conflict / skip
    src: str | None
    dst: str | None
    rel: str
    reason: str


def build_sync_plan(root: Entry, left: str, right: str, mode: str = "update_right") -> list[SyncAction]:
    """mode: update_right (copy new+changed left->right), update_left, mirror_right
    (make right identical to left, deletes extra files), mirror_left, both (newer wins,
    identical-time conflicts are reported, never guessed)."""
    plan: list[SyncAction] = []
    L = lambda r: os.path.join(left, r)   # noqa: E731
    R = lambda r: os.path.join(right, r)  # noqa: E731
    for e in walk(root):
        if e.is_dir or e.status in (SAME, EXCLUDED):
            continue
        st = e.status
        if st == RENAMED:
            st = LEFT_ONLY if e.left_size is not None else RIGHT_ONLY
        to_right = mode in ("update_right", "mirror_right")
        to_left = mode in ("update_left", "mirror_left")
        if mode == "both":
            if st == LEFT_ONLY:
                plan.append(SyncAction("copy", L(e.rel), R(e.rel), e.rel, "only on left"))
            elif st == RIGHT_ONLY:
                plan.append(SyncAction("copy", R(e.rel), L(e.rel), e.rel, "only on right"))
            elif st == NEWER_LEFT:
                plan.append(SyncAction("overwrite", L(e.rel), R(e.rel), e.rel, "left is newer"))
            elif st == NEWER_RIGHT:
                plan.append(SyncAction("overwrite", R(e.rel), L(e.rel), e.rel, "right is newer"))
            else:
                plan.append(SyncAction("conflict", L(e.rel), R(e.rel), e.rel, "content differs, same date: decide manually"))
            continue
        if to_right:
            if st == LEFT_ONLY:
                plan.append(SyncAction("copy", L(e.rel), R(e.rel), e.rel, "missing on right"))
            elif st in (DIFFERENT, NEWER_LEFT, NEWER_RIGHT):
                if st == NEWER_RIGHT and mode == "update_right":
                    plan.append(SyncAction("conflict", L(e.rel), R(e.rel), e.rel, "right file is newer: not overwritten"))
                else:
                    plan.append(SyncAction("overwrite", L(e.rel), R(e.rel), e.rel, "content differs"))
            elif st == RIGHT_ONLY and mode == "mirror_right":
                plan.append(SyncAction("delete", None, R(e.rel), e.rel, "not on left (mirror)"))
        elif to_left:
            if st == RIGHT_ONLY:
                plan.append(SyncAction("copy", R(e.rel), L(e.rel), e.rel, "missing on left"))
            elif st in (DIFFERENT, NEWER_LEFT, NEWER_RIGHT):
                if st == NEWER_LEFT and mode == "update_left":
                    plan.append(SyncAction("conflict", R(e.rel), L(e.rel), e.rel, "left file is newer: not overwritten"))
                else:
                    plan.append(SyncAction("overwrite", R(e.rel), L(e.rel), e.rel, "content differs"))
            elif st == LEFT_ONLY and mode == "mirror_left":
                plan.append(SyncAction("delete", None, L(e.rel), e.rel, "not on right (mirror)"))
    return plan


def execute_plan(plan: list[SyncAction], backup_dir: str | None = None, log_path: str | None = None) -> list[dict]:
    """Run a sync plan. Files that are overwritten or deleted are first copied into
    backup_dir (keeping relative paths). Conflicts are skipped. Returns an audit log."""
    log = []
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for a in plan:
        entry = asdict(a)
        entry["time"] = _dt.datetime.now().isoformat(timespec="seconds")
        try:
            if a.action in ("conflict", "skip"):
                entry["result"] = "skipped"
            else:
                if backup_dir and a.dst and os.path.isfile(a.dst) and a.action in ("overwrite", "delete"):
                    b = os.path.join(backup_dir, stamp, a.rel)
                    os.makedirs(os.path.dirname(b), exist_ok=True)
                    shutil.copy2(a.dst, b)
                    entry["backup"] = b
                if a.action == "delete":
                    os.remove(a.dst)
                else:
                    os.makedirs(os.path.dirname(a.dst), exist_ok=True)
                    shutil.copy2(a.src, a.dst)
                entry["result"] = "ok"
        except OSError as ex:
            entry["result"] = f"error: {ex}"
        log.append(entry)
    if log_path:
        with open(log_path, "w", encoding="utf-8") as fh:
            json.dump(log, fh, indent=2)
    return log


# ---------------------------------------------------------------- N-way
def compare_many(folders: list[str], opts: FolderOptions | None = None) -> list[dict]:
    """Compare two or more folders. Returns one row per relative file path with,
    per folder, the content hash (or None when missing) and a status."""
    opts = opts or FolderOptions()
    files: dict[str, list] = {}
    for k, base in enumerate(folders):
        for dirpath, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if not _excluded(d, opts, True)]
            for n in names:
                p = os.path.join(dirpath, n)
                rel = os.path.relpath(p, base)
                if _excluded(n, opts, False, rel, os.stat(p)):
                    continue
                files.setdefault(rel, [None] * len(folders))[k] = p
    rows = []
    for rel in sorted(files, key=str.lower):
        paths = files[rel]
        hashes = []
        for p in paths:
            try:
                hashes.append(file_hash(p) if p else None)
            except OSError:
                hashes.append("?")
        present = [h for h in hashes if h]
        if len(present) < len(folders):
            status = "missing in some"
        elif len(set(present)) == 1:
            status = SAME
        else:
            status = DIFFERENT
        rows.append({"rel": rel, "hashes": hashes, "status": status,
                     "variants": len(set(present))})
    return rows
