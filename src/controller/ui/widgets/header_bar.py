from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from src.controller.domain.models import MachineState

if TYPE_CHECKING:
    from src.controller.core.machine.controller import MachineController


class HeaderBar(QWidget):
    """
    Kopfzeile der Anwendung.
    Zeigt links eine Status-LED (basierend auf dem MachineState) und daneben
    den Namen oder aktuellen Status der Anwendung als Text.
    """

    def __init__(self, controller: MachineController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctrl = controller

        self._init_ui()

        # Signal-Slot-Verbindung für automatische Updates der LED
        self._ctrl.machine_state_changed.connect(self._on_machine_state_changed)

        # Initialen Zustand direkt setzen
        self._on_machine_state_changed(self._ctrl.machine_state)

    def _init_ui(self) -> None:
        # Ein horizontales Layout für LED + Text
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 5, 15, 5)
        layout.setSpacing(12)

        # 1. Status-LED (als rundes QLabel via CSS)
        self.led = QLabel()
        self.led.setFixedSize(16, 16)
        layout.addWidget(self.led, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 2. Text-Label (Titel oder Info)
        self.title_label = QLabel("Title")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        layout.addWidget(self.title_label, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Optional: Dehnt das Layout nach rechts aus, damit alles linksbündig bleibt
        layout.addStretch(1)

        # Grund-Styling der Header-Bar selbst
        self.setStyleSheet("background-color: #1a1a1a; min-height: 40px;")

    def _on_machine_state_changed(self, state: MachineState) -> None:
        """Wird alle 50ms gefeuert, wenn sich der Zustand der CNC ändert."""
        # Farb-Mapping basierend auf dem Domain-Model MachineState
        match state:
            case MachineState.ON:
                color = "#2ecc71"  # Grün (Betriebsbereit)
            case MachineState.OFF:
                color = "#f39c12"  # Orange (Gekoppelt, aber Motoren aus)
            case MachineState.ESTOP_RESET:
                color = "#3498db"  # Blau (E-Stop quittiert, wartet auf Einschalten)
            case MachineState.ESTOP | _:
                color = "#e74c3c"  # Rot (Not-Aus aktiv / Fehler)

        # LED über border-radius kreisrund machen und Farbe injizieren
        self.led.setStyleSheet(
            f"background-color: {color}; "
            f"border-radius: 8px; "
            f"border: 1px solid rgba(255, 255, 255, 0.2);"
        )