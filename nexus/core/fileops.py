"""Directory listing, statistics, duplicates, batch rename and file attributes."""
from __future__ import annotations

import csv
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import stat as _stat
import sys
from dataclasses import dataclass, field

IS_WIN = sys.platform == "win32"


# ---------------------------------------------------------------- listing
@dataclass
class FileInfo:
    path: str
    rel: str
    name: str
    ext: str
    is_dir: bool
    size: int
    created: float
    modified: float
    accessed: float
    attrs: str             # e.g. "RHSA"
    depth: int

    def row(self):
        f = lambda t: _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
        return [self.rel, self.name, self.ext, "folder" if self.is_dir else "file", self.size,
                f(self.created), f(self.modified), f(self.accessed), self.attrs]


HEADER = ["Path", "Name", "Extension", "Type", "Size (bytes)", "Created", "Modified", "Accessed", "Attributes"]


def attr_string(st) -> str:
    out = ""
    fa = getattr(st, "st_file_attributes", None)
    if fa is not None:
        for flag, ch in ((_stat.FILE_ATTRIBUTE_READONLY, "R"), (_stat.FILE_ATTRIBUTE_HIDDEN, "H"),
                         (_stat.FILE_ATTRIBUTE_SYSTEM, "S"), (_stat.FILE_ATTRIBUTE_ARCHIVE, "A")):
            if fa & flag:
                out += ch
        return out
    if not st.st_mode & _stat.S_IWUSR:
        out += "R"
    return out


def list_directory(root: str, recursive=True, include="*", exclude="", include_dirs=True,
                   max_depth: int | None = None, progress=None, cancel=None) -> list[FileInfo]:
    inc = [p.strip() for p in include.split(";") if p.strip()] or ["*"]
    exc = [p.strip() for p in exclude.split(";") if p.strip()]
    out: list[FileInfo] = []
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirs, names in os.walk(root):
        if cancel and cancel():
            break
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, p) for p in exc)]
        if not recursive or (max_depth is not None and depth >= max_depth):
            sub = list(dirs)
            dirs[:] = []
        else:
            sub = dirs
        entries = [(d, True) for d in sub] if include_dirs else []
        entries += [(n, False) for n in names]
        for n, is_dir in entries:
            if not is_dir and (not any(fnmatch.fnmatch(n, p) for p in inc) or any(fnmatch.fnmatch(n, p) for p in exc)):
                continue
            p = os.path.join(dirpath, n)
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            created = getattr(st, "st_birthtime", None) or st.st_ctime
            out.append(FileInfo(p, os.path.relpath(p, root), n, "" if is_dir else os.path.splitext(n)[1].lower(),
                                is_dir, 0 if is_dir else st.st_size, created, st.st_mtime, st.st_atime,
                                attr_string(st), depth + 1))
        if progress:
            progress(dirpath, len(out))
    return out


def extension_stats(files: list[FileInfo]) -> list[tuple[str, int, int]]:
    """[(extension, count, total bytes)] sorted by size."""
    agg: dict[str, list[int]] = {}
    for f in files:
        if f.is_dir:
            continue
        a = agg.setdefault(f.ext or "(none)", [0, 0])
        a[0] += 1
        a[1] += f.size
    return sorted(((e, c, s) for e, (c, s) in agg.items()), key=lambda x: -x[2])


def folder_sizes(files: list[FileInfo], root: str, level: int = 1) -> list[tuple[str, int, int]]:
    """Total size and file count per folder at the given depth below root."""
    agg: dict[str, list[int]] = {}
    for f in files:
        if f.is_dir:
            continue
        parts = f.rel.split(os.sep)
        key = os.sep.join(parts[:level]) if len(parts) > level else "(files in root)" if level == 1 else os.sep.join(parts[:-1])
        a = agg.setdefault(key, [0, 0])
        a[0] += f.size
        a[1] += 1
    return sorted(((k, s, c) for k, (s, c) in agg.items()), key=lambda x: -x[1])


def export_list(files: list[FileInfo], path: str, fmt: str | None = None) -> None:
    fmt = fmt or os.path.splitext(path)[1].lower().lstrip(".")
    if fmt == "csv":
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(HEADER)
            for f in files:
                w.writerow(f.row())
    elif fmt == "xlsx":
        import openpyxl
        from openpyxl.styles import Font

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Files"
        ws.append(HEADER)
        for c in ws[1]:
            c.font = Font(bold=True)
        for f in files:
            ws.append(f.row())
        ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = "A2"
        for col, width in zip("ABCDEFGHI", (60, 30, 10, 8, 14, 20, 20, 20, 10)):
            ws.column_dimensions[col].width = width
        st = wb.create_sheet("Extensions")
        st.append(["Extension", "Files", "Bytes"])
        for row in extension_stats(files):
            st.append(list(row))
        wb.save(path)
    else:  # txt tree
        with open(path, "w", encoding="utf-8") as fh:
            for f in sorted(files, key=lambda x: x.rel.lower()):
                indent = "    " * (f.depth - 1)
                if f.is_dir:
                    fh.write(f"{indent}[{f.name}]\n")
                else:
                    fh.write(f"{indent}{f.name}    {f.size:,} B    {_dt.datetime.fromtimestamp(f.modified):%Y-%m-%d %H:%M}\n")


# ---------------------------------------------------------------- duplicates
def _hash(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        if limit:
            h.update(fh.read(limit))
        else:
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
    return h.hexdigest()


def find_duplicates(paths: list[str], min_size: int = 1, progress=None) -> list[list[str]]:
    """Groups of identical files (same size, then same first 64 KB, then same SHA-256)."""
    by_size: dict[int, list[str]] = {}
    for p in paths:
        try:
            s = os.path.getsize(p)
        except OSError:
            continue
        if s >= min_size:
            by_size.setdefault(s, []).append(p)
    groups = []
    for s, ps in by_size.items():
        if len(ps) < 2:
            continue
        by_head: dict[str, list[str]] = {}
        for p in ps:
            try:
                by_head.setdefault(_hash(p, 65536), []).append(p)
            except OSError:
                pass
        for hp in by_head.values():
            if len(hp) < 2:
                continue
            by_full: dict[str, list[str]] = {}
            for p in hp:
                try:
                    by_full.setdefault(_hash(p) if s > 65536 else "small", []).append(p)
                except OSError:
                    pass
                if progress:
                    progress(p)
            groups.extend(g for g in by_full.values() if len(g) > 1)
    return sorted(groups, key=lambda g: -os.path.getsize(g[0]))


def find_similar_names(files: list[FileInfo], threshold: float = 0.85) -> list[tuple[str, str, float]]:
    """Near-duplicate detection by file name similarity (e.g. 'Report v2 final.docx'
    vs 'Report v2 final (1).docx'). Quadratic per extension, capped for speed."""
    import difflib

    by_ext: dict[str, list[FileInfo]] = {}
    for f in files:
        if not f.is_dir:
            by_ext.setdefault(f.ext, []).append(f)
    out = []
    for lst in by_ext.values():
        lst = lst[:1500]
        stems = [re.sub(r"[\W_]+", " ", os.path.splitext(f.name)[0].lower()).strip() for f in lst]
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if stems[i] == stems[j] and lst[i].name == lst[j].name:
                    continue
                r = difflib.SequenceMatcher(None, stems[i], stems[j]).ratio()
                if r >= threshold:
                    out.append((lst[i].path, lst[j].path, round(r, 3)))
    return sorted(out, key=lambda x: -x[2])


# ---------------------------------------------------------------- batch rename
@dataclass
class RenameRules:
    find: str = ""
    replace: str = ""
    regex: bool = False
    case_sensitive: bool = False
    case: str = ""              # "", "upper", "lower", "title", "sentence"
    prefix: str = ""
    suffix: str = ""            # added before the extension
    numbering: bool = False
    num_start: int = 1
    num_step: int = 1
    num_digits: int = 3
    num_position: str = "suffix"   # prefix / suffix
    num_separator: str = "_"
    new_extension: str | None = None   # without dot; "" removes it
    apply_to_extension: bool = False   # include extension in find/replace & case
    template: str = ""          # optional: e.g. "{name}_{mtime:%Y%m%d}{ext}" – overrides others
    strip_spaces: bool = False
    replace_spaces_with: str | None = None


def _apply_one(name: str, path: str, idx: int, r: RenameRules) -> str:
    stem, ext = os.path.splitext(name)
    if r.template:
        st = os.stat(path)
        n = r.num_start + idx * r.num_step
        return r.template.format(name=stem, ext=ext, n=n, num=str(n).zfill(r.num_digits),
                                 mtime=_dt.datetime.fromtimestamp(st.st_mtime),
                                 ctime=_dt.datetime.fromtimestamp(getattr(st, "st_birthtime", None) or st.st_ctime),
                                 size=st.st_size, parent=os.path.basename(os.path.dirname(path)))
    target = name if r.apply_to_extension else stem
    if r.find:
        flags = 0 if r.case_sensitive else re.IGNORECASE
        pat = r.find if r.regex else re.escape(r.find)
        target = re.sub(pat, r.replace if r.regex else r.replace.replace("\\", "\\\\"), target, flags=flags)
    if r.strip_spaces:
        target = target.strip()
    if r.replace_spaces_with is not None:
        target = re.sub(r"\s+", r.replace_spaces_with, target)
    if r.case == "upper":
        target = target.upper()
    elif r.case == "lower":
        target = target.lower()
    elif r.case == "title":
        target = target.title()
    elif r.case == "sentence":
        target = target[:1].upper() + target[1:].lower()
    if r.apply_to_extension:
        stem, ext = os.path.splitext(target)
    else:
        stem = target
    stem = r.prefix + stem + r.suffix
    if r.numbering:
        num = str(r.num_start + idx * r.num_step).zfill(r.num_digits)
        stem = f"{num}{r.num_separator}{stem}" if r.num_position == "prefix" else f"{stem}{r.num_separator}{num}"
    if r.new_extension is not None:
        ext = ("." + r.new_extension.lstrip(".")) if r.new_extension else ""
    return stem + ext


INVALID_WIN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def preview_rename(paths: list[str], rules: RenameRules) -> list[dict]:
    """Returns [{old, new, path, new_path, status}] with status ok / unchanged /
    conflict / invalid / error. Nothing is changed on disk."""
    out = []
    targets: dict[str, int] = {}
    for i, p in enumerate(paths):
        name = os.path.basename(p)
        try:
            new = _apply_one(name, p, i, rules)
            status = "ok" if new != name else "unchanged"
            if not new or INVALID_WIN.search(new) or new.endswith((" ", ".")):
                status = "invalid"
        except Exception as ex:  # noqa: BLE001 - template errors are shown to the user
            new, status = name, f"error: {ex}"
        np = os.path.join(os.path.dirname(p), new)
        out.append({"old": name, "new": new, "path": p, "new_path": np, "status": status})
        targets[os.path.normcase(np)] = targets.get(os.path.normcase(np), 0) + 1
    olds = {os.path.normcase(p) for p in paths}
    for row in out:
        key = os.path.normcase(row["new_path"])
        if row["status"] == "ok" and (targets[key] > 1 or (os.path.exists(row["new_path"]) and key not in olds)):
            row["status"] = "conflict"
    return out


def apply_rename(preview: list[dict], undo_log: str | None = None) -> list[dict]:
    """Rename all rows with status ok, in two phases (via temporary names) so that
    swaps like a->b, b->a work. Writes an undo log (JSON) if a path is given."""
    rows = [r for r in preview if r["status"] == "ok"]
    temps = []
    done = []
    for k, r in enumerate(rows):
        tmp = os.path.join(os.path.dirname(r["path"]), f".nexus-rename-{os.getpid()}-{k}.tmp")
        os.rename(r["path"], tmp)
        temps.append((tmp, r))
    for tmp, r in temps:
        try:
            os.rename(tmp, r["new_path"])
            done.append({"from": r["path"], "to": r["new_path"]})
        except OSError as ex:
            os.rename(tmp, r["path"])
            r["status"] = f"error: {ex}"
    if undo_log:
        with open(undo_log, "w", encoding="utf-8") as fh:
            json.dump(done, fh, indent=2)
    return done


def undo_rename(undo_log: str) -> int:
    with open(undo_log, encoding="utf-8") as fh:
        done = json.load(fh)
    n = 0
    for d in reversed(done):
        if os.path.exists(d["to"]) and not os.path.exists(d["from"]):
            os.rename(d["to"], d["from"])
            n += 1
    return n


# ---------------------------------------------------------------- attributes & timestamps
@dataclass
class AttrChange:
    readonly: bool | None = None
    hidden: bool | None = None      # Windows only
    system: bool | None = None      # Windows only
    archive: bool | None = None     # Windows only
    modified: _dt.datetime | None = None
    accessed: _dt.datetime | None = None
    created: _dt.datetime | None = None   # Windows only


def get_attributes(path: str) -> dict:
    st = os.stat(path)
    a = attr_string(st)
    return {"readonly": "R" in a, "hidden": "H" in a, "system": "S" in a, "archive": "A" in a,
            "modified": _dt.datetime.fromtimestamp(st.st_mtime), "accessed": _dt.datetime.fromtimestamp(st.st_atime),
            "created": _dt.datetime.fromtimestamp(getattr(st, "st_birthtime", None) or st.st_ctime)}


def set_attributes(path: str, ch: AttrChange) -> list[str]:
    """Apply attribute/timestamp changes. Returns notes about anything not supported."""
    notes = []
    st = os.stat(path)
    if IS_WIN and any(v is not None for v in (ch.readonly, ch.hidden, ch.system, ch.archive)):
        import ctypes

        fa = st.st_file_attributes
        for val, flag in ((ch.readonly, _stat.FILE_ATTRIBUTE_READONLY), (ch.hidden, _stat.FILE_ATTRIBUTE_HIDDEN),
                          (ch.system, _stat.FILE_ATTRIBUTE_SYSTEM), (ch.archive, _stat.FILE_ATTRIBUTE_ARCHIVE)):
            if val is True:
                fa |= flag
            elif val is False:
                fa &= ~flag
        if not ctypes.windll.kernel32.SetFileAttributesW(str(path), fa or _stat.FILE_ATTRIBUTE_NORMAL):
            notes.append(f"SetFileAttributes failed for {path}")
    else:
        if ch.readonly is not None:
            mode = st.st_mode
            mode = (mode & ~(_stat.S_IWUSR | _stat.S_IWGRP | _stat.S_IWOTH)) if ch.readonly else (mode | _stat.S_IWUSR)
            os.chmod(path, mode)
        for name in ("hidden", "system", "archive"):
            if getattr(ch, name) is not None:
                notes.append(f"'{name}' attribute exists only on Windows")
    if ch.modified or ch.accessed:
        st = os.stat(path)
        at = ch.accessed.timestamp() if ch.accessed else st.st_atime
        mt = ch.modified.timestamp() if ch.modified else st.st_mtime
        os.utime(path, (at, mt))
    if ch.created:
        if IS_WIN:
            _set_creation_time_win(path, ch.created)
        else:
            notes.append("Creation time can only be changed on Windows")
    return notes


def _set_creation_time_win(path, when: _dt.datetime):
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    ts = int((when.timestamp() + 11644473600) * 10_000_000)
    ft = wintypes.FILETIME(ts & 0xFFFFFFFF, ts >> 32)
    FILE_WRITE_ATTRIBUTES, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS = 0x100, 3, 0x02000000
    h = k32.CreateFileW(str(path), FILE_WRITE_ATTRIBUTES, 0x7, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
    if h in (-1, 0xFFFFFFFFFFFFFFFF):
        raise OSError(f"Cannot open {path}")
    try:
        k32.SetFileTime(h, ctypes.byref(ft), None, None)
    finally:
        k32.CloseHandle(h)
