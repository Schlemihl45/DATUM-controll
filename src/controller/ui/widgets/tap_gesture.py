"""
ui/widgets/tap_gesture.py — TapGestureMixin: press/release distance-
threshold tap detection, shared by every card that navigates on click
across WorkpieceBrowserPage, WorkpieceDetailPage, and ProgramDetailPage.

Firing a `clicked` signal straight from mousePressEvent (as several of
these cards used to, and as CardButton — ui/widgets/card_button.py —
still does) means the very START of any gesture over the card, including
the very first moment of a touch-scroll flick, already counts as a click:
a press always happens before Qt/QScroller has any chance to tell a tap
from a scroll apart. tool_card_widget.py's _CardHeader/DragHoldMixin
(ui/widgets/tool_drag.py) already solved exactly this problem for its own
click-vs-drag distinction by deciding on RELEASE, based on how far the
pointer travelled since the matching press — this mixin reuses that same
distance-threshold idea, minus DragHoldMixin's hold-timer/drag-start
machinery (nothing here needs to gate a competing drag, just tell a tap
from a scroll flick apart).

Usage: mix into a QWidget subclass that defines `clicked = Signal()`,
listing TapGestureMixin FIRST in the base class list so its
mousePressEvent/mouseReleaseEvent take priority over any base class's own
(in particular CardButton's, which emits `clicked` on press — see
ui/pages/workpiece_browser_page.py's _NewWorkpieceCard/_NewFolderCard for
that exact case). No init call needed — state is created lazily on first
press.

    class MyCard(TapGestureMixin, Card):
        clicked = Signal()
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


class TapGestureMixin:
    """See module docstring."""

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._tap_press_pos = event.position().toPoint()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        press_pos = getattr(self, "_tap_press_pos", None)
        if event.button() == Qt.MouseButton.LeftButton and press_pos is not None:
            moved = (event.position().toPoint() - press_pos).manhattanLength()
            if moved < QApplication.startDragDistance():
                self.clicked.emit()
        self._tap_press_pos = None
        event.accept()
