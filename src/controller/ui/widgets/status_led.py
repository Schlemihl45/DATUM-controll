from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QWidget


class StatusLed(QWidget):

    def __init__(self, size: int = 16, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size

        self.setFixedSize(QSize(self._size, self._size))

        self.set_color("#7f8c8d")

    def set_color(self, hex_color: str) -> None:
        border_radius = self._size // 2
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {hex_color};
                border: 2px solid #1a1a1a;
                border-radius: {border_radius}px;
            }}
        """)