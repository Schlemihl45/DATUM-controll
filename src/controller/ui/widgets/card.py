"""
ui/widgets/card.py — Rounded panel with an optional heading, used as
the base for every info card and CardButton in the app.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class Card(QFrame):
    def __init__(self, title: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setObjectName("Card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        if title is not None:
            title_label = QLabel(title, self)
            title_label.setObjectName("CardTitle")
            outer.addWidget(title_label)

        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(4)
        outer.addLayout(self.content_layout)