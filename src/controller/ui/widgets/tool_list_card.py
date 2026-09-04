"""
ui/widgets/tool_list_card.py — ToolListView (the scrollable vertical tool
list), _CreateToolCard (its pinned first row) and CreateToolDialog (the
popup that "Create new tool" now opens — see ToolPage._on_create_tool()).
Per-tool rows are ToolCardWidget instances (tool_card_widget.py) — this
module no longer defines a list-row card class of its own (the old,
page-navigating ToolListCard was replaced by ToolCardWidget's inline
expansion).

ToolListView is also the ONE place dropping a tool empties its magazine
pocket (pocket -> UNASSIGNED_POCKET) — see tool_magazine_bar.py's
_PocketSlot: it's a drag source, but per the current design a rejected
drop does NOT fall back to "empty the pocket" anymore; only an explicit
drop of a drag that ORIGINATED from the magazine bar (tagged with
TOOL_ORIGIN_MAGAZINE_MIME_TYPE — see that module's docstring) onto this
list does. Dragging a card around within the list itself carries no such
tag, so it can never empty a pocket; any drop that isn't accepted leaves
the tool exactly where it was (Qt's own drag-rejection animation is what
makes it visually "spring back" to its slot/position).

Update-in-place, not rebuild-from-scratch: set_tools() reuses existing
ToolCardWidget instances (matched by tool_number) instead of tearing the
whole list down every time — a full rebuild would collapse every
expanded card (and interrupt in-progress typing) on every single
auto-save, since each auto-save write re-triggers this via
ToolDatabaseSignals.tool_changed. A card currently holding keyboard focus
is skipped so an in-progress, not-yet-committed edit is never overwritten
by a reload triggered by some OTHER field's save.

Exactly one card open at a time (accordion): _on_card_expanded_changed()
collapses every other card the moment one of them expands — wired up
once per card in set_tools(), driven by ToolCardWidget.expanded_changed.

Touch-scroll vs. card drag: while any card's header is mid-drag (moving a
tool into/out of a magazine pocket, or just picked up and about to be
dropped back), the list's own touch-scroll gesture is temporarily
released (set_scroll_enabled(False), driven by ToolCardWidget's
drag_started/drag_finished) so the two gestures can't fight each other —
without this, a drag that starts with a bit of vertical motion could get
mistaken for a scroll (or vice versa).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QScroller, QSizePolicy, QVBoxLayout, QWidget,
)

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card_button import CardButton
from controller.ui.widgets.tool_card_widget import ToolCardWidget
from controller.ui.widgets.tool_magazine_bar import TOOL_MIME_TYPE, TOOL_ORIGIN_MAGAZINE_MIME_TYPE


class _CreateToolCard(CardButton):
    """Pinned first row: a big "+ Create new tool" button-card. Clicking
    it no longer expands a row inline (see ToolPage._on_create_tool()) —
    it opens CreateToolDialog instead."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Create new tool", icon=get_icon("addTool", tint=True), icon_size=32,
        )
        self.setProperty("variant", "create_tool")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(64)


class CreateToolDialog(QDialog):
    """Popup shown by "Create new tool" — the exact same ToolCardWidget
    visual design (icon, header, expanded editable body, live 2D
    preview, auto-save), just hosted in a modal dialog instead of as a
    row inserted into the scrolling list. The wrapped card auto-saves
    field-by-field exactly like any list row, so by the time this dialog
    closes (Fertig, Escape, or the window's own close control — all
    equivalent, nothing here needs a separate "confirm" step) the tool is
    already fully persisted and shows up in the list on its own via the
    usual ToolDatabaseSignals.tool_changed -> ToolPage._reload() path."""

    def __init__(self, tool: ToolDefinition, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CreateToolDialog")
        self.setWindowTitle("Neues Werkzeug")
        self.setModal(True)
        self.resize(900, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.card = ToolCardWidget(tool)
        self.card.set_expanded(True)
        layout.addWidget(self.card, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        done_btn = QPushButton("Fertig")
        done_btn.setObjectName("CreateToolDialogDoneButton")
        done_btn.setMinimumSize(120, 40)
        done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        done_btn.clicked.connect(self.accept)
        footer.addWidget(done_btn)
        layout.addLayout(footer)


class _ListContainer(QWidget):
    """The actual scrolled widget inside ToolListView, and the real drop
    target — a QScrollArea delivers drag/drop events to the widget it
    scrolls, not to itself."""

    tool_dropped_for_removal = Signal(int)   # tool_number

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def _is_unassign_drop(self, mime) -> bool:
        return mime.hasFormat(TOOL_MIME_TYPE) and mime.hasFormat(TOOL_ORIGIN_MAGAZINE_MIME_TYPE)

    def dragEnterEvent(self, event) -> None:
        if self._is_unassign_drop(event.mimeData()):
            event.acceptProposedAction()
        # else: not accepted -> Qt's own rejection animation springs the
        # drag back to its origin; a card dragged around within the list
        # itself (no origin-magazine tag) never reaches here as accepted.

    def dropEvent(self, event) -> None:
        if not self._is_unassign_drop(event.mimeData()):
            return
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
        # QScroller.hasScroller() is NOT a usable "is grabbed right now"
        # check -- it starts False, flips True on the first grabGesture()
        # call, and then stays True forever, even across ungrabGesture()
        # (confirmed against the actual PySide6 QScroller implementation).
        # Track our own state instead of asking Qt.
        self._scroll_gesture_grabbed = True

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
                # Accordion: collapse every other card the moment this
                # one expands. Scroll suppression: don't fight a card's
                # own drag gesture with the list's touch-scroll.
                card.expanded_changed.connect(
                    lambda expanded, tn=tool.tool_number: self._on_card_expanded_changed(tn, expanded)
                )
                card.drag_started.connect(lambda: self.set_scroll_enabled(False))
                card.drag_finished.connect(lambda: self.set_scroll_enabled(True))
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

    def scroll_to(self, tool_number: int) -> None:
        """Bring a card into view WITHOUT expanding it — e.g. after the
        "Create new tool" popup (CreateToolDialog) closes, so the just-
        created tool is easy to find without also opening it (that would
        contradict the accordion's "nothing open unless explicitly
        clicked" rule)."""
        card = self._cards.get(tool_number)
        if card is None:
            return
        self.ensureWidgetVisible(card)

    def _on_card_expanded_changed(self, tool_number: int, expanded: bool) -> None:
        """Accordion: at most one card open at a time. Only reacts to a
        card actually opening — collapsing one never needs to touch any
        other (and set_expanded()'s own no-op guard keeps this from
        looping back)."""
        if not expanded:
            return
        for tn, card in self._cards.items():
            if tn != tool_number and card.is_expanded():
                card.set_expanded(False)

    def set_scroll_enabled(self, enabled: bool) -> None:
        """Temporarily release/re-grab the list's touch-scroll gesture —
        see module docstring's "Touch-scroll vs. card drag" section.

        Deliberately tracks its own self._scroll_gesture_grabbed flag
        rather than querying QScroller.hasScroller(vp): that call reports
        whether a QScroller was EVER created for the viewport, not
        whether it is currently grabbed, so it stays True forever after
        the very first grab -- using it as a guard here would silently
        skip every re-grab after the first ungrab."""
        if enabled == self._scroll_gesture_grabbed:
            return
        vp = self.viewport()
        if enabled:
            QScroller.grabGesture(vp, QScroller.ScrollerGestureType.TouchGesture)
        else:
            QScroller.ungrabGesture(vp)
        self._scroll_gesture_grabbed = enabled
