# NEXUS ONE: module status, gaps and development log

Status of the NEXUS ONE brief (12 modules) in release **0.4.0**. **Done** means it works and has automated tests or a screenshot. **Partial** names what is missing. **Roadmap** means not built; nothing in the app pretends otherwise.

Stack decision (2026-11-21): the brief prefers C#/.NET, but the owner chose to keep Python + Qt (PySide6) and extend the working app. A .NET port remains possible later because the engines (`nexus/core`) are separate from the UI.

## Modules

| # | Module | Status | Works today | Gaps / next step |
|---|---|---|---|---|
| 01 | File Explorer | Partial | Dual pane with folder tree, copy/move/rename/new folder, delete to recycle bin, compare selected, open in editor, send to rename/attributes/listing. UNC paths work. | Tabs per pane, file preview, bookmarks and recent locations, hidden-file toggle, column chooser. |
| 02 | Folder & file compare | Done | Content/SHA-256/size+date compare, rename detection, filters, N-way matrix, sync (5 modes) with preview, backups and audit log, HTML report, side-by-side text with synced scrolling and difference navigation. | Three-way folder sync, saved named sessions, .gitignore filters. |
| 03 | Text editor | Partial | Tabs, 500+ languages, folding, split view, bookmarks, regex find/replace, find in files, encodings, EOL, crash recovery, files up to 500 MB. | Multi-cursor editing, macros, plugin API. |
| 04 | Bulk rename | **Done (new)** | Ordered rules (replace, remove, insert, prefix/suffix, case, spaces, numbering incl. per folder/alpha/roman, dates, extension, template, CSV/Excel mapping), tokens incl. parent folder and file content, files and folders, full preview, conflict/invalid/long-path checks, explicit confirmation listing every change, journal + automatic rollback, history with undo, crash recovery, profiles. | EXIF/ID3/Office metadata tokens. |
| 05 | Directory listing & reports | **Done (new)** | Recursive scan, depth, filters (wildcards, regex, size, created/modified dates, hidden, only empty folders), 16 selectable columns incl. SHA-256 and folder totals, sort, group, statistics, empty folders, tree diagram, export to XLSX, CSV, TXT, HTML, PDF, Markdown, XML, JSON, print preview, report templates. | Report designer beyond title/header/columns. |
| 06 | Word compare | Partial | DOCX paragraphs, tables, headers/footers, run formatting, word-level highlight, reports; DOC through Word/LibreOffice. | Metadata, comments/track changes, image and object inventory. |
| 07 | Excel & CSV compare | Done | XLSX/XLSM/XLS/CSV/TSV, position and key modes, formulas, formatting, renamed/missing sheets, named ranges, duplicate keys, header alignment, tolerance, coloured export. | Merged cells, charts, pivot tables. |
| 08 | XML/JSON/structured | Partial | Keyed structural XML/JSON compare, XSD validation, formatting-insensitive. | YAML/INI structure, ignore lists in the UI, four-way change classification (format/structure/data/reference). |
| 09 | VDI 3805 | Done | Real file layout, profiles built from the guideline documents, hierarchical keys, product structure, format validation, engineering compare (value / notation-only / equivalent), Excel/HTML reports, XML catalogues via XSD. | Executing 600/610 formula terms, geometry rendering, ISO 16757 / ETIM / ECLASS profiles. |
| 10 | Global search | Done | Several folders, regex, whole word, case, multi-line, DOCX/XLSX search, replace with preview and backups, history, export. | Search inside XML structure paths. |
| 11 | Duplicate finder | **Done (new)** | Identical content (size → head hash → SHA-256), same name, same content with different names, near-duplicate text, version series (v1/v2, "(1)", "- Kopie", dates, final/alt), groups by extension/size/month, export; never deletes. | Image similarity. |
| 12 | Automation | Partial | JSON jobs (folder/file/VDI compare, XML validation, snapshot, **directory report, duplicates, rename preview**), batch workflows (template "Delivery workflow"), CLI with exit codes, Windows Task Scheduler command, SQLite history. | File monitoring, custom scripts, resumable jobs. |

## Safety model (brief section 7)

| Requirement | State |
|---|---|
| Never modify originals without permission | Every change asks first: sync, replace, rename, copy/move, delete (recycle bin), attributes. |
| Preview sync and rename | Done. Rename shows the complete list in the confirmation. |
| Backup before bulk changes | Sync and replace back up; rename is reversible through its journal. |
| Undo / rollback | Rename: undo any run; failure mid-run rolls back. Sync: backups. |
| Collisions, overwriting | Rename blocks duplicate and existing targets; copy/move asks. |
| Log all operations | Sync audit log, rename journals, run history (SQLite). Explorer copy/move: no log yet. |
| Locked files | Rename rolls back on a locked file; search/duplicates report unreadable files. |
| Interrupted operations | Rename: detected at next start and restorable. Sync: roadmap. |
| Offline, no account | Yes. The optional AI summary is off by default and asks before sending anything. |

## Test coverage of the brief's list (section 9)

Covered by automated tests: identical/modified/new/deleted/renamed files, encodings, line endings, XML formatting vs structural changes, JSON differences, Excel formula changes, reordered CSV records, duplicate files, rename conflicts, special characters, long paths (flagged), locked files (simulated during rename), VDI 3805 attribute changes, schema validation errors. Not yet: very large trees as a benchmark, interrupted synchronisation, missing connection designations and invalid geometry references as dedicated VDI checks.

## Development log

* **2026-11-21, 0.4.0 (NEXUS ONE).** Brief received. Research updated (Bulk Rename Utility, Directory List & Print Pro, listDir uncertainty). New engines `renamer`, `dirreport`, `duplicates`, `pdf`; new views for Bulk Rename, Directory Listing & Reports and Duplicate Finder; three new job types; rebrand to NEXUS ONE; 16 new tests (72 in total).
* **2026-11-21, 0.3.x.** Light/dark/Follow-Windows environments, publisher branding, GitHub repository and Windows build pipeline.
* **2026-11-21, 0.3.0.** Real VDI 3805 support and profiles from the guideline documents.
