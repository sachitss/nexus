"""HTML -> PDF using Qt's text engine (QTextDocument + QPdfWriter).

The only module in nexus.core that touches Qt; it is imported lazily so the
rest of the core stays Qt-free. Works headless: a QGuiApplication with the
offscreen platform is created when none exists (command line, automation)."""
from __future__ import annotations

import os


def _ensure_app():
    from PySide6.QtWidgets import QApplication     # a full QApplication, so GUI code can still start later
    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([])
    return app


def html_to_pdf(html: str, path: str, landscape: bool = False, title: str = "") -> str:
    _ensure_app()
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    w = QPdfWriter(path)
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    w.setPageOrientation(QPageLayout.Orientation.Landscape if landscape else QPageLayout.Orientation.Portrait)
    w.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
    w.setResolution(96)
    w.setTitle(title or "NEXUS ONE report")
    w.setCreator("NEXUS ONE")
    doc = QTextDocument()
    doc.setHtml(html)
    rect = w.pageLayout().paintRectPixels(w.resolution())
    doc.setPageSize(QSizeF(rect.width(), rect.height()))
    doc.print_(w)
    return path
