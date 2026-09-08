"""
ui/widgets/card_button.py — Clickable card, same visual language as Card.

label is optional — omit it for icon-only buttons.
icon_size controls both the reserved icon slot and the rendered pixmap.

Checkable support: QFrame has no built-in checked state (unlike
QAbstractButton), so it's implemented manually here via a dynamic
property ("checked") that QSS can select on.

pressed/released: QFrame (unlike QAbstractButton) has no built-in
press/release signal pair either. Added here — not jog-specific, but
first needed by JogControlPanel, whose movement buttons must start
motion on press and stop it on release, not on click. clicked keeps
firing on press (unchanged, existing behavior/consumers rely on that),
so a caller that only cares about "was this button activated" doesn't
need to change; a caller that needs the actual hold-duration (like
jogging) uses pressed/released instead.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal, QEvent
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QWidget

from controller.ui.widgets.card import Card


class CardButton(Card):

    clicked = Signal()
    toggled = Signal(bool)
    pressed = Signal()
    released = Signal()

    def __init__(
        self,
        label: str | None = None,
        icon: QIcon | None = None,
        icon_size: int | QSize = 28,
        parent: QWidget | None = None,
        orientation: Qt.Orientation = Qt.Orientation.Vertical,
    ) -> None:
        super().__init__(title=None, parent=parent, orientation=orientation)

        self._icon_size = QSize(icon_size, icon_size) if isinstance(icon_size, int) else icon_size
        self._checkable = False
        self._checked = False
        self._pressed = False
        self.setProperty("checked", False)
        self.setProperty("pressed", False)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Horizontal (icon left, label right — e.g. SettingsPage's nav)
        # left-aligns instead of centering, so the row reads as a normal
        # left-to-right menu item rather than a centered icon+text cluster.
        self.content_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if orientation == Qt.Orientation.Horizontal
            else Qt.AlignmentFlag.AlignCenter
        )



        self._icon_label = QLabel(self)
        self._icon_label.setObjectName("CardButtonIcon")
        self._icon_label.setFixedSize(self._icon_size)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setScaledContents(True)
        self._icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.content_layout.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._text_label: QLabel | None = None
        if label is not None:
            self._text_label = QLabel(label, self)
            self._text_label.setObjectName("CardButtonLabel")
            self._text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.content_layout.addWidget(self._text_label, alignment=Qt.AlignmentFlag.AlignCenter)
        elif icon is not None:
            self.setToolTip("")

        if icon is not None:
            self.set_icon(icon)

        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

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

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.TouchBegin:
            self.pressed.emit()
            self.setProperty("pressed", True)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()
            if self._checkable:
                self.toggle()
            self.clicked.emit()
            return True
        if e.type() in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
            self.setProperty("pressed", False)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()
            self.released.emit()
            return True
        return super().event(e)