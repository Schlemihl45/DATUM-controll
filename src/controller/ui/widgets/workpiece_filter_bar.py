"""
ui/widgets/workpiece_filter_bar.py — WorkpieceBrowserPage's search bar,
directly below its header: free-text search by name.

No sort-by control here (unlike ToolPage's ToolFilterBar) — the browser's
sort order is fixed by spec (groups before workpieces, alphabetical
within each, see WorkpieceBrowserPage._refresh_list()/_BrowserListView),
so a sort dropdown would just be a dead control that never changes
anything.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget


class WorkpieceFilterBar(QWidget):
    search_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Suche (Name) …")
        self._search.textChanged.connect(self.search_changed)
        root.addWidget(self._search, stretch=1)

    def search_text(self) -> str:
        return self._search.text().strip().lower()
