"""
ui/widgets/tool_list_card.py — ToolListView (the scrollable vertical tool
list) and _CreateToolCard (its pinned first row). Per-tool rows are
ToolCardWidget instances (tool_card_widget.py) — this module no longer
defines a list-row card class of its own (the old, page-navigating
ToolListCard was replaced by ToolCardWidget's inline expansion).

ToolListView is also the ONE place dropping a tool empties its magazine
pocket (pocket -> UNASSIGNED_POCKET) — see tool_magazine_bar.py's
_PocketSlot: it's a drag source, but per the current design a rejected
drop does NOT fall back to "empty the pocket" anymore; only an explicit
drop onto this list does. A drop anywhere else leaves the tool exactly
where it was (Qt's own drag-rejection animation is what makes it visually
"spring back" to its slot).

Update-in-place, not rebuild-from-scratch: set_tools() reuses existing
ToolCardWidget instances (matched by tool_number) instead of tearing the
whole list down every time — a full rebuild would collapse every
expanded card (and interrupt in-progress typing) on every single
auto-save, since each auto-save write re-triggers this via
ToolDatabaseSignals.tool_changed. A card currently holding keyboard focus
is skipped so an in-progress, not-yet-committed edit is never overwritten
by a reload triggered by some OTHER field's save.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QLabel, QScrollArea, QScroller, QSizePolicy, QVBoxLayout, QWidget,
)

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card_button import CardButton
from controller.ui.widgets.tool_card_widget import ToolCardWidget
from controller.ui.widgets.tool_magazine_bar import TOOL_MIME_TYPE


class _CreateToolCard(CardButton):
    """Pinned first row: a big "+ Create new tool" button-card."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Create new tool", icon=get_icon("addTool", tint=True), icon_size=32,
        )
        self.setProperty("variant", "create_tool")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(64)


class _ListContainer(QWidget):
    """The actual scrolled widget inside ToolListView, and the real drop
    target — a QScrollArea delivers drag/drop events to the widget it
    scrolls, not to itself."""

    tool_dropped_for_removal = Signal(int)   # tool_number

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(TOOL_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        raw = bytes(event.mimeData().data(TOOL_MIME_TYPE)).decode("utf-8")
        try:
            tool_number = int(raw)
        except ValueError:
            return
        event.acceptProposedAction()
        self.tool_dropped_for_removal.emit(tool_number)


class ToolListView(QScrollArea):
    """Vertically scrollable list: pinned _CreateToolCard first, then one
    ToolCardWidget per tool."""

    create_tool_requested = Signal()
    tool_dropped_for_removal = Signal(int)   # tool_number
    pocket_change_requested = Signal(int, int)   # tool_number, target_pocket

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Touch-drag-to-scroll from anywhere in the list, not just via the
        # scrollbar — QAbstractScrollArea doesn't enable this on its own,
        # it needs an explicit gesture grab (per explicit request; the
        # same gesture is grabbed on ToolCardWidget's own horizontal
        # parameter strip for the same reason).
        QScroller.grabGesture(self.viewport(), QScroller.ScrollerGestureType.TouchGesture)

        self._col = QVBoxLayout()
        self._col.setContentsMargins(8, 8, 8, 8)
        self._col.setSpacing(6)
        self._col.setAlignment(Qt.AlignmentFlag.AlignTop)

        container = _ListContainer()
        container.setLayout(self._col)
        container.tool_dropped_for_removal.connect(self.tool_dropped_for_removal)
        self.setWidget(container)

        self._create_card = _CreateToolCard()
        self._create_card.clicked.connect(self.create_tool_requested)
        self._col.addWidget(self._create_card)

        self._cards: dict[int, ToolCardWidget] = {}
        self._empty_lbl: QLabel | None = None

    def set_tools(self, tools: list[ToolDefinition]) -> None:
        """Reconcile the card list (below the pinned create-card) with
        *tools*, in order — see module docstring for why this updates
        existing ToolCardWidget instances in place rather than tearing
        everything down and rebuilding."""
        wanted = {t.tool_number: t for t in tools}
        focused = QApplication.instance().focusWidget() if QApplication.instance() else None

        # Drop cards for tools no longer present (deleted, or filtered out).
        for tn in list(self._cards.keys()):
            if tn not in wanted:
                card = self._cards.pop(tn)
                self._col.removeWidget(card)
                card.deleteLater()

        if self._empty_lbl is not None:
            self._col.removeWidget(self._empty_lbl)
            self._empty_lbl.deleteLater()
            self._empty_lbl = None

        if not tools:
            self._empty_lbl = QLabel("Keine Werkzeuge gefunden.")
            self._empty_lbl.setObjectName("CardTitle")
            self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._col.addWidget(self._empty_lbl)
            return

        for position, tool in enumerate(tools):
            card = self._cards.get(tool.tool_number)
            if card is None:
                card = ToolCardWidget(tool)
                card.pocket_change_requested.connect(self.pocket_change_requested)
                self._cards[tool.tool_number] = card
            elif focused is None or not card.isAncestorOf(focused):
                # Skip refreshing a card the user is actively typing in —
                # see module docstring.
                card.set_tool(tool)

            target_index = position + 1   # index 0 is the create-card
            if self._col.indexOf(card) != target_index:
                self._col.removeWidget(card)
                self._col.insertWidget(target_index, card)

    def card_for(self, tool_number: int) -> ToolCardWidget | None:
        return self._cards.get(tool_number)

    def expand_and_scroll_to(self, tool_number: int) -> None:
        card = self._cards.get(tool_number)
        if card is None:
            return
        card.set_expanded(True)
        self.ensureWidgetVisible(card)
