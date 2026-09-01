"""
ui/widgets/sim_placeholder.py — Stand-in for the planned 3D simulation
view (previously provided by an external, undeclared `datum_sim`
package that is not part of this repository and has no build
dependency entry — importing it made the application unstartable on
any machine that didn't happen to have it installed locally).

SimPlaceholder implements the exact minimal interface MachinePage
needs (set_mode/set_position/set_line/set_state/set_file) as plain
text readouts, so the rest of the page works end-to-end without a 3D
toolkit. Swap this widget out for a real 3D view later — MachinePage
only depends on the method names below, nothing Qt-specific about the
implementation leaks into callers.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget


class SimPlaceholder(QWidget):

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SimPlaceholder")

        self._mode_label = QLabel("SIM")
        self._state_label = QLabel("—")
        self._file_label = QLabel("No program loaded")
        self._line_label = QLabel("0")
        self._position_label = QLabel("X 0.00  Y 0.00  Z 0.00")

        for label in (
            self._mode_label, self._state_label, self._file_label,
            self._line_label, self._position_label,
        ):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        grid = QGridLayout(self)
        grid.addWidget(QLabel("3D-Ansicht (Platzhalter)"), 0, 0, 1, 2,
                        alignment=Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(QLabel("Mode:"), 1, 0)
        grid.addWidget(self._mode_label, 1, 1)
        grid.addWidget(QLabel("State:"), 2, 0)
        grid.addWidget(self._state_label, 2, 1)
        grid.addWidget(QLabel("File:"), 3, 0)
        grid.addWidget(self._file_label, 3, 1)
        grid.addWidget(QLabel("Line:"), 4, 0)
        grid.addWidget(self._line_label, 4, 1)
        grid.addWidget(self._position_label, 5, 0, 1, 2,
                        alignment=Qt.AlignmentFlag.AlignCenter)

    # ------------------------------------------------------------------
    # Minimal interface used by MachinePage — keep in sync with any
    # future real 3D-view implementation.
    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        self._mode_label.setText(mode)

    def set_state(self, state: str) -> None:
        self._state_label.setText(state)

    def set_file(self, path: str) -> None:
        self._file_label.setText(path)

    def set_line(self, line: int) -> None:
        self._line_label.setText(str(line))

    def set_position(self, x: float, y: float, z: float) -> None:
        self._position_label.setText(f"X {x:.2f}  Y {y:.2f}  Z {z:.2f}")
