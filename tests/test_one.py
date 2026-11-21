"""NEXUS ONE 0.4: bulk rename engine, directory reports, duplicate finder, new job types."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus.core import automation, dirreport, duplicates, renamer  # noqa: E402


@pytest.fixture(autouse=True)
def _appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


def _files(d, names, text="x"):
    out = []
    for n in names:
        p = d / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text if not callable(text) else text(n), encoding="utf-8")
        out.append(str(p))
    return out


# ---------------------------------------------------------------- rename
def test_rule_order_and_tokens(tmp_path):
    ps = _files(tmp_path, ["Foto 1.JPG", "Foto 2.JPG"], lambda n: f"Titel: Pumpe {n[5]}\n")
    rules = [renamer.new_rule("replace", find="Foto ", replace="img_"),
             renamer.new_rule("case", mode="lower", extension="lower"),
             renamer.new_rule("numbering", position="prefix", digits=2, start=10, step=5),
             renamer.new_rule("insert", text="_{content:Titel: (\\w+)}", position=-1)]
    rows = renamer.preview(ps, rules)
    assert [r["new"] for r in rows] == ["10_img_1_Pumpe.jpg", "15_img_2_Pumpe.jpg"]
    rules[1]["enabled"] = False                       # unticked rules are skipped
    assert renamer.preview(ps, rules)[0]["new"] == "10_img_1_Pumpe.JPG"
    rows = renamer.preview(ps, [renamer.new_rule("add", prefix="{parent}-", suffix="_{num:4}")])
    assert rows[1]["new"] == f"{tmp_path.name}-Foto 2_0001.JPG"
    assert [renamer.format_number(n, 1, "roman") for n in (4, 9, 14)] == ["IV", "IX", "XIV"]
    assert renamer.format_number(28, 1, "alpha_upper") == "AB"


def test_remove_extension_date_and_per_folder_numbering(tmp_path):
    ps = _files(tmp_path, ["a/x  draft  01.txt", "a/y.txt", "b/z.txt"])
    r = renamer.preview(ps[:1], [renamer.new_rule("remove", last=3, digits=True, collapse=True)])[0]
    assert r["new"] == "x draft.txt"
    r = renamer.preview(ps[:1], [renamer.new_rule("extension", mode="set", value="md")])[0]
    assert r["new"].endswith(".md")
    r = renamer.preview(ps[1:2], [renamer.new_rule("date", source="modified", format="%Y")])[0]
    assert r["new"][:4].isdigit() and r["new"].endswith("_y.txt")
    rows = renamer.preview(ps, [renamer.new_rule("numbering", per_folder=True, digits=1)])
    assert [r["new"] for r in rows][1:] == ["y_2.txt", "z_1.txt"]


def test_conflicts_invalid_and_special_characters(tmp_path):
    ps = _files(tmp_path, ["a.txt", "b.txt", "c.txt", "Ünïcødé ä ß.txt"])
    rows = renamer.preview(ps[:2], [renamer.new_rule("template", template="same{ext}")])
    assert {r["status"] for r in rows} == {"conflict"}                       # duplicate destination
    rows = renamer.preview(ps[:1], [renamer.new_rule("template", template="c.txt")])
    assert rows[0]["status"] == "conflict" and "exists" in rows[0]["note"]   # existing file
    for bad in ("a:b.txt", "CON.txt", "name. ", ""):
        assert renamer.preview(ps[:1], [renamer.new_rule("template", template=bad)])[0]["status"] == "invalid"
    # a swap a->b, b->a is fine: both targets are being renamed away
    swap = [renamer.new_rule("mapping", file=_map(tmp_path, [("a.txt", "b.txt"), ("b.txt", "a.txt")]))]
    rows = renamer.preview(ps[:2], swap)
    assert [r["status"] for r in rows] == ["ok", "ok"]
    (tmp_path / "a.txt").write_text("A")
    res = renamer.execute(rows)
    assert res["error"] is None and (tmp_path / "b.txt").read_text() == "A"
    rows = renamer.preview([ps[3]], [renamer.new_rule("case", mode="upper")])
    assert rows[0]["new"] == "ÜNÏCØDÉ Ä SS.txt"
    renamer.execute(rows)
    assert (tmp_path / "ÜNÏCØDÉ Ä SS.txt").exists()


def _map(d, pairs, name="map.csv"):
    p = d / name
    p.write_text("old;new\n" + "".join(f"{a};{b}\n" for a, b in pairs), encoding="utf-8")
    return str(p)


def test_mapping_csv_and_excel(tmp_path):
    import openpyxl
    ps = _files(tmp_path / "f", ["P-001.dwg", "P-002.dwg", "other.dwg"])
    m = _map(tmp_path, [("P-001.dwg", "Grundriss EG"), ("P-002", "Schnitt A")])
    rows = renamer.preview(ps, [renamer.new_rule("mapping", file=m)])
    assert [r["new"] for r in rows] == ["Grundriss EG.dwg", "P-002.dwg", "other.dwg"]
    rows = renamer.preview(ps, [renamer.new_rule("mapping", file=m, match="stem")])
    assert rows[1]["new"] == "Schnitt A.dwg"
    x = tmp_path / "map.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["Alter Name", "Neuer Name"])
    wb.active.append(["other.dwg", "Ansicht Nord.dwg"])
    wb.save(x)
    assert renamer.preview(ps, [renamer.new_rule("mapping", file=str(x))])[2]["new"] == "Ansicht Nord.dwg"


def test_folders_with_contents_undo_and_history(tmp_path):
    ps = _files(tmp_path, ["Proj/a.txt", "Proj/sub/b.txt"])
    items = [str(tmp_path / "Proj"), ps[0], str(tmp_path / "Proj" / "sub"), ps[1]]
    rows = renamer.preview(items, [renamer.new_rule("add", prefix="X_")])
    res = renamer.execute(rows, rules=[{"type": "add"}])
    assert res["error"] is None and len(res["done"]) == 4
    assert (tmp_path / "X_Proj" / "X_sub" / "X_b.txt").exists()
    j = json.load(open(res["journal"], encoding="utf-8"))
    assert j["status"] == "complete" and j["rules"]
    assert renamer.history()[0]["count"] == 4
    u = renamer.undo(res["journal"])
    assert u["restored"] == 4 and (tmp_path / "Proj" / "sub" / "b.txt").exists()


def test_failure_rolls_back_everything(tmp_path, monkeypatch):
    ps = _files(tmp_path, ["1.txt", "2.txt", "3.txt"])
    rows = renamer.preview(ps, [renamer.new_rule("add", prefix="new_")])
    real = os.rename
    calls = {"n": 0}

    def flaky(a, b):            # simulates a locked file on the 5th rename call
        calls["n"] += 1
        if calls["n"] == 5:
            raise PermissionError("locked by another process")
        return real(a, b)

    monkeypatch.setattr(renamer.os, "rename", flaky)
    res = renamer.execute(rows)
    monkeypatch.setattr(renamer.os, "rename", real)
    assert res["error"] and "locked" in res["error"]
    assert sorted(os.listdir(tmp_path)) == ["1.txt", "2.txt", "3.txt", "appdata"]
    assert json.load(open(res["journal"], encoding="utf-8"))["status"] == "rolled back"


def test_interrupted_run_is_detected(tmp_path):
    ps = _files(tmp_path, ["a.txt"])
    j = os.path.join(renamer.history_dir(), "rename-x.json")
    new = str(tmp_path / "b.txt")
    os.rename(ps[0], new)                      # the program "died" after this rename
    with open(j, "w", encoding="utf-8") as fh:
        json.dump({"status": "running", "planned": [{"from": ps[0], "to": new}],
                   "done": [{"from": ps[0], "to": new}]}, fh)
    assert j in renamer.interrupted()
    assert renamer.undo(j)["restored"] == 1 and os.path.exists(ps[0])
    assert j not in renamer.interrupted()


def test_profiles_and_preview_export(tmp_path):
    rules = [renamer.new_rule("case", mode="upper")]
    p = renamer.save_profile("Großbuchstaben", rules)
    assert p in renamer.list_profiles() and renamer.load_profile(p) == rules
    ps = _files(tmp_path, ["a.txt"])
    renamer.export_preview(renamer.preview(ps, rules), str(tmp_path / "prev.csv"))
    assert "A.txt" in (tmp_path / "prev.csv").read_text(encoding="utf-8-sig")


def test_long_paths_are_flagged(tmp_path):
    ps = _files(tmp_path, ["a.txt"])
    r = renamer.preview(ps, [renamer.new_rule("template", template="n" * 250 + ".txt")])[0]
    assert r["status"] == "ok" and "long path" in r["note"]


# ---------------------------------------------------------------- directory reports
def _tree(tmp_path):
    _files(tmp_path / "root", ["a.pdf", "b.txt", "docs/c.pdf", "docs/deep/d.dwg"])
    (tmp_path / "root" / "empty").mkdir()
    (tmp_path / "root" / "b.txt").write_text("x" * 5000)
    return str(tmp_path / "root")


def test_report_filters_columns_groups_and_stats(tmp_path):
    root = _tree(tmp_path)
    o = dirreport.ReportOptions(columns=["rel", "size", "files", "empty"], group_by="ext", include_tree=True)
    rep = dirreport.scan_and_build(root, o)
    groups = dict(rep.groups)
    assert {r[0] for r in groups[".pdf"]} == {"a.pdf", os.path.join("docs", "c.pdf")}
    assert rep.stats["files"] == 4 and rep.stats["empty_folders"] == ["empty"]
    docs = [r for r in groups["(folder)"] if r[0] == "docs"][0]
    assert docs[2] == 2 and docs[1] == 2                  # recursive file count and bytes of docs/
    assert "└── " in rep.tree and "deep/" in rep.tree
    o = dirreport.ReportOptions(name_pattern="*.pdf;*.dwg", folders=False, sort_by="name", descending=True)
    rep = dirreport.scan_and_build(root, o)
    assert [os.path.basename(f.path) for f in rep.items] == ["d.dwg", "c.pdf", "a.pdf"]
    rep = dirreport.scan_and_build(root, dirreport.ReportOptions(min_size=1024, folders=False))
    assert [f.name for f in rep.items] == ["b.txt"]
    rep = dirreport.scan_and_build(root, dirreport.ReportOptions(only_empty_folders=True))
    assert [f.name for f in rep.items] == ["empty"]
    rep = dirreport.scan_and_build(root, dirreport.ReportOptions(name_regex=r"^[cd]\.", folders=False,
                                                                 modified_from="2000-01-01"))
    assert sorted(f.name for f in rep.items) == ["c.pdf", "d.dwg"]
    rep = dirreport.scan_and_build(root, dirreport.ReportOptions(modified_to="2000-01-01"))
    assert rep.items == []


def test_report_exports_all_formats(tmp_path):
    root = _tree(tmp_path)
    rep = dirreport.scan_and_build(root, dirreport.ReportOptions(columns=["rel", "size_h", "sha256"], include_tree=True,
                                                                 header_text="Projekt 4711 – Übergabe"))
    for fmt in dirreport.FORMATS:
        p = dirreport.export(rep, str(tmp_path / f"r.{fmt}"), branding="Powered by test")
        assert os.path.getsize(p) > 100, fmt
    assert open(tmp_path / "r.pdf", "rb").read(5) == b"%PDF-"
    j = json.load(open(tmp_path / "r.json", encoding="utf-8"))
    assert j["statistics"]["files"] == 4 and "SHA-256" in j["columns"]
    import xml.etree.ElementTree as ET
    assert len(ET.parse(tmp_path / "r.xml").getroot().findall(".//entry")) == len(rep.items)
    assert "| Relative path |" in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "Projekt 4711 – Übergabe" in (tmp_path / "r.html").read_text(encoding="utf-8")
    import openpyxl
    assert {"Listing", "Statistics", "Empty folders", "Tree"} <= set(openpyxl.load_workbook(tmp_path / "r.xlsx").sheetnames)


def test_report_templates(tmp_path):
    o = dirreport.ReportOptions(title="Lieferung", columns=["name", "sha256"], group_by="folder")
    p = dirreport.save_template(o, "Lieferliste")
    assert dirreport.load_template(p) == o


# ---------------------------------------------------------------- duplicates
def _dups(tmp_path):
    body = "\n".join(f"line {i} value {i * 3}" for i in range(300))
    _files(tmp_path / "d", ["A/report.txt", "B/report.txt", "B/Bericht Kopie.txt"], body)
    _files(tmp_path / "d", ["A/notes.txt"], body.replace("line 7 ", "line seven "))
    _files(tmp_path / "d", ["A/Plan_v1.pdf", "A/Plan_v2.pdf", "A/Plan - Kopie.pdf", "A/Plan (1).pdf", "A/Other.pdf"],
           lambda n: n)
    return str(tmp_path / "d")


def test_duplicate_analysis(tmp_path):
    res = duplicates.analyse([_dups(tmp_path)])
    assert res.files == 9
    assert len(res.identical) == 1 and len(res.identical[0]["paths"]) == 3
    assert res.wasted == 2 * res.identical[0]["size"]
    assert [g["name"] for g in res.same_name] == ["report.txt"]
    assert len(res.renamed_copies) == 1                    # same content, different names
    assert len(res.near) == 1 and res.near[0]["similarity"] > 0.99   # one representative per identical group
    assert [v["series"] for v in res.versions] == ["plan.pdf"] and len(res.versions[0]["files"]) == 4
    assert sum(f["newest"] for f in res.versions[0]["files"]) == 1
    assert res.by_ext[0][0] in (".txt", ".pdf")
    for ext in ("xlsx", "csv"):
        assert os.path.getsize(duplicates.export(res, str(tmp_path / f"d.{ext}"))) > 100
    assert os.path.exists(os.path.join(_dups(tmp_path), "A", "Plan_v1.pdf"))     # nothing deleted


def test_series_key():
    assert duplicates.series_key("Plan_v3 - Kopie.pdf") == "plan.pdf"
    assert duplicates.series_key("Bericht 01.03.2025.docx") == "bericht.docx"
    assert duplicates.series_key("Angebot_2025-11-21_final.xlsx") == "angebot.xlsx"
    assert duplicates.series_key("final.txt") == "final.txt"


# ---------------------------------------------------------------- jobs
def test_new_job_types(tmp_path):
    root = _tree(tmp_path)
    out = str(tmp_path / "out" / "{name}.html")
    r = automation.run_job({"name": "list", "type": "dir_report", "folder": root, "report": out,
                            "options": {"format": "html", "include_tree": True}})[0]
    assert r.ok and r.report.endswith(".html") and "4 files" in r.summary
    r = automation.run_job({"name": "dups", "type": "duplicates", "folders": [_dups(tmp_path)],
                            "report": str(tmp_path / "out" / "dups.xlsx")})[0]
    assert r.ok and "1 identical group" in r.summary
    prof = renamer.save_profile("p", [renamer.new_rule("case", mode="upper")])
    r = automation.run_job({"name": "ren", "type": "rename_preview", "folder": root, "options": {"profile": prof},
                            "report": str(tmp_path / "out" / "ren.xlsx")})[0]
    assert r.ok and "2 would be renamed" in r.summary and os.path.exists(os.path.join(root, "a.pdf"))
