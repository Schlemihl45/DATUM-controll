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

Usage (after `pip install -e .`):
    datum-control --simulate       # Phase 1: SimulatedBackend, no hardware
    datum-control                  # Phase 2: LinuxCNCBackend (not yet implemented)

Usage (without installing, from the repo root):
    python -m controller.main --simulate
"""

from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtWidgets import QApplication

from controller.core.backends.base import AbstractBackend
from controller.core.machine.controller import MachineController
from controller.ui.main_window import MainWindow
from controller.ui.theme_loader import load_stylesheet

logger = logging.getLogger(__name__)


def _create_backend(simulate: bool) -> AbstractBackend:
    """Instantiate the correct backend based on the startup flag."""
    if simulate:
        from controller.core.backends.simulated import SimulatedBackend
        return SimulatedBackend()

    # Phase 2 — real hardware. LinuxCNCBackend only carries a docstring so
    # far (see core/backends/linuxcnc.py); fail loudly instead of letting
    # Python raise a confusing "Can't instantiate abstract class" error.
    raise NotImplementedError(
        "LinuxCNCBackend is not implemented yet. Run with --simulate."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DATUM Control — CNC HMI")
    parser.add_argument(
        "--simulate",
        action="store_true",
        default=False,
        help="Run without LinuxCNC (SimulatedBackend)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("DATUM Control")

    backend = _create_backend(simulate=args.simulate)
    controller = MachineController(backend=backend)

    load_stylesheet(qt_app)

    window = MainWindow(controller=controller)
    window.show()

    controller.start()
    if args.simulate:
        # Bring the simulated machine into a ready state on startup so the
        # UI has something to show immediately, without a human having to
        # click through estop-reset -> on -> home first.
        backend.estop_reset()
        backend.set_machine_on()
        backend.home_all()

    exit_code = qt_app.exec()

    controller.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
