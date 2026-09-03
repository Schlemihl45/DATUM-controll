"""
ui/widgets/tool_param_group.py — ParamGroup: one labelled cluster of tool
parameters for ToolCardWidget's expanded body's lower block. Several of
these sit side by side, separated by thin vertical rules (see
tool_card_widget.py's _vline() helper), left-aligned with a trailing
stretch.

Three-row structure per field column, as specced:
    Row 0: category title (e.g. "Geometry"), spanning the whole group
    Row 1: a short symbol + unit (e.g. "⌀ mm", "α °") — a placeholder for
           a proper per-parameter icon (none exists in the icon set yet;
           see get_icon()'s asset list) that still visually distinguishes
           fields sharing the same unit within one group
    Row 2: the actual input widget

The full field name (e.g. "Diameter") isn't a fourth row — kept to the
exact three rows asked for — but is available as a tooltip on both the
symbol/unit label and the input widget itself.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget


class ParamGroup(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(6, 4, 6, 4)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(4)

        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("CardTitle")
        self._grid.addWidget(self._title_lbl, 0, 0, 1, 1)

        self._col = 0

    def add_field(self, symbol: str, unit: str, widget: QWidget, full_name: str) -> None:
        """Append one field column. *symbol* is a short placeholder glyph
        for the variable (e.g. "⌀", "L", "α"), *unit* its physical unit
        (e.g. "mm", "°", empty for a plain count), *full_name* the human
        label surfaced via tooltip (row 1 + the input widget itself)."""
        col = self._col
        self._col += 1

        row1_text = f"{symbol} {unit}".strip() if unit else symbol
        unit_lbl = QLabel(row1_text)
        unit_lbl.setObjectName("ParamUnitLabel")
        unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        unit_lbl.setToolTip(full_name)
        self._grid.addWidget(unit_lbl, 1, col)

        widget.setToolTip(full_name)
        self._grid.addWidget(widget, 2, col)
        # Column stretch keeps the title row from being the only wide
        # thing in the layout once more field columns are added.
        self._grid.setColumnMinimumWidth(col, 64)

    def set_field_visible(self, widget: QWidget, visible: bool) -> None:
        """Show/hide one field's whole column (unit label + input) — used
        for tool-type-dependent fields (corner radius, point angle, taper
        angle). Finds the paired unit label by grid position."""
        idx = self._grid.indexOf(widget)
        if idx == -1:
            return
        row, col, *_ = self._grid.getItemPosition(idx)
        widget.setVisible(visible)
        unit_item = self._grid.itemAtPosition(1, col)
        if unit_item is not None and unit_item.widget() is not None:
            unit_item.widget().setVisible(visible)
