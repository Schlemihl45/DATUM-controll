"""
ui/widgets/tool_drag.py — shared press-and-hold drag gating + scaled
drag-preview pixmap for ToolPage's two drag sources (tool_magazine_bar.py's
_PocketSlot, tool_card_widget.py's _CardHeader).

Both used to start QDrag.exec() unconditionally from mousePressEvent —
meaning every plain click (no movement at all) also fired a full,
blocking drag attempt, swallowing ordinary clicks and rendering a
full-widget-sized drag preview. DragHoldMixin fixes this with the
standard Qt idiom: track the press position, and only actually start the
drag from mouseMoveEvent, once a hold delay has elapsed AND the mouse has
then moved past Qt's own drag-start distance. The hold delay in
particular is what keeps a quick press-release (an ordinary click, or the
start of an unrelated gesture over the card) from ever being
misinterpreted as "start dragging".

_dh_release() additionally reports whether the release itself was a
genuine tap: a press followed by a release with no meaningful movement in
between, regardless of whether the hold timer ever armed. This matters
for a fast touch-scroll flick that starts and ends inside the hold delay
(< 350ms) — the old code only ever checked "was there a press", so that
flick's release still counted as a click and mis-fired an expand/collapse
or a magazine-slot select mid-scroll. Checking the actual travelled
distance instead fixes that regardless of gesture backend.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

_HOLD_MS = 350
# Enlarged per explicit follow-up request (better touch feedback) — still
# capped well below the full source widget's on-screen size (the earlier,
# smaller cap made the preview hard to read while dragging), just no
# longer the previous, much smaller ceiling.
_PREVIEW_MAX_SIZE = QSize(140, 100)


class DragHoldMixin:
    """Mix into a QWidget subclass (plain Python mixin, no QObject base of
    its own — relies on `self` already being a QWidget at runtime). Call
    _dh_press()/_dh_move()/_dh_release() from the corresponding mouse
    event overrides; start the actual QDrag only once _dh_move() returns
    True."""

    def _dh_init(self) -> None:
        self._dh_armed = False
        self._dh_press_pos: QPoint | None = None
        self._dh_timer = QTimer(self)
        self._dh_timer.setSingleShot(True)
        self._dh_timer.timeout.connect(self._dh_arm)

    def _dh_arm(self) -> None:
        self._dh_armed = True

    def _dh_press(self, pos: QPoint) -> None:
        self._dh_press_pos = pos
        self._dh_armed = False
        self._dh_timer.start(_HOLD_MS)

    def _dh_move(self, pos: QPoint) -> bool:
        """Returns True if a drag should start NOW."""
        if self._dh_press_pos is None or not self._dh_armed:
            return False
        moved = (pos - self._dh_press_pos).manhattanLength()
        return moved >= QApplication.startDragDistance()

    def _dh_release(self, pos: QPoint | None = None) -> bool:
        """Stop tracking the current press/drag attempt. If *pos* (the
        release position) is given, returns whether this was a genuine
        tap — a release within Qt's own drag-start distance of the
        original press position — regardless of whether the hold timer
        had armed. Callers not interested in that verdict (e.g. right
        before starting an actual drag) can omit *pos* and ignore the
        return value."""
        self._dh_timer.stop()
        self._dh_armed = False
        was_click = False
        if pos is not None and self._dh_press_pos is not None:
            moved = (pos - self._dh_press_pos).manhattanLength()
            was_click = moved < QApplication.startDragDistance()
        self._dh_press_pos = None
        return was_click


def scaled_drag_pixmap(widget: QWidget, max_size: QSize = _PREVIEW_MAX_SIZE) -> QPixmap:
    """A drastically shrunk drag-preview pixmap — the old
    drag.setPixmap(widget.grab()) rendered the dragged card/slot at full
    on-screen size, which read as far too large while dragging."""
    return widget.grab().scaled(
        max_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
