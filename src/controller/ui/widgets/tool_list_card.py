"""
ui/widgets/tool_list_card.py — ToolListCard (one row in ToolPage's main
list) and ToolListView (the scrollable list of them).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QMimeData, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QGridLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card import Card
from controller.ui.widgets.card_button import CardButton
from controller.ui.widgets.tool_icons import tool_type_icon
from controller.ui.widgets.tool_magazine_bar import TOOL_MIME_TYPE


class ToolListCard(Card):
    """One row: pocket number | type icon | 2x4 name/diameter/flute-length/
    flutes grid | details button. Also a drag source (same MIME type
    ToolMagazineBar's pocket slots use) so a tool can be dragged straight
    from the list onto a magazine pocket."""

    tool_details_requested = Signal(int)   # tool_number

    def __init__(self, tool: ToolDefinition, parent: QWidget | None = None) -> None:
        super().__init__(title=None, parent=parent, orientation=Qt.Orientation.Horizontal)
        self._tool_number = tool.tool_number
        self.content_layout.setSpacing(12)

        pocket_lbl = QLabel(str(tool.pocket) if tool.pocket >= 1 else "-")
        pocket_lbl.setObjectName("CardTitle")
        pocket_lbl.setFixedWidth(24)
        pocket_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(pocket_lbl)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(32, 32)
        icon_lbl.setPixmap(tool_type_icon(tool.tool_type, size=32).pixmap(32, 32))
        self.content_layout.addWidget(icon_lbl)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(2)
        display_name = tool.name or tool.remark or f"T{tool.tool_number}"
        for col, (label, value) in enumerate((
            ("Name",            display_name),
            ("Diameter",        f"{tool.diameter:.2f} mm"),
            ("Flute Length",    f"{tool.flute_length:.1f} mm"),
            ("Flutes",          str(tool.flute_count)),
        )):
            lbl = QLabel(label)
            lbl.setObjectName("CardTitle")
            val = QLabel(value)
            val.setObjectName("CardButtonLabel")
            grid.addWidget(lbl, 0, col)
            grid.addWidget(val, 1, col)
        grid_widget = QWidget()
        grid_widget.setLayout(grid)
        self.content_layout.addWidget(grid_widget, stretch=1)

        self._details_btn = CardButton(icon=get_icon("settings", tint=True), icon_size=22)
        self._details_btn.setFixedSize(40, 40)
        self._details_btn.setToolTip("Werkzeugdetails")
        self._details_btn.clicked.connect(
            lambda: self.tool_details_requested.emit(self._tool_number)
        )
        self.content_layout.addWidget(self._details_btn)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ── Drag source (into a magazine pocket — see tool_magazine_bar.py) ────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(TOOL_MIME_TYPE, str(self._tool_number).encode("utf-8"))
            drag.setMimeData(mime)
            drag.setPixmap(self.grab())
            drag.exec(Qt.DropAction.MoveAction)
        super().mousePressEvent(event)


class ToolListView(QScrollArea):
    """Vertically scrollable list of ToolListCard rows."""

    tool_details_requested = Signal(int)   # tool_number

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._col = QVBoxLayout()
        self._col.setContentsMargins(8, 8, 8, 8)
        self._col.setSpacing(6)
        self._col.setAlignment(Qt.AlignmentFlag.AlignTop)

        container = QWidget()
        container.setLayout(self._col)
        self.setWidget(container)

        self._empty_lbl = QLabel("Keine Werkzeuge gefunden.")
        self._empty_lbl.setObjectName("CardTitle")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._col.addWidget(self._empty_lbl)
        self._empty_lbl.setVisible(False)

    def set_tools(self, tools: list[ToolDefinition]) -> None:
        """Rebuild the card list. Simple full-teardown-and-rebuild — no
        model/view layer needed at the expected (low hundreds) tool count."""
        while self._col.count():
            item = self._col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not tools:
            empty = QLabel("Keine Werkzeuge gefunden.")
            empty.setObjectName("CardTitle")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._col.addWidget(empty)
            return

        for tool in tools:
            card = ToolListCard(tool)
            card.tool_details_requested.connect(self.tool_details_requested)
            self._col.addWidget(card)
        self._col.addStretch()
