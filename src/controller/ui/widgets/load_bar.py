"""
ui/widgets/load_bar.py — Background load-fill painted behind a row or
card. Left edge rounded (fixed track boundary), right edge stays a
hard cut (moving fill boundary). Fill uses a subtle vertical gradient
for a slight "physical bar" depth instead of flat color.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

_LOW_COLOR = (89, 130, 196)      # dezent, unter der Warnschwelle
_WARN_COLOR = (255, 176, 32)     # #FFB020
_CRITICAL_COLOR = (229, 72, 77)  # #E5484D

_WARN_THRESHOLD = 0.60
_CRITICAL_THRESHOLD = 0.85


class LoadBar(QWidget):
    """Transparent overlay — als erstes Kind hinzufügen und lower(),
    sonst verdeckt der Balken den eigentlichen Inhalt."""

    def __init__(self, radius: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._fraction = 0.0
        self._radius = radius

    def set_load(self, fraction: float) -> None:
        self._fraction = max(0.0, min(fraction, 1.0))
        self.update()

    def _rounded_left_path(self) -> QPainterPath:
        r = self._radius
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.moveTo(w, 0)
        path.lineTo(r, 0)
        path.quadTo(0, 0, 0, r)
        path.lineTo(0, h - r)
        path.quadTo(0, h, r, h)
        path.lineTo(w, h)
        path.closeSubpath()
        return path

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Clip auf die ganze Widget-Form (links gerundet) — dadurch
        # bekommt auch die Füllung automatisch eine gerundete linke
        # Kante, unabhängig vom aktuellen Füllstand.
        painter.setClipPath(self._rounded_left_path())

        if self._fraction >= _CRITICAL_THRESHOLD:
            r, g, b = _CRITICAL_COLOR
        elif self._fraction >= _WARN_THRESHOLD:
            r, g, b = _WARN_COLOR
        else:
            r, g, b = _LOW_COLOR

        fill_width = self.width() * self._fraction
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(r, g, b, 110))
        gradient.setColorAt(1.0, QColor(r, g, b, 55))

        painter.fillRect(QRectF(0, 0, fill_width, self.height()), gradient)