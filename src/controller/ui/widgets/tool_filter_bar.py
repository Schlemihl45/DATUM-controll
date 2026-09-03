"""
ui/widgets/tool_filter_bar.py — ToolPage's search/filter bar, directly
below the magazine bar: free-text search, a sort-by combobox (Diameter /
Flutes / Type), and an "In Magazine" toggle filter.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QWidget

from controller.ui.widgets.card_button import CardButton

SORT_OPTIONS: list[tuple[str, str]] = [
    ("Name",         "name"),
    ("Diameter",     "diameter"),
    ("Flutes",       "flute_count"),
    ("Type",         "tool_type"),
]


class ToolFilterBar(QWidget):
    search_changed         = Signal(str)
    sort_changed           = Signal(str)   # one of SORT_OPTIONS' key values
    magazine_only_toggled  = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Suche (Name, Bemerkung) …")
        self._search.textChanged.connect(self.search_changed)
        root.addWidget(self._search, stretch=1)

        self._sort_combo = QComboBox()
        for label, key in SORT_OPTIONS:
            self._sort_combo.addItem(label, userData=key)
        self._sort_combo.currentIndexChanged.connect(
            lambda i: self.sort_changed.emit(self._sort_combo.itemData(i))
        )
        root.addWidget(self._sort_combo)

        self._magazine_only_btn = CardButton("In Magazine")
        self._magazine_only_btn.setCheckable(True)
        self._magazine_only_btn.setProperty("variant", "sim_nav")
        self._magazine_only_btn.setFixedHeight(36)
        self._magazine_only_btn.toggled.connect(self.magazine_only_toggled)
        root.addWidget(self._magazine_only_btn)

    def sort_key(self) -> str:
        return self._sort_combo.currentData()
