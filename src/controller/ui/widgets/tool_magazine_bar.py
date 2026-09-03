"""
ui/widgets/tool_magazine_bar.py — ToolPage's pinned magazine bar: a
horizontal row of fixed pocket slots (P1..Pn) showing which tool (if any)
occupies each, with drag & drop to reassign pockets.

Drag & drop mechanics
----------------------
No drag & drop existed anywhere in the app before this — built from
scratch on Qt's standard QDrag/QMimeData mechanism. Both _PocketSlot (drag
a tool OUT of its pocket, onto another pocket) and ToolListCard (drag a
tool from the main list ONTO a pocket) are drag sources using the same
custom MIME type, "application/x-datum-tool", whose payload is just the
UTF-8-encoded tool_number — sufficient since a tool's identity is fully
determined by that single int, no need for a JSON payload. _PocketSlot is
the only drop target (dropping onto the list itself isn't a supported
gesture — the pocket assignment always has an explicit pocket number as
its target).

Pocket "unassigned" convention: pocket < 0 (this module always uses -1)
displays as "-" / "Frei" everywhere in the UI. The `pocket` column is a
plain INTEGER with no CHECK constraint (see persistence/tool_db.py), so
this needs no schema change — it's just a value convention, mirroring how
LinuxCNC tool tables already use out-of-range pocket numbers loosely.
"""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.ui.widgets.tool_drag import DragHoldMixin, scaled_drag_pixmap
from controller.ui.widgets.tool_icons import tool_type_icon

TOOL_MIME_TYPE = "application/x-datum-tool"

# Pocket number meaning "not assigned to any magazine slot" — see module
# docstring. Exported so ToolPage/tool_list_card.py share the same
# convention rather than each hard-coding -1 independently.
UNASSIGNED_POCKET = -1

_SLOT_SIZE = QSize(96, 128)


class _PocketSlot(DragHoldMixin, QFrame):
    """One pocket: number, tool-type icon (if occupied), name or "Frei".
    Drag source (dragging a tool back out of its pocket) AND drop target
    (accepting a tool dragged in from the list or another pocket). A
    plain click (no drag — see DragHoldMixin/tool_drag.py) on an occupied
    slot emits tool_clicked to open that tool's detail page."""

    # tool_number, target_pocket — this slot's own pocket_number
    tool_dropped = Signal(int, int)
    tool_clicked = Signal(int)   # tool_number

    def __init__(self, pocket_number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pocket_number = pocket_number
        self._tool: ToolDefinition | None = None
        self._dh_init()

        self.setObjectName("Card")
        self.setProperty("variant", "sim_nav")
        self.setFixedSize(_SLOT_SIZE)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(2)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._pocket_lbl = QLabel(f"P{pocket_number}")
        self._pocket_lbl.setObjectName("CardTitle")
        self._pocket_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._pocket_lbl)

        root.addStretch()

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(64, 64)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._icon_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        self._name_lbl = QLabel("Free")
        self._name_lbl.setObjectName("CardButtonLabel")
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_lbl.setWordWrap(True)
        root.addWidget(self._name_lbl)

    @property
    def pocket_number(self) -> int:
        return self._pocket_number

    def set_tool(self, tool: ToolDefinition | None) -> None:
        self._tool = tool
        if tool is None:
            self._icon_lbl.clear()
            self._name_lbl.setText("Free")
            self.setToolTip("")
        else:
            self._icon_lbl.setPixmap(tool_type_icon(tool.tool_type, size=28).pixmap(28, 28))
            self._name_lbl.setText(tool.name or tool.remark or f"T{tool.tool_number}")
            self.setToolTip(f"T{tool.tool_number} — {tool.name or tool.remark}")

    # ── Drag source (drag the occupying tool back out) / click-to-open ─────
    # Gated by DragHoldMixin: a plain click (press+release with no drag
    # ever starting) emits tool_clicked; the drag itself only starts from
    # mouseMoveEvent once press-and-hold + a move past the threshold are
    # both satisfied — see tool_drag.py.

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dh_press(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._tool is not None and self._dh_move(event.position().toPoint()):
            self._dh_release()
            self._start_drag()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_click = self._dh_press_pos is not None
        self._dh_release()
        if (
            was_click and event.button() == Qt.MouseButton.LeftButton
            and self._tool is not None
        ):
            self.tool_clicked.emit(self._tool.tool_number)
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        if self._tool is None:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(TOOL_MIME_TYPE, str(self._tool.tool_number).encode("utf-8"))
        drag.setMimeData(mime)
        drag.setPixmap(scaled_drag_pixmap(self))
        result = drag.exec(Qt.DropAction.MoveAction)
        if result != Qt.DropAction.MoveAction:
            # Not accepted by any other pocket (dropped on the list, the
            # filter bar, outside the window, ...) — treat as "pulled the
            # tool out and put it down somewhere else": empty this pocket.
            self.tool_dropped.emit(self._tool.tool_number, UNASSIGNED_POCKET)

    # ── Drop target ──────────────────────────────────────────────────────────

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
        self.tool_dropped.emit(tool_number, self._pocket_number)


class ToolMagazineBar(QScrollArea):
    """Horizontally scrollable row of _PocketSlot widgets, P1..Pn."""

    # tool_number, target_pocket — re-emitted from whichever slot received
    # the drop; ToolPage connects this once instead of per-slot.
    tool_dropped = Signal(int, int)
    tool_clicked = Signal(int)   # tool_number — re-emitted, see above

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # _SLOT_SIZE.height() + row margins (8+8) + headroom for the
        # horizontal scrollbar that appears once pockets overflow the
        # visible width (~15-16px) — the previous "+20" left almost no
        # slack and visibly squished the slots once the scrollbar showed.
        self.setFixedHeight(_SLOT_SIZE.height() + 8 + 8 + 16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._row = QHBoxLayout()
        self._row.setContentsMargins(8, 8, 8, 8)
        self._row.setSpacing(6)
        #self._row.addStretch()

        container = QWidget()
        container.setLayout(self._row)
        self.setWidget(container)

        self._slots: list[_PocketSlot] = []
        self._pocket_count = 0

    def set_pocket_count(self, n: int) -> None:
        """Rebuild the slot row for a new pocket count. Cheap enough to
        just tear down and rebuild — this only happens when the user
        edits AppSettings.tool_pocket_count in the Tools settings tab, not
        on any hot path."""
        if n == self._pocket_count and self._slots:
            return
        self._pocket_count = n
        while self._row.count() > 1:   # keep the trailing stretch
            item = self._row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._slots = []
        for i in range(1, n + 1):
            slot = _PocketSlot(i)
            slot.tool_dropped.connect(self.tool_dropped)
            slot.tool_clicked.connect(self.tool_clicked)
            self._row.insertWidget(self._row.count() - 1, slot)
            self._slots.append(slot)

    def set_tools(self, tools: list[ToolDefinition]) -> None:
        """Assign each tool to its pocket slot (by tool.pocket); slots with
        no matching tool show as "Frei". Tools whose pocket falls outside
        [1, pocket_count] (including UNASSIGNED_POCKET) simply aren't shown
        in the bar — they still appear in the main list."""
        by_pocket = {t.pocket: t for t in tools if t.pocket >= 1}
        for slot in self._slots:
            slot.set_tool(by_pocket.get(slot.pocket_number))
