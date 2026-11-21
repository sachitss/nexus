import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus.core import loaders, textdiff, tablediff, folderdiff, report  # noqa: E402

S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


def test_text_diff():
    l = loaders.load_as_lines(f"{S}/code/left.py")
    r = loaders.load_as_lines(f"{S}/code/right.py")
    res = textdiff.diff_lines(l, r)
    st = res.stats()
    assert st["right"] >= 1 and st["changed"] >= 3
    ch = [x for x in res.rows if x.tag == "changed"]
    assert all(x.left_spans or x.right_spans for x in ch)
    assert textdiff.diff_lines(l, l).identical


def test_text_options():
    o = textdiff.DiffOptions(ignore_case=True, ignore_whitespace=True)
    assert textdiff.diff_lines(["Hello  World"], ["hello world"], o).identical
    assert not textdiff.diff_lines(["a"], ["b"]).identical


def test_csv_key_compare():
    l = loaders.load_table(f"{S}/csv/left.csv")[0]
    r = loaders.load_table(f"{S}/csv/right.csv")[0]
    assert l.rows[1][1] == "Heizkörper Typ 22"   # cp1252 decoded
    res = tablediff.diff_tables(l.rows, r.rows, tablediff.TableOptions(key_columns=[0]))
    st = res.stats()
    assert (st["changed"], st["left"], st["right"], st["equal"]) == (2, 1, 1, 1)


def test_numeric_tolerance():
    o = tablediff.TableOptions(header_row=False, numeric_tolerance=0.01)
    assert tablediff.diff_tables([["1,00"]], [["1.001"]], o).identical


def test_xlsx():
    l = loaders.load_table(f"{S}/excel/left.xlsx")
    r = loaders.load_table(f"{S}/excel/right.xlsx")
    assert [s.name for s in l] == ["Products", "Notes"]
    res = tablediff.diff_tables(l[0].rows, r[0].rows, tablediff.TableOptions(key_columns=[0]))
    assert res.stats()["changed"] == 1


def test_column_reorder():
    l = [["a", "b"], ["1", "2"]]
    r = [["b", "a"], ["2", "1"]]
    assert all(x.tag == "equal" for x in tablediff.diff_tables(l, r).rows)


def test_docx():
    lines = loaders.docx_to_lines(f"{S}/word/left.docx")
    assert any("Kessel" in x for x in lines)
    assert loaders.compare_kind(f"{S}/word/left.docx", f"{S}/word/right.docx") == "word"


def test_vdi():
    p = f"{S}/vdi3805/left.002"
    assert loaders.is_vdi3805(p)
    assert not loaders.is_vdi3805(f"{S}/code/left.py")
    sh = loaders.load_vdi3805(p)
    assert sh.rows[0][0] == "010"
    r = loaders.load_vdi3805(f"{S}/vdi3805/right.002")
    res = tablediff.diff_tables(sh.rows, r.rows, tablediff.TableOptions(header_row=False, key_columns=[0, 1, 2, 3]))
    st = res.stats()
    assert st["left"] == 1 and st["right"] == 1 and st["changed"] >= 2


def test_folder():
    root = folderdiff.compare_folders(f"{S}/folders/left", f"{S}/folders/right")
    s = folderdiff.summary(root)
    assert s.get("left") == 3 and s.get("right") == 1 and s.get("same") == 2 and s.get("renamed") == 2
    assert sum(v for k, v in s.items() if k.startswith("newer") or k == "different") == 1


def test_reports():
    res = textdiff.diff_lines(["a", "b"], ["a", "c"])
    assert "<table>" in report.text_report(res, "l", "r")
    t = tablediff.diff_tables([["h"], ["1"]], [["h"], ["2"]])
    assert "Table comparison" in report.table_report(t, "l", "r")
