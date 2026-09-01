"""
tests/test_simulated_backend.py — Core state-machine behaviour of
SimulatedBackend, independent of the UI.
"""

from __future__ import annotations

from controller.core.backends.simulated import SimulatedBackend
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
