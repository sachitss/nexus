# Technical architecture and stack decision

## 1. Stack evaluation

The brief asked for an evaluation of C#/.NET options against alternatives. All options below can produce a Windows 10/11 desktop app.

| Criterion | C# / .NET 8 + WPF | C# / .NET 8 + WinUI 3 | C# / .NET 8 + Avalonia | **Python 3.12 + Qt 6 (PySide6)** |
|---|---|---|---|---|
| Native Windows look, ribbon | ✔ (Fluent.Ribbon, MIT) | ✔ (no stock ribbon) | ◐ (community ribbon) | ✔ (own ribbon widget, 150 lines) |
| Code editor component | AvalonEdit (MIT) or Scintilla.NET (MIT) | WebView2 + Monaco | AvaloniaEdit (MIT) | QPlainTextEdit + Pygments (BSD); QScintilla is GPL/commercial |
| Diff library | DiffPlex (Apache-2.0) | DiffPlex | DiffPlex | difflib (stdlib) plus own algorithms |
| Word / Excel | Open XML SDK (MIT), ClosedXML (MIT) | same | same | python-docx (MIT), openpyxl (MIT), xlrd (BSD) |
| XML / XSD | System.Xml (built in) | same | same | lxml (BSD) |
| History DB | Microsoft.Data.Sqlite (MIT) | same | same | sqlite3 (stdlib) |
| Startup / large-file performance | very good | very good | very good | good (Qt is C++; hot loops in Python) |
| Distribution | single-file publish, MSIX | MSIX (packaging friction) | single-file | PyInstaller one-folder + Inno Setup |
| Cross-platform later | – | – | ✔ | ✔ |
| Speed of development for this scope | medium | slow | medium | **fast** |
| Licence risk | low | low | low | low: Qt via **LGPL-3.0** (dynamic linking, one-folder build keeps the Qt DLLs replaceable) |

**Decision (made by the project owner): Python + Qt (PySide6).** It is the fastest way to deliver this broad scope, it had a working base already (DeltaPad v0.1), and it gives a native Qt UI on Windows. The core engines are plain Python with no UI imports. A later port of the UI to .NET, or of hot spots to a compiled language, is therefore possible without rewriting the comparison logic.

**Licence implications**

* PySide6/Qt is **LGPL-3.0**. Commercial and closed distribution is fine if Qt is dynamically linked and users can replace the Qt libraries. The PyInstaller *one-folder* build satisfies this. Ship the LGPL text and a notice, and do not statically link Qt.
* The other dependencies are permissive: Pygments (BSD-2), openpyxl (MIT), python-docx (MIT), lxml (BSD-3), xlrd (BSD), PyInstaller (GPL with a bootloader exception, which allows any licence for the built app).
* chardet is historically LGPL-2.1 and newer releases have changed licence, so check the installed version. It is imported dynamically as a pure-Python module, which is compatible either way.
* `anthropic` (MIT) is optional and only installed if AI summaries are wanted.
* The QScintilla editor component was **not** used because it is GPL or commercial.

## 2. Structure

```
nexus/
  core/          pure Python, no Qt (used by GUI, CLI and jobs)
    textio        encoding/EOL detection, safe read/write
    textdiff      line diff with ignore rules, char/word highlight, unified diff
    tablediff     table diff (header alignment, key columns, tolerance)
    folderdiff    folder diff, filters, renames, sync plan + execution, N-way
    loaders       file type detection; Word/Excel/CSV/VDI loaders; legacy conversion
    office        workbook compare (formulas, formats, names, sheet pairing), exports
    structdiff    XML/JSON flatten + keyed diff, XSD validation
    bindiff       hex diff with insert/delete alignment
    merge3        three-way merge (diff3 regions)
    engvalue      numbers with units, decimal comma, tolerance, unit table
    vdi3805       VDI 3805 profile, keys, structure, validation, compare, reports
    vdiguide      builds VDI 3805 profiles from the guideline Word documents
    fileops       directory list, stats, duplicates, batch rename (+undo), attributes
    msearch       multi-folder search/replace with preview and backups
    report        HTML reports
    automation    jobs, SQLite history, snapshots, Task Scheduler command
    ai            offline summary + optional Claude summary
  ui/            PySide6 widgets (ribbon, dashboard, editor, one view per module)
  resources/     icons, example VDI profile
tests/           pytest: core engines + offscreen GUI smoke tests + sample generator
installer/       Inno Setup script
```

**Data flow.** Each view takes paths and options, calls a `core` function (in a worker thread for long runs), shows the result model, and emits `compared(kind, left, right, summary, identical)`. The main window stores that in the history database. The same `core` functions back the CLI (`--diff`, `--job`) and scheduled jobs, so GUI and automation give identical results.

## 3. Principles from the brief and how they are enforced

| Principle | Implementation |
|---|---|
| Local-first, no automatic uploads | No network code except `core/ai.summarize_with_claude`. It runs only after the user enables it in Settings and confirms each request, and it sends the difference text, never whole files. |
| Never modify originals without explicit confirmation | Every writing action (copy, sync, replace, rename, attributes, delete, save merge over an input) shows a confirmation first. Sync and replace back up files, and rename writes an undo log. Search inside Office files is read-only. |
| Reproducible and auditable | Each run is stored in SQLite with time, inputs, options (JSON), summary and report path. Sync writes a JSON audit log. Jobs are JSON files. |
| Engineering values | `engvalue.compare_values` returns EQUAL / FORMAT_ONLY / EQUIVALENT / DIFFERENT. EQUIVALENT requires an explicit tolerance or allowed unit conversion, and °C ↔ K is never converted. |
| Offline usability | Everything except the optional AI summary works offline. |

## 4. Performance (measured, Linux container, Python 3.13)

| Operation | Size | Time |
|---|---|---|
| Folder compare, byte-exact content | 10,000 files in 100 folders | 0.27 s |
| Table compare with key column | 100,000 rows | 0.53 s |
| Text diff | 200,000 lines, 200 changes | 7.8 s |
| Editor | highlighting is switched off above 3 M characters; files up to 500 MB open | – |

The text diff is the slowest path: difflib in pure Python. On the roadmap: a patience/Myers diff in a compiled extension, or chunking by unique-line anchors, for files above 100k lines.

## 5. Extensibility (plugin architecture)

Today, new compare kinds are added by writing a `core` module plus a view and registering it in `MainWindow.open_compare` and the ribbon. A formal plugin API is on the roadmap: entry points `nexus.comparers` and `nexus.tools`, each declaring the file types it accepts and providing a widget factory.
