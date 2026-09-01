"""
ui/widgets/override_panel.py — Feed/Rapid/Spindle override sliders.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from controller.core.machine.controller import MachineController
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card import Card

_ICON_SIZE = 32


class _OverrideSlider(QWidget):
    def __init__(self, name: str,icon_name: str, min_pct: int, max_pct: int, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()

        icon = QLabel()
        icon.setObjectName("OverrideLabel")
        icon.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        icon.setPixmap(
            get_icon(icon_name, size=QSize(_ICON_SIZE, _ICON_SIZE)).pixmap(_ICON_SIZE, _ICON_SIZE)
        )

        self._value_label = QLabel("100%")
        self._value_label.setObjectName("OverrideValue")
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        name_label = QLabel(name)
        header.addWidget(name_label)
        header.addWidget(icon)
        header.addStretch(1)
        header.addWidget(self._value_label)
        layout.addLayout(header)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(min_pct, max_pct)
        self.slider.setValue(100)
        self.slider.valueChanged.connect(lambda v: self._value_label.setText(f"{v}%"))
        layout.addWidget(self.slider)


class OverridePanel(Card):
    def __init__(self, controller: MachineController, parent: QWidget | None = None) -> None:
        super().__init__(title="Override", parent=parent)
        self._controller = controller

        row = QHBoxLayout()
        row.setSpacing(24)
        self.content_layout.addLayout(row)

        self._feed = _OverrideSlider("Feedrate", "feedrate_iso", 0, 150)
        self._rapid = _OverrideSlider("Rapid", "rapid", 0, 150)
        self._spindle = _OverrideSlider("Spindle", "spindle", 0, 150)

        self._feed.slider.valueChanged.connect(
            lambda v: controller.set_feed_override(v / 100.0))
        self._rapid.slider.valueChanged.connect(
            lambda v: controller.set_rapid_override(v / 100.0))
        self._spindle.slider.valueChanged.connect(
            lambda v: controller.set_spindle_override(v / 100.0))

        for widget in (self._feed, self._rapid, self._spindle):
            row.addWidget(widget)