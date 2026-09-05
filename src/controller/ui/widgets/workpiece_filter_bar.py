"""
ui/widgets/workpiece_filter_bar.py — WorkpiecesPage's search/filter bar,
directly below its header: free-text search (by name) and a sort-by
combobox (Name / Erstellt / Geändert) — same idiom as ToolPage's
ToolFilterBar (tool_filter_bar.py), scoped to the fields Workpiece
actually has (no magazine-only-style toggle here, nothing analogous
exists for workpieces).
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QWidget

SORT_OPTIONS: list[tuple[str, str]] = [
    ("Name",      "name"),
    ("Erstellt",  "created_at"),
    ("Geändert",  "modified_at"),
]


class WorkpieceFilterBar(QWidget):
    search_changed = Signal(str)
    sort_changed   = Signal(str)   # one of SORT_OPTIONS' key values

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Suche (Name) …")
        self._search.textChanged.connect(self.search_changed)
        root.addWidget(self._search, stretch=1)

        self._sort_combo = QComboBox()
        for label, key in SORT_OPTIONS:
            self._sort_combo.addItem(label, userData=key)
        self._sort_combo.currentIndexChanged.connect(
            lambda i: self.sort_changed.emit(self._sort_combo.itemData(i))
        )
        root.addWidget(self._sort_combo)

    def search_text(self) -> str:
        return self._search.text().strip().lower()

    def sort_key(self) -> str:
        return self._sort_combo.currentData()
