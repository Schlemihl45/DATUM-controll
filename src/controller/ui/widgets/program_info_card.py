"""
ui/widgets/program_info_card.py — Program name, runtime, estimated
total time, modal state (WCS).

Runtime is tracked locally here (QTimer, 1s tick) — the controller
has no elapsed-time concept, only instantaneous state. Estimated
total time is a placeholder: it needs path-length/feedrate analysis
from datum_sim's PathBuffer, not built yet.

Plane (G17/18/19) and units (G20/G21) are placeholders too — the
backend currently only exposes active_wcs, not the full modal group.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QLabel, QGridLayout

from src.controller.core.machine.controller import MachineController
from src.controller.domain.models import ProgramState
from src.controller.ui.widgets.card import Card

_WCS_NAMES = {1: "G54", 2: "G55", 3: "G56", 4: "G57", 5: "G58", 6: "G59",
              7: "G59.1", 8: "G59.2", 9: "G59.3"}

class ProgramInfoCard(Card):
    def __init__(self, controller: MachineController, parent: QWidget | None = None) -> None:
        super().__init__(title="Program", parent=parent)
        self._controller = controller
        self._elapsed_seconds = 0

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.content_layout.addLayout(grid)

        self._name_label = QLabel("No program loaded")
        self._name_label.setObjectName("InfoValueSecondary")
        grid.addWidget(QLabel("File"), 0, 0)
        grid.addWidget(self._name_label, 0, 1)

        self._runtime_label = QLabel("00:00:00")
        self._runtime_label.setObjectName("InfoValueSecondary")
        grid.addWidget(QLabel("Runtime"), 1, 0)
        grid.addWidget(self._runtime_label, 1, 1)

        self._estimated_label = QLabel("Unknown")
        self._estimated_label.setObjectName("InfoValueSecondary")
        grid.addWidget(QLabel("Approximated"), 2, 0)
        grid.addWidget(self._estimated_label, 2, 1)

        self._wcs_label = QLabel("—")
        self._wcs_label.setObjectName("InfoValueSecondary")
        grid.addWidget(QLabel("WCS"), 3, 0)
        grid.addWidget(self._wcs_label, 3, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        controller.program_state_changed.connect(self._on_program_state)
        controller.file_changed.connect(self._on_file_changed)
        controller.wcs_changed.connect(self._on_wcs_changed)

    def _on_file_changed(self, path: str) -> None:
        self._name_label.setText(Path(path).name if path else "No program loaded")

    def _on_wcs_changed(self, wcs: int) -> None:
        self._wcs_label.setText(_WCS_NAMES.get(wcs, f"#{wcs}"))

    def _on_program_state(self, state: ProgramState) -> None:
        if state == ProgramState.RUNNING:
            self._timer.start()
        elif state == ProgramState.PAUSED:
            self._timer.stop()
        elif state == ProgramState.IDLE:
            self._timer.stop()
            self._elapsed_seconds = 0
            self._runtime_label.setText("00:00:00")

    def _tick(self) -> None:
        self._elapsed_seconds += 1
        h, rem = divmod(self._elapsed_seconds, 3600)
        m,s = divmod(rem, 60)
        self._runtime_label.setText(f"{h:02d}:{m:02d}:{s:02d}")


