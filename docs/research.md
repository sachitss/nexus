# Research and competitive analysis

Nexus Compare & Editor combines four kinds of tool: a file and folder comparison tool, a programmer's text editor, a directory lister and a file attribute changer. It also adds engineering data comparison, with VDI 3805 as the top priority.

**Method.** The installers and archives supplied with the brief were Beyond Compare 5.1, Notepad++ 8.4.9, DirectoryListPrint portable and File Attribute Changer x64. They were used only to learn which features matter. None of them was run, unpacked, decompiled or copied. Their names, artwork and layout are not reused. Feature knowledge comes from the products' public documentation and general domain knowledge. Nothing in Nexus is derived from their code or resources.

## 1. Feature matrix

✔ = present · ◐ = partial / basic · – = absent. The Nexus column shows the state of v0.3.0.

| Capability | Beyond Compare | Notepad++ | Directory list tools | Attribute changer | **Nexus 0.3** |
|---|---|---|---|---|---|
| Folder compare (two-way) | ✔ | – | – | – | ✔ |
| Folder compare N-way (3+) | ◐ (3-way merge only) | – | – | – | ✔ (hash matrix) |
| Rename / move detection | ◐ | – | – | – | ✔ (size + SHA-256) |
| Checksum compare | ✔ (CRC/MD5/SHA) | – | – | – | ✔ (byte-exact and SHA-256) |
| Filters: wildcard, regex, size, date | ✔ | – | ◐ | – | ✔ |
| Sync with preview | ✔ | – | – | – | ✔ (5 modes, backups, JSON audit log) |
| Text diff with intra-line highlight | ✔ | plugin | – | – | ✔ (char or word) |
| Ignore whitespace, case, regex, comments | ✔ | – | – | – | ✔ |
| Three-way merge | ✔ (Pro) | – | – | – | ✔ |
| Table / CSV compare with key columns | ✔ | – | – | – | ✔ |
| Excel: formulas, formats, named ranges, renamed sheets | ◐ (values via conversion) | – | – | – | ✔ |
| Word: text, tables, formatting | ◐ (text via conversion) | – | – | – | ✔ (text and run formatting) |
| XML / JSON structural compare | ◐ (text rules) | plugin | – | – | ✔ (keyed elements) |
| XSD validation | – | plugin | – | – | ✔ |
| **VDI 3805 aware compare** | – | – | – | – | ✔ (records, keys, structure, rules) |
| Engineering units and tolerances | – | – | – | – | ✔ (explicit rules only) |
| Hex / binary compare | ✔ | plugin | – | – | ✔ |
| Code editor: highlighting for many languages | ◐ | ✔ (80+) | – | – | ✔ (500+ via Pygments) |
| Tabs, split view, bookmarks, folding | ◐ | ✔ | – | – | ✔ |
| Encodings and EOL conversion | ✔ | ✔ | – | – | ✔ |
| Find in files / replace in files | ◐ | ✔ | – | – | ✔ (with preview, backups, Office search) |
| Macro recording | – | ✔ | – | – | – (roadmap) |
| Plugin API | ◐ | ✔ | – | – | – (roadmap) |
| Directory listing and export (CSV, Excel, TXT tree) | ◐ | – | ✔ | – | ✔ |
| Extension statistics, folder sizes | – | – | ✔ | – | ✔ |
| Duplicate finder | – | – | ◐ | – | ✔ (size, head hash, full hash) |
| Batch rename with preview and undo | ◐ | – | – | – | ✔ |
| Attributes and timestamps (R/H/S/A, created/modified/accessed) | ◐ | – | – | ✔ | ✔ |
| Reports (HTML, Excel, CSV) | ✔ | – | ✔ | – | ✔ (PDF via browser print) |
| Scripting / scheduled jobs | ✔ (script language) | – | – | – | ✔ (JSON jobs, CLI, Windows Task Scheduler) |
| Run history / audit trail | – | – | – | – | ✔ (SQLite) |
| AI change summary | – | – | – | – | ✔ (optional, opt-in, off by default) |
| Ribbon UI, dark and light | – | – | – | – | ✔ |

### What each reference tool does best, and what Nexus takes from it

* **Beyond Compare** is the benchmark for comparison. It has a session concept, rule-based "unimportant" differences, alignment, folder sync and reports. Nexus keeps this workflow: open a pair, navigate the differences, copy blocks, sync with a preview. It goes further with structure-aware Office and XML comparison and engineering rules.
* **Notepad++** is the benchmark for editing. It is fast, uses tabs, and has folding, bookmarks, find in files, macros and plugins. Nexus covers the daily editing set. Macros and a plugin API are on the roadmap.
* **Directory list tools** list folders with sizes, dates and attributes, and export the result to Excel/CSV/TXT for documentation. Nexus has this under Files → Directory list, plus duplicates and statistics.
* **Attribute changers** set read-only/hidden/system/archive flags and the created, modified and accessed timestamps. Nexus has this under Files → Attributes, for many files at once and with confirmation.

### Gaps in the market that Nexus targets

1. **Engineering exchange data.** None of the reference tools understands VDI 3805 or ISO 16757. Their text compare reports "400" vs "400,0" as a difference. A reordered record shows up as a delete plus an insert. Nexus keys records, rebuilds the product structure and classifies each difference as *value*, *notation only* or *equivalent*.
2. **Office documents without Office.** Nexus reads DOCX/XLSX directly (formulas, formats, named ranges). It converts legacy DOC/XLS through LibreOffice or Microsoft Office when either is installed.
3. **Auditability.** Every comparison and job is logged with its options. Sync, replace and rename write backups and logs.

## 2. VDI 3805 and ISO 16757 findings

* **What it is.** VDI 3805 is the German guideline series for exchanging product data in building services: heating, ventilation, sanitary, valves, pumps and so on. Part 1 defines the basics and each later part defines one product group. Manufacturers publish catalogues that planning software imports.
* **File format (Blatt 1, Stand 2020-10).** The exchange files are ANSI text (ISO 8859-1), one record per line, fields separated by `;` and each record ends with `;` + CR LF. A `;` inside text is escaped as `\;`, and `¶` stands for a line break. Field 1 is the record type (`Satzart`, e.g. `010` header, `700` product element data, decimal sub-records such as `710.01` or `970.01`). Field 2 is a running **index**. Numbers use a **decimal point** and may be written Fortran style (`123.`, `1.23E5`). Field formats are `A`/`A<n>` (text, max. 512 or n characters), `I` (integer), `N`/`R` (number), `T` (formula term) and `TA` (text term).
* **Hierarchy.** The file order carries the product structure: 100 → 110 → variants 200–560, declarations 600/610, product elements 700 with their sub-records 710.xx/720/730, TGA numbers 800/810, and the top-level lists 900–970 (accessories, media, geometry). Indices restart below each parent (for example 700 below each 110, 710.01 below each 700). A record is therefore identified by the chain of (record type, index) from its top-level ancestor, for example `100:1 / 110:3 / 700:12 / 710.01:4`.
* **Profiles from the guidelines.** Each sheet describes every record type with a header table (Satzname, Satzart `PARTnn-xxx`) and field tables (Lfd. Nr., Feldname, Merkmal-ID, Einheit, Format, Wertebereich). Nexus reads these tables from the Word documents and builds a JSON profile per part: names (German and English), units, formats, roles, value ranges, repeating fields ("4 bis n"), the part-specific meaning of the variant records ("Produktvariante A – Satzart 200 – Maximaler Betriebsdruck") and the parent of each record type. Profiles were built from the committee documents for Blatt 1 and parts 2, 3, 4, 6, 8, 17, 18, 21, 24, 26, 27, 28, 29, 38, 44, 50, 60 and 62. They are derived from licensed VDI material and are kept out of the program package.
* **Tested on real catalogues.** Kermi Part 6 (90,490 records), a Siemens catalogue (158,779 records), the Blatt 50 controller sample and the Blatt 62 cable sample all load, validate and compare. Load plus validation takes about 3 s for the Kermi file. Real findings: 146 Kermi `700` records carry 38 fields where the Part 6 draft defines 37; one Siemens `900` record has text in the integer GTIN field; the Blatt 62 sample has an 8-digit date where `010` field 3 expects `JJJJMM`.
* **Without a profile** Nexus still recognises the real layout and uses the same hierarchical keys. For other files it falls back to the generic model: keys are the shortest unique prefix of fields per record type, and the parent is the closest preceding lower-type record whose index fields are a prefix of its own.
* **Successor.** ISO 16757 is the international successor ("Data structures for electronic product catalogues for building services"). It plans an XML/XSD-based exchange format (Part 5). Nexus already compares XML catalogues structurally, with keyed elements and the same engineering value rules, and validates them against an XSD.
* **Engineering rule** (from the brief): two engineering values are never treated as equivalent unless their units, precision and comparison rules have been established. Nexus follows this rule:
  * The result is *notation only* when the parsed numbers are identical, such as `400` and `400,0`.
  * The result is *equivalent* only when the profile or the user sets a tolerance or allows unit conversion. Conversion uses only an explicit unit table and never converts °C to K.
  * Everything else is a *value change*.

## 3. Assumptions to confirm

* Which draft of each part sheet is authoritative. The profiles use the newest Word draft in the committee folder (for example Part 28 "2021-03-17 spiess final"). Parts that exist only as PDF (5, 7, 40) or as legacy .doc (45) are not covered yet.
* Part 99 (the Siemens catalogue header) has no sheet in the folder, so it is checked against Blatt 1 only.
* Whether XML catalogues per ISO 16757 are already in use, and which XSD.
* Your network share layouts for folder sync, such as UNC paths and DFS.
