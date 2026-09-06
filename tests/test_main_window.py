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
    """Verifies Start/Stop wiring reaches the controller — NOT the
    collision pre-check dialog's UX, which is why collision detection is
    disabled here: _EXAMPLE_GCODE_PATH's bundled example program has a
    real, correctly-detected shank collision at line 12 (confirmed via
    run_prepass()), and its confirmation dialog defaults to "Abbrechen"
    with no one there to click "Trotzdem starten" in a headless test —
    that's a real, intentional safety behavior, not a bug, but it isn't
    what this test is about."""
    win = _make_window(machine_on)
    qtbot.addWidget(win)

    machine_page = win._stack.widget(_MACHINE_PAGE_INDEX)
    # Patch this widget's OWN method rather than the global
    # AppSettings.collision_detection_enabled singleton — flipping that
    # global setting fires collision_detection_enabled_changed at every
    # SimPanel instance alive in the process, including ones left over
    # (with already-C++-deleted checkboxes) from earlier tests in the
    # same session, causing an unrelated RuntimeError there.
    machine_page._sim._effective_collision_enabled = lambda: False

    with qtbot.waitSignal(machine_page._sim.file_ready, timeout=5000):
        machine_page.load_file(_EXAMPLE_GCODE_PATH)
    # load_file()'s G-code compile now runs in a background thread (see
    # DatumSimWidget.set_file()) — _on_start_clicked() is a deliberate,
    # hard no-op until _file_ready is True (tool validation/collision
    # pre-check would otherwise run against a stale/empty program), so
    # this test must wait for the real completion signal rather than
    # assuming load_file() itself was synchronous, as it used to be.
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
