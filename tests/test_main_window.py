"""
tests/test_main_window.py — Verifies every button in MainWindow that
claims to control the machine actually reaches the backend through
MachineController, using SimulatedBackend as a real (if fake) target.
Not a UI/pixel test — it asserts on backend/controller state.
"""

from __future__ import annotations

from controller.core.backends.simulated import SimulatedBackend
from controller.core.machine.controller import MachineController
from controller.domain.models import ProgramState
from controller.ui.main_window import _HOME_INDEX, _MACHINE_PAGE_INDEX, MainWindow


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
    # 6 nav buttons total: Machine, Tools, Workpieces (enabled) +
    # Setup, Statistics (disabled placeholders) + Settings (enabled).
    disabled = [b for b in card_buttons if not b.isEnabled()]
    assert len(disabled) == 2


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
    machine_page._on_start_clicked()
    machine_on.poll_once()

    assert machine_on.program_state == ProgramState.RUNNING

    machine_page._on_stop_clicked()
    machine_on.poll_once()
    assert machine_on.program_state == ProgramState.PAUSED
