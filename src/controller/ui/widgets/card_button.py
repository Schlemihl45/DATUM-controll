"""
ui/widgets/card_button.py — Clickable card, same visual language as Card.

label is optional — omit it for icon-only buttons.
icon_size controls both the reserved icon slot and the rendered pixmap.

Checkable support: QFrame has no built-in checked state (unlike
QAbstractButton), so it's implemented manually here via a dynamic
property ("checked") that QSS can select on.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QWidget

from controller.ui.widgets.card import Card


class CardButton(Card):

    clicked = Signal()
    toggled = Signal(bool)

    def __init__(
        self,
        label: str | None = None,
        icon: QIcon | None = None,
        icon_size: int | QSize = 28,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title=None, parent=parent)

        self._icon_size = QSize(icon_size, icon_size) if isinstance(icon_size, int) else icon_size
        self._checkable = False
        self._checked = False
        self.setProperty("checked", False)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_label = QLabel(self)
        self._icon_label.setObjectName("CardButtonIcon")
        self._icon_label.setFixedSize(self._icon_size)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._text_label: QLabel | None = None
        if label is not None:
            self._text_label = QLabel(label, self)
            self._text_label.setObjectName("CardButtonLabel")
            self._text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content_layout.addWidget(self._text_label, alignment=Qt.AlignmentFlag.AlignCenter)
        elif icon is not None:
            self.setToolTip("")

        if icon is not None:
            self.set_icon(icon)

    def set_icon(self, icon: QIcon) -> None:
        self._icon_label.setPixmap(icon.pixmap(self._icon_size))

    def set_text(self, text: str) -> None:
        self._text_label.setText(text)

    # ------------------------------------------------------------------
    # Checkable state — manual, since QFrame has none built in
    # ------------------------------------------------------------------

    def setCheckable(self, checkable: bool) -> None:
        self._checkable = checkable

    def isCheckable(self) -> bool:
        return self._checkable

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if not self._checkable or checked == self._checked:
            return
        self._checked = checked
        self.setProperty("checked", checked)
        # Dynamic property changes don't auto-trigger a QSS repaint —
        # force the style to re-evaluate selectors for this widget.
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
        self.toggled.emit(checked)

    def toggle(self) -> None:
        self.setChecked(not self._checked)

    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._checkable:
                self.toggle()
            self.clicked.emit()
        super().mousePressEvent(event)