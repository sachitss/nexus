"""GUI smoke tests (offscreen): every view opens on the samples and reports a result."""
import json
import os
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(ROOT, "samples")
sys.path.insert(0, ROOT)
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    os.environ["APPDATA"] = str(tmp_path_factory.mktemp("appdata"))
    from PySide6.QtWidgets import QApplication, QMessageBox
    for n in ("information", "warning", "question", "critical"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    from nexus.ui import theme
    from nexus.ui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    theme.apply(app, True)
    w = MainWindow()
    w.show()
    w._app = app
    yield w
    w.history.close()


def pump(app, t=0.3):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()


@pytest.mark.parametrize("sub,kind,expect", [
    ("vdi3805", None, "VdiView"), ("excel", "workbook", "WorkbookView"), ("excel", None, "TableCompareView"),
    ("csv", None, "TableCompareView"), ("word", None, "TextCompareView"), ("code", None, "TextCompareView"),
    ("json", None, "StructCompareView"), ("binary", None, "BinaryCompareView")])
def test_compare_views(win, sub, kind, expect):
    files = sorted(os.path.join(S, sub, f) for f in os.listdir(os.path.join(S, sub)))[:2]
    v = win.open_compare(*files, kind)
    pump(win._app)
    assert type(v).__name__ == expect


def test_history_and_tools(win):
    from nexus.ui.engviews import MergeView
    win.open_compare(f"{S}/xml/catalogue_v1.xml", f"{S}/xml/catalogue_v2.xml")
    m = win._add(MergeView(f"{S}/merge/base.txt", f"{S}/merge/mine.txt", f"{S}/merge/theirs.txt"))
    for f in (win.open_explorer, win.open_search, win.open_validation, win.open_automation, win.open_settings, win.open_nway):
        assert f() is not None
    pump(win._app, 0.6)
    kinds = {r["kind"] for r in win.history.recent(50)}
    assert {"vdi", "xml", "merge"} <= kinds
    assert m.regions


def test_editor_bookmarks_folding_split(win):
    t = win.open_file(f"{S}/code/right.py")
    ed = t.editor
    ed.toggle_bookmark(3)
    ed.go_to_line(1)
    ed.next_bookmark(1)
    assert ed.textCursor().blockNumber() == 3
    b = ed.document().findBlockByNumber(3)
    assert ed._foldable(b)
    ed.toggle_fold(b)
    assert not b.next().isVisible()
    ed.toggle_fold(b)
    assert b.next().isVisible()
    assert t.toggle_split() and t.second.document() is ed.document()


def test_cli_job(tmp_path):
    job = {"name": "t", "type": "folder_compare", "left": f"{S}/folders/left", "right": f"{S}/folders/right",
           "report": str(tmp_path / "r.html")}
    p = tmp_path / "job.json"
    p.write_text(json.dumps(job))
    env = dict(os.environ, APPDATA=str(tmp_path))
    r = subprocess.run([sys.executable, "-m", "nexus", "--job", str(p)], cwd=ROOT, capture_output=True, text=True, env=env)
    assert r.returncode == 1, r.stderr      # differences found
    assert (tmp_path / "r.html").exists()


def test_hash_method():
    from nexus.core.folderdiff import FolderOptions, compare_folders, summary
    s = summary(compare_folders(f"{S}/folders/left", f"{S}/folders/right", FolderOptions(method="hash")))
    assert s.get("same", 0) >= 2


def test_light_dark_environment(win):
    from PySide6.QtGui import QPalette
    from nexus.ui import theme
    app = win._app
    win.set_theme("light")
    pump(app)
    assert not theme.Theme.dark and win.settings.value("theme") == "light"
    assert app.palette().color(QPalette.ColorRole.Base).name() == "#ffffff"
    assert win.theme_actions["light"].isChecked() and win.theme_label.text().startswith("Light")
    win.toggle_theme()
    pump(app)
    assert theme.Theme.dark and app.palette().color(QPalette.ColorRole.Base).name() == "#0f1a2e"
    win.set_theme("system")                                   # follows the OS scheme (light when unknown)
    assert theme.Theme.dark == theme.system_is_dark(app) and win.theme_actions["system"].isChecked()
    win.set_theme("dark")
    assert win.ribbon.find_action("Toggle light / dark") is not None


def test_branding(win):
    from nexus import POWERED_BY, PUBLISHER
    from nexus.ui.dashboard import LOGO
    from PySide6.QtWidgets import QLabel
    assert os.path.exists(LOGO) and PUBLISHER == "Ing.-Büro Sachit Shrestha"
    texts = [w.text() for w in win.findChildren(QLabel)]
    assert any(POWERED_BY in t for t in texts)
