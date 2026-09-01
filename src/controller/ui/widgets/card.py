from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QGraphicsDropShadowEffect,
    QWidget,
)


class Card(QFrame):
    def __init__(self, title: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setObjectName("Card")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setXOffset(0)
        shadow.setYOffset(8)
        shadow.setColor(QColor(8, 10, 14, 140))
        #self.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(4)
        outer.addLayout(self.content_layout)