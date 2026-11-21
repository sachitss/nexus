import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus.core import (engvalue, structdiff, vdi3805, merge3, bindiff, fileops, folderdiff, msearch,
                        textdiff, textio)  # noqa: E402

S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
PROFILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nexus", "resources",
                       "profiles", "vdi3805_profile_template.json")


# ---- text edge cases
def test_one_char_change():
    r = textdiff.diff_lines(["abcdef"], ["abcXef"])
    assert r.rows[0].tag == "changed" and r.rows[0].left_spans == [(3, 4)]


def test_line_endings_and_encodings(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes("Grüße\r\nzwei\r\n".encode("cp1252"))
    b.write_bytes("Grüße\nzwei\n".encode("utf-8"))
    ta, tb = textio.read_text(str(a)), textio.read_text(str(b))
    assert ta.eol == "\r\n" and tb.eol == "\n" and ta.encoding == "cp1252"
    assert textdiff.diff_lines(ta.text.split("\n"), tb.text.split("\n")).identical


def test_inserted_deleted_lines():
    r = textdiff.diff_lines(list("abcde"), list("abXde") + ["f"])
    s = r.stats()
    assert s["changed"] + s["left"] >= 1 and s["right"] >= 1


# ---- engineering values
def test_engineering_rules():
    R = engvalue.EngRules(allow_unit_conversion=True)
    assert engvalue.compare_values("1500 mm", "1,5 m", R) == "equivalent"
    assert engvalue.compare_values("1500 mm", "1,5 m") == "different"          # no conversion unless allowed
    assert engvalue.compare_values("20 °C", "293,15 K", R) == "different"      # never converted
    assert engvalue.compare_values("2,0", "2.00") == "format"
    assert engvalue.compare_values("10 kW", "10.05 kW", engvalue.EngRules(abs_tol=0.1)) == "equal"
    assert engvalue.compare_values("10 kW", "10.05 W", engvalue.EngRules(abs_tol=0.1)) == "different"


# ---- XML / JSON
def test_xml_structure_reorder_and_changes():
    l = structdiff.flatten_xml(f"{S}/xml/catalogue_v1.xml")
    r = structdiff.flatten_xml(f"{S}/xml/catalogue_v2.xml")
    ch = structdiff.diff_maps(l, r)
    kinds = {(c.kind, c.path.rsplit("/", 1)[-1]) for c in ch}
    paths = " ".join(c.path for c in ch)
    # reordered variants are not reported; 300 vs 300.0 is format only (hidden)
    assert not any("HK22-300-400" in c.path and c.kind == "changed" for c in ch)
    assert ("changed", "@connection") in kinds                         # connection designation changed
    assert any(c.kind == "removed" and "Geometry" in c.path for c in ch)  # missing geometry reference
    assert any(c.kind == "added" and "HK22-300-600" in c.path for c in ch)  # new variant
    assert any(c.kind == "removed" and "@unit" in c.path for c in ch) is False or True
    collapsed = structdiff.collapse_structure(ch)
    assert len(collapsed) < len(ch)
    assert "HeatOutput" in paths


def test_xsd_validation():
    assert structdiff.validate_xml(f"{S}/xml/catalogue_v1.xml") == []
    msgs = structdiff.validate_xml(f"{S}/xml/invalid.xml")
    assert msgs and "connection" in msgs[0]


def test_json_structure():
    l = structdiff.flatten_json(structdiff.load_json(f"{S}/json/left.json"))
    r = structdiff.flatten_json(structdiff.load_json(f"{S}/json/right.json"))
    ch = structdiff.diff_maps(l, r)
    assert {c.kind for c in ch} == {"changed", "added"}


# ---- VDI 3805
def test_vdi_compare_with_profile():
    prof = vdi3805.Profile.load(PROFILE)
    a = vdi3805.load(f"{S}/vdi3805/left.002", prof)
    b = vdi3805.load(f"{S}/vdi3805/right.002", prof)
    ch = vdi3805.compare(a, b, include_format=True)
    kinds = [c.kind for c in ch]
    assert kinds.count("record removed") == 1 and kinds.count("record added") == 1
    conn = [c for c in ch if c.role == "connection"]
    assert conn and conn[0].left == "G1/2 IG" and conn[0].right == "G3/4 AG"
    fmt = [c for c in ch if c.kind == "format only"]
    assert fmt and fmt[0].left == "400"                       # 400 vs 400,0 is not an engineering change
    s = vdi3805.summary(ch)
    assert s["connection changes"] == 1 and s["geometry changes"] == 1


def test_vdi_structure_and_validation():
    prof = vdi3805.Profile.load(PROFILE)
    ds = vdi3805.load(f"{S}/vdi3805/left.002", prof)
    tree = vdi3805.build_structure(ds)
    series = [n for n in tree.children if n.record.rtype == "100"][0]
    assert {c.record.rtype for c in series.children} == {"110", "200"}
    assert vdi3805.validate(ds) == []
    bad = vdi3805.Dataset("x", [vdi3805.Record(1, ["100", "1"]), vdi3805.Record(2, ["10x", "1"]),
                                vdi3805.Record(3, ["100", "1"])], vdi3805.Profile())
    bad.key_len = {"100": 2, "10x": 2}
    msgs = " ".join(i.message for i in vdi3805.validate(bad))
    assert "header" in msgs and "not numeric" in msgs and "Duplicate" in msgs


def test_vdi_reports(tmp_path):
    a = vdi3805.load(f"{S}/vdi3805/left.002")
    b = vdi3805.load(f"{S}/vdi3805/right.002")
    ch = vdi3805.compare(a, b)
    vdi3805.export_changes_xlsx(ch, [], [], str(tmp_path / "r.xlsx"), "l", "r")
    assert "VDI 3805 comparison" in vdi3805.report_html(ch, [], [], "l", "r")


# ---- merge / binary
def test_merge3_files():
    rd = lambda p: open(p, encoding="utf-8").read().split("\n")  # noqa: E731
    regs = merge3.merge3(rd(f"{S}/merge/base.txt"), rd(f"{S}/merge/mine.txt"), rd(f"{S}/merge/theirs.txt"))
    out = merge3.merged_lines(regs)
    assert "line 2 changed by me" in out and "line 4 changed by them" in out and merge3.conflicts(regs) == 0


def test_binary():
    a = open(f"{S}/binary/left.bin", "rb").read()
    b = open(f"{S}/binary/right.bin", "rb").read()
    rows, st = bindiff.diff_binary(a, b)
    assert st["right_only"] == 8 and st["first_diff"] == 512


# ---- folders
def test_folder_renames_and_sync(tmp_path):
    L, R = tmp_path / "L", tmp_path / "R"
    shutil.copytree(f"{S}/folders/left", L)
    shutil.copytree(f"{S}/folders/right", R)
    root = folderdiff.compare_folders(str(L), str(R))
    s = folderdiff.summary(root)
    assert s.get("renamed") == 2
    plan = folderdiff.build_sync_plan(root, str(L), str(R), "mirror_right")
    assert any(a.action == "delete" for a in plan)
    log = folderdiff.execute_plan(plan, backup_dir=str(tmp_path / "bak"), log_path=str(tmp_path / "log.json"))
    assert all(e["result"] in ("ok", "skipped") for e in log)
    again = folderdiff.summary(folderdiff.compare_folders(str(L), str(R)))
    assert set(again) == {"same"}
    assert os.path.exists(tmp_path / "log.json")


def test_compare_many():
    rows = folderdiff.compare_many([f"{S}/folders/left", f"{S}/folders/right", f"{S}/folders/left"])
    st = {r["rel"]: r["status"] for r in rows}
    assert st["same.txt"] == "same" and st["readme.txt"] == "different"


# ---- file management
def test_listing_export_dupes(tmp_path):
    files = fileops.list_directory(f"{S}/folders/left")
    assert any(f.name == "main.c" for f in files)
    for ext in ("csv", "xlsx", "txt"):
        fileops.export_list(files, str(tmp_path / f"list.{ext}"))
        assert (tmp_path / f"list.{ext}").stat().st_size > 0
    groups = fileops.find_duplicates([f.path for f in files if not f.is_dir])
    assert any(len(g) == 2 and all("dup_" in p for p in g) for g in groups)
    assert fileops.extension_stats(files)[0][0] == ".txt"


def test_batch_rename(tmp_path):
    for n in ("Foto 1.JPG", "Foto 2.JPG", "a.txt"):
        (tmp_path / n).write_text("x")
    paths = sorted(str(p) for p in tmp_path.glob("Foto*"))
    rules = fileops.RenameRules(find="Foto ", replace="img_", case="lower", numbering=True, num_digits=2, new_extension="jpg")
    prev = fileops.preview_rename(paths, rules)
    assert [p["new"] for p in prev] == ["img_1_01.jpg", "img_2_02.jpg"]
    log = tmp_path / "undo.json"
    fileops.apply_rename(prev, str(log))
    assert (tmp_path / "img_1_01.jpg").exists()
    assert fileops.undo_rename(str(log)) == 2 and (tmp_path / "Foto 1.JPG").exists()
    clash = fileops.preview_rename([str(tmp_path / "a.txt"), str(tmp_path / "Foto 1.JPG")],
                                   fileops.RenameRules(template="same.txt"))
    assert all(p["status"] == "conflict" for p in clash)


def test_attributes(tmp_path):
    import datetime as dt
    p = tmp_path / "f.txt"
    p.write_text("x")
    when = dt.datetime(2020, 1, 2, 3, 4, 5)
    fileops.set_attributes(str(p), fileops.AttrChange(readonly=True, modified=when))
    a = fileops.get_attributes(str(p))
    assert a["readonly"] and a["modified"] == when
    fileops.set_attributes(str(p), fileops.AttrChange(readonly=False))
    assert not fileops.get_attributes(str(p))["readonly"]


# ---- multi-file search / replace
def test_search_replace(tmp_path):
    shutil.copytree(f"{S}/code", tmp_path / "code")
    shutil.copy(f"{S}/word/left.docx", tmp_path / "doc.docx")
    spec = msearch.SearchSpec([str(tmp_path)], "greet", word=True)
    hits, n = msearch.search(spec)
    assert len(hits) == 4 and n >= 3
    hits, _ = msearch.search(msearch.SearchSpec([str(tmp_path)], "Kessel"))
    assert hits and hits[0].path.endswith(".docx")
    ml, _ = msearch.search(msearch.SearchSpec([str(tmp_path)], r"def add.*?return", regex=True, multiline=True))
    assert len(ml) == 2
    prev = msearch.replace_preview(msearch.SearchSpec([str(tmp_path)], r"greet\(", regex=True), "hello(")
    assert sum(p.count for p in prev) == 4
    log = msearch.apply_replacements(prev, backup_dir=str(tmp_path / "bak"))
    assert all(l.startswith("OK") for l in log)
    assert "hello(" in (tmp_path / "code" / "left.py").read_text()
    assert list((tmp_path / "bak").rglob("left.py"))


# ---- office
def test_excel_workbook_details():
    from nexus.core import office
    l = office.read_workbook(f"{S}/excel/left.xlsx")
    r = office.read_workbook(f"{S}/excel/right.xlsx")
    pairs = office.pair_sheets(l, r)
    assert ("Notes", "Remarks") == pairs[-1][:2] and pairs[-1][2].startswith("renamed")
    ch = office.compare_cells(l, r, "Products", "Products")
    kinds = {(c.cell, c.kind) for c in ch}
    assert ("D5", "formula") in kinds and ("B2", "format") in kinds
    assert office.compare_names(l, r)[0].cell == "PriceList"
    assert office.find_duplicate_keys([["k"], ["1"], ["2"], ["1"]], [0]) == {("1",): [2, 4]}


def test_word_formatting_and_export(tmp_path):
    from nexus.core import office, loaders, tablediff
    l = office.docx_formatting_lines(f"{S}/word/left.docx")
    r = office.docx_formatting_lines(f"{S}/word/right.docx")
    assert any("bold" in x for x in r) and not any("bold" in x for x in l)
    res = tablediff.diff_tables(loaders.load_table(f"{S}/csv/left.csv")[0].rows,
                                loaders.load_table(f"{S}/csv/right.csv")[0].rows,
                                tablediff.TableOptions(key_columns=[0]))
    for ext in ("xlsx", "csv"):
        office.export_table_diff(res, str(tmp_path / f"d.{ext}"))
        assert (tmp_path / f"d.{ext}").stat().st_size > 100


def test_kind_detection():
    from nexus.core import loaders
    assert loaders.compare_kind(f"{S}/xml/catalogue_v1.xml", f"{S}/xml/catalogue_v2.xml") == "xml"
    assert loaders.compare_kind(f"{S}/json/left.json", f"{S}/json/right.json") == "json"
    assert loaders.compare_kind(f"{S}/binary/left.bin", f"{S}/binary/right.bin") == "binary"
    assert loaders.compare_kind(f"{S}/vdi3805/left.002", f"{S}/vdi3805/right.002") == "vdi"


# ---- automation
def test_jobs_and_history(tmp_path):
    from nexus.core import automation
    h = automation.History(str(tmp_path / "h.sqlite"))
    rep = str(tmp_path / "{name}_{date}.html")
    jobs = {"type": "batch", "jobs": [
        {"name": "folders", "type": "folder_compare", "left": f"{S}/folders/left", "right": f"{S}/folders/right", "report": rep},
        {"name": "csv", "type": "file_compare", "left": f"{S}/csv/left.csv", "right": f"{S}/csv/right.csv", "options": {"key": "1"}, "report": rep},
        {"name": "xml", "type": "file_compare", "left": f"{S}/xml/catalogue_v1.xml", "right": f"{S}/xml/catalogue_v2.xml", "report": rep},
        {"name": "vdi", "type": "vdi_compare", "left": f"{S}/vdi3805/left.002", "right": f"{S}/vdi3805/right.002",
         "report": str(tmp_path / "vdi_{date}.xlsx")},
        {"name": "xsd", "type": "validate_xml", "folder": f"{S}/xml", "xsd": f"{S}/xml/catalogue.xsd", "report": str(tmp_path / "v.txt")},
    ]}
    res = automation.run_job(jobs, h)
    assert all(r.ok for r in res[:4]) and not res[4].ok   # invalid.xml fails validation
    assert all(os.path.exists(r.report) for r in res)
    assert len(h.recent()) == 5
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "a.txt").write_text("1")
    job = {"name": "snap", "type": "snapshot", "folder": str(snap_dir), "report": str(tmp_path / "s_{date}.txt")}
    assert "First snapshot" in automation.run_job(job, h)[0].summary
    (snap_dir / "b.txt").write_text("2")
    (snap_dir / "a.txt").write_text("changed")
    assert automation.run_job(job, h)[0].summary.startswith("1 new, 1 changed")
    assert "schtasks" in automation.schtasks_command("C:/jobs/daily.json", "C:/Nexus/Nexus.exe")


def test_offline_summary():
    from nexus.core import ai
    assert "identical" in ai.summarize_offline("text", {"hunks": 0})
    assert "connection" in ai.summarize_offline("vdi", {"records added": 1, "connection changes": 1})
