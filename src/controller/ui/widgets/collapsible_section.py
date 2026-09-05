"""
ui/widgets/collapsible_section.py — CollapsibleSection: a titled section
with a QToolButton arrow that shows/hides its body.

The accordion pattern ToolPage's ToolCardWidget popularized (see that
module's docstring, "ToolPage: Accordion, Scroll-vs-Klick") for its
inline-expanding tool cards, reused here for a different purpose: a
collapsed-by-default sub-section on a detail page (a G-code preview, a
version history list, a used-tools list) rather than a whole list row.
Unlike ToolCardWidget's accordion, sections here are independent — opening
one never closes another.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget


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
    """Caller fills `.body`'s layout with whatever content the section
    should show once expanded; the header (arrow + title) is built here."""

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

        self.body = QWidget(self)
        self.body.setVisible(False)
        outer.addWidget(self.body)

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
