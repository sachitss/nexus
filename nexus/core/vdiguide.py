"""Build VDI 3805 profiles from the guideline documents themselves.

The VDI 3805 sheets (Blatt 1 = basics, Blatt NN = product group) describe each
record type with the same two-part layout:

* a header table: "Dateiname: PARTNN_XXX.VDI | Satzbezeichnung: ... |
  Satzname: <record name> | Satzart: PARTNN-<record type>" (English twin:
  "Record name" / "Record type");
* one or more field tables: "Lfd. Nr. | Feldname/Feldbezeichnung | Merkmal-ID |
  Einheit | Format | Wertebereich/Bemerkungen" (English: "S/N | Field name ...").

This module reads those tables from a .docx with python-docx and produces a
profile (see vdi3805.Profile). Nothing is guessed silently: every record and
field in the profile comes from a table in the guideline, and the source file
is recorded. Only the parent/child hierarchy of record types is derived from
rules (Blatt 1 product structure, decimal sub-records, document order), and
the profile marks those parents as derived.

The guideline files are only read, never written.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re

RTYPE_RX = re.compile(r"PART(?:\d{2,3}|XX)\s*[-–]\s*(\d{3}(?:\.\d{2})?|XX0(?:\.01)?)", re.I)
VARIANT_RX = re.compile(r"^\s*([^\n–]{3,45}?)\s*[–-]\s*Satzart\s*(\d{3}(?:\.\d{2})?)\s*[–-]\s*(.{2,80}?)\s*$")
# record types that use the generic "XX0" layout of Blatt 1 (product properties)
XX0_TYPES = ["160", "200", "250", "260", "300", "350", "360", "400", "450", "460", "500", "550", "560"]
XX0_NAMES = {"160": "Zubehörhauptgruppe II", "200": "Produktvariante A", "250": "Variantenart A1",
             "260": "Variantenart A2", "300": "Produktvariante B", "350": "Variantenart B1",
             "360": "Variantenart B2", "400": "Produktvariante C", "450": "Variantenart C1",
             "460": "Variantenart C2", "500": "Produktvariante D", "550": "Variantenart D1",
             "560": "Variantenart D2"}
PART_RX = re.compile(r"PART(\d{2,3})", re.I)
NUM_FORMATS = {"I", "N", "R", "D"}

# Fixed parents of the main record types (VDI 3805 Blatt 1, product structure:
# TGA number AAA=100, BBB=110, DDD=200, EEE=250 ...; 700 product element data;
# 800 TGA number; 9xx accessories, media, geometry are top-level lists).
BASE_PARENTS = {
    "010": None, "100": None, "110": "100", "160": "110",
    "200": "110", "250": "200", "260": "200",
    "300": "110", "350": "300", "360": "300",
    "400": "110", "450": "400", "460": "400",
    "500": "110", "550": "500", "560": "500",
    "600": "110", "610": "600",
    "700": "110", "720": "700", "730": "700", "740": "700", "760": "700",
    "800": "110", "810": "800", "820": "800",
    "830": None, "900": None, "920": "900", "930": None, "940": None,
    "950": None, "960": None, "970": None,
}


def _cells(row) -> list[str]:
    out, prev = [], None
    for c in row.cells:
        if c._tc is prev:          # merged cells repeat the same element
            continue
        prev = c._tc
        out.append(c.text.strip())
    return out


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s.replace("\n", " ")).strip()
    s = re.sub(r"\s+[a-h]\)$", "", s)          # footnote markers "Sektionslänge a)"
    return s.strip(" :")


def _after(label_rx: str, text: str) -> str:
    m = re.search(label_rx + r"\s*:?\s*(.*)", text, re.S | re.I)
    return _clean(m.group(1)) if m else ""


def _field_numbers(s: str) -> list[int]:
    s = s.replace("–", "-").replace("—", "-").replace(" ", "")
    m = re.fullmatch(r"(\d+)(?:-(\d+))?\.?", s)
    if not m:
        return []
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else a
    return list(range(a, b + 1)) if 0 < a <= b < a + 60 else []


def _lang_of_header(h: str) -> str | None:
    h = h.lower().replace(" ", "")
    if h.startswith("lfd"):
        return "de"
    if h.startswith(("s/n", "no.", "serialno", "seq")):
        return "en"
    return None


def _col(hdr: list[str], *keys) -> int | None:
    for i, h in enumerate(hdr):
        hl = h.lower()
        if any(k in hl for k in keys):
            return i
    return None


def extract(path: str) -> dict:
    """Read one guideline .docx. Returns {"part", "title", "source", "records": {rtype: {...}}, "order": [...]}"""
    import docx  # python-docx

    d = docx.Document(path)
    part, title = "", ""
    records: dict[str, dict] = {}
    order: list[str] = []
    current: list[str] = []
    cur_lang = "de"
    for t in d.tables:
        if not t.rows:
            continue
        rows = [_cells(r) for r in t.rows]
        flat = re.sub(r"PART\s*(\d)\s*(\d)(\s*\d(?=\s*[-–]))?", lambda m: "PART" + m.group(1) + m.group(2) + (m.group(3) or "").strip(),
                      " | ".join(" | ".join(r) for r in rows[:3]))
        types = RTYPE_RX.findall(flat)
        if types and re.search(r"Satzart|Record type", flat, re.I) and not _lang_of_header(rows[0][0] if rows[0] else ""):
            # header table of one or more record types
            en = bool(re.search(r"Record type", flat, re.I)) and not re.search(r"Satzart", flat)
            cur_lang = "en" if en else "de"
            body = " | ".join(" | ".join(r) for r in rows[1:]) or flat
            name = _after(r"(?:Record name|Satzname)", body.split("|")[2] if body.count("|") >= 3 else body)
            name = re.split(r"\s*\|\s*", name)[0]
            desig = _after(r"(?:Record designation|Satzbezeichnung)", body.split("|")[1] if body.count("|") >= 2 else "")
            pm = PART_RX.search(flat)
            if pm and not part:
                part = pm.group(1).lstrip("0") or "0"
            if desig and not title and cur_lang == "de":
                title = desig
            current = []
            exp = []
            for rt in types:
                if rt.upper().startswith("XX0"):
                    sfx = rt[3:]
                    exp += [x + sfx for x in XX0_TYPES]
                else:
                    exp.append(rt)
            for rt in exp:
                rec = records.get(rt)
                if rec is None:
                    rec = records[rt] = {"name": "", "name_en": "", "fields": {}}
                    order.append(rt)
                if rt[:3] in XX0_NAMES and types[0].upper().startswith("XX0"):
                    nm0 = XX0_NAMES[rt[:3]] + (" – Querverweise Zubehör" if "." in rt else "")
                    rec["name"] = rec["name"] or nm0
                if cur_lang == "de" and not rec["name"]:
                    rec["name"] = name
                elif cur_lang == "en" and not rec["name_en"]:
                    rec["name_en"] = name
                current.append(rt)
            continue
        lang = _lang_of_header(rows[0][0]) if rows[0] else None
        if lang is None or not current:
            continue
        hdr = [h.lower() for h in rows[0]]
        c_name = _col(hdr, "feldname", "field name", "field designation")
        c_unit = _col(hdr, "einheit", "unit")
        c_fmt = _col(hdr, "format")
        c_rem = _col(hdr, "wertebereich", "range", "bemerk", "comment")
        c_id = _col(hdr, "merkmal", "feature", "property id")
        if c_name is None:
            continue
        for r in rows[1:]:
            if not r or len(r) <= c_name:
                continue
            nums = _field_numbers(r[0])
            if not nums:
                rep = re.fullmatch(r"(\d+)\s*(?:bis|to|[–-]|\+)\s*n|n", r[0].replace("\n", " ").strip(), re.I)
                if rep and current:
                    start = int(rep.group(1)) if rep.group(1) else None
                    for rt in current:
                        rec = records[rt]
                        st = start if start and not r[0].strip().startswith(str(start) + " +") else None
                        if st is None:   # "24 + n": the field after the last fixed one repeats
                            st = max(rec["fields"], default=0) + (0 if "+" not in r[0] else 0)
                        rec["repeat_from"] = rec.get("repeat_from") or st
                        rec["repeat_name"] = rec.get("repeat_name") or _clean(r[c_name])
                        g2 = lambda c: _clean(r[c]) if c is not None and c < len(r) else ""
                        rec["repeat_format"] = rec.get("repeat_format") or g2(c_fmt).replace(" ", "")
                        rec["repeat_unit"] = rec.get("repeat_unit") or g2(c_unit)
                continue
            fname = _clean(r[c_name])
            get = lambda c: _clean(r[c]) if c is not None and c < len(r) else ""
            unit, fmt, rem, fid = get(c_unit), get(c_fmt).replace(" ", ""), get(c_rem), get(c_id)
            for k, n in enumerate(nums):
                nm = fname if len(nums) == 1 else re.sub(r"\d+\s*[–-]\s*\d+", str(k + 1), fname, count=1)
                for rt in current:
                    f = records[rt]["fields"].setdefault(n, {"name": "", "name_en": "", "unit": "", "format": "",
                                                            "remarks": "", "feature_id": ""})
                    if lang == "de":
                        f["name"] = f["name"] or nm
                        f["remarks"] = f["remarks"] or rem[:200]
                    else:
                        f["name_en"] = f["name_en"] or nm
                    f["unit"] = f["unit"] or unit
                    f["format"] = f["format"] or fmt
                    f["feature_id"] = f["feature_id"] or fid
    # Blatt 1: "Satzarten 100 und 110" share one layout
    if "100" in records and "110" not in records and part in ("", "1"):
        records["110"] = {"name": "Produkthauptgruppe II", "name_en": "",
                          "fields": {k: dict(v) for k, v in records["100"]["fields"].items()}}
        order.insert(order.index("100") + 1, "110")
    # part-specific meaning of the selection records ("Produktvariante A – Satzart 200 – Gehäusebauformen")
    variants = {}
    texts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            prev = None
            for c in row.cells:
                if c._tc is not prev and "Satzart" in c.text:
                    texts.append(c.text)
                prev = c._tc
    head_only = re.compile(r"^\s*([^\n–]{3,45}?)\s*[–-]\s*Satzart\s*(\d{3}(?:\.\d{2})?)\s*[–-]?\s*$")
    for txt in texts:
        lines = [ln.replace("\xa0", " ") for ln in txt.replace("\t", " ").split("\n")]
        for i, line in enumerate(lines):
            m = VARIANT_RX.match(line)
            if m and m.group(2) not in variants:
                variants[m.group(2)] = (_clean(m.group(1)), _clean(m.group(3)))
                continue
            m = head_only.match(line)       # "Produkt-Hauptgruppe I – Satzart 100" + meaning on the next line
            if m and m.group(2) not in variants:
                nxt = next((x.strip() for x in lines[i + 1:i + 3] if x.strip()), "")
                if nxt and len(nxt) <= 80 and "Satzart" not in nxt and not nxt.endswith("."):
                    variants[m.group(2)] = (_clean(m.group(1)), _clean(nxt))
    return {"part": part, "title": title, "source": os.path.basename(path), "records": records, "order": order,
            "variants": variants}


# ---------------------------------------------------------------- hierarchy and roles
def _base(rt: str) -> str:
    return rt.split(".")[0]


def derive_parents(order: list[str], known: set[str]) -> dict[str, str | None]:
    """Parent record type for each type, using BASE_PARENTS and, for decimal
    sub-records NNN.ab, document order: NNN.ab (a, b > 0) belongs to NNN.0a
    when it is described right after NNN.0a (e.g. 710.11 after 710.01,
    950.21 after 950.02); otherwise it belongs to the main record NNN (or 700
    for the 710.xx group)."""
    out: dict[str, str | None] = {}
    group: dict[str, int | None] = {}
    for rt in order:
        if "." not in rt:
            out[rt] = BASE_PARENTS.get(rt, None)
            continue
        base, sub = rt.split(".")
        a, b = int(sub[0]), int(sub[1])
        main = base if base in known or base in BASE_PARENTS else _family_root(base)
        if a == 0:
            group[base] = b
            out[rt] = main
        elif b != 0 and group.get(base) == a and f"{base}.0{a}" in known:
            out[rt] = f"{base}.0{a}"
        else:
            group[base] = None
            out[rt] = main
    return out


def _family_root(base: str) -> str:
    """710 -> 700, 160 -> 160 ... (sub-record groups without a main record)."""
    if base.endswith("10") and base != "010":
        return base[:-2] + "00"
    return base


def role_for(rt: str, idx: int, f: dict) -> str:
    name = (f.get("name") or f.get("name_en") or "").lower()
    unit = (f.get("unit") or "").lower()
    if idx == 0 or (idx == 1 and name.startswith(("index", "lfd"))):
        return "identifier"
    if re.search(r"\bindex\b|verweis|zeiger|querverweis|anbindenummer|reference", name):
        return "reference"
    if rt.startswith(("970.01", "950")) or re.search(r"anschluss|connection|verbindungs", name):
        return "connection"
    if rt.startswith(("970.02", "970.03", "970.05", "970.06", "970.1")) or (
            unit in ("mm", "m", "°", "grad") and re.search(
                r"position|länge|hoehe|höhe|breite|tiefe|durchmesser|abmessung|abstand|bau|maß|mass|winkel|"
                r"length|height|width|depth|diameter|dimension|distance|angle|[xyz]\d?\b", name)):
        return "geometry"
    fmt = (f.get("format") or "").upper()
    if fmt.startswith("A") or fmt == "TA":
        return "text"
    return "parameter"


def to_profile(docs: list[dict], name: str | None = None) -> dict:
    """Merge extracted guideline documents (Blatt 1 first, then part sheets;
    later documents override earlier ones for the same record type)."""
    records: dict[str, dict] = {}
    parents: dict[str, str | None] = {}
    part, title = "", ""
    for d in docs:
        known = set(d["records"]) | set(records)
        parents.update(derive_parents(d["order"], known))
        for rt in d["order"]:
            records[rt] = d["records"][rt]
        if d["part"] and d["part"] != "1":
            part, title = d["part"], d["title"] or title
        for rt, (label, meaning) in d.get("variants", {}).items():
            if rt in records:
                records[rt] = dict(records[rt], name=f"{label}: {meaning}")
            else:
                records[rt] = {"name": f"{label}: {meaning}", "name_en": "", "fields": {}}
                parents.setdefault(rt, BASE_PARENTS.get(rt))
    prof_records = {}
    for rt in sorted(records, key=lambda r: float(r)):
        rec = records[rt]
        nmax = max(rec["fields"], default=0)
        fields = []
        for n in range(1, nmax + 1):
            f = rec["fields"].get(n, {"name": f"Field {n}"})
            fmt = (f.get("format") or "").upper()
            fd = {"name": f.get("name") or f.get("name_en") or f"Field {n}"}
            if f.get("name_en") and f.get("name_en") != fd["name"]:
                fd["name_en"] = f["name_en"]
            if f.get("unit"):
                fd["unit"] = f["unit"]
            if fmt:
                fd["format"] = fmt
            if fmt in NUM_FORMATS:
                fd["numeric"] = True
            role = role_for(rt, n - 1, f)
            if role:
                fd["role"] = role
            if n == 1 or (n == 2 and fd["name"].lower().startswith("index")):
                fd["mandatory"] = True
            if f.get("feature_id"):
                fd["feature_id"] = f["feature_id"]
            if f.get("remarks"):
                fd["remarks"] = f["remarks"]
            fields.append(fd)
        r = {"name": rec["name"] or rec["name_en"], "parent": parents.get(rt), "fields": fields}
        if rec.get("repeat_from"):
            r["repeat_from"] = rec["repeat_from"]
            r["repeat_name"] = rec.get("repeat_name", "")
            if rec.get("repeat_format"):
                r["repeat_format"] = rec["repeat_format"].upper()
            if rec.get("repeat_unit"):
                r["repeat_unit"] = rec["repeat_unit"]
        if rec.get("name_en") and rec["name_en"] != r["name"]:
            r["name_en"] = rec["name_en"]
        prof_records[rt] = r
    label = f"VDI 3805 Part {part}" + (f" – {title}" if title else "") if part else "VDI 3805 Part 1 (basics)"
    return {
        "name": name or label,
        "part": part or "1",
        "title": title,
        "hierarchical_keys": True,
        "default_key_fields": None,
        "rules": {"abs_tol": 0, "rel_tol": 0, "allow_unit_conversion": False, "decimal_comma": "point"},
        "generated": {"from": [d["source"] for d in docs], "on": _dt.date.today().isoformat(),
                      "note": "Built from the VDI guideline documents by Nexus. Record parents are derived "
                              "(Blatt 1 product structure and document order). Derived from licensed VDI "
                              "material: for internal use, do not redistribute."},
        "records": prof_records,
    }


def build(base_docx: str | None, part_docx: list[str], out_path: str | None = None, name: str | None = None) -> dict:
    docs = ([extract(base_docx)] if base_docx else []) + [extract(p) for p in part_docx]
    prof = to_profile(docs, name)
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(prof, fh, ensure_ascii=False, indent=1)
    return prof


def profile_filename(prof: dict) -> str:
    p = prof.get("part") or "1"
    return f"VDI3805-PART{int(p):02d}.json" if p.isdigit() else f"VDI3805-{p}.json"
