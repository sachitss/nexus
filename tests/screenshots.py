"""Render each view offscreen and save screenshots (also a GUI smoke test).

    python tests/screenshots.py screenshots [--light]
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

for _n in ("information", "warning", "question", "critical"):   # never block on dialogs
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

from nexus.ui import theme  # noqa: E402
from nexus.ui.mainwindow import MainWindow  # noqa: E402
from nexus.ui.engviews import MergeView, PROFILE_DIR  # noqa: E402

out = next((a for a in sys.argv[1:] if not a.startswith("--")), "screenshots")
dark = "--light" not in sys.argv
os.makedirs(out, exist_ok=True)
S = os.path.join(ROOT, "samples")
app = QApplication([])
from PySide6.QtCore import QSettings  # noqa: E402
QSettings("Nexus", "Nexus").setValue("theme", "dark" if dark else "light")
theme.apply(app, dark)
win = MainWindow()
win.resize(1500, 860)
win.show()


def pump(t=0.4):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()


def shot(name, ribbon_tab=None):
    if ribbon_tab is not None:
        win.ribbon.setCurrentIndex(ribbon_tab)
    pump()
    win.grab().save(os.path.join(out, name + ("" if dark else ".light") + ".png"))


shot("01-dashboard", 0)
t = win.open_file(f"{S}/code/right.py")
t.editor.highlighter.relex()
t.editor.toggle_bookmark(3)
win.findbar.show_find()
win.findbar.find.setText("greet")
shot("02-editor", 2)
win.findbar.hide_bar()
v = win.open_compare(f"{S}/code/left.py", f"{S}/code/right.py")
v.goto_hunk(1)
shot("03-text-compare", 1)
win.open_compare(f"{S}/word/left.docx", f"{S}/word/right.docx")
shot("04-word", 1)
w = win.open_compare(f"{S}/excel/left.xlsx", f"{S}/excel/right.xlsx", "workbook")
pump()
shot("05-excel-workbook", 5)
w = win.open_compare(f"{S}/csv/left.csv", f"{S}/csv/right.csv")
w.goto_diff(1)
shot("06-csv-table", 1)
# real-layout sample; with NEXUS_VDI_PROFILE pointing to a profile built from the guideline (Part 6)
real_prof = os.environ.get("NEXUS_VDI_PROFILE", "")
if real_prof:
    w = win.open_compare(f"{S}/vdi3805/PART06_MUSTER_2024.vdi", f"{S}/vdi3805/PART06_MUSTER_2025.vdi")
    w.profile.setText(real_prof)
else:
    w = win.open_compare(f"{S}/vdi3805/left.002", f"{S}/vdi3805/right.002")
    w.profile.setText(os.path.join(PROFILE_DIR, "vdi3805_profile_template.json"))
w.run()
pump()
shot("07-vdi3805", 5)
w.tabs.setCurrentIndex(1)
for t in w.trees:
    t.expandToDepth(3)
shot("08-vdi3805-structure", 5)
w = win.open_compare(f"{S}/xml/catalogue_v1.xml", f"{S}/xml/catalogue_v2.xml")
pump()
w.validate()
shot("09-xml-structure", 5)
w = win.open_compare(f"{S}/folders/left", f"{S}/folders/right")
w.worker.wait()
shot("10-folders", 1)
m = win._add(MergeView(f"{S}/merge/base.txt", f"{S}/merge/mine.txt", f"{S}/merge/theirs.txt"))
shot("11-merge", 1)
w = win.open_compare(f"{S}/binary/left.bin", f"{S}/binary/right.bin")
shot("12-binary", 1)
e = win.open_explorer(1)
e.listing.root.setText(f"{S}")
e.listing.scan()
e.listing.task.wait()
shot("13-directory-list", 4)
e.rename.add_paths([os.path.join(f"{S}/code", n) for n in os.listdir(f"{S}/code")])
e.rename.prefix.setText("2026_")
e.rename.num.setChecked(True)
e.tabs.setCurrentIndex(2)
shot("14-batch-rename", 4)
s = win.open_search()
s.roots.setText(S)
s.pattern.setCurrentText("price")
s.search()
s.task.wait()
shot("15-search", 3)
win.open_automation()
shot("16-automation", 6)
win.open_settings()
shot("17-settings", 7)
print("ok", out)
