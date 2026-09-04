"""
ui/widgets/elided_label.py — ElidedLabel: a QLabel that keeps its own
full text and re-elides ("...") it to fit whenever its width changes.

Plain QLabel has no such behavior (its own elision, when enabled, only
recomputes on setText(), not on a later resize) — needed anywhere a
label's available width isn't fixed at set-text time, e.g.
tool_magazine_bar.py's pocket-slot names (slots stretch/shrink with the
bar) and tool_card_widget.py's collapsed-header info grid (the card's
width tracks the list's).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QWidget


class ElidedLabel(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self._recompute()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recompute()

    def _recompute(self) -> None:
        fm = QFontMetrics(self.font())
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, self.width())
        super().setText(elided)
