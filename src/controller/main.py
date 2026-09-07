"""
main.py — DATUM Control entry point

Start order:
    1. Parse arguments
    2. Configure OpenGL surface format (MUST be before QApplication)
    3. Create QApplication
    4. Create ThemeManager, apply saved/default theme
    5. Create backend  (SimulatedBackend | LinuxCNCBackend)
    6. Create MachineController
    7. Create MainWindow (passes ThemeManager)
    8. Show window
    9. Start poll loop  <-- after show() so signals find connected slots
   10. Run Qt event loop
   11. Cleanup on exit

OpenGL surface format is set before QApplication so QOpenGLWidget picks it
up for every surface created in the session. This is required for ModernGL
3.3 Core Profile; calling it after the first surface is created is silently
ignored by Qt on most platforms.

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

from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from controller.core.backends.base import AbstractBackend
from controller.core.machine.controller import MachineController
from controller.ui.main_window import MainWindow
from controller.ui.theme_manager import ThemeManager

logger = logging.getLogger(__name__)


# ── OpenGL configuration ──────────────────────────────────────────────────────

def _configure_opengl() -> None:
    """Set the global default QSurfaceFormat for OpenGL 3.3 Core Profile.

    Must be called BEFORE QApplication() — any surface created after this
    will inherit the format automatically.

    Required by ModernGL which targets OpenGL 3.3 Core Profile. Without depth
    buffer and MSAA the viewport renders incorrectly or fails to initialize.
    """
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)


# ── Backend factory ───────────────────────────────────────────────────────────

def _create_backend(simulate: bool) -> AbstractBackend:
    """Instantiate the correct backend based on the startup flag."""
    if simulate:
        from controller.core.backends.simulated import SimulatedBackend
        return SimulatedBackend()

    # Phase 2 — real hardware (see core/backends/linuxcnc.py). Imported
    # lazily so --simulate never requires the `linuxcnc` Python module,
    # which isn't installed on a machine without LinuxCNC. If it's still
    # missing here, LinuxCNCBackend itself raises a clear
    # LinuxCNCNotAvailableError instead of a confusing
    # "Can't instantiate abstract class" or ModuleNotFoundError.
    from controller.core.backends.linuxcnc import LinuxCNCBackend
    return LinuxCNCBackend()


# ── CLI ───────────────────────────────────────────────────────────────────────

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
    parser.add_argument(
        "--theme",
        default=None,
        help="Override the startup theme (e.g. 'light'). Defaults to last-used theme.",
    )
    return parser.parse_args()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 1. OpenGL format BEFORE QApplication
    _configure_opengl()

    # 2. Qt application
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("DATUM Control")

    # 3. Theme manager — apply theme before any window is shown so the
    #    stylesheet is set before widget polish/resize events fire.
    theme_manager = ThemeManager(qt_app)
    theme_manager.apply_theme(args.theme)   # None → restore last-used / default

    # 4. Backend + controller
    backend    = _create_backend(simulate=args.simulate)
    controller = MachineController(backend=backend)

    # 5. Main window
    window = MainWindow(controller=controller, theme_manager=theme_manager)
    window.show()

    # 6. Start controller poll loop (after show so signals reach connected slots)
    controller.start()
    if args.simulate:
        # Put the simulated machine into a ready state immediately so the UI
        # has something to display without requiring manual estop-reset → on →
        # home clicks.
        backend.estop_reset()
        backend.set_machine_on()
        backend.home_all()

    exit_code = qt_app.exec()

    controller.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
