"""
main.py — DATUM Control entry point

Start order:
    1. Parse arguments
    2. Create backend  (SimulatedBackend | LinuxCNCBackend)
    3. Create MachineController
    4. Create QApplication + MainWindow
    5. Show window
    6. Start poll loop  <-- after show() so signals find connected slots
    7. Run Qt event loop
    8. Cleanup on exit

Usage:
    python main.py --simulate      # Phase 1: SimulatedBackend, no hardware
    python main.py                 # Phase 2: LinuxCNCBackend (not yet implemented)
"""

from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from src.controller.core.backends.base import AbstractBackend
from src.controller.core.machine.controller import MachineController
from src.controller.ui.pages.machine_page import MachinePage


def _create_backend(simulate: bool) -> AbstractBackend:
    """Instantiate the correct backend based on the startup flag."""
    if simulate:
        from src.controller.core.backends.simulated import SimulatedBackend
        return SimulatedBackend()
    else:
        pass

    from src.controller.core.backends.linuxcnc import LinuxCNCBackend
    return LinuxCNCBackend()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DATUM Control — CNC HMI")
    parser.add_argument(
        "--simulate",
        action="store_true",
        default=False,
        help="Run without LinuxCNC (SimulatedBackend)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    qt_app = QApplication(sys.argv)
    print(f"[DEBUG] QApplication existiert bereits: {QApplication.instance() is not None}")
    qt_app.setApplicationName("DATUM Control")

    backend    = _create_backend(simulate=args.simulate)
    controller = MachineController(backend=backend)

    from ui.theme_loader import load_stylesheet
    load_stylesheet(qt_app)

    from ui.main_window import MainWindow
    window = MainWindow(controller=controller)
    window.show()

    controller.start()
    if args.simulate:
        backend.estop_reset()
        backend.set_machine_on()
        backend.home_all()

    exit_code = qt_app.exec()

    controller.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()