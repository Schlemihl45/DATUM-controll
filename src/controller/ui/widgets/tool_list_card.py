"""
ui/widgets/tool_list_card.py — ToolListCard (one row in ToolPage's main
list) and ToolListView (the scrollable list of them).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QMimeData, QSize, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QGridLayout, QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card import Card
from controller.ui.widgets.tool_drag import DragHoldMixin, scaled_drag_pixmap
from controller.ui.widgets.tool_icons import tool_type_icon
from controller.ui.widgets.tool_magazine_bar import TOOL_MIME_TYPE


class ToolListCard(DragHoldMixin, Card):
    """One row: pocket number | type icon | 2x4 name/diameter/flute-length/
    flutes grid | details button. Also a drag source (same MIME type
    ToolMagazineBar's pocket slots use) so a tool can be dragged straight
    from the list onto a magazine pocket — gated by DragHoldMixin (press-
    and-hold + move threshold) so an ordinary click never misfires as a
    drag (see tool_drag.py)."""

    tool_details_requested = Signal(int)   # tool_number

    def __init__(self, tool: ToolDefinition, parent: QWidget | None = None) -> None:
        super().__init__(title=None, parent=parent, orientation=Qt.Orientation.Horizontal)
        self._tool_number = tool.tool_number
        self._dh_init()
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

        # Plain QToolButton, not the Card-based CardButton — Card's
        # hardcoded 16px margins don't fit a compact 36px inline button.
        self._details_btn = QToolButton()
        self._details_btn.setIcon(get_icon("settings", tint=True))
        self._details_btn.setIconSize(QSize(20, 20))
        self._details_btn.setFixedSize(36, 36)
        self._details_btn.setObjectName("ToolDetailsButton")
        self._details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._details_btn.setToolTip("Werkzeugdetails")
        self._details_btn.clicked.connect(
            lambda: self.tool_details_requested.emit(self._tool_number)
        )
        self.content_layout.addWidget(self._details_btn)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ── Drag source (into a magazine pocket — see tool_magazine_bar.py) ────────
    # Gated by DragHoldMixin: the drag itself only starts from
    # mouseMoveEvent, once press-and-hold + a move past the threshold are
    # both satisfied — see tool_drag.py's module docstring for why.

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dh_press(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dh_move(event.position().toPoint()):
            self._dh_release()
            self._start_drag()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._dh_release()
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(TOOL_MIME_TYPE, str(self._tool_number).encode("utf-8"))
        drag.setMimeData(mime)
        drag.setPixmap(scaled_drag_pixmap(self))
        drag.exec(Qt.DropAction.MoveAction)


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
