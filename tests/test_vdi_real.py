"""VDI 3805 in the real Blatt 1 layout, and profiles built from guideline documents.

The guideline document used here is generated on the fly with python-docx and
only imitates the table layout of the VDI sheets (no VDI content)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus.core import engvalue, loaders, vdi3805, vdiguide  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(ROOT, "samples", "vdi3805")
V1, V2 = os.path.join(S, "PART06_MUSTER_2024.vdi"), os.path.join(S, "PART06_MUSTER_2025.vdi")


def _guideline(path, part="06"):
    """Write a small .docx with the header/field table layout of a VDI 3805 sheet."""
    import docx
    d = docx.Document()
    d.add_paragraph("Produktvariante A – Satzart 200 – Maximaler Betriebsdruck")

    def header(rt, name, lang="de"):
        t = d.add_table(rows=2, cols=4)
        t.cell(0, 0).text = f"VDI 3805 Blatt {int(part)}"
        t.cell(0, 1).text = "Satzbeschreibung" if lang == "de" else "Record description"
        if lang == "de":
            vals = [f"Dateiname: PART{part}_XXX.VDI", "Satzbezeichnung: Testprodukte", f"Satzname: {name}",
                    f"Satzart: PART{part[0]} {part[1]}-{rt}"]          # spaced number as in some drafts
        else:
            vals = [f"File name: PART{part}_XXX.VDI", "Record designation: Test products", f"Record name: {name}",
                    f"Record type: PART{part}-{rt}"]
        for i, v in enumerate(vals):
            t.cell(1, i).text = v

    def fields(rows, lang="de"):
        hdr = (["Lfd. Nr.", "Feldname/Feldbezeichnung", "Einheit", "Format", "Wertebereich/Bemerkungen"] if lang == "de"
               else ["S/N", "Field name/field designation", "Unit", "Format", "Range of values/comments"])
        t = d.add_table(rows=1 + len(rows), cols=5)
        for i, h in enumerate(hdr):
            t.cell(0, i).text = h
        for r, row in enumerate(rows, 1):
            for i, v in enumerate(row):
                t.cell(r, i).text = v

    header("700", "Grunddaten")
    fields([["1", "Satzart", "", "A3", "700"], ["2", "Index", "", "I", "1 bis 99999"],
            ["3", "Sortiernummer", "", "I", ""], ["4", "Bauhöhe a)", "mm", "N", ""], ["5", "Bautiefe", "mm", "N", ""],
            ["6", "Typ", "", "A", ""], ["7", "Registriernummer", "", "A10", ""]])
    header("700", "Basic data", "en")
    fields([["1", "Record type", "", "A3", ""], ["4", "Overall height", "mm", "N", ""]], "en")
    header("710.01", "Baulängen")
    fields([["1", "Satzart", "", "A6", ""], ["2", "Index", "", "I", ""], ["3", "Baulänge", "mm", "N", ""]])
    header("710.11", "Baulängenabhängiges Zubehör")
    fields([["1", "Satzart", "", "A6", ""], ["2", "Index", "", "I", ""], ["3–5", "Parameter 1–3", "mm", "T", ""],
            ["6 bis n", "Weitere Werte", "", "N", ""]])
    header("710.02", "Leistungsangaben")
    fields([["1", "Satzart", "", "A6", ""], ["2", "Index", "", "I", ""], ["3", "Vorlauftemperatur", "°C", "N", ""]])
    d.save(path)


# ---- loader and numbers
def test_loader_real_layout(tmp_path):
    p = tmp_path / "x.vdi"
    p.write_bytes("010;6;201507;A\\;B;\r\n710.01;1;400;\r\n\x1a".encode("cp1252"))
    rows = loaders.load_vdi3805(str(p)).rows
    assert rows[0][3] == "A;B" and rows[1][0] == "710.01" and len(rows) == 2
    q = tmp_path / "noext"
    q.write_bytes("010;6;201507;X;\n710.01;1;400;\n710.02;1;1;\n".encode("cp1252"))
    assert loaders.is_vdi3805(str(q))


def test_fortran_numbers():
    for s, v in (("0.", 0.0), ("+123000.", 123000.0), ("1.e5", 1e5), ("-5.0637", -5.0637)):
        assert engvalue.parse_number(s, "point") == (v, "")
    assert engvalue.compare_values("450.", "450", engvalue.EngRules(decimal_comma="point")) == engvalue.FORMAT_ONLY
    assert engvalue.compare_values("155", "155.0", engvalue.EngRules(decimal_comma="auto")) == engvalue.FORMAT_ONLY


# ---- guideline -> profile
def test_extract_guideline(tmp_path):
    g = tmp_path / "VDI_3805_06.docx"
    _guideline(str(g))
    d = vdiguide.extract(str(g))
    assert d["part"] == "6" and d["title"] == "Testprodukte"
    assert d["order"] == ["700", "710.01", "710.11", "710.02"]
    f700 = d["records"]["700"]["fields"]
    assert f700[4]["name"] == "Bauhöhe" and f700[4]["unit"] == "mm" and f700[4]["format"] == "N"
    assert f700[4]["name_en"] == "Overall height" and d["records"]["700"]["name_en"] == "Basic data"
    f711 = d["records"]["710.11"]["fields"]
    assert [f711[n]["name"] for n in (3, 4, 5)] == ["Parameter 1", "Parameter 2", "Parameter 3"]
    assert d["records"]["710.11"]["repeat_from"] == 6
    assert d["variants"]["200"] == ("Produktvariante A", "Maximaler Betriebsdruck")
    prof = vdiguide.to_profile([d])
    r = prof["records"]
    assert r["710.01"]["parent"] == "700" and r["710.11"]["parent"] == "710.01" and r["710.02"]["parent"] == "700"
    assert r["700"]["parent"] == "110" and r["200"]["name"] == "Produktvariante A: Maximaler Betriebsdruck"
    assert r["700"]["fields"][3]["role"] == "geometry" and r["700"]["fields"][3].get("numeric")
    assert prof["hierarchical_keys"] and prof["rules"]["decimal_comma"] == "point"
    assert vdiguide.profile_filename(prof) == "VDI3805-PART06.json"


def test_derived_parents_follow_document_order():
    known = {"710.01", "710.09", "710.10", "710.11", "710.91", "950.02", "950.21", "970.11", "970.01"}
    p = vdiguide.derive_parents(["710.09", "710.91", "710.10", "710.11"], known)
    assert p == {"710.09": "700", "710.91": "710.09", "710.10": "700", "710.11": "700"}
    p = vdiguide.derive_parents(["710.01", "710.11"], known)
    assert p["710.11"] == "710.01"
    p = vdiguide.derive_parents(["970", "970.11", "970.01", "950", "950.02", "950.21"], known | {"970", "950"})
    assert p["970.11"] == "970" and p["950.21"] == "950.02"


# ---- engine on the real layout
def _profile(tmp_path):
    g = tmp_path / "g.docx"
    _guideline(str(g))
    out = tmp_path / "VDI3805-PART06.json"
    vdiguide.build(None, [str(g)], str(out))
    return vdi3805.Profile.load(str(out))


def test_hierarchical_keys_and_structure(tmp_path):
    prof = _profile(tmp_path)
    ds = vdi3805.load(V1, prof)
    assert ds.hierarchical
    keys = [vdi3805.key_text(ds.key(r)) for r in ds.records if r.rtype == "710.01"]
    assert keys[0] == "100:1 / 110:1 / 700:1 / 710.01:1" and keys[2] == "100:1 / 110:1 / 700:2 / 710.01:1"
    assert len(set(keys)) == len(keys)                 # indices restart below each 700
    tree = vdi3805.build_structure(ds)
    p100 = [n for n in tree.children if n.record.rtype == "100"][0]
    t110 = p100.children[0]
    assert {c.record.rtype for c in t110.children} >= {"200", "300", "700"}
    e700 = [c for c in t110.children if c.record.rtype == "700"][0]
    assert {c.record.rtype for c in e700.children} == {"710.01", "710.02", "720"}


def test_compare_real_layout(tmp_path):
    prof = _profile(tmp_path)
    ch = vdi3805.compare(vdi3805.load(V1, prof), vdi3805.load(V2, prof), include_format=True)
    by = {(c.kind, c.rtype, c.field_no) for c in ch}
    assert ("record added", "710.01", None) in by            # 710.01:4 below the second 700 only
    assert ("value changed", "700", 5) in by                  # Bautiefe 100 -> 110 (geometry)
    assert ("format only", "710.02", 6) in by                 # 1.3 vs 1.30
    added = [c for c in ch if c.kind == "record added"]
    assert len(added) == 1 and added[0].key.endswith("700:2 / 710.01:4")
    assert vdi3805.summary(ch)["geometry changes"] == 1


def test_validation_formats_and_orphans(tmp_path):
    prof = _profile(tmp_path)
    recs = [vdi3805.Record(1, ["010", "6"]), vdi3805.Record(2, ["100", "1"]), vdi3805.Record(3, ["110", "1"]),
            vdi3805.Record(4, ["700", "1", "x", "12,5", "5", "T", "ABCDEFGHIJKL"]),
            vdi3805.Record(5, ["710.11", "1", "1", "2", "3", "4", "5", "abc"]),
            vdi3805.Record(6, ["710.01", "1", "400"])]
    ds = vdi3805.Dataset("x", recs, prof)
    vdi3805.prepare(ds)
    msgs = [i.message for i in vdi3805.validate(ds)]
    assert any("Sortiernummer" in m and "integer" in m for m in msgs)
    assert any("Bauhöhe" in m and "decimal point" in m for m in msgs)
    assert any("Registriernummer" in m and "A10" in m for m in msgs)
    assert any("orphan" in m and "710.01" in m for m in msgs)              # 710.11 before any 710.01
    assert any("Weitere Werte [3]" in m for m in msgs)                       # repeating field, 'abc' not numeric
    assert not any("fields, profile defines" in m for m in msgs)


def test_find_profile_by_header(tmp_path):
    (tmp_path / "VDI3805-PART06.json").write_text(json.dumps({"name": "p6"}), encoding="utf-8")
    (tmp_path / "VDI3805-PART01.json").write_text(json.dumps({"name": "p1"}), encoding="utf-8")
    assert vdi3805.header_part(V1) == "6"
    assert vdi3805.find_profile(V1, [str(tmp_path)]).endswith("PART06.json")
    other = tmp_path / "p4.vdi"
    other.write_text("010;4;201507;X;\n", encoding="cp1252")
    assert vdi3805.find_profile(str(other), [str(tmp_path)]).endswith("PART01.json")


def test_cli_build_profile_and_diff(tmp_path):
    g = tmp_path / "g.docx"
    _guideline(str(g))
    out = tmp_path / "p.json"
    env = dict(os.environ, APPDATA=str(tmp_path), XDG_DATA_HOME=str(tmp_path), HOME=str(tmp_path))
    r = subprocess.run([sys.executable, "-m", "nexus", "--build-profile", str(g), "-o", str(out)], cwd=ROOT,
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and out.exists() and "Part 6" in r.stdout
    r = subprocess.run([sys.executable, "-m", "nexus", "--diff", V1, V2], cwd=ROOT, capture_output=True, text=True,
                       env=env)
    assert r.returncode == 1 and "710.01:4" in r.stdout
