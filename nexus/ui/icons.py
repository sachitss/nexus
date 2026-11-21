"""Small line icons drawn as SVG (own artwork), tinted for light/dark themes."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

_P = {
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "folder-compare": '<path d="M3 6h6l2 2h10v11H3z"/><path d="M9 13h6M13 11l2 2-2 2M15 17H9M11 15l-2 2 2 2"/>',
    "editor": '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>',
    "smart-diff": '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M6 8.5V14a4 4 0 004 4h5.5M18 15.5V10a4 4 0 00-4-4H8.5"/><path d="M13 16l2.5 2L13 20M11 4L8.5 6 11 8"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16M15 4v16"/>',
    "word": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M7 8l1.5 8 2-6 2 6L14 8M16 8h1M16 12h1M16 16h1"/>',
    "vdi": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "xml": '<path d="M7 7l-4 5 4 5M17 7l4 5-4 5"/><circle cx="9.5" cy="12" r="1"/><circle cx="14.5" cy="12" r="1"/>',
    "binary": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9v6M10 9h2v6h-2zM15 9v6M17.5 9v6"/>',
    "merge": '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M6 7c0 5 6 5 6 10M18 7c0 5-6 5-6 10"/>',
    "explorer": '<path d="M3 6h6l2 2h10v11H3z"/><path d="M3 11h18"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "replace": '<path d="M4 7h11l-3-3M20 17H9l3 3"/>',
    "validate": '<path d="M12 3l8 3v6c0 4.5-3.5 8-8 9-4.5-1-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    "report": '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4M9 17v-3M12 17v-6M15 17v-4"/>',
    "automation": '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3.5 2"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z"/>',
    "home": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    "new": '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4M12 11v6M9 14h6"/>',
    "open": '<path d="M3 7h6l2 2h10v10H3z"/><path d="M12 12v5M9.5 14.5L12 12l2.5 2.5"/>',
    "save": '<path d="M5 3h12l3 3v15H4V3z"/><path d="M8 3v5h8V3M8 21v-7h8v7"/>',
    "save-all": '<path d="M7 5h10l3 3v13H6V5z"/><path d="M4 3h11M9 5v4h7V5M9 21v-6h7v6"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 010 10h-3"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H9a5 5 0 000 10h3"/>',
    "bookmark": '<path d="M6 3h12v18l-6-4-6 4z"/>',
    "fold": '<path d="M7 8l5 5 5-5M7 14l5 5 5-5"/>',
    "split": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/>',
    "wrap": '<path d="M4 6h16M4 12h13a3 3 0 010 6h-4M4 18h5M14 16l-2 2 2 2"/>',
    "theme": '<path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"/>',
    "goto": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
    "rename": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
    "attrs": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h2M8 13h2M8 17h2M13 9h3M13 13h3M13 17h3"/>',
    "duplicate": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3"/>',
    "sync": '<path d="M20 12a8 8 0 01-13.7 5.6M4 12a8 8 0 0113.7-5.6"/><path d="M18 3v4h-4M6 21v-4h4"/>',
    "play": '<path d="M7 4l13 8-13 8z"/>',
    "history": '<path d="M3 12a9 9 0 103-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
    "ai": '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z"/>',
    "export": '<path d="M12 15V3M7 8l5-5 5 5"/><path d="M5 13v8h14v-8"/>',
    "prev": '<path d="M15 6l-6 6 6 6"/>',
    "next": '<path d="M9 6l6 6-6 6"/>',
    "copy-right": '<path d="M4 12h14M13 7l5 5-5 5"/><path d="M21 5v14"/>',
    "copy-left": '<path d="M20 12H6M11 7l-5 5 5 5"/><path d="M3 5v14"/>',
    "refresh": '<path d="M20 11a8 8 0 10-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "filter": '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
    "structure": '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 8v4M6 16v-4h12v4"/>',
    "find-files": '<path d="M6 3h9l4 4v6"/><path d="M6 3v18h6"/><circle cx="16.5" cy="16.5" r="3"/><path d="M21 21l-2.3-2.3"/>',
}


def icon(name: str, color: str = "#4c8dff", size: int = 32) -> QIcon:
    body = _P.get(name, _P["new"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for s in (16, 24, size, size * 2):
        pm = QPixmap(QSize(s, s))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        r.render(p)
        p.end()
        ic.addPixmap(pm)
    return ic


def app_icon() -> QIcon:
    return icon("smart-diff", "#5aa2ff", 64)
