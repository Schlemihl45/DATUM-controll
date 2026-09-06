"""
ui/widgets/collapsible_section.py — CollapsibleSection: a generic titled
section with a QToolButton chevron that shows/hides its body.

The accordion pattern ToolPage's ToolCardWidget popularized (see that
module's docstring, "ToolPage: Accordion, Scroll-vs-Klick") for its
inline-expanding tool cards is fixed/hard-wired into that one widget —
this is the first GENERIC, reusable extraction of the same idea, for a
different purpose: a collapsed-by-default sub-section on a detail page (a
G-code preview, a version history list) rather than a whole list row.
Unlike ToolCardWidget's accordion, sections here are independent —
opening one never closes another.

The body is an internally-scrollable QScrollArea (no visible scrollbar;
touch-scroll via QScroller — same idiom as tool_list_card.py's
ToolListView, just with the vertical policy ALSO turned off, since that
one only disables the horizontal bar). Callers add their content to
`section.body_layout`, not to `section.body` directly. Call
set_max_body_height() so an expanded section's content scrolls internally
past that height instead of growing the section (and everything below it
in the parent layout) without bound — see ui/pages/program_detail_page.py,
whose whole-page layout is fixed and relies on this.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QScroller, QToolButton,
    QVBoxLayout, QWidget,
)


class _HeaderRow(QWidget):
    """The arrow+title row — a click anywhere on it (not just the small
    arrow button itself) toggles the section, a bigger/easier target."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class CollapsibleSection(QWidget):
    """Caller fills `.body_layout` with whatever content the section
    should show once expanded; the header (arrow + title) and the
    internally-scrollable body container are built here."""

    expanded_changed = Signal(bool)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        header = _HeaderRow(self)
        header.clicked.connect(lambda: self.set_expanded(not self._expanded))
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)

        self._arrow_btn = QToolButton(header)
        self._arrow_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._arrow_btn.setAutoRaise(True)
        self._arrow_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header_row.addWidget(self._arrow_btn)

        title_lbl = QLabel(title, header)
        title_lbl.setObjectName("CardTitle")
        header_row.addWidget(title_lbl)
        header_row.addStretch(1)
        outer.addWidget(header)

        self.body = QScrollArea(self)
        self.body.setWidgetResizable(True)
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        QScroller.grabGesture(self.body.viewport(), QScroller.ScrollerGestureType.TouchGesture)

        body_content = QWidget()
        self.body_layout = QVBoxLayout(body_content)
        self.body_layout.setContentsMargins(0, 4, 0, 0)
        self.body.setWidget(body_content)

        self.body.setVisible(False)
        outer.addWidget(self.body)

    def set_max_body_height(self, height: int) -> None:
        """Cap the expanded body's height — content beyond it scrolls
        internally instead of growing this section further."""
        self.body.setMaximumHeight(height)

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._arrow_btn.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.body.setVisible(expanded)
        self.expanded_changed.emit(expanded)

    def is_expanded(self) -> bool:
        return self._expanded
