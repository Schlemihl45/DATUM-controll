"""
tests/test_linuxcnc_backend.py — Structural verification of
LinuxCNCBackend against mocked `linuxcnc.stat()` / `command()` /
`error_channel()` objects.

No real LinuxCNC installation is available in this environment (the
`linuxcnc` package ships with a LinuxCNC install, it's not a PyPI
dependency) — see fake_linuxcnc.py for why and how a fake module is
injected into sys.modules so `controller.core.backends.linuxcnc`'s own
`import linuxcnc` succeeds. These tests can't replace a run against a
real/simulated LinuxCNC instance (see the task's Schritt 7) — they only
confirm that LinuxCNCBackend calls the exact stat fields / cmd methods
that AbstractBackend's docstrings (core/backends/base.py) specify, in
the order specified where order matters.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fake_linuxcnc  # noqa: E402

# Must happen before the first `import controller.core.backends.linuxcnc`
# anywhere in the test session, so that module's own `import linuxcnc`
# resolves to our fake instead of failing with ModuleNotFoundError.
sys.modules["linuxcnc"] = fake_linuxcnc

import controller.core.backends.linuxcnc as lcnc_module  # noqa: E402
from controller.core.backends.linuxcnc import (  # noqa: E402
    LinuxCNCBackend,
    LinuxCNCNotAvailableError,
)
from controller.domain.models import (  # noqa: E402
    ErrorSeverity,
    MachineState,
    ProgramState,
)

FL = fake_linuxcnc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_stat() -> MagicMock:
    m = MagicMock()
    m.task_state = FL.STATE_ESTOP
    m.homed = (0, 0, 0)
    m.joints = 3
    m.inpos = True
    m.task_paused = 0
    m.interp_state = FL.INTERP_IDLE
    m.file = ""
    m.current_line = 0
    m.distance_to_go = 0.0
    m.optional_stop = 0
    m.block_delete = 0
    m.feed_hold_enabled = 0
    m.actual_position = (0.0,) * 9
    m.g5x_index = 1
    m.g5x_offset = (0.0,) * 9
    m.current_vel = 0.0
    m.spindle = [{"speed": 0.0, "brake": False}]
    m.feedrate = 1.0
    m.rapidrate = 1.0
    m.flood = FL.FLOOD_OFF
    m.mist = FL.MIST_OFF
    m.lube = 0
    m.tool_in_spindle = 0
    m.task_mode = FL.MODE_MANUAL
    return m


@pytest.fixture
def mock_cmd() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_err() -> MagicMock:
    m = MagicMock()
    m.poll.return_value = None
    return m


@pytest.fixture
def backend(mock_stat: MagicMock, mock_cmd: MagicMock, mock_err: MagicMock) -> LinuxCNCBackend:
    return LinuxCNCBackend(
        stat_factory=lambda: mock_stat,
        command_factory=lambda: mock_cmd,
        error_factory=lambda: mock_err,
    )


def _make_on_homed_idle(mock_stat: MagicMock) -> None:
    """Bring the mock stat into ON + homed + INTERP_IDLE, the common
    precondition for run_program()/send_mdi()/jog_continuous()."""
    mock_stat.task_state = FL.STATE_ON
    mock_stat.homed = (1, 1, 1)
    mock_stat.joints = 3
    mock_stat.interp_state = FL.INTERP_IDLE
    mock_stat.task_paused = 0


# ---------------------------------------------------------------------------
# Construction / import-availability
# ---------------------------------------------------------------------------

def test_stat_cmd_err_created_exactly_once(
    mock_stat: MagicMock, mock_cmd: MagicMock, mock_err: MagicMock
) -> None:
    stat_factory = MagicMock(return_value=mock_stat)
    command_factory = MagicMock(return_value=mock_cmd)
    error_factory = MagicMock(return_value=mock_err)
    b = LinuxCNCBackend(
        stat_factory=stat_factory, command_factory=command_factory, error_factory=error_factory
    )
    b.poll()
    b.poll()
    b.get_machine_state()
    stat_factory.assert_called_once()
    command_factory.assert_called_once()
    error_factory.assert_called_once()


def test_raises_clear_error_when_linuxcnc_module_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(lcnc_module, "linuxcnc", None)
    with pytest.raises(LinuxCNCNotAvailableError):
        LinuxCNCBackend(
            stat_factory=MagicMock(), command_factory=MagicMock(), error_factory=MagicMock()
        )


# ---------------------------------------------------------------------------
# Reads — machine state
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        (FL.STATE_ESTOP, MachineState.ESTOP),
        (FL.STATE_ESTOP_RESET, MachineState.ESTOP_RESET),
        (FL.STATE_OFF, MachineState.OFF),
        (FL.STATE_ON, MachineState.ON),
    ],
)
def test_get_machine_state_mapping(backend, mock_stat, raw, expected) -> None:
    mock_stat.task_state = raw
    backend.poll()
    assert backend.get_machine_state() == expected


def test_is_homed_true_only_when_all_configured_joints_homed(backend, mock_stat) -> None:
    mock_stat.homed = (1, 1, 1, 0)
    mock_stat.joints = 3
    backend.poll()
    assert backend.is_homed() is True

    mock_stat.homed = (1, 1, 0, 1)
    mock_stat.joints = 3
    backend.poll()
    assert backend.is_homed() is False


def test_get_inpos(backend, mock_stat) -> None:
    mock_stat.inpos = False
    backend.poll()
    assert backend.get_inpos() is False
    mock_stat.inpos = True
    backend.poll()
    assert backend.get_inpos() is True


# ---------------------------------------------------------------------------
# Reads — program / interpreter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "interp_state,task_paused,expected",
    [
        (FL.INTERP_IDLE, 0, ProgramState.IDLE),
        (FL.INTERP_READING, 0, ProgramState.RUNNING),
        (FL.INTERP_WAITING, 0, ProgramState.RUNNING),
        (FL.INTERP_PAUSED, 0, ProgramState.PAUSED),
        (FL.INTERP_READING, 1, ProgramState.PAUSED),  # task_paused wins
    ],
)
def test_get_program_state_mapping(backend, mock_stat, interp_state, task_paused, expected) -> None:
    mock_stat.interp_state = interp_state
    mock_stat.task_paused = task_paused
    backend.poll()
    assert backend.get_program_state() == expected


def test_program_reads(backend, mock_stat) -> None:
    mock_stat.file = "/workpieces/part.ngc"
    mock_stat.current_line = 42
    mock_stat.distance_to_go = 3.5
    mock_stat.optional_stop = 1
    mock_stat.block_delete = 1
    mock_stat.feed_hold_enabled = 1
    backend.poll()
    assert backend.get_loaded_file() == "/workpieces/part.ngc"
    assert backend.get_current_line() == 42
    assert backend.get_distance_to_go() == 3.5
    assert backend.get_optional_stop() is True
    assert backend.get_block_delete() is True
    assert backend.get_feed_hold() is True


# ---------------------------------------------------------------------------
# Reads — position / WCS
# ---------------------------------------------------------------------------

def test_get_position_drops_uvw(backend, mock_stat) -> None:
    mock_stat.actual_position = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 99.0, 98.0, 97.0)
    backend.poll()
    pos = backend.get_position()
    assert (pos.x, pos.y, pos.z, pos.a, pos.b, pos.c) == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_get_wcs_offset_same_mapping(backend, mock_stat) -> None:
    mock_stat.g5x_offset = (10.0, 20.0, 30.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    backend.poll()
    off = backend.get_wcs_offset()
    assert (off.x, off.y, off.z) == (10.0, 20.0, 30.0)


def test_get_active_wcs(backend, mock_stat) -> None:
    mock_stat.g5x_index = 3
    backend.poll()
    assert backend.get_active_wcs() == 3


# ---------------------------------------------------------------------------
# Reads — feed / spindle / axis loads
# ---------------------------------------------------------------------------

def test_get_feed_data(backend, mock_stat) -> None:
    mock_stat.current_vel = 12.5
    mock_stat.spindle = [{"speed": 8000.0, "brake": False}]
    mock_stat.feedrate = 0.75
    backend.poll()
    fd = backend.get_feed_data()
    assert fd.feed_actual == 12.5
    assert fd.spindle_rpm == 8000.0
    assert fd.feed_override == 0.75


def test_get_rapid_override(backend, mock_stat) -> None:
    mock_stat.rapidrate = 0.5
    backend.poll()
    assert backend.get_rapid_override() == 0.5


def test_get_axis_loads_always_zero_no_hal_access(backend, mock_stat) -> None:
    # AbstractBackend explicitly excludes HAL-layer access; NML/stat has
    # no per-axis load, so this always returns the zeroed default.
    backend.poll()
    loads = backend.get_axis_loads()
    assert loads.x.percent == 0.0
    assert loads.y.percent == 0.0
    assert loads.z.percent == 0.0


# ---------------------------------------------------------------------------
# Reads — coolant / tool
# ---------------------------------------------------------------------------

def test_coolant_reads(backend, mock_stat) -> None:
    mock_stat.flood = FL.FLOOD_ON
    mock_stat.mist = FL.MIST_ON
    mock_stat.lube = 1
    backend.poll()
    assert backend.get_flood() is True
    assert backend.get_mist() is True
    assert backend.get_lube() is True

    mock_stat.flood = FL.FLOOD_OFF
    mock_stat.mist = FL.MIST_OFF
    backend.poll()
    assert backend.get_flood() is False
    assert backend.get_mist() is False


def test_tool_reads(backend, mock_stat) -> None:
    mock_stat.tool_in_spindle = 7
    mock_stat.spindle = [{"speed": 0.0, "brake": True}]
    backend.poll()
    assert backend.get_tool_in_spindle() == 7
    assert backend.get_spindle_brake() is True


# ---------------------------------------------------------------------------
# poll() error handling
# ---------------------------------------------------------------------------

def test_poll_catches_stat_error_without_crashing(backend, mock_stat) -> None:
    mock_stat.poll.side_effect = FL.error("NML connection lost")
    backend.poll()  # must not raise
    err = backend.get_error()
    assert err is not None
    assert err.severity == ErrorSeverity.CRITICAL


def test_poll_keeps_last_cache_when_stat_poll_fails(backend, mock_stat) -> None:
    mock_stat.task_state = FL.STATE_ON
    backend.poll()
    assert backend.get_machine_state() == MachineState.ON

    mock_stat.poll.side_effect = FL.error("gone")
    backend.poll()
    assert backend.get_machine_state() == MachineState.ON  # unchanged, not reset


# ---------------------------------------------------------------------------
# Error channel — queue semantics
# ---------------------------------------------------------------------------

def test_get_error_drains_queue_in_order(backend, mock_err) -> None:
    mock_err.poll.side_effect = [
        (FL.NML_ERROR, "bad thing happened"),
        (FL.OPERATOR_TEXT, "just fyi"),
        None,
    ]
    backend.poll()

    first = backend.get_error()
    assert first is not None
    assert first.message == "bad thing happened"
    assert first.severity == ErrorSeverity.ERROR

    second = backend.get_error()
    assert second is not None
    assert second.message == "just fyi"
    assert second.severity == ErrorSeverity.INFO

    assert backend.get_error() is None


def test_get_error_none_when_channel_empty(backend, mock_err) -> None:
    mock_err.poll.return_value = None
    backend.poll()
    assert backend.get_error() is None


# ---------------------------------------------------------------------------
# Commands — machine state
# ---------------------------------------------------------------------------

def test_estop_always_allowed(backend, mock_cmd) -> None:
    backend.estop()
    mock_cmd.state.assert_called_once_with(FL.STATE_ESTOP)


def test_estop_reset_precondition(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_ESTOP
    backend.poll()
    backend.estop_reset()
    mock_cmd.state.assert_called_once_with(FL.STATE_ESTOP_RESET)


def test_estop_reset_noop_when_not_in_estop(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_ON
    backend.poll()
    backend.estop_reset()
    mock_cmd.state.assert_not_called()


def test_set_machine_on_precondition(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_ESTOP_RESET
    backend.poll()
    backend.set_machine_on()
    mock_cmd.state.assert_called_once_with(FL.STATE_ON)


def test_set_machine_on_noop_otherwise(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_ESTOP
    backend.poll()
    backend.set_machine_on()
    mock_cmd.state.assert_not_called()


def test_set_machine_off_precondition(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_ON
    backend.poll()
    backend.set_machine_off()
    mock_cmd.state.assert_called_once_with(FL.STATE_OFF)


def test_set_machine_off_noop_otherwise(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_OFF
    backend.poll()
    backend.set_machine_off()
    mock_cmd.state.assert_not_called()


# ---------------------------------------------------------------------------
# Commands — program: run_program() order matters
# ---------------------------------------------------------------------------

def test_run_program_call_order(backend, mock_stat, mock_cmd) -> None:
    _make_on_homed_idle(mock_stat)
    backend.poll()

    backend.run_program("/workpieces/part.ngc")

    assert mock_cmd.mock_calls == [
        call.mode(FL.MODE_AUTO),
        call.wait_complete(),
        call.program_open("/workpieces/part.ngc"),
        call.auto(FL.AUTO_RUN, 0),
    ]


def test_run_program_rejected_when_not_on(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_OFF
    backend.poll()
    backend.run_program("/workpieces/part.ngc")
    assert mock_cmd.mock_calls == []
    err = backend.get_error()
    assert err is not None
    assert err.severity == ErrorSeverity.ERROR


def test_run_program_rejected_when_not_homed(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_ON
    mock_stat.homed = (0, 0, 0)
    mock_stat.joints = 3
    backend.poll()
    backend.run_program("/workpieces/part.ngc")
    assert mock_cmd.mock_calls == []
    err = backend.get_error()
    assert err is not None


def test_run_program_rejected_when_already_running(backend, mock_stat, mock_cmd) -> None:
    _make_on_homed_idle(mock_stat)
    mock_stat.interp_state = FL.INTERP_READING
    backend.poll()
    backend.run_program("/workpieces/part.ngc")
    assert mock_cmd.mock_calls == []
    err = backend.get_error()
    assert err is not None
    assert err.severity == ErrorSeverity.INFO


# ---------------------------------------------------------------------------
# Commands — program: pause/resume/stop/auto_step
# ---------------------------------------------------------------------------

def test_pause_program_precondition(backend, mock_stat, mock_cmd) -> None:
    mock_stat.interp_state = FL.INTERP_READING  # -> ProgramState.RUNNING
    backend.poll()
    backend.pause_program()
    mock_cmd.auto.assert_called_once_with(FL.AUTO_PAUSE)


def test_pause_program_noop_when_idle(backend, mock_stat, mock_cmd) -> None:
    mock_stat.interp_state = FL.INTERP_IDLE
    backend.poll()
    backend.pause_program()
    mock_cmd.auto.assert_not_called()


def test_resume_program_precondition(backend, mock_stat, mock_cmd) -> None:
    mock_stat.interp_state = FL.INTERP_READING
    mock_stat.task_paused = 1  # -> ProgramState.PAUSED
    backend.poll()
    backend.resume_program()
    mock_cmd.auto.assert_called_once_with(FL.AUTO_RESUME)


def test_resume_program_noop_when_not_paused(backend, mock_stat, mock_cmd) -> None:
    mock_stat.interp_state = FL.INTERP_IDLE
    mock_stat.task_paused = 0
    backend.poll()
    backend.resume_program()
    mock_cmd.auto.assert_not_called()


@pytest.mark.parametrize(
    "interp_state,task_paused",
    [(FL.INTERP_READING, 0), (FL.INTERP_READING, 1)],
)
def test_stop_program_precondition(backend, mock_stat, mock_cmd, interp_state, task_paused) -> None:
    mock_stat.interp_state = interp_state
    mock_stat.task_paused = task_paused
    backend.poll()
    backend.stop_program()
    mock_cmd.abort.assert_called_once_with()


def test_stop_program_noop_when_idle(backend, mock_stat, mock_cmd) -> None:
    mock_stat.interp_state = FL.INTERP_IDLE
    backend.poll()
    backend.stop_program()
    mock_cmd.abort.assert_not_called()


def test_auto_step_requires_loaded_file(backend, mock_stat, mock_cmd) -> None:
    mock_stat.file = ""
    backend.poll()
    backend.auto_step()
    mock_cmd.auto.assert_not_called()

    mock_stat.file = "/workpieces/part.ngc"
    mock_stat.interp_state = FL.INTERP_IDLE
    backend.poll()
    backend.auto_step()
    mock_cmd.auto.assert_called_once_with(FL.AUTO_STEP)


def test_auto_step_noop_while_running(backend, mock_stat, mock_cmd) -> None:
    mock_stat.file = "/workpieces/part.ngc"
    mock_stat.interp_state = FL.INTERP_READING
    backend.poll()
    backend.auto_step()
    mock_cmd.auto.assert_not_called()


def test_single_block_is_local_flag_no_cmd_call(backend, mock_cmd) -> None:
    assert backend.get_single_block() is False
    backend.set_single_block(True)
    assert backend.get_single_block() is True
    assert mock_cmd.mock_calls == []


def test_rewind_program_noop_without_loaded_file(backend, mock_stat, mock_cmd) -> None:
    mock_stat.file = ""
    backend.poll()
    backend.rewind_program()
    assert mock_cmd.mock_calls == []


def test_rewind_program_sequence(backend, mock_stat, mock_cmd) -> None:
    mock_stat.file = "/workpieces/part.ngc"
    backend.poll()
    backend.rewind_program()
    assert mock_cmd.mock_calls == [
        call.abort(),
        call.spindle(FL.SPINDLE_OFF),
        call.program_open("/workpieces/part.ngc"),
    ]


# ---------------------------------------------------------------------------
# Commands — interpreter flags
# ---------------------------------------------------------------------------

def test_set_optional_stop(backend, mock_cmd) -> None:
    backend.set_optional_stop(True)
    mock_cmd.set_optional_stop.assert_called_once_with(1)
    backend.set_optional_stop(False)
    mock_cmd.set_optional_stop.assert_called_with(0)


def test_set_block_delete(backend, mock_cmd) -> None:
    backend.set_block_delete(True)
    mock_cmd.set_block_delete.assert_called_once_with(1)


def test_set_feed_hold_precondition(backend, mock_stat, mock_cmd) -> None:
    mock_stat.interp_state = FL.INTERP_READING  # RUNNING
    backend.poll()
    backend.set_feed_hold(True)
    mock_cmd.set_feed_hold.assert_called_once_with(1)


def test_set_feed_hold_noop_when_not_running(backend, mock_stat, mock_cmd) -> None:
    mock_stat.interp_state = FL.INTERP_IDLE
    backend.poll()
    backend.set_feed_hold(True)
    mock_cmd.set_feed_hold.assert_not_called()


def test_reset_interpreter_always_allowed(backend, mock_cmd) -> None:
    backend.reset_interpreter()
    mock_cmd.reset_interpreter.assert_called_once_with()


# ---------------------------------------------------------------------------
# Commands — jog
# ---------------------------------------------------------------------------

def test_jog_continuous_call_order(backend, mock_cmd) -> None:
    backend.jog_continuous(0, 25.0)
    assert mock_cmd.mock_calls == [
        call.mode(FL.MODE_MANUAL),
        call.teleop_enable(False),
        call.jog(FL.JOG_CONTINUOUS, False, 0, 25.0),
    ]


def test_jog_increment(backend, mock_cmd) -> None:
    backend.jog_increment(1, -10.0, 0.5)
    mock_cmd.jog.assert_called_once_with(FL.JOG_INCREMENT, False, 1, -10.0, 0.5)


def test_jog_stop(backend, mock_cmd) -> None:
    backend.jog_stop(2)
    mock_cmd.jog.assert_called_once_with(FL.JOG_STOP, False, 2)


# ---------------------------------------------------------------------------
# Commands — homing
# ---------------------------------------------------------------------------

def test_home_joint_always_allowed(backend, mock_cmd) -> None:
    backend.home_joint(3)
    mock_cmd.home.assert_called_once_with(3)


def test_home_all_precondition(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_ON
    backend.poll()
    backend.home_all()
    mock_cmd.home.assert_called_once_with(-1)


def test_home_all_noop_when_not_on(backend, mock_stat, mock_cmd) -> None:
    mock_stat.task_state = FL.STATE_OFF
    backend.poll()
    backend.home_all()
    mock_cmd.home.assert_not_called()


def test_unhome_joint_always_allowed(backend, mock_cmd) -> None:
    backend.unhome_joint(1)
    mock_cmd.unhome.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# Commands — overrides
# ---------------------------------------------------------------------------

def test_set_feed_override_clamped(backend, mock_cmd) -> None:
    backend.set_feed_override(3.0)
    mock_cmd.feedrate.assert_called_once_with(2.0)
    backend.set_feed_override(-1.0)
    mock_cmd.feedrate.assert_called_with(0.0)


def test_set_rapid_override_clamped(backend, mock_cmd) -> None:
    backend.set_rapid_override(2.0)
    mock_cmd.rapidrate.assert_called_once_with(1.0)


def test_set_spindle_override(backend, mock_cmd) -> None:
    backend.set_spindle_override(1.5)
    mock_cmd.spindleoverride.assert_called_once_with(1.5)


def test_set_max_velocity_floor(backend, mock_cmd) -> None:
    backend.set_max_velocity(0.0)
    mock_cmd.maxvel.assert_called_once_with(0.1)
    backend.set_max_velocity(50.0)
    mock_cmd.maxvel.assert_called_with(50.0)


# ---------------------------------------------------------------------------
# Commands — spindle
# ---------------------------------------------------------------------------

def test_spindle_on_forward(backend, mock_cmd) -> None:
    backend.spindle_on(8000.0, forward=True)
    mock_cmd.spindle.assert_called_once_with(FL.SPINDLE_FORWARD, 8000.0)


def test_spindle_on_reverse(backend, mock_cmd) -> None:
    backend.spindle_on(4000.0, forward=False)
    mock_cmd.spindle.assert_called_once_with(FL.SPINDLE_REVERSE, 4000.0)


def test_spindle_off(backend, mock_cmd) -> None:
    backend.spindle_off()
    mock_cmd.spindle.assert_called_once_with(FL.SPINDLE_OFF)


def test_spindle_brake(backend, mock_cmd) -> None:
    backend.spindle_brake(True)
    mock_cmd.brake.assert_called_once_with(FL.BRAKE_ENGAGE)
    backend.spindle_brake(False)
    mock_cmd.brake.assert_called_with(FL.BRAKE_RELEASE)


# ---------------------------------------------------------------------------
# Commands — coolant
# ---------------------------------------------------------------------------

def test_coolant_commands(backend, mock_cmd) -> None:
    backend.flood_on()
    mock_cmd.flood.assert_called_with(FL.FLOOD_ON)
    backend.flood_off()
    mock_cmd.flood.assert_called_with(FL.FLOOD_OFF)
    backend.mist_on()
    mock_cmd.mist.assert_called_with(FL.MIST_ON)
    backend.mist_off()
    mock_cmd.mist.assert_called_with(FL.MIST_OFF)


# ---------------------------------------------------------------------------
# Commands — limit override / tool management
# ---------------------------------------------------------------------------

def test_override_limits(backend, mock_cmd) -> None:
    backend.override_limits()
    mock_cmd.override_limits.assert_called_once_with()


def test_load_tool_table(backend, mock_cmd) -> None:
    backend.load_tool_table()
    mock_cmd.load_tool_table.assert_called_once_with()


def test_set_tool_offset(backend, mock_cmd) -> None:
    backend.set_tool_offset(1, -50.0, 0.1, 6.0, 0.0, 0.0, 0)
    mock_cmd.tool_offset.assert_called_once_with(1, -50.0, 0.1, 6.0, 0.0, 0.0, 0)


# ---------------------------------------------------------------------------
# Commands — MDI, order matters
# ---------------------------------------------------------------------------

def test_send_mdi_call_order(backend, mock_cmd) -> None:
    backend.send_mdi("G0 X10 Y10")
    assert mock_cmd.mock_calls == [
        call.mode(FL.MODE_MDI),
        call.wait_complete(),
        call.mdi("G0 X10 Y10"),
    ]
