"""
ui/widgets/tool_magazine_bar.py — ToolPage's pinned magazine bar: a
horizontal row of pocket slots (P1..Pn, always left-to-right, P1
leftmost) showing which tool (if any) occupies each, with drag & drop to
reassign pockets.

Layout: slots stretch to fill the available width evenly while they all
still fit at their minimum width; once there isn't room for that, the row
switches to fixed-minimum-width slots and the bar scrolls horizontally
instead of compressing them further — see _update_layout_mode().

Per-slot visual (top to bottom): a scaled 2D preview of the actual tool
and a "#N" pocket badge. An empty slot shows only the badge.

The preview is tool_profile_widget.py's real silhouette renderer (same
metallic-gradient zone fills as ToolCardWidget's live 2D preview), rotated
90° clockwise so the holder/spindle side sits on top and the cutting tip
points down — how the tool actually sits in the machine. It is NOT repainted
live: _PocketSlot caches one QPixmap per slot (see _tool_geometry_key())
and only re-renders it when the occupying tool's own geometry actually
changes, so scrolling/resizing the bar never re-runs the profile renderer.

Drag & drop mechanics
----------------------
Both _PocketSlot (drag a tool OUT of its pocket, onto another pocket) and
ToolCardWidget's header (drag a tool from the list ONTO a pocket) are
drag sources using the custom MIME type "application/x-datum-tool", whose
payload is just the UTF-8-encoded tool_number.

A tool is emptied from its pocket (pocket -> UNASSIGNED_POCKET) in
exactly ONE place: an explicit drop onto the vertical tool list (see
tool_list_card.py's _ListContainer) of a drag that ORIGINATED from this
magazine bar. _PocketSlot._start_drag() tags its drag with the second
marker MIME type TOOL_ORIGIN_MAGAZINE_MIME_TYPE (empty payload, presence
is all that matters); _CardHeader's drag (a tool being dragged around
within/out of the list itself) never sets it, so the list's drop handler
only ever treats a magazine->list drop as "unassign" — dragging a card
around within the list can never empty a pocket. Dropping a pocket's tool
anywhere else that doesn't accept it (another window, empty space, back
onto the list from a card drag, ...) is simply rejected.
"""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QScroller, QSizePolicy, QVBoxLayout, QWidget,
)

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.sim.simulation.tool_holder import STANDARD_HOLDERS
from controller.ui.widgets.tool_drag import DragHoldMixin, scaled_drag_pixmap
from controller.ui.widgets.tool_profile_widget import render_tool_pixmap

TOOL_MIME_TYPE = "application/x-datum-tool"
# Marker-only MIME type (payload is irrelevant, only presence matters) —
# see the module docstring's "Drag & drop mechanics" section.
TOOL_ORIGIN_MAGAZINE_MIME_TYPE = "application/x-datum-tool-origin-magazine"

_SLOT_MIN_WIDTH = 76
_SLOT_MAX_WIDTH = 128
_SLOT_HEIGHT = 196
_VISUAL_SIZE = QSize(96, 256)
_ICON_RENDER_SIZE = QSize(
    round(_VISUAL_SIZE.width()), round(_VISUAL_SIZE.height()),
)


def _tool_geometry_key(tool: ToolDefinition) -> tuple:
    """The subset of a tool's fields that actually affect its rendered 2D
    profile — anything else (name, material, service life, ...) changing
    must NOT invalidate a slot's cached preview pixmap; see
    _PocketSlot.set_tool()."""
    return (
        tool.tool_type, tool.diameter, tool.total_length, tool.cutting_length,
        tool.shank_diameter, tool.corner_radius, tool.tip_angle, tool.taper_angle,
        tool.holder_preset,
    )


class _SlotVisual(QWidget):
    """Tool-type icon visual. Shows a subtle highlight while a drag is
    hovering this slot as a valid (empty) drop target."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_VISUAL_SIZE)
        self._icon: QPixmap | None = None
        self._hover = False

    def set_icon(self, icon: QPixmap | None) -> None:
        self._icon = icon
        self.update()

    def set_drag_hover(self, hover: bool) -> None:
        if hover != self._hover:
            self._hover = hover
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        if self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(90, 200, 130, 40))
            painter.drawRoundedRect(self.rect(), 8, 8)

        if self._icon is not None:
            # Etwas zentrierter, da der Schatten darunter wegfällt
            icon_rect = QRectF(w * 0.14, h * 0.05, w * 0.72, h * 0.85).toRect()
            painter.drawPixmap(icon_rect, self._icon)


class _PocketSlot(DragHoldMixin, QFrame):
    """One pocket. Drag source (dragging its tool back out) AND drop
    target (accepting a tool dragged in from the list or another pocket).
    A plain click (no drag — see DragHoldMixin/tool_drag.py) on an
    occupied slot emits tool_clicked to expand that tool's list card."""

    tool_dropped = Signal(int, int)   # tool_number, target_pocket (this slot's own)
    tool_clicked = Signal(int)        # tool_number

    def __init__(self, pocket_number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pocket_number = pocket_number
        self._tool: ToolDefinition | None = None
        self._preview_key: tuple | None = None
        self._dh_init()

        self.setObjectName("Card")
        self.setProperty("variant", "sim_nav")
        self.setMinimumWidth(_SLOT_MIN_WIDTH)
        self.setMaximumWidth(_SLOT_MAX_WIDTH)
        self.setFixedHeight(_SLOT_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 6, 4, 6)
        root.setSpacing(3)
        root.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._visual = _SlotVisual(self)
        root.addWidget(self._visual, alignment=Qt.AlignmentFlag.AlignCenter)

        self._badge_lbl = QLabel(f"#{pocket_number}")
        self._badge_lbl.setObjectName("PocketBadge")
        self._badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._badge_lbl)


    @property
    def pocket_number(self) -> int:
        return self._pocket_number

    def set_tool(self, tool: ToolDefinition | None) -> None:
        self._tool = tool
        if tool is None:
            self._visual.set_icon(None)
            self.setToolTip("")
            self._preview_key = None
            return

        key = _tool_geometry_key(tool)
        if key != self._preview_key:
            holder = STANDARD_HOLDERS.get(tool.holder_preset)
            pixmap = render_tool_pixmap(tool, holder, _ICON_RENDER_SIZE, rotate_cw=True)
            self._visual.set_icon(pixmap)
            self._preview_key = key

        display = tool.name or tool.remark or f"T{tool.tool_number}"
        self.setToolTip(f"T{tool.tool_number} — {display}")

    # ── Drag source (drag the occupying tool back out) / click-to-expand ───
    # Gated by DragHoldMixin: a plain click emits tool_clicked; the drag
    # itself only starts from mouseMoveEvent once press-and-hold + a move
    # past the threshold are both satisfied — see tool_drag.py.

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
        was_click = self._dh_release(event.position().toPoint())
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
        # Marks this drag as having come FROM the magazine — the vertical
        # list's drop handler (tool_list_card.py's _ListContainer) only
        # accepts a drop as "unassign this pocket" when this marker is
        # present, so dragging a tool CARD around (which never sets it)
        # can never empty a pocket, only an explicit magazine->list drop
        # can (see that module's docstring).
        mime.setData(TOOL_ORIGIN_MAGAZINE_MIME_TYPE, b"1")
        drag.setMimeData(mime)
        drag.setPixmap(scaled_drag_pixmap(self))
        drag.exec(Qt.DropAction.MoveAction)
        # No fallback here on a rejected drop — see module docstring:
        # emptying a pocket only happens via an explicit list drop.

    # ── Drop target ──────────────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(TOOL_MIME_TYPE):
            event.acceptProposedAction()
            if self._tool is None:
                self._visual.set_drag_hover(True)

    def dragLeaveEvent(self, event) -> None:
        self._visual.set_drag_hover(False)

    def dropEvent(self, event) -> None:
        self._visual.set_drag_hover(False)
        raw = bytes(event.mimeData().data(TOOL_MIME_TYPE)).decode("utf-8")
        try:
            tool_number = int(raw)
        except ValueError:
            return
        event.acceptProposedAction()
        self.tool_dropped.emit(tool_number, self._pocket_number)


class ToolMagazineBar(QScrollArea):
    """Horizontally scrollable row of _PocketSlot widgets, P1..Pn, always
    left-to-right (P1 leftmost) — see _update_layout_mode() for the
    even-distribution-until-it-doesn't-fit sizing behaviour."""

    tool_dropped = Signal(int, int)
    tool_clicked = Signal(int)   # tool_number — re-emitted, see above

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # _SLOT_HEIGHT + row margins (8+8) + headroom for the horizontal
        # scrollbar that appears once pockets overflow the visible width.
        self.setFixedHeight(_SLOT_HEIGHT + 8 + 8 + 16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Touch-drag-to-scroll from anywhere in the bar, not just via the
        # scrollbar — see ToolListView's identical grab for why this needs
        # to be explicit.
        QScroller.grabGesture(self.viewport(), QScroller.ScrollerGestureType.TouchGesture)

        self._row = QHBoxLayout()
        self._row.setContentsMargins(8, 8, 8, 8)
        self._row.setSpacing(6)

        container = QWidget()
        container.setLayout(self._row)
        self.setWidget(container)

        self._slots: list[_PocketSlot] = []
        self._pocket_count = 0

    def set_pocket_count(self, n: int) -> None:
        """Rebuild the slot row for a new pocket count. Cheap enough to
        just tear down and rebuild — this only happens when the user
        edits AppSettings.tool_pocket_count in the Tools settings tab,
        not on any hot path."""
        if n == self._pocket_count and self._slots:
            return
        self._pocket_count = n
        while self._row.count():
            item = self._row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._slots = []
        for i in range(1, n + 1):
            slot = _PocketSlot(i)
            slot.tool_dropped.connect(self.tool_dropped)
            slot.tool_clicked.connect(self.tool_clicked)
            self._row.addWidget(slot)
            self._slots.append(slot)
        self._update_layout_mode()

    def set_tools(self, tools: list[ToolDefinition]) -> None:
        """Assign each tool to its pocket slot (by tool.pocket); slots with
        no matching tool show as empty. Tools whose pocket falls outside
        [1, pocket_count] (including UNASSIGNED_POCKET) simply aren't shown
        in the bar — they still appear in the main list."""
        by_pocket = {t.pocket: t for t in tools if t.pocket >= 1}
        for slot in self._slots:
            slot.set_tool(by_pocket.get(slot.pocket_number))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_layout_mode()

    def _update_layout_mode(self) -> None:
        """Distribute slots evenly across the available width while they
        all still fit at their minimum width; once they don't, freeze
        every slot at that minimum and let the bar scroll horizontally
        instead of compressing them further."""
        n = len(self._slots)
        if n == 0:
            return
        margins = self._row.contentsMargins()
        needed = (
            n * _SLOT_MIN_WIDTH + (n - 1) * self._row.spacing()
            + margins.left() + margins.right()
        )
        overflow = needed > self.viewport().width()
        self.setWidgetResizable(not overflow)
        for slot in self._slots:
            if overflow:
                slot.setFixedWidth(_SLOT_MIN_WIDTH)
            else:
                slot.setMinimumWidth(_SLOT_MIN_WIDTH)
                slot.setMaximumWidth(_SLOT_MAX_WIDTH)