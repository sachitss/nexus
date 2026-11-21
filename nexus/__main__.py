"""Nexus entry point.

    python -m nexus                 open the editor
    python -m nexus FILE...         open files in the editor
    python -m nexus LEFT RIGHT -c   compare two files or folders
    python -m nexus --diff LEFT RIGHT   print a text diff to the console (no GUI)
    python -m nexus --build-profile BLATT1.docx PARTNN.docx [-o OUT.json]
                                    build a VDI 3805 profile from the guideline documents
"""
from __future__ import annotations

import argparse
import os
import sys


def _auto_key(l, r):
    """Use the first column as key when its values are unique on both sides."""
    for rows in (l, r):
        col = [x[0] for x in rows[1:] if x]
        if not col or len(set(col)) != len(col):
            return []
    return [0]


def _cli_diff(left, right, key=None) -> int:
    from nexus.core import loaders, tablediff, folderdiff
    from nexus.core.textdiff import unified_diff

    kind = loaders.compare_kind(left, right)
    if kind == "folder":
        root = folderdiff.compare_folders(left, right)
        for e in folderdiff.walk(root):
            if not e.is_dir and e.status != "same":
                print(f"{e.status:12} {e.rel}")
        return 0 if root.status == "same" else 1
    if kind == "vdi" and not key:
        from nexus.core import vdi3805
        pp = vdi3805.find_profile(left)
        prof = vdi3805.Profile.load(pp) if pp else vdi3805.Profile()
        a, b = vdi3805.load(left, prof), vdi3805.load(right, prof)
        changes = vdi3805.compare(a, b)
        print(f"# profile: {prof.name}" + (f" ({pp})" if pp else ""))
        for c in changes:
            fld = f" field {c.field_no} {c.field_name}" if c.field_no else ""
            print(f"{c.kind:15} {c.rtype:7} {c.key}{fld}: {c.left!r} -> {c.right!r}")
        for side, ds in (("left", a), ("right", b)):
            for i in vdi3805.validate(ds):
                if i.severity == "error":
                    print(f"! {side} line {i.line}: {i.rtype} {i.message}")
        return 1 if any(c.engineering for c in changes) else 0
    if kind in ("table", "vdi"):
        l = loaders.load_vdi3805(left).rows if kind == "vdi" else loaders.load_table(left)[0].rows
        r = loaders.load_vdi3805(right).rows if kind == "vdi" else loaders.load_table(right)[0].rows
        if kind == "vdi":
            opts = tablediff.TableOptions(header_row=False, key_columns=list(range(int(key) if key else 4)))
        else:
            keys = [int(k) - 1 for k in key.split(",")] if key else _auto_key(l, r)
            opts = tablediff.TableOptions(key_columns=keys)
        res = tablediff.diff_tables(l, r, opts)
        for row in res.rows:
            if row.tag == "equal":
                continue
            lv = [res.cell("left", row.left, u) for u in range(len(res.columns))]
            rv = [res.cell("right", row.right, u) for u in range(len(res.columns))]
            if row.tag == "changed":
                cols = ", ".join(f"{res.columns[u]}: {lv[u]!r} → {rv[u]!r}" for u in sorted(row.changed_cols))
                print(f"~ {';'.join(lv[:3])}…: {cols}")
            elif row.tag == "left":
                print(f"- {';'.join(lv)}")
            else:
                print(f"+ {';'.join(rv)}")
        return 0 if res.identical else 1
    out = unified_diff(loaders.load_as_lines(left), loaders.load_as_lines(right), left, right)
    print(out)
    return 1 if out else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nexus", description="Compare files and folders, edit text and code.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("-c", "--compare", action="store_true", help="compare the two given paths in the GUI")
    ap.add_argument("--diff", action="store_true", help="print differences to the console and exit")
    ap.add_argument("--key", help="--diff on tables: key column numbers, e.g. 1 or 1,2 (VDI 3805: number of key fields)")
    ap.add_argument("--no-session", action="store_true", help="do not reopen files from the last session")
    ap.add_argument("--job", help="run an automation job (JSON file) without the GUI and exit; "
                                  "exit code 0 = all identical, 1 = differences, 2 = errors")
    ap.add_argument("--build-profile", action="store_true",
                    help="build a VDI 3805 profile: first path = Blatt 1 (.docx), further paths = part sheets")
    ap.add_argument("-o", "--output", help="--build-profile: output file (default: the user profile folder)")
    args = ap.parse_args(argv)

    if args.build_profile:
        from nexus.core import vdiguide, vdi3805
        if not args.paths:
            ap.error("--build-profile needs at least the Blatt 1 .docx")
        prof = vdiguide.build(args.paths[0], args.paths[1:])
        out = args.output or os.path.join(vdi3805.user_profile_dir(), vdiguide.profile_filename(prof))
        vdiguide.build(args.paths[0], args.paths[1:], out)
        print(f"{prof['name']}: {len(prof['records'])} record types -> {out}")
        return 0

    if args.job:
        from nexus.core import automation
        hist = automation.History()
        results = automation.run_job(automation.load_job(args.job), hist)
        for r in results:
            print(f"{'OK ' if r.ok else 'ERR'} {r.name}: {r.summary}" + (f"  -> {r.report}" if r.report else ""))
        hist.close()
        if not all(r.ok for r in results):
            return 2
        return 0 if all(r.identical for r in results) else 1

    if args.diff:
        if len(args.paths) != 2:
            ap.error("--diff needs exactly two paths")
        return _cli_diff(*args.paths, key=args.key)

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QIcon
    from nexus.ui import theme
    from nexus.ui.mainwindow import MainWindow
    from nexus import APP_NAME

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    theme.apply_mode(app, theme.stored_mode(QSettings("Nexus", "Nexus")))
    win = MainWindow()
    if len(args.paths) == 2 and (args.compare or all(os.path.isdir(p) for p in args.paths)):
        win.open_compare(*args.paths)
    elif args.paths:
        for p in args.paths:
            win.open_file(p)
    elif not args.no_session:
        win.restore_session()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
