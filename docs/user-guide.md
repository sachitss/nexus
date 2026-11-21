# Nexus Compare & Editor: user guide

## Start
* **Installer:** run `Nexus-0.3.0-setup.exe`. It needs no admin rights unless you choose "install for all users".
* **Portable:** copy the `Nexus` folder anywhere and start `Nexus.exe`.
* **From source:** run `run_from_source.bat`.

The **Home** tab (dashboard) has tiles for every module and the list of recent comparisons; double-click an entry to repeat it or open its report. Drop **one** file on the window to edit it, or **two** files/folders to compare them.

## The ribbon
| Tab | What is there |
|---|---|
| Home | New/Open/Save, Smart compare (Ctrl+K), Folders (Ctrl+Alt+K), three-way merge, find/replace, Explorer, Search & Replace, Validate |
| Compare | Every comparison type, compare the current document with another tab / the saved file / the clipboard, navigate differences (F7 / Shift+F7), copy blocks, Recompare (F5), **Change summary**, reports, folder sync |
| Edit | Clipboard, line tools, sort/clean, case, format JSON/XML, Base64, **bookmarks** (Ctrl+F2, F2), **folding** (Ctrl+Shift+[) |
| Search | Find/replace in the document, Find in files, Search & Replace across folders |
| Files | Dual-pane explorer, directory list, batch rename, attributes |
| Engineering | VDI 3805 compare and validation, build profile from guideline, profiles folder, profile template, XML/JSON structure compare, XSD validation, Excel workbook, Word |
| Automation | Jobs, run a job file, history |
| View | Theme (**Light**, **Dark (navy)**, **Follow Windows**; Ctrl+Shift+D toggles, also from the status bar and the Quick Access Toolbar), word wrap, whitespace, **split view** (Ctrl+Alt+V), zoom, encoding and line endings, settings, shortcuts (F1) |

Right-click any ribbon button and choose **Add to Quick Access Toolbar** to pin it to the top row. Double-click a ribbon tab or press Ctrl+F1 to collapse the ribbon.

## Comparing
* **Smart compare** detects the type. In the dialog you can force a type, for example *Text* for an XML file, or *Excel workbook* to compare formulas and formatting.
* **Folders:** colours are green identical, red different, yellow modified (one side newer), blue new, grey excluded and purple renamed. Use **Filters…** for regex, size and date filters. **Synchronise…** shows a plan first. Nothing is copied until you press *Run* and confirm. Overwritten and deleted files are backed up, and an audit log is written.
* **Text / Word:** the *Options* menu has ignore case/whitespace/blank lines/comments/regex, word highlighting and *Word: compare formatting too*.
* **Excel / CSV table:** set key columns (names or numbers, e.g. `ID` or `1,3`) to match rows by key instead of position.
* **Excel workbook:** cells by position with values, formulas and formatting, renamed sheets, named ranges and duplicate keys. *Intelligent mode* opens the key-based table view.
* **XML / JSON:** elements with `id`, `name`, `code` and similar attributes are matched by key, so reordering is not reported. *Validate both* checks the files against the XSD.
* **VDI 3805:** choose the two files. Nexus reads the part number from the `010` header and picks the matching profile from your profile folder (`%APPDATA%\Nexus\profiles`) automatically; you can also choose a profile file yourself. To create profiles, click **Build profile from guideline…** (also on the Engineering tab), choose Blatt 1 and the part sheets as .docx, and click *Build profiles*. The Word files are only read. Records are identified by their position in the product structure, e.g. `100:1 / 110:3 / 700:12 / 710.01:4`, so a removed length does not shift the others. The *Changes* tab can be filtered by change type, role (connection, geometry…), record type or text. *Structure* shows the product tree, and *Validation* lists issues for each side. Export to Excel or HTML. A difference like `400` vs `400,0` is shown as *notation only*. Values are only *equivalent* when you set a tolerance or allow unit conversion.
* **Three-way merge:** choose base, mine and theirs. Pick a version for each conflict, or edit the result, then *Save result…*.

## Files
* **Directory list:** scan, filter, then export to CSV, Excel or a TXT tree. The right panel shows extensions, folder sizes, duplicates and similar names.
* **Batch rename:** add files, set rules, and check the preview (conflicts are red). Click *Rename…* to apply and *Undo last rename…* to revert.
* **Attributes:** a partially checked box means "keep". Set timestamps with the *set* boxes.

## Search & Replace across folders
Enter one or more folders (separated by `;`), the text or regex, and the replacement. Run *Preview replace* and check the diff of each file, then click *Replace in files…*. Originals are backed up. Word and Excel files are searched but never changed.

## Automation
Click *New from template*, edit the JSON, then *Save* and *Run now*. *Windows scheduled task…* copies a `schtasks` command that runs the job daily. Unattended:
```
Nexus.exe --job C:\path\job.json      (exit code 0 identical, 1 differences, 2 errors)
Nexus.exe --diff left right           (console output)
```

## Settings and privacy
Settings covers the environment (Light, Dark or Follow Windows, which tracks the Windows app mode live), font size, recovery snapshot interval, session restore, backup folder and the **optional AI summary**. The AI summary is off by default. When it is on, *Change summary* asks before sending the list of differences (never whole files) to the Anthropic API. Without it, an offline summary is shown. Unsaved editor text is snapshotted to `%APPDATA%\Nexus\recovery` and offered back after a crash.

Data folder: `%APPDATA%\Nexus` (history.sqlite, jobs, backups, sync-logs, rename-logs, recovery).
