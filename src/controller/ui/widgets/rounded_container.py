"""
ui/widgets/rounded_container.py — Generic rounded-corner clip container.

Uses QWidget.setMask() because border-radius in QSS only rounds a
widget's own background paint — it never clips child widgets, which
matters here since the GL viewport fills the container edge-to-edge.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QWidget


class RoundedContainer(QWidget):

    def __init__(self, radius: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))