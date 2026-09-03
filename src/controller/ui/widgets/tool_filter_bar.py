"""
ui/widgets/tool_filter_bar.py — ToolPage's search/filter bar, directly
below the magazine bar: free-text search, a sort-by combobox (Diameter /
Flutes / Type), and an "In Magazine" toggle filter.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPushButton, QWidget

SORT_OPTIONS: list[tuple[str, str]] = [
    ("Pocket Number",  "pocket"),
    ("Name",           "name"),
    ("Diameter",       "diameter"),
    ("Flutes",         "flute_count"),
    ("Type",           "tool_type"),
    ("Tool Number",    "tool_number"),
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
        self._search.setPlaceholderText("Search (Name, Comment) …")
        self._search.textChanged.connect(self.search_changed)
        root.addWidget(self._search, stretch=1)

        self._sort_combo = QComboBox()
        for label, key in SORT_OPTIONS:
            self._sort_combo.addItem(label, userData=key)
        self._sort_combo.currentIndexChanged.connect(
            lambda i: self.sort_changed.emit(self._sort_combo.itemData(i))
        )
        root.addWidget(self._sort_combo)

        # Plain QPushButton, not the Card-based CardButton — Card's
        # hardcoded 16px margins (card.py) don't fit a compact toggle at
        # this row's height; see dark.qss/light.qss's
        # QPushButton#ToolFilterToggle:checked rule for the highlight.
        self._magazine_only_btn = QPushButton("In Magazine")
        self._magazine_only_btn.setCheckable(True)
        self._magazine_only_btn.setObjectName("ToolFilterToggle")
        self._magazine_only_btn.setFixedHeight(32)
        self._magazine_only_btn.toggled.connect(self.magazine_only_toggled)
        root.addWidget(self._magazine_only_btn)

    def sort_key(self) -> str:
        return self._sort_combo.currentData()
