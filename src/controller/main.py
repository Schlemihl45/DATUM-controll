"""
main.py - entry point for the controll software

Starts:
    1. The Controller (MockUp or LinuxCNC)
    2. The PySide6 UI

Arguments:
    --simulate Starts the app without LinuxCNC-Connection
"""

from PySide6.QtWidgets import QApplication
import sys
import argparse
import threading

from core.machine.controller import MachineController

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CNC Controller")
    parser.add_argument(
        "--simulate",
        action="store_true",
        default=True,
        help="Simulated version without LinuxCNC-Connection",
    )
    return parser.parse_args()


def main() -> None:
    controller = MachineController()
    controller.connect()

    qt_app = QApplication(sys.argv)
    cnc_app = CNCApplication(controller=controller)
    cnc_app.show()

    # Qt blockiert hier bis das Fenster geschlossen wird
    exit_code = qt_app.exec()

    # Cleanup nach UI-Ende
    controller.disconnect()
    sys.exit(exit_code)

if __name__ == "__main__":
    main()