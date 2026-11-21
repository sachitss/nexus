"""Structural comparison of XML and JSON documents.

Both documents are flattened into ordered {path: value} maps. Children of the
same element are matched by a key attribute/field (id, name, ...) when one is
present, otherwise by tag name and occurrence number. This makes reordering of
keyed elements invisible while still reporting real additions/removals.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .engvalue import EngRules, compare_values, EQUAL, FORMAT_ONLY, EQUIVALENT

KEY_ATTRS = ("id", "ID", "Id", "key", "name", "Name", "code", "Code", "nr", "Nr", "uid", "guid", "GUID", "ref")

ADDED, REMOVED, CHANGED, EQUIV, FORMAT = "added", "removed", "changed", "equivalent", "format"


@dataclass
class Change:
    path: str
    kind: str              # added / removed / changed / equivalent / format
    left: str | None
    right: str | None
    category: str = ""     # structure / value / text / attribute

    def as_row(self):
        return [self.kind, self.category, self.path, self.left or "", self.right or ""]


# ------------------------------------------------------------------ XML
def _local(tag) -> str:
    if not isinstance(tag, str):
        return "#comment"
    return tag.rsplit("}", 1)[-1]


def flatten_xml(path_or_bytes, key_attrs=KEY_ATTRS, ignore_comments=True, ignore_ns=True) -> dict[str, str]:
    from lxml import etree

    parser = etree.XMLParser(remove_comments=ignore_comments, remove_blank_text=True, resolve_entities=False,
                             no_network=True, huge_tree=True)
    if isinstance(path_or_bytes, (bytes, bytearray)):
        root = etree.fromstring(path_or_bytes, parser)
    else:
        root = etree.parse(path_or_bytes, parser).getroot()
    out: dict[str, str] = {}

    def name(el):
        return _local(el.tag) if ignore_ns else str(el.tag)

    def walk(el, path):
        for k, v in el.attrib.items():
            out[f"{path}/@{_local(k) if ignore_ns else k}"] = v
        txt = (el.text or "").strip()
        if txt:
            out[f"{path}/#text"] = txt
        counts: dict[str, int] = {}
        for ch in el:
            if not isinstance(ch.tag, str):
                continue
            n = name(ch)
            key = next((f"{a}={ch.get(a)}" for a in key_attrs if ch.get(a) is not None), None)
            if key:
                seg = f"{n}[{key}]"
            else:
                counts[n] = counts.get(n, 0) + 1
                seg = f"{n}[{counts[n]}]"
            cp = f"{path}/{seg}"
            if not len(ch) and not ch.attrib and not (ch.text or "").strip():
                out[cp] = ""   # empty element still exists
            walk(ch, cp)

    walk(root, "/" + name(root))
    return out


# ------------------------------------------------------------------ JSON
def flatten_json(obj, key_fields=KEY_ATTRS) -> dict[str, str]:
    out: dict[str, str] = {}

    def walk(o, path):
        if isinstance(o, dict):
            if not o:
                out[path or "/"] = "{}"
            for k, v in o.items():
                walk(v, f"{path}/{k}")
        elif isinstance(o, list):
            if not o:
                out[path or "/"] = "[]"
            for i, v in enumerate(o):
                key = None
                if isinstance(v, dict):
                    key = next((f"{f}={v[f]}" for f in key_fields if f in v and not isinstance(v[f], (dict, list))), None)
                walk(v, f"{path}[{key or i}]")
        else:
            out[path or "/"] = json.dumps(o, ensure_ascii=False) if not isinstance(o, str) else o

    walk(obj, "")
    return out


def load_json(path):
    from .textio import read_text
    return json.loads(read_text(path).text)


# ------------------------------------------------------------------ diff
def diff_maps(left: dict[str, str], right: dict[str, str], rules: EngRules | None = None,
              show_format=False) -> list[Change]:
    rules = rules or EngRules()
    changes: list[Change] = []
    lkeys = list(left)
    rset = set(right)
    seen = set()

    def cat(path, a, b):
        if path.rsplit("/", 1)[-1].startswith("@"):
            return "attribute"
        return "value" if compare_values(a or "", "0") != "different" or _isnum(a) or _isnum(b) else "text"

    # walk in left order and insert right-only paths after their nearest left neighbour
    rorder = list(right)
    rpos = {p: i for i, p in enumerate(rorder)}
    emitted_right = set()
    for p in lkeys:
        if p in rset:
            seen.add(p)
            res = compare_values(left[p], right[p], rules)
            if res == EQUAL:
                pass
            elif res == FORMAT_ONLY:
                if show_format:
                    changes.append(Change(p, FORMAT, left[p], right[p], cat(p, left[p], right[p])))
            elif res == EQUIVALENT:
                changes.append(Change(p, EQUIV, left[p], right[p], cat(p, left[p], right[p])))
            else:
                changes.append(Change(p, CHANGED, left[p], right[p], cat(p, left[p], right[p])))
            # right-only paths that come right after this one
            i = rpos[p] + 1
            while i < len(rorder) and rorder[i] not in left:
                q = rorder[i]
                changes.append(Change(q, ADDED, None, right[q], "structure"))
                emitted_right.add(q)
                i += 1
        else:
            changes.append(Change(p, REMOVED, left[p], None, "structure"))
    for q in rorder:
        if q not in left and q not in emitted_right:
            changes.append(Change(q, ADDED, None, right[q], "structure"))
    return changes


def _isnum(s):
    from .engvalue import parse_number
    return bool(s) and parse_number(s) is not None


def collapse_structure(changes: list[Change]) -> list[Change]:
    """Fold added/removed descendants under the first added/removed element so a
    whole missing product variant shows up as one line (with a count)."""
    out: list[Change] = []
    block_prefix = None
    block_change = None
    count = 0
    for c in changes:
        if c.kind in (ADDED, REMOVED) and block_prefix and c.path.startswith(block_prefix) and c.kind == block_change.kind:
            count += 1
            continue
        if block_change is not None and count:
            block_change.right = block_change.right if block_change.kind == ADDED else block_change.right
            block_change.category = f"structure (+{count} nested)"
        block_prefix, block_change, count = None, None, 0
        if c.kind in (ADDED, REMOVED):
            base = c.path.rsplit("/@", 1)[0].rsplit("/#text", 1)[0]
            block_prefix = base + "/"
            block_change = Change(base, c.kind, c.left, c.right, "structure")
            out.append(block_change)
            if base != c.path:
                count += 1
        else:
            out.append(c)
    if block_change is not None and count:
        block_change.category = f"structure (+{count} nested)"
    return out


def summarize(changes: list[Change]) -> dict[str, int]:
    s: dict[str, int] = {}
    for c in changes:
        s[c.kind] = s.get(c.kind, 0) + 1
    return s


# ------------------------------------------------------------------ XSD validation
def validate_xml(xml_path: str, xsd_path: str | None = None) -> list[str]:
    """Well-formedness check and, if an XSD is given (or referenced via
    xsi:schemaLocation / noNamespaceSchemaLocation next to the file), schema validation.
    Returns a list of messages; empty = valid."""
    import os
    from lxml import etree

    msgs: list[str] = []
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    try:
        doc = etree.parse(xml_path, parser)
    except etree.XMLSyntaxError as ex:
        return [f"Not well-formed: {ex}"]
    if xsd_path is None:
        root = doc.getroot()
        xsi = "{http://www.w3.org/2001/XMLSchema-instance}"
        loc = root.get(xsi + "noNamespaceSchemaLocation")
        if not loc and root.get(xsi + "schemaLocation"):
            parts = root.get(xsi + "schemaLocation").split()
            loc = parts[1] if len(parts) > 1 else None
        if loc and not loc.startswith(("http:", "https:")):
            cand = os.path.join(os.path.dirname(os.path.abspath(xml_path)), loc)
            if os.path.exists(cand):
                xsd_path = cand
        if xsd_path is None:
            return ["Well-formed. No local XSD found or given, so no schema validation was done."] if loc else []
    try:
        schema = etree.XMLSchema(etree.parse(xsd_path, parser))
    except (etree.XMLSchemaParseError, etree.XMLSyntaxError, OSError) as ex:
        return [f"Schema could not be loaded ({xsd_path}): {ex}"]
    if not schema.validate(doc):
        for e in schema.error_log:
            msgs.append(f"line {e.line}: {e.message}")
    return msgs
