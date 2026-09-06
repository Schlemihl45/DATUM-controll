"""
tests/test_main_window.py — Verifies every button in MainWindow that
claims to control the machine actually reaches the backend through
MachineController, using SimulatedBackend as a real (if fake) target.
Not a UI/pixel test — it asserts on backend/controller state.
"""

from __future__ import annotations

from pathlib import Path

from controller.core.backends.simulated import SimulatedBackend
from controller.core.machine.controller import MachineController
from controller.domain.models import ProgramState
from controller.ui.main_window import _HOME_INDEX, _MACHINE_PAGE_INDEX, MainWindow

# Bundled example G-code, still shipped in the repo for exactly this kind
# of test — MachinePage itself no longer loads it automatically (see
# MachinePage.open_workpieces_requested), so tests exercising the Start
# button now load it explicitly first via the public load_file() API.
_EXAMPLE_GCODE_PATH = str(Path(__file__).resolve().parents[1] / "workpieces" / "Gcode.cnc")


def _make_window(machine_on: MachineController) -> MainWindow:
    return MainWindow(controller=machine_on)


def test_navigation_between_home_and_machine_page(qtbot, machine_on):
    win = _make_window(machine_on)
    qtbot.addWidget(win)

    assert win._stack.currentIndex() == _HOME_INDEX

    win._on_machine_btn_clicked()
    assert win._stack.currentIndex() == _MACHINE_PAGE_INDEX

    win._on_return_clicked()
    assert win._stack.currentIndex() == _HOME_INDEX


def test_unimplemented_pages_are_disabled_not_faked(qtbot, machine_on):
    win = _make_window(machine_on)
    qtbot.addWidget(win)

    home_page = win._stack.widget(_HOME_INDEX)
    card_buttons = [
        w for w in home_page.findChildren(type(win.light_btn))
    ]
    # 6 nav buttons total: Machine, Tools, Workpieces, Manuell (renamed
    # from the former "Setup" placeholder — see ManualPage), Settings
    # (all enabled) + Statistics (still a disabled placeholder).
    disabled = [b for b in card_buttons if not b.isEnabled()]
    assert len(disabled) == 1


def test_coolant_button_wires_to_flood_on_off(qtbot, machine_on, backend: SimulatedBackend):
    win = _make_window(machine_on)
    qtbot.addWidget(win)

    assert backend.get_flood() is False

    win.coolant_btn.setChecked(True)
    assert backend.get_flood() is True

    win.coolant_btn.setChecked(False)
    assert backend.get_flood() is False


def test_light_button_sends_mdi_without_crashing(qtbot, machine_on):
    win = _make_window(machine_on)
    qtbot.addWidget(win)

    # SimulatedBackend.send_mdi() only special-cases T<n>; M64/M65 are
    # accepted silently — this asserts the call path doesn't raise,
    # not that a simulated light state changes (there is none yet).
    win.light_btn.setChecked(True)
    win.light_btn.setChecked(False)


def test_start_button_runs_program_via_controller(qtbot, machine_on):
    win = _make_window(machine_on)
    qtbot.addWidget(win)

    machine_page = win._stack.widget(_MACHINE_PAGE_INDEX)
    machine_page.load_file(_EXAMPLE_GCODE_PATH)
    machine_page._on_start_clicked()
    machine_on.poll_once()

    assert machine_on.program_state == ProgramState.RUNNING

    machine_page._on_stop_clicked()
    machine_on.poll_once()
    assert machine_on.program_state == ProgramState.PAUSED


def test_load_file_button_requests_workpieces_navigation(qtbot, machine_on):
    """MachinePage no longer loads a fixed file itself — clicking "Datei
    laden" with nothing loaded yet must ask main_window.py to switch to
    the Workpieces page instead (see MachinePage.open_workpieces_requested
    and main_window.py's connection to it)."""
    win = _make_window(machine_on)
    qtbot.addWidget(win)

    win._on_machine_btn_clicked()
    machine_page = win._stack.widget(_MACHINE_PAGE_INDEX)
    assert machine_page._loaded_path is None

    machine_page._gcode_no_file.open_clicked.emit()

    assert win._stack.currentIndex() == win._stack.indexOf(win._workpieces_section)
