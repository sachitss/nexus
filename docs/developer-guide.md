# Developer and maintenance guide

## Setup
```
py -3.12 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt pytest pyinstaller
python tests/make_samples.py
python -m nexus            # GUI
python -m pytest -q tests  # 54 tests; GUI tests run offscreen (QT_QPA_PLATFORM=offscreen)
```

## Build and release
1. Raise `__version__` in `nexus/__init__.py`, `version` in `pyproject.toml` and `AppVersion` in `installer/nexus.iss`.
2. Run `build_windows.bat`, which creates `dist\Nexus\Nexus.exe` (one-folder build, needed for Qt LGPL compliance).
3. Run `ISCC installer\nexus.iss`, which creates `dist\Nexus-<ver>-setup.exe`.
4. Alternatively, push to GitHub: `.github/workflows/build-windows.yml` runs the tests and uploads the portable folder and the installer as artifacts.
5. Sign `Nexus.exe` and the setup with your code-signing certificate (signtool) before you distribute them.

## Code layout and rules
* `nexus/core` must not import Qt. Everything there is usable from the CLI, jobs and tests.
* Long operations run in `ui/common.Task` (a QThread) and never block the UI.
* Any function that writes user files must: (a) be called only after a confirmation dialog, (b) support a backup or undo log, and (c) be recorded (history, audit log).
* New engineering comparisons must use `engvalue.compare_values` with explicit `EngRules`. Never declare values equivalent without a rule.

## Adding a comparison type
1. Add a `core/<kind>.py` with a pure function `compare(a, b, options) -> result` and a `summary(result) -> dict`.
2. Add a view in `ui/` that subclasses `QWidget`, provides `title()`, `has_unsaved()` and `run()`, and emits `compared(kind, left, right, summary, identical)`.
3. Register it in `MainWindow.open_compare`, in `KINDS` (compare dialog) and on the ribbon.
4. Add a sample to `tests/make_samples.py` and tests to `tests/`.

## VDI 3805 profiles
A profile is a JSON file (see `nexus/resources/profiles/vdi3805_profile_template.json`):
```json
{"name": "...", "part": "...", "default_key_fields": null,
 "rules": {"abs_tol": 0, "rel_tol": 0, "allow_unit_conversion": false, "decimal_comma": "comma"},
 "records": {"110": {"name": "Variant", "key_fields": 4, "parent": "100",
   "fields": [{"name": "Record type"}, ..., {"name": "Length", "unit": "mm", "role": "geometry", "numeric": true}]}}}
```
Roles: `identifier`, `connection`, `geometry`, `parameter`, `text`, `reference`. `parent: null` makes a record a root.

Profiles built from the guidelines (`core/vdiguide.py`, `python -m nexus --build-profile BLATT1.docx PARTNN.docx`) add:
`"hierarchical_keys": true` (key = chain of type:index), `"rules": {"decimal_comma": "point"}`, per field `format` (A, A20, I, N, R, T, TA), `name_en`, `remarks`, `feature_id`, and per record `repeat_from` / `repeat_name` / `repeat_format` for "4 bis n" fields. The extractor looks for header tables containing `Satzart: PARTnn-xxx` (or `Record type:`) and field tables whose first column is `Lfd. Nr.` / `S/N`. Parents come from `BASE_PARENTS` (Blatt 1 structure) and, for `NNN.ab`, document order (`710.11` described right after `710.01` belongs to it). Generated profiles are saved as `VDI3805-PARTnn.json` in `%APPDATA%\Nexus\profiles`; `vdi3805.find_profile` picks one by the part number in field 2 of the `010` header. They are derived from licensed VDI documents: do not add them to `nexus/resources`.

## Data and logs
`%APPDATA%\Nexus` contains:
* `history.sqlite`, with the tables `runs` and `snapshots`;
* `jobs\*.json`;
* `backups\`, `sync-logs\`, `rename-logs\` and `recovery\`.

User settings live in QSettings under "Nexus/Nexus" (the registry on Windows).

## Maintenance checklist
* Update the dependencies quarterly. Check PySide6, which is LGPL; keep the one-folder build.
* Re-run `tests/screenshots.py` after UI changes and review the PNGs.
* Back up `history.sqlite` if the history serves as an audit trail. It is append-only from the app.
