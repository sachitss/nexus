"""Bulk rename engine: an ordered list of rules, a full preview, safe execution,
rollback, history and reusable profiles.

A rule is a dict with a "type", an "enabled" flag and its parameters, e.g.

    {"type": "replace", "find": "draft", "replace": "final", "regex": False}
    {"type": "numbering", "start": 1, "digits": 3, "position": "prefix"}

Rules run top to bottom; each one sees the result of the previous one.
Nothing is changed on disk by `preview()`. `execute()` renames only rows whose
status is "ok", writes a journal first (so an interrupted run can be rolled
back) and undoes everything it did if a rename fails part-way.

Tokens usable in "insert", "add" and "template" rules:
    {name} {ext} {parent} {grandparent} {n} {num} {size} {size_kb}
    {mtime:%Y-%m-%d} {ctime:%Y%m%d} {now:%Y%m%d} {line1} {content:REGEX} {orig}
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import re
import uuid

INVALID_WIN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_WIN = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_PATH = 260

# type -> (label, [(param, kind, default, label)])   kind: str, bool, int, choice:<a|b>, file
RULE_TYPES: dict[str, tuple[str, list]] = {
    "replace": ("Find and replace", [("find", "str", "", "Find"), ("replace", "str", "", "Replace with"),
                                     ("regex", "bool", False, "Regular expression"),
                                     ("case_sensitive", "bool", False, "Case-sensitive"),
                                     ("scope", "choice:name|extension|both", "name", "Apply to")]),
    "remove": ("Remove characters", [("first", "int", 0, "First n characters"), ("last", "int", 0, "Last n characters"),
                                     ("start", "int", 0, "From position (1-based, 0 = off)"),
                                     ("count", "int", 0, "Number of characters"),
                                     ("chars", "str", "", "These characters"),
                                     ("digits", "bool", False, "All digits"),
                                     ("symbols", "bool", False, "Symbols (keep letters, digits, - _ . space)"),
                                     ("trim", "bool", True, "Trim spaces at the ends"),
                                     ("collapse", "bool", False, "Collapse repeated spaces")]),
    "insert": ("Insert text", [("text", "str", "", "Text (tokens allowed)"),
                               ("position", "int", 0, "Position (0 = start, -1 = end, negative counts from the end)")]),
    "add": ("Prefix / suffix", [("prefix", "str", "", "Prefix (tokens allowed)"),
                                ("suffix", "str", "", "Suffix before the extension (tokens allowed)")]),
    "case": ("Change case", [("mode", "choice:upper|lower|title|sentence|invert", "lower", "Case"),
                             ("extension", "choice:keep|lower|upper", "keep", "Extension")]),
    "spaces": ("Spaces and separators", [("replace_with", "str", "_", "Replace whitespace with"),
                                         ("from_chars", "str", "", "Also replace these characters")]),
    "numbering": ("Numbering", [("start", "int", 1, "Start"), ("step", "int", 1, "Step"),
                                ("digits", "int", 3, "Digits (padding)"),
                                ("position", "choice:prefix|suffix|insert", "suffix", "Position"),
                                ("insert_at", "int", 0, "Insert at (for position = insert)"),
                                ("separator", "str", "_", "Separator"),
                                ("per_folder", "bool", False, "Restart in every folder"),
                                ("base", "choice:decimal|alpha_lower|alpha_upper|roman", "decimal", "Format")]),
    "date": ("Insert date / time", [("source", "choice:modified|created|now", "modified", "Date of"),
                                    ("format", "str", "%Y-%m-%d", "Format (strftime)"),
                                    ("position", "choice:prefix|suffix", "prefix", "Position"),
                                    ("separator", "str", "_", "Separator")]),
    "extension": ("Extension", [("mode", "choice:set|remove|lower|upper|append", "lower", "Action"),
                                ("value", "str", "", "New / appended extension (without dot)")]),
    "template": ("Template (whole name)", [("template", "str", "{name}{ext}", "Template (tokens)")]),
    "mapping": ("Rename from CSV / Excel list", [("file", "file", "", "CSV, TXT or XLSX file"),
                                                 ("match", "choice:name|stem|path", "name", "Column 1 matches"),
                                                 ("sheet", "str", "", "Worksheet (Excel, empty = first)"),
                                                 ("keep_extension", "bool", True, "Keep the extension if the new name has none")]),
}


def new_rule(rtype: str, **params) -> dict:
    r = {"type": rtype, "enabled": True}
    for p, _k, default, _l in RULE_TYPES[rtype][1]:
        r[p] = default
    r.update(params)
    return r


def describe(rule: dict) -> str:
    """One-line human description of a rule (shown in the rule list)."""
    t = rule["type"]
    label = RULE_TYPES.get(t, (t, []))[0]
    bits = []
    for p, kind, default, _l in RULE_TYPES.get(t, (t, []))[1]:
        v = rule.get(p, default)
        if v != default or p in ("find", "text", "template", "file", "mode", "position"):
            if v not in ("", None, False, 0) or p in ("position",):
                bits.append(f"{p}={v!r}" if kind == "str" or kind == "file" else f"{p}={v}")
    return f"{label}: " + ", ".join(bits) if bits else label


# ---------------------------------------------------------------- tokens
_TOKEN = re.compile(r"\{(\w+)(?::((?:[^{}]|\{\d*,?\d*\})*))?\}")


class _Ctx:
    """Per-file information used by tokens (stat is read lazily)."""

    def __init__(self, path: str, index: int, folder_index: int):
        self.path, self.index, self.folder_index = path, index, folder_index
        self.orig = os.path.basename(path)
        self._st = None
        self._head = None

    @property
    def st(self):
        if self._st is None:
            self._st = os.stat(self.path)
        return self._st

    def when(self, which):
        if which == "now":
            return _dt.datetime.now()
        if which == "created":
            return _dt.datetime.fromtimestamp(getattr(self.st, "st_birthtime", None) or self.st.st_ctime)
        return _dt.datetime.fromtimestamp(self.st.st_mtime)

    def head(self) -> str:
        """First 64 KB of a text file (DOCX: its paragraphs), for content tokens."""
        if self._head is None:
            self._head = ""
            if os.path.isfile(self.path):
                try:
                    if self.path.lower().endswith(".docx"):
                        import docx
                        self._head = "\n".join(p.text for p in docx.Document(self.path).paragraphs[:200])
                    else:
                        with open(self.path, "rb") as fh:
                            raw = fh.read(65536)
                        if b"\x00" not in raw[:4096]:
                            for enc in ("utf-8", "cp1252"):
                                try:
                                    self._head = raw.decode(enc)
                                    break
                                except UnicodeDecodeError:
                                    continue
                except Exception:  # noqa: BLE001 - unreadable content gives an empty token
                    self._head = ""
        return self._head


def expand(text: str, name: str, ctx: _Ctx, n: int | None = None, digits: int = 3) -> str:
    stem, ext = os.path.splitext(name)

    def tok(m):
        key, arg = m.group(1), m.group(2)
        if key == "name":
            return stem
        if key == "ext":
            return ext
        if key == "orig":
            return os.path.splitext(ctx.orig)[0]
        if key == "parent":
            return os.path.basename(os.path.dirname(ctx.path))
        if key == "grandparent":
            return os.path.basename(os.path.dirname(os.path.dirname(ctx.path)))
        if key in ("n", "num"):
            k = (ctx.index if n is None else n)
            return str(k) if key == "n" else str(k).zfill(int(arg) if arg and arg.isdigit() else digits)
        if key == "size":
            return str(ctx.st.st_size)
        if key == "size_kb":
            return str(round(ctx.st.st_size / 1024))
        if key in ("mtime", "ctime", "now"):
            d = ctx.when({"mtime": "modified", "ctime": "created", "now": "now"}[key])
            return d.strftime(arg or "%Y-%m-%d")
        if key == "line1":
            return next((ln.strip() for ln in ctx.head().splitlines() if ln.strip()), "")[:120]
        if key == "content":
            mm = re.search(arg or r"\S+", ctx.head(), re.MULTILINE)
            if not mm:
                return ""
            return (mm.group(1) if mm.groups() else mm.group(0)).strip()
        raise ValueError(f"unknown token {{{key}}}")

    return _TOKEN.sub(tok, text)


# ---------------------------------------------------------------- numbering formats
def _alpha(n: int, upper: bool) -> str:
    s = ""
    n = max(n, 1)
    while n:
        n, r = divmod(n - 1, 26)
        s = chr((65 if upper else 97) + r) + s
    return s


def _roman(n: int) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out or "0"


def format_number(n: int, digits: int, base: str = "decimal") -> str:
    if base == "alpha_lower":
        return _alpha(n, False)
    if base == "alpha_upper":
        return _alpha(n, True)
    if base == "roman":
        return _roman(n)
    return ("-" if n < 0 else "") + str(abs(n)).zfill(digits)


# ---------------------------------------------------------------- mapping lists
def load_mapping(path: str, sheet: str = "") -> dict[str, str]:
    """Two columns: current name (or path) -> new name. CSV/TXT (; , or tab) or XLSX.
    A header row is skipped when it says old/new, alt/neu, current/new, from/to."""
    rows: list[list[str]] = []
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        for r in ws.iter_rows(values_only=True):
            if r and len(r) >= 2 and r[0] is not None and r[1] is not None:
                rows.append([str(r[0]).strip(), str(r[1]).strip()])
        wb.close()
    else:
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            delim = "\t" if "\t" in sample else ";" if sample.count(";") >= sample.count(",") else ","
            for r in csv.reader(fh, delimiter=delim):
                if len(r) >= 2 and r[0].strip():
                    rows.append([r[0].strip(), r[1].strip()])
    if rows and rows[0][0].lower() in ("old", "alt", "current", "from", "name", "old name", "alter name", "aktueller name"):
        rows = rows[1:]
    return {os.path.normcase(a): b for a, b in rows}


# ---------------------------------------------------------------- one rule
def _split(name: str, is_dir: bool):
    if is_dir:
        return name, ""
    return os.path.splitext(name)


def apply_rule(rule: dict, name: str, ctx: _Ctx, is_dir: bool, cache: dict) -> str:
    t = rule["type"]
    stem, ext = _split(name, is_dir)
    g = lambda k: rule.get(k, next(d for p, _k, d, _l in RULE_TYPES[t][1] if p == k))  # noqa: E731
    if t == "replace":
        if not g("find"):
            return name
        scope = g("scope")
        target = stem if scope == "name" else ext.lstrip(".") if scope == "extension" else name
        flags = 0 if g("case_sensitive") else re.IGNORECASE
        if g("regex"):
            target = re.sub(g("find"), g("replace"), target, flags=flags)
        else:
            target = re.sub(re.escape(g("find")), lambda _m: g("replace"), target, flags=flags)
        if scope == "name":
            return target + ext
        if scope == "extension":
            return stem + ("." + target if target else "")
        return target
    if t == "remove":
        s = stem
        if g("first"):
            s = s[g("first"):]
        if g("last"):
            s = s[:-g("last")] if g("last") < len(s) else ""
        if g("start") and g("count"):
            i = g("start") - 1
            s = s[:i] + s[i + g("count"):]
        if g("chars"):
            s = "".join(c for c in s if c not in g("chars"))
        if g("digits"):
            s = re.sub(r"\d", "", s)
        if g("symbols"):
            s = re.sub(r"[^\w\-. ]", "", s)
        if g("collapse"):
            s = re.sub(r"\s{2,}", " ", s)
        if g("trim"):
            s = s.strip()
        return s + ext
    if t == "insert":
        txt = expand(g("text"), name, ctx)
        pos = g("position")
        if pos == -1:
            return stem + txt + ext
        if pos < 0:
            pos = max(len(stem) + pos + 1, 0)
        return stem[:pos] + txt + stem[pos:] + ext
    if t == "add":
        return expand(g("prefix"), name, ctx) + stem + expand(g("suffix"), name, ctx) + ext
    if t == "case":
        m = g("mode")
        s = {"upper": stem.upper(), "lower": stem.lower(), "title": stem.title(),
             "sentence": stem[:1].upper() + stem[1:].lower(), "invert": stem.swapcase()}.get(m, stem)
        e = {"lower": ext.lower(), "upper": ext.upper()}.get(g("extension"), ext)
        return s + e
    if t == "spaces":
        s = re.sub(r"\s+", g("replace_with"), stem)
        for c in g("from_chars"):
            s = s.replace(c, g("replace_with"))
        return s + ext
    if t == "numbering":
        k = ctx.folder_index if g("per_folder") else ctx.index
        num = format_number(g("start") + k * g("step"), g("digits"), g("base"))
        sep, pos = g("separator"), g("position")
        if pos == "prefix":
            return num + sep + stem + ext
        if pos == "insert":
            i = g("insert_at")
            return stem[:i] + num + stem[i:] + ext
        return stem + sep + num + ext
    if t == "date":
        d = ctx.when(g("source")).strftime(g("format"))
        return (d + g("separator") + stem if g("position") == "prefix" else stem + g("separator") + d) + ext
    if t == "extension":
        if is_dir:
            return name
        m, val = g("mode"), g("value").lstrip(".")
        if m == "set":
            return stem + ("." + val if val else "")
        if m == "remove":
            return stem
        if m == "upper":
            return stem + ext.upper()
        if m == "append":
            return name + ("." + val if val else "")
        return stem + ext.lower()
    if t == "template":
        return expand(g("template"), name, ctx)
    if t == "mapping":
        key = ("map", g("file"), g("sheet"))
        if key not in cache:
            cache[key] = load_mapping(g("file"), g("sheet")) if g("file") else {}
        mp = cache[key]
        look = {"name": ctx.orig, "stem": os.path.splitext(ctx.orig)[0], "path": ctx.path}[g("match")]
        new = mp.get(os.path.normcase(look))
        if new is None:
            return name
        if g("keep_extension") and not is_dir and not os.path.splitext(new)[1]:
            new += ext
        return new
    raise ValueError(f"unknown rule type {t}")


# ---------------------------------------------------------------- preview
def check_name(new: str) -> str | None:
    """Reason why a name is not valid on Windows, or None."""
    if not new or new in (".", ".."):
        return "empty name"
    if INVALID_WIN.search(new):
        return "invalid character"
    if new.endswith((" ", ".")):
        return "ends with space or dot"
    if os.path.splitext(new)[0].upper() in RESERVED_WIN:
        return "reserved Windows name"
    if len(new) > 255:
        return "name longer than 255 characters"
    return None


def preview(paths: list[str], rules: list[dict]) -> list[dict]:
    """Rows: {path, old, new, new_path, status, note}. Status is ok, unchanged,
    conflict, invalid or error. Nothing is changed on disk."""
    rows = []
    cache: dict = {}
    per_folder: dict[str, int] = {}
    active = [r for r in rules if r.get("enabled", True)]
    for i, p in enumerate(paths):
        folder = os.path.dirname(p)
        fi = per_folder.get(folder, 0)
        per_folder[folder] = fi + 1
        ctx = _Ctx(p, i, fi)
        old = os.path.basename(p)
        is_dir = os.path.isdir(p)
        row = {"path": p, "old": old, "new": old, "new_path": p, "status": "unchanged", "note": "",
               "is_dir": is_dir}
        try:
            name = old
            for r in active:
                name = apply_rule(r, name, ctx, is_dir, cache)
            row["new"] = name
            row["new_path"] = os.path.join(folder, name)
            bad = check_name(name)
            if bad:
                row["status"], row["note"] = "invalid", bad
            elif name != old:
                row["status"] = "ok"
                if len(row["new_path"]) >= MAX_PATH:
                    row["note"] = f"long path ({len(row['new_path'])} characters)"
        except Exception as ex:  # noqa: BLE001 - shown in the preview
            row["status"], row["note"] = "error", str(ex)
        rows.append(row)
    # collisions: two rows with the same target, or a target that exists and is not being renamed away
    count: dict[str, int] = {}
    for r in rows:
        count[os.path.normcase(r["new_path"])] = count.get(os.path.normcase(r["new_path"]), 0) + 1
    leaving = {os.path.normcase(r["path"]) for r in rows if r["status"] == "ok"}
    for r in rows:
        if r["status"] != "ok":
            continue
        key = os.path.normcase(r["new_path"])
        case_only = key == os.path.normcase(r["path"])
        if count[key] > 1:
            r["status"], r["note"] = "conflict", "duplicate target name"
        elif os.path.exists(r["new_path"]) and not case_only and key not in leaving:
            r["status"], r["note"] = "conflict", "a file with this name already exists"
    return rows


def summary(rows: list[dict]) -> dict:
    out = {"ok": 0, "unchanged": 0, "conflict": 0, "invalid": 0, "error": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


# ---------------------------------------------------------------- execution
def history_dir() -> str:
    from .automation import app_data_dir
    d = os.path.join(app_data_dir(), "rename-logs")
    os.makedirs(d, exist_ok=True)
    return d


def _depth(p):
    return os.path.normpath(p).count(os.sep)


def execute(rows: list[dict], journal: str | None = None, rules: list[dict] | None = None) -> dict:
    """Rename every row with status ok. Deepest paths first, so folders can be
    renamed together with their contents. Two phases (temporary names) so that
    swaps such as a->b, b->a work. On any error everything already done is
    rolled back. Returns {"done": [...], "error": str|None, "journal": path}."""
    todo = sorted((r for r in rows if r["status"] == "ok"), key=lambda r: -_depth(r["path"]))
    journal = journal or os.path.join(history_dir(), _dt.datetime.now().strftime("rename-%Y%m%d-%H%M%S-")
                                      + uuid.uuid4().hex[:6] + ".json")
    state = {"time": _dt.datetime.now().isoformat(timespec="seconds"), "status": "running",
             "rules": rules or [], "planned": [{"from": r["path"], "to": r["new_path"]} for r in todo], "done": []}

    def save():
        tmp = journal + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
        os.replace(tmp, journal)

    save()
    done: list[dict] = []          # completed renames, in order (for rollback)
    error = None
    tag = uuid.uuid4().hex[:8]
    # group by depth so a folder is renamed only after everything inside it
    by_depth: dict[int, list[dict]] = {}
    for r in todo:
        by_depth.setdefault(_depth(r["path"]), []).append(r)
    try:
        for d in sorted(by_depth, reverse=True):
            level = by_depth[d]
            temps = []
            for k, r in enumerate(level):
                tmp = os.path.join(os.path.dirname(r["path"]), f".nexus-{tag}-{d}-{k}.tmp")
                os.rename(r["path"], tmp)
                done.append({"from": r["path"], "to": tmp})
                temps.append((tmp, r))
            for tmp, r in temps:
                if os.path.exists(r["new_path"]) and os.path.normcase(r["new_path"]) != os.path.normcase(r["path"]):
                    raise FileExistsError(f"target appeared meanwhile: {r['new_path']}")
                os.rename(tmp, r["new_path"])
                done.append({"from": tmp, "to": r["new_path"]})
            state["done"] = _net(done)
            save()
    except OSError as ex:
        error = f"{type(ex).__name__}: {ex}"
        _rollback(done)
        done = []
        state["status"], state["error"], state["done"] = "rolled back", error, []
        save()
        return {"done": [], "error": error, "journal": journal}
    state["status"], state["done"] = "complete", _net(done)
    save()
    return {"done": state["done"], "error": None, "journal": journal}


def _net(steps: list[dict]) -> list[dict]:
    """Collapse old->tmp->new chains to old->new pairs."""
    via = {}
    out = []
    for s in steps:
        if os.path.basename(s["to"]).startswith(".nexus-"):
            via[s["to"]] = s["from"]
        else:
            out.append({"from": via.pop(s["from"], s["from"]), "to": s["to"]})
    return out


def _rollback(steps: list[dict]) -> int:
    n = 0
    for s in reversed(steps):
        try:
            if os.path.exists(s["to"]) and not os.path.exists(s["from"]):
                os.rename(s["to"], s["from"])
                n += 1
        except OSError:
            pass
    return n


def undo(journal: str) -> dict:
    """Reverse a completed (or interrupted) rename. Returns {"restored": n, "skipped": [...]}."""
    with open(journal, encoding="utf-8") as fh:
        state = json.load(fh)
    restored, skipped = 0, []
    steps = state.get("done", [])
    # parents were renamed last, so undo them first: their children's recorded paths then exist again
    for s in reversed(steps):
        if os.path.exists(s["to"]) and not os.path.exists(s["from"]):
            os.rename(s["to"], s["from"])
            restored += 1
        else:
            skipped.append(s["to"])
    # leftovers of an interrupted run
    for p in state.get("planned", []):
        d = os.path.dirname(p["from"])
        if os.path.isdir(d):
            for n in os.listdir(d):
                if n.startswith(".nexus-") and n.endswith(".tmp"):
                    skipped.append(os.path.join(d, n))
    state["status"] = "undone"
    with open(journal, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    return {"restored": restored, "skipped": sorted(set(skipped))}


def interrupted() -> list[str]:
    """Journals whose run never finished (e.g. the program was killed)."""
    out = []
    for n in sorted(os.listdir(history_dir())):
        p = os.path.join(history_dir(), n)
        if n.endswith(".json"):
            try:
                with open(p, encoding="utf-8") as fh:
                    if json.load(fh).get("status") == "running":
                        out.append(p)
            except (OSError, ValueError):
                continue
    return out


def history(limit: int = 100) -> list[dict]:
    """Newest first: {journal, time, status, count, first}."""
    out = []
    for n in sorted(os.listdir(history_dir()), reverse=True)[:limit]:
        p = os.path.join(history_dir(), n)
        if not n.endswith(".json"):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(st, list):            # logs written by Nexus 0.3
            st = {"time": "", "status": "complete", "done": st}
        done = st.get("done", [])
        out.append({"journal": p, "time": st.get("time", ""), "status": st.get("status", ""), "count": len(done),
                    "first": f"{os.path.basename(done[0]['from'])} → {os.path.basename(done[0]['to'])}" if done else ""})
    return out


# ---------------------------------------------------------------- profiles
def profile_dir() -> str:
    from .automation import app_data_dir
    d = os.path.join(app_data_dir(), "rename-profiles")
    os.makedirs(d, exist_ok=True)
    return d


def save_profile(name: str, rules: list[dict], directory: str | None = None) -> str:
    safe = re.sub(r"[^\w\- ]", "_", name).strip() or "profile"
    p = os.path.join(directory or profile_dir(), safe + ".json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"name": name, "rules": rules}, fh, indent=2, ensure_ascii=False)
    return p


def load_profile(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    rules = d["rules"] if isinstance(d, dict) else d
    for r in rules:
        if r.get("type") not in RULE_TYPES:
            raise ValueError(f"unknown rule type {r.get('type')!r} in {path}")
    return rules


def list_profiles(directory: str | None = None) -> list[str]:
    d = directory or profile_dir()
    return sorted(os.path.join(d, n) for n in os.listdir(d) if n.endswith(".json"))


def export_preview(rows: list[dict], path: str) -> None:
    """Write the before/after list to CSV or XLSX (for review or as a mapping list)."""
    header = ["Status", "Current name", "New name", "Folder", "Note"]
    data = [[r["status"], r["old"], r["new"], os.path.dirname(r["path"]), r["note"]] for r in rows]
    if path.lower().endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Rename preview"
        ws.append(header)
        for d in data:
            ws.append(d)
        wb.save(path)
    else:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(header)
            w.writerows(data)
