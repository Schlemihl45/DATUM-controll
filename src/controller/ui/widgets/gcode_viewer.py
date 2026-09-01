"""
ui/widgets/gcode_viewer.py — QPlainTextEdit wrapped with scroll-fade
overlays. Fades are clipped to the container's own rounded-rect shape
so the top/bottom corners stay rounded even while a fade is visible —
otherwise a plain rectangular overlay squares off the corner.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QLinearGradient, QColor, QPainterPath
from PySide6.QtWidgets import QWidget, QPlainTextEdit, QScroller

_RADIUS = 8  # muss zum border-radius von QPlainTextEdit#GCodeView passen


class _FadeOverlay(QWidget):

    def __init__(self, top: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._top = top
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Auf die Form des GESAMTEN Containers clippen, nicht nur des
        # eigenen schmalen Streifens — dadurch bleibt die Ecken-Rundung
        # exakt gleich, egal ob der Fade sichtbar ist oder nicht.
        container = self.parentWidget()
        full_rect = QRectF(0, -self.y(), container.width(), container.height())
        path = QPainterPath()
        path.addRoundedRect(full_rect, _RADIUS, _RADIUS)
        painter.setClipPath(path)

        gradient = QLinearGradient(0, 0, 0, self.height())
        base = QColor(38, 47, 64, 235)
        transparent = QColor(38, 47, 64, 0)

        if self._top:
            gradient.setColorAt(0.0, base)
            gradient.setColorAt(1.0, transparent)
        else:
            gradient.setColorAt(0.0, transparent)
            gradient.setColorAt(1.0, base)

        painter.fillRect(self.rect(), gradient)


class GCodeViewer(QWidget):

    FADE_HEIGHT = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setObjectName("GCodeView")
        self.text_edit.setReadOnly(True)

        self.text_edit.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        QScroller.grabGesture(
            self.text_edit.viewport(), QScroller.ScrollerGestureType.TouchGesture
        )

        self._fade_top = _FadeOverlay(top=True, parent=self)
        self._fade_top.setFixedHeight(self.FADE_HEIGHT)

        self._fade_bottom = _FadeOverlay(top=False, parent=self)
        self._fade_bottom.setFixedHeight(self.FADE_HEIGHT)

        bar = self.text_edit.verticalScrollBar()
        bar.valueChanged.connect(self._update_fades)
        bar.rangeChanged.connect(self._update_fades)

        self._update_fades()

    def setPlainText(self, text: str) -> None:
        self.text_edit.setPlainText(text)
        self._update_fades()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        h = self.FADE_HEIGHT
        self.text_edit.setGeometry(0, 0, self.width(), self.height())
        self._fade_top.setGeometry(0, 0, self.width(), h)
        self._fade_bottom.setGeometry(0, self.height() - h, self.width(), h)
        self._fade_top.raise_()
        self._fade_bottom.raise_()
        self._update_fades()

    def _update_fades(self) -> None:
        bar = self.text_edit.verticalScrollBar()
        self._fade_top.setVisible(bar.value() > bar.minimum())
        self._fade_bottom.setVisible(bar.value() < bar.maximum())