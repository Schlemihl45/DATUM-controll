"""
ui/widgets/status_bar.py — Persistent top bar: log/console output +
clock. Always visible, independent of the active page.

Log shows the most recent controller message, color-coded by
severity. Reads exclusively via MachineController signals — never
touches the backend directly.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel

from src.controller.core.machine.controller import MachineController
from src.controller.domain.models import ErrorSeverity, MachineError

_SEVERITY_PROPERTY_VALUES = {
    ErrorSeverity.INFO: "info",
    ErrorSeverity.WARNING: "warning",
    ErrorSeverity.ERROR: "error",
}


class StatusBar(QWidget):

    def __init__(
        self,
        controller: MachineController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller

        self.setObjectName("StatusBar")
        self.setFixedHeight(32)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(24)

        self._log_label = QLabel("Bereit.", self)
        self._log_label.setObjectName("StatusBarLog")
        layout.addWidget(self._log_label, stretch=1)

        self._clock_label = QLabel(self)
        self._clock_label.setObjectName("StatusBarClock")
        layout.addWidget(self._clock_label)

        controller.error_occurred.connect(self._on_error)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._update_clock()

    def _on_error(self, error: MachineError) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._log_label.setText(f"[{timestamp}] {error.message}")
        self._log_label.setProperty(
            "severity", _SEVERITY_PROPERTY_VALUES.get(error.severity, "info")
        )
        self._log_label.style().unpolish(self._log_label)
        self._log_label.style().polish(self._log_label)

    def _update_clock(self) -> None:
        self._clock_label.setText(datetime.now().strftime("%H:%M:%S"))