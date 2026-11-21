# PyInstaller spec: builds dist/Nexus/Nexus.exe (one-folder build, starts fast)
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["run_nexus.py"],
    datas=[("nexus/resources", "nexus/resources")],
    hiddenimports=collect_submodules("pygments.lexers") + collect_submodules("pygments.styles") + ["lxml.etree", "xlrd", "PySide6.QtSvg"],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
              "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Nexus", console=False,
          icon="nexus/resources/icon.ico", version="installer/version_info.txt")
coll = COLLECT(exe, a.binaries, a.datas, name="Nexus")
