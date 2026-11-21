"""Search and replace across many files and folders, with preview and backups."""
from __future__ import annotations

import csv
import datetime as _dt
import fnmatch
import os
import re
import shutil
from dataclasses import dataclass, field

from .textio import read_text, write_text, is_binary

OFFICE = {".docx", ".xlsx", ".xlsm"}


@dataclass
class SearchSpec:
    roots: list[str]
    pattern: str
    regex: bool = False
    case: bool = False
    word: bool = False
    multiline: bool = False      # '.' matches newlines, ^/$ per line; matches can span lines
    include: str = "*"
    exclude: str = ".git;.svn;node_modules;__pycache__;*.exe;*.dll;*.zip"
    recursive: bool = True
    search_office: bool = True   # look inside .docx/.xlsx (read-only)
    max_size: int = 50_000_000

    def compile(self):
        pat = self.pattern if self.regex else re.escape(self.pattern)
        if self.word:
            pat = rf"\b{pat}\b"
        flags = re.MULTILINE
        if not self.case:
            flags |= re.IGNORECASE
        if self.multiline:
            flags |= re.DOTALL
        return re.compile(pat, flags)


@dataclass
class Hit:
    path: str
    line: int
    col: int
    text: str        # the line (first line of a multi-line match)
    match: str


def iter_files(spec: SearchSpec):
    inc = [p.strip() for p in spec.include.split(";") if p.strip()] or ["*"]
    inc = ["*" if p == "*.*" else p for p in inc]
    exc = [p.strip() for p in spec.exclude.split(";") if p.strip()]
    for root in spec.roots:
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, p) for p in exc)]
            if not spec.recursive:
                dirs[:] = []
            for n in names:
                if any(fnmatch.fnmatch(n, p) for p in inc) and not any(fnmatch.fnmatch(n, p) for p in exc):
                    yield os.path.join(dirpath, n)


def _text_of(path, spec) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    try:
        if os.path.getsize(path) > spec.max_size:
            return None
        if ext in OFFICE:
            if not spec.search_office:
                return None
            from . import loaders
            if ext == ".docx":
                return "\n".join(loaders.docx_to_lines(path))
            return "\n".join(f"[{sh.name}] " + "\t".join(r) for sh in loaders.load_xlsx(path) for r in sh.rows)
        with open(path, "rb") as fh:
            if is_binary(fh.read(8000)):
                return None
        return read_text(path).text
    except Exception:  # noqa: BLE001 - unreadable files are skipped
        return None


def search(spec: SearchSpec, on_hit=None, cancel=None, limit: int = 100_000) -> tuple[list[Hit], int]:
    rx = spec.compile()
    hits: list[Hit] = []
    nfiles = 0
    for p in iter_files(spec):
        if cancel and cancel():
            break
        text = _text_of(p, spec)
        if text is None:
            continue
        nfiles += 1
        starts = None
        for m in rx.finditer(text):
            if m.end() == m.start():
                continue
            if starts is None:
                starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
            import bisect
            ln = bisect.bisect_right(starts, m.start()) - 1
            line_end = text.find("\n", starts[ln])
            line = text[starts[ln]: line_end if line_end >= 0 else len(text)]
            h = Hit(p, ln + 1, m.start() - starts[ln] + 1, line[:500], m.group(0)[:500])
            hits.append(h)
            if on_hit:
                on_hit(h)
            if len(hits) >= limit:
                return hits, nfiles
    return hits, nfiles


@dataclass
class FileReplacement:
    path: str
    count: int
    old_text: str
    new_text: str
    encoding: str
    eol: str


def replace_preview(spec: SearchSpec, replacement: str, cancel=None) -> list[FileReplacement]:
    """Compute replacements for every matching text file without writing anything.
    Office files are never modified."""
    rx = spec.compile()
    out = []
    for p in iter_files(spec):
        if cancel and cancel():
            break
        if os.path.splitext(p)[1].lower() in OFFICE:
            continue
        try:
            with open(p, "rb") as fh:
                if is_binary(fh.read(8000)) or os.path.getsize(p) > spec.max_size:
                    continue
            tf = read_text(p)
        except OSError:
            continue
        repl = replacement if spec.regex else replacement.replace("\\", "\\\\")
        new, n = rx.subn(repl, tf.text)
        if n:
            out.append(FileReplacement(p, n, tf.text, new, tf.encoding, tf.eol))
    return out


def apply_replacements(items: list[FileReplacement], backup_dir: str | None = None) -> list[str]:
    """Write the previewed replacements. Each original file is copied to backup_dir
    first (if given). Files changed on disk since the preview are skipped."""
    log = []
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for it in items:
        try:
            if read_text(it.path).text != it.old_text:
                log.append(f"SKIPPED (changed since preview): {it.path}")
                continue
            if backup_dir:
                drive, tail = os.path.splitdrive(os.path.abspath(it.path))
                b = os.path.join(backup_dir, stamp, drive.replace(":", ""), tail.lstrip("\\/"))
                os.makedirs(os.path.dirname(b), exist_ok=True)
                shutil.copy2(it.path, b)
            write_text(it.path, it.new_text, it.encoding, it.eol)
            log.append(f"OK ({it.count} replacement(s)): {it.path}")
        except OSError as ex:
            log.append(f"ERROR {ex}: {it.path}")
    return log


def export_hits(hits: list[Hit], path: str) -> None:
    if path.lower().endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["File", "Line", "Column", "Match", "Line text"])
        for h in hits:
            ws.append([h.path, h.line, h.col, h.match, h.text])
        wb.save(path)
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["File", "Line", "Column", "Match", "Line text"])
        for h in hits:
            w.writerow([h.path, h.line, h.col, h.match, h.text])
