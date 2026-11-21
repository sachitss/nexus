# Feature specification, status and roadmap (v0.3.0)

Status key: **Done** = implemented and covered by tests or a screenshot · **Partial** = works with the stated limits · **Roadmap** = not built yet, nothing is shown as working.

## Module status

| # | Module | Status | What works | Limits / next step |
|---|---|---|---|---|
| 1 | Folder compare | Done | Two-way compare by content, SHA-256, size+date or size. Include/exclude wildcards, path regex, size and date ranges. Rename/move detection. Spec colours (green identical, red different, yellow modified, blue new, grey excluded, purple renamed). Copy with confirmation. **Sync** with five modes, preview, backups and JSON audit log. **N-way** hash matrix. HTML report. | Exclusion by .gitignore files is on the roadmap. |
| 2 | Text editor | Done | Tabs, Pygments highlighting (500+ languages), line numbers, bookmarks, indentation folding, split view, find/replace with regex, find in files, encodings (8) and EOL conversion, line tools, case, comment toggle, JSON/XML format, Base64, zoom, session restore, file-change detection, **auto-save recovery** after a crash. Large files: up to 500 MB, highlighting off above 3 M characters. | Macro recording, multi-cursor and a plugin API are on the roadmap. |
| 3 | Intelligent compare | Done | Auto-detection of the file type. Text with ignore case/whitespace/blank lines/regex/**comments**, char or **word** highlight. Table, XML/JSON structure, binary (hex) and engineering modes. Copy blocks both ways. **Three-way merge** with conflict resolution. Unified diff export. | Moved-block detection inside text is on the roadmap. |
| 4 | Word | Done | DOCX text, tables and headers/footers as lines; optional run **formatting** (bold, italic, underline, size, colour, highlight) per paragraph. DOC via LibreOffice or Word. Reports. | Track changes / comments and document metadata compare are on the roadmap. |
| 5 | Excel / CSV | Done | XLSX/XLSM/XLS/CSV/TSV. **Normal mode**: cells by position with values, **formulas** and **formatting**, sheet pairing with **renamed-sheet** detection, missing sheets, **named ranges**, **duplicate keys**. **Intelligent mode**: key columns, header alignment, numeric tolerance. Export of differences to XLSX (coloured) or CSV. | Charts, pivot tables and VBA are not compared. |
| 6 | VDI 3805 (priority) | Done (real layout) | Real Blatt 1 file layout: decimal sub-records (710.01, 970.03), escaped `\;`, Fortran numbers, **hierarchical keys** (type:index chain, indices restart below each parent), product structure from file order. **Profiles built from the guideline documents** (Build profile from guideline…, `--build-profile`): record and field names DE/EN, units, formats, roles, repeating fields, variant meanings, parents. Profile chosen automatically from the 010 header (part number). Validation against field formats (I, N, A<n>, T), mandatory index, orphans, duplicate indices, field counts, undefined record types. Engineering compare with value / notation-only / equivalent; tolerances and unit conversion only when set. Excel/HTML reports. Large catalogues (150k records) compare in the background. XML catalogues via structure compare and XSD validation. | Parts available only as PDF or .doc need a .docx export first. Geometry is compared as values and references, not rendered. 600/610 functions are compared as text, not executed. |
| 7 | Search and replace | Done | Several folders, regex, case, whole word, **multi-line**, include/exclude, search inside DOCX/XLSX (read-only), replace **preview** with diff, backups, files changed since the preview are skipped, history, export to CSV/XLSX. | – |
| 8 | File explorer | Done | Dual pane (copy/move/rename/new folder/recycle bin, compare selected). Directory list with export to CSV/XLSX/TXT tree, extension stats, folder sizes, duplicates, similar names. **Batch rename** with rules, template, preview, conflict check, undo log. **Attributes and timestamps.** | Created time can only be set on Windows. |
| 9 | Automation | Done | JSON jobs (folder, file, VDI, XML validation, snapshot, batch), run now, `--job` CLI with exit codes, Windows Task Scheduler command, SQLite history. | Sessions (saved workspace of open tabs and options) are on the roadmap. Today the editor session reopens files. |
| – | UI | Done | Ribbon (8 tabs, collapsible), customizable Quick Access Toolbar, navy dark and light environments with a Follow Windows mode, dashboard in the concept style, tabs, drag and drop, shortcuts, context menus, resizable panels. | Freely editable ribbon groups are on the roadmap. |
| – | Reports and AI | Done | HTML, Excel and CSV reports. PDF by printing the HTML report. Offline rule-based summary. Optional Claude summary (opt-in, confirmed per request, difference text only). | Native PDF export is on the roadmap. |
| – | Installer | Done (script) | PyInstaller spec, Inno Setup script, GitHub Actions build that runs the tests and produces the portable folder and the setup.exe. | Code signing needs your certificate. |

## Roadmap

**Phase 3 (next)**
1. VDI 3805: execute 600/610 functions to resolve geometry terms; render 970.xx geometry; import part sheets from PDF.
2. Plugin API (entry points for comparers and tools).
3. Compiled fast diff for files over 100k lines.
4. Saved sessions / workspaces and comparison profiles (named option sets per file type).
5. Word track changes, comments and metadata. Excel charts and data validation rules.

**Phase 4**
6. Macro recording in the editor; multi-cursor.
7. Native PDF reports, report templates with company branding.
8. ISO 16757 XML profiles when the XSD is published or available to you.
9. Moved-block detection in text; image compare.
10. Signed MSIX package and auto-update channel.

## Test suite

`python -m pytest -q tests` runs 54 tests:

* core engines: text, table, folder, sync, rename detection, N-way, XML/JSON, XSD, binary, merge, VDI 3805 keys/structure/validation/rules, engineering values, Office, search/replace, batch rename/undo, attributes, automation and history;
* VDI 3805 real layout: loader escapes, Fortran numbers, guideline-to-profile extraction (generated test document), derived parents, hierarchical keys, compare, format validation, profile lookup by header, `--build-profile` and `--diff` CLI;
* offscreen GUI smoke tests: every compare view on the samples, the tool views, history recording, editor bookmarks/folding/split, the `--job` CLI.

`tests/make_samples.py` regenerates the sample files. `tests/screenshots.py` renders all views to PNG.
