"""
tests/test_simulated_backend.py — Core state-machine behaviour of
SimulatedBackend, independent of the UI.
"""

from __future__ import annotations

import time

from controller.core.backends.simulated import SimulatedBackend
from controller.core.machine.controller import MachineController
from controller.domain.models import MachineState, ProgramState


def test_state_machine_requires_estop_reset_before_on(backend: SimulatedBackend):
    assert backend.get_machine_state() == MachineState.ESTOP

    backend.set_machine_on()  # precondition not met -> ignored
    assert backend.get_machine_state() == MachineState.ESTOP

    backend.estop_reset()
    assert backend.get_machine_state() == MachineState.ESTOP_RESET

    backend.set_machine_on()
    assert backend.get_machine_state() == MachineState.ON


def test_run_program_requires_on_and_homed(backend: SimulatedBackend):
    backend.estop_reset()
    backend.set_machine_on()
    # not homed yet -> rejected, stays IDLE
    backend.run_program("workpieces/Gcode.cnc")
    assert backend.get_program_state() == ProgramState.IDLE

    backend.home_all()
    backend.run_program("workpieces/Gcode.cnc")
    assert backend.get_program_state() == ProgramState.RUNNING


def test_resume_from_single_block_sets_spindle_to_8000_rpm(backend: SimulatedBackend):
    """Regression test for a fixed typo: resume_program() used to set
    25000.0 rpm instead of the 8000.0 rpm used everywhere else."""
    backend.estop_reset()
    backend.set_machine_on()
    backend.home_all()
    backend.set_single_block(True)
    backend.run_program("workpieces/Gcode.cnc")
    backend.pause_program()

    backend.resume_program()

    assert backend._spindle_target_rpm == 8000.0


def test_jog_continuous_moves_position_over_time(backend: SimulatedBackend):
    """Regression test: jog_continuous() used to be a no-op stub ("Phase 1:
    no continuous motion tracking") — pressing a Jog button in continuous
    mode (ManualPage's JogControlPanel, no step size selected) never moved
    the axis display at all, only the discrete step mode (jog_increment())
    did anything. poll() must now actually advance position for as long as
    a jog stays active, exactly like a real controller's DRO would."""
    backend.estop_reset()
    backend.set_machine_on()
    backend.home_all()

    backend.jog_continuous(0, 10.0)   # X+ at 10 mm/s
    # Simulate ~100ms having passed since the last poll() tick, without an
    # actual sleep() — poll() computes dt from _last_poll_time itself.
    backend._last_poll_time = time.monotonic() - 0.1
    backend.poll()

    assert backend.get_position().x > 0.0, "continuous jog must move position"

    x_after_first_tick = backend.get_position().x
    backend._last_poll_time = time.monotonic() - 0.1
    backend.poll()
    assert backend.get_position().x > x_after_first_tick, "jog must keep moving until stopped"

    backend.jog_stop(0)
    x_after_stop = backend.get_position().x
    backend._last_poll_time = time.monotonic() - 0.1
    backend.poll()
    assert backend.get_position().x == x_after_stop, "jog_stop() must halt further motion"


def test_jog_continuous_direction_and_axis(backend: SimulatedBackend):
    backend.estop_reset()
    backend.set_machine_on()
    backend.home_all()

    backend.jog_continuous(1, -5.0)   # Y- at 5 mm/s
    backend._last_poll_time = time.monotonic() - 0.1
    backend.poll()

    pos = backend.get_position()
    assert pos.y < 0.0
    assert pos.x == 0.0 and pos.z == 0.0   # only the jogged axis moves


def test_jog_continuous_through_machine_controller(machine_on: MachineController):
    """Same behaviour through the public MachineController API (what
    ManualPage's JogControlPanel actually calls) — including the
    position_changed signal firing, so the axis display card really would
    update."""
    backend = machine_on._backend
    positions_seen: list[float] = []
    machine_on.position_changed.connect(lambda p: positions_seen.append(p.x))

    machine_on.jog_continuous(0, 20.0)
    backend._last_poll_time = time.monotonic() - 0.1
    machine_on.poll_once()

    assert positions_seen, "position_changed must fire while jogging"
    assert positions_seen[-1] > 0.0

    machine_on.jog_stop(0)
    backend._last_poll_time = time.monotonic() - 0.1
    machine_on.poll_once()
    # No further forward motion once stopped (poll_once() may still emit
    # once more if some OTHER field changed, but x itself must not grow).
    assert backend.get_position().x == positions_seen[-1]


def test_estop_clears_active_jog(backend: SimulatedBackend):
    backend.estop_reset()
    backend.set_machine_on()
    backend.home_all()
    backend.jog_continuous(2, 15.0)

    backend.estop()

    assert backend._active_jogs == {}
