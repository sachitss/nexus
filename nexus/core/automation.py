"""Automation: reusable comparison jobs, folder snapshots and run history.

A job is a JSON file, e.g.

    {"name": "Daily project check", "type": "folder_compare",
     "left": "D:/Projects/A", "right": "\\\\server/Projects/A",
     "options": {"method": "content", "exclude": "*.tmp;~$*"},
     "report": "D:/Reports/{name}_{date}.html"}

Job types: folder_compare, file_compare, vdi_compare, validate_xml (folder of
XML files against an XSD), snapshot (detect new/changed/removed files since the
last run of the same job), batch (a list of other jobs).

Run from the command line (e.g. via Windows Task Scheduler):
    Nexus.exe --job daily.json
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from dataclasses import dataclass

from . import folderdiff, loaders, report, structdiff, tablediff, textdiff, vdi3805
from .ai import summarize_offline


def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "Nexus")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- history
class History:
    """SQLite log of every comparison/job run (auditable and reproducible:
    the options used are stored with each entry)."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(app_data_dir(), "history.sqlite")
        self.db = sqlite3.connect(self.path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY, time TEXT, kind TEXT, left TEXT, right TEXT,
            options TEXT, summary TEXT, report TEXT, identical INTEGER)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS snapshots(
            job TEXT, rel TEXT, size INTEGER, mtime REAL, hash TEXT, PRIMARY KEY(job, rel))""")
        self.db.commit()

    def add(self, kind, left, right, options=None, summary="", report_path="", identical=None) -> int:
        cur = self.db.execute("INSERT INTO runs(time, kind, left, right, options, summary, report, identical) VALUES (?,?,?,?,?,?,?,?)",
                              (_dt.datetime.now().isoformat(timespec="seconds"), kind, left, right,
                               json.dumps(options or {}, default=str), summary, report_path,
                               None if identical is None else int(identical)))
        self.db.commit()
        return cur.lastrowid

    def recent(self, limit=50) -> list[dict]:
        cur = self.db.execute("SELECT id, time, kind, left, right, summary, report, identical, options FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict:
        cur = self.db.execute("SELECT kind, COUNT(*) FROM runs GROUP BY kind")
        return dict(cur.fetchall())

    def close(self):
        self.db.close()


# ---------------------------------------------------------------- jobs
@dataclass
class JobResult:
    name: str
    ok: bool
    identical: bool | None
    summary: str
    report: str = ""


def _report_path(job: dict, ext: str) -> str:
    pat = job.get("report") or os.path.join(app_data_dir(), "reports", "{name}_{date}." + ext)
    path = pat.format(name="".join(c if c.isalnum() or c in "-_" else "_" for c in job.get("name", "job")),
                      date=_dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path


def run_job(job: dict, history: History | None = None) -> list[JobResult]:
    t = job.get("type")
    name = job.get("name", t or "job")
    opts = job.get("options", {})
    if t == "batch":
        out = []
        for sub in job.get("jobs", []):
            out.extend(run_job(sub, history))
        return out
    try:
        if t == "folder_compare":
            fo = folderdiff.FolderOptions(method=opts.get("method", "content"))
            if "exclude" in opts:
                fo.exclude = [p for p in opts["exclude"].split(";") if p]
            if "include" in opts:
                fo.include = [p for p in opts["include"].split(";") if p]
            root = folderdiff.compare_folders(job["left"], job["right"], fo)
            s = folderdiff.summary(root)
            rp = _report_path(job, "html")
            report.save(report.folder_report(root, job["left"], job["right"]), rp)
            res = JobResult(name, True, set(s) <= {"same"}, ", ".join(f"{v} {k}" for k, v in sorted(s.items())), rp)
        elif t == "file_compare":
            kind = loaders.compare_kind(job["left"], job["right"])
            rp = _report_path(job, "html")
            if kind in ("table",):
                l, r = loaders.load_table(job["left"])[0].rows, loaders.load_table(job["right"])[0].rows
                keys = [int(k) - 1 for k in str(opts.get("key", "")).split(",") if k.strip()]
                tr = tablediff.diff_tables(l, r, tablediff.TableOptions(key_columns=keys))
                report.save(report.table_report(tr, job["left"], job["right"]), rp)
                res = JobResult(name, True, tr.identical, summarize_offline("table", tr.stats()), rp)
            elif kind in ("xml", "json"):
                if kind == "xml":
                    l, r = structdiff.flatten_xml(job["left"]), structdiff.flatten_xml(job["right"])
                else:
                    l = structdiff.flatten_json(structdiff.load_json(job["left"]))
                    r = structdiff.flatten_json(structdiff.load_json(job["right"]))
                ch = structdiff.diff_maps(l, r)
                with open(rp, "w", encoding="utf-8") as fh:
                    fh.write(structure_report_html(ch, job["left"], job["right"]))
                res = JobResult(name, True, not ch, summarize_offline("xml", structdiff.summarize(ch)), rp)
            else:
                d = textdiff.diff_lines(loaders.load_as_lines(job["left"]), loaders.load_as_lines(job["right"]))
                report.save(report.text_report(d, job["left"], job["right"], only_diffs=True), rp)
                res = JobResult(name, True, d.identical, summarize_offline("text", d.stats()), rp)
        elif t == "vdi_compare":
            pp = opts.get("profile") or "auto"
            if pp == "auto":                       # profile of the part named in the 010 header, if installed
                pp = vdi3805.find_profile(job["left"]) or ""
            prof = vdi3805.Profile.load(pp) if pp else vdi3805.Profile()
            a, b = vdi3805.load(job["left"], prof), vdi3805.load(job["right"], prof)
            ch = vdi3805.compare(a, b)
            rp = _report_path(job, "xlsx")
            vdi3805.export_changes_xlsx(ch, vdi3805.validate(a), vdi3805.validate(b), rp, job["left"], job["right"])
            res = JobResult(name, True, not any(c.engineering for c in ch), summarize_offline("vdi", vdi3805.summary(ch)), rp)
        elif t == "validate_xml":
            folder, xsd = job["folder"], job.get("xsd")
            lines, bad, n = [], 0, 0
            for dirpath, _, names in os.walk(folder):
                for f in names:
                    if f.lower().endswith(".xml"):
                        n += 1
                        p = os.path.join(dirpath, f)
                        msgs = structdiff.validate_xml(p, xsd)
                        if msgs:
                            bad += 1
                            lines.append(f"{p}\n  " + "\n  ".join(msgs))
            rp = _report_path(job, "txt")
            with open(rp, "w", encoding="utf-8") as fh:
                fh.write(f"{n} XML file(s) checked, {bad} with problems\n\n" + "\n".join(lines))
            res = JobResult(name, bad == 0, bad == 0, f"{n} XML file(s) checked, {bad} with problems", rp)
        elif t == "snapshot":
            res = _snapshot_job(job, history or History())
        else:
            raise ValueError(f"Unknown job type: {t}")
    except Exception as ex:  # noqa: BLE001 - job errors are reported, not raised
        res = JobResult(name, False, None, f"ERROR: {ex}")
    if history:
        history.add(f"job:{t}", job.get("left") or job.get("folder", ""), job.get("right", ""), job, res.summary,
                    res.report, res.identical)
    return [res]


def _snapshot_job(job, history: History) -> JobResult:
    folder = job["folder"]
    name = job.get("name", folder)
    cur = {}
    for dirpath, _, names in os.walk(folder):
        for n in names:
            p = os.path.join(dirpath, n)
            st = os.stat(p)
            cur[os.path.relpath(p, folder)] = (st.st_size, st.st_mtime)
    prev = {r: (s, m, h) for r, s, m, h in history.db.execute("SELECT rel, size, mtime, hash FROM snapshots WHERE job=?", (name,))}
    new, changed, removed = [], [], []
    rows = []
    for rel, (size, mtime) in cur.items():
        old = prev.get(rel)
        h = None
        if old is None or old[0] != size or abs(old[1] - mtime) > 1:
            h = folderdiff.file_hash(os.path.join(folder, rel))
            if old is None:
                new.append(rel)
            elif old[2] != h:
                changed.append(rel)
        else:
            h = old[2]
        rows.append((name, rel, size, mtime, h))
    removed = [r for r in prev if r not in cur]
    history.db.execute("DELETE FROM snapshots WHERE job=?", (name,))
    history.db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?)", rows)
    history.db.commit()
    rp = _report_path(job, "txt")
    first = not prev
    with open(rp, "w", encoding="utf-8") as fh:
        if first:
            fh.write(f"First snapshot of {folder}: {len(cur)} file(s) recorded.\n")
        for title, lst in (("NEW", new), ("CHANGED", changed), ("REMOVED", removed)):
            if not first:
                fh.write(f"{title} ({len(lst)}):\n" + "".join(f"  {x}\n" for x in sorted(lst)))
    summ = (f"First snapshot: {len(cur)} file(s)" if first else
            f"{len(new)} new, {len(changed)} changed, {len(removed)} removed file(s)")
    return JobResult(name, True, first or not (new or changed or removed), summ, rp)


def structure_report_html(changes, left, right) -> str:
    import html
    rows = "".join(f"<tr class='{c.kind}'><td>{c.kind}</td><td>{html.escape(c.category)}</td><td>{html.escape(c.path)}</td>"
                   f"<td>{html.escape(c.left or '')}</td><td>{html.escape(c.right or '')}</td></tr>" for c in changes)
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Structure comparison</title><style>"
            "body{font-family:Segoe UI,Arial;margin:16px}table{border-collapse:collapse;width:100%;font-size:12px}"
            "td,th{border:1px solid #ddd;padding:2px 6px}th{background:#17233b;color:#fff}.added{background:#ddebf7}"
            ".removed{background:#fce4e4}.changed{background:#fff2cc}.equivalent{background:#e2efda}</style></head><body>"
            f"<h1>Structure comparison</h1><p>Left: {html.escape(left)}<br>Right: {html.escape(right)}</p>"
            f"<table><tr><th>Kind</th><th>Category</th><th>Path</th><th>Left</th><th>Right</th></tr>{rows}</table></body></html>")


def load_job(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def schtasks_command(job_path: str, exe: str, time: str = "07:00") -> str:
    """Command line that registers a daily Windows scheduled task for a job."""
    name = "Nexus - " + os.path.splitext(os.path.basename(job_path))[0]
    return f'schtasks /Create /SC DAILY /ST {time} /TN "{name}" /TR "\\"{exe}\\" --job \\"{job_path}\\""'
