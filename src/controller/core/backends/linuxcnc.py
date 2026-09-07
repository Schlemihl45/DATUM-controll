"""
core/backends/linuxcnc.py — Real-hardware backend, talking to a running
LinuxCNC instance through its `linuxcnc` Python module.

Method groups map 1:1 onto the LinuxCNC Python API, exactly as specified
by the docstrings on AbstractBackend (core/backends/base.py):
    stat   -> all get_*() reads    (populated once per poll() call)
    cmd    -> all command methods  (write path)
    err_ch -> get_error()          (error channel, queue semantics)

The `linuxcnc` package is NOT a normal PyPI dependency — it ships with a
LinuxCNC installation and is only importable on a machine that has one.
The import below is therefore deferred and wrapped in try/except so that
merely importing this module (e.g. because a test collects every backend
module, or because some other code enumerates AbstractBackend subclasses)
never fails on a dev machine without LinuxCNC installed. Instantiating
LinuxCNCBackend still requires the real package (or an injected fake, see
the *_factory constructor parameters) and raises a clear error otherwise.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from controller.core.backends.base import AbstractBackend
from controller.domain.models import (
    AxisLoads,
    ErrorSeverity,
    FeedData,
    MachineError,
    MachineState,
    Position,
    ProgramState,
)

_IMPORT_ERROR: ImportError | None
try:
    import linuxcnc
except ImportError as exc:  # pragma: no cover - exercised via test_raises_clear_error_*
    linuxcnc = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class LinuxCNCNotAvailableError(RuntimeError):
    """
    Raised when LinuxCNCBackend is instantiated but the `linuxcnc` Python
    module could not be imported.

    This is expected on any machine that doesn't have LinuxCNC installed
    (e.g. a plain development machine) — run with --simulate instead, or
    pass explicit stat_factory/command_factory/error_factory callables
    (as the tests in tests/test_linuxcnc_backend.py do) to use this class
    without the real package.
    """


# ---------------------------------------------------------------------------
# Internal state cache — populated once per poll(), read by every get_*().
# See AbstractBackend's class docstring: get_*() methods must never
# perform their own IO, they only ever read this cache.
# ---------------------------------------------------------------------------
@dataclass
class _StatCache:
    machine_state: MachineState = MachineState.ESTOP
    homed: bool = False
    inpos: bool = False

    program_state: ProgramState = ProgramState.IDLE
    loaded_file: str = ""
    current_line: int = 0
    distance_to_go: float = 0.0
    optional_stop: bool = False
    block_delete: bool = False
    feed_hold: bool = False

    position: Position = field(default_factory=Position)
    active_wcs: int = 1
    wcs_offset: Position = field(default_factory=Position)

    feed_data: FeedData = field(default_factory=FeedData)
    rapid_override: float = 1.0
    axis_loads: AxisLoads = field(default_factory=AxisLoads)

    flood: bool = False
    mist: bool = False
    lube: bool = False

    tool_in_spindle: int = 0
    spindle_brake: bool = False


class LinuxCNCBackend(AbstractBackend):
    """
    Talks to a running LinuxCNC instance via the `linuxcnc` Python module.

    Exactly one stat/command/error_channel object is created in __init__
    and reused for the lifetime of the instance (LinuxCNC's NML channels
    are not cheap to open, and reopening them per call would also race
    with poll()'s "must not block" contract).

    Preconditions on individual commands mirror SimulatedBackend's own
    checks method-for-method (see each method below) so that, from
    MachineController's point of view — and from a test calling backend
    methods directly, bypassing MachineController's own gating — both
    backends behave identically: never an exception on one side and a
    silent no-op on the other.
    """

    def __init__(
        self,
        stat_factory: Callable[[], Any] | None = None,
        command_factory: Callable[[], Any] | None = None,
        error_factory: Callable[[], Any] | None = None,
    ) -> None:
        if linuxcnc is None:
            raise LinuxCNCNotAvailableError(
                "The 'linuxcnc' Python module is not available on this "
                "machine (it ships with a LinuxCNC installation, it is "
                "not a PyPI package). Run with --simulate, or install "
                "LinuxCNC first."
            ) from _IMPORT_ERROR

        stat_factory = stat_factory or linuxcnc.stat
        command_factory = command_factory or linuxcnc.command
        error_factory = error_factory or linuxcnc.error_channel

        self._stat = stat_factory()
        self._cmd = command_factory()
        self._err = error_factory()

        self._cache = _StatCache()
        self._error_queue: deque[MachineError] = deque()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _push_error(self, message: str, severity: ErrorSeverity = ErrorSeverity.ERROR) -> None:
        self._error_queue.append(MachineError(message, severity, source="LinuxCNCBackend"))

    @staticmethod
    def _to_position(nine_tuple: Sequence[float]) -> Position:
        x, y, z, a, b, c = nine_tuple[:6]  # u/v/w (indices 6-8) are ignored
        return Position(x=x, y=y, z=z, a=a, b=b, c=c)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def poll(self) -> None:
        """
        Update the internal state cache from a fresh stat.poll(), and
        drain the error channel into our own queue.

        MUST NOT BLOCK: no wait_complete() here, matching AbstractBackend's
        contract. A dropped/unavailable NML connection (linuxcnc.error) is
        caught and surfaced as a queued MachineError instead of crashing —
        the cache simply keeps its last known values for this cycle.
        """
        try:
            self._stat.poll()
        except linuxcnc.error as exc:
            self._push_error(
                f"LinuxCNC connection lost: {exc}", ErrorSeverity.CRITICAL
            )
            return

        self._drain_error_channel()
        self._refresh_cache()

    def _drain_error_channel(self) -> None:
        """Read every pending message off error_channel into our own
        queue. This is the ONE reader of error_channel per the NML queue
        semantics documented on get_error() — nothing else may poll it."""
        try:
            while True:
                item = self._err.poll()
                if item is None:
                    break
                kind, text = item
                if kind in (linuxcnc.NML_ERROR, linuxcnc.OPERATOR_ERROR):
                    severity = ErrorSeverity.ERROR
                elif kind in (linuxcnc.NML_TEXT, linuxcnc.OPERATOR_TEXT):
                    severity = ErrorSeverity.INFO
                else:
                    severity = ErrorSeverity.WARNING
                self._error_queue.append(MachineError(text, severity, source="LinuxCNC"))
        except linuxcnc.error as exc:
            self._push_error(
                f"LinuxCNC error channel unreachable: {exc}", ErrorSeverity.CRITICAL
            )

    def _refresh_cache(self) -> None:
        s = self._stat
        c = self._cache

        c.machine_state = {
            linuxcnc.STATE_ESTOP: MachineState.ESTOP,
            linuxcnc.STATE_ESTOP_RESET: MachineState.ESTOP_RESET,
            linuxcnc.STATE_OFF: MachineState.OFF,
            linuxcnc.STATE_ON: MachineState.ON,
        }.get(s.task_state, MachineState.ESTOP)

        c.homed = all(s.homed[: s.joints])
        c.inpos = bool(s.inpos)

        if s.task_paused:
            c.program_state = ProgramState.PAUSED
        elif s.interp_state == linuxcnc.INTERP_IDLE:
            c.program_state = ProgramState.IDLE
        elif s.interp_state in (linuxcnc.INTERP_READING, linuxcnc.INTERP_WAITING):
            c.program_state = ProgramState.RUNNING
        elif s.interp_state == linuxcnc.INTERP_PAUSED:
            c.program_state = ProgramState.PAUSED
        else:
            c.program_state = ProgramState.IDLE

        c.loaded_file = s.file
        c.current_line = s.current_line
        c.distance_to_go = s.distance_to_go
        c.optional_stop = bool(s.optional_stop)
        c.block_delete = bool(s.block_delete)
        c.feed_hold = bool(s.feed_hold_enabled)

        c.position = self._to_position(s.actual_position)
        c.active_wcs = s.g5x_index
        c.wcs_offset = self._to_position(s.g5x_offset)

        c.feed_data = FeedData(
            feed_actual=s.current_vel,
            spindle_rpm=s.spindle[0]["speed"],
            feed_override=s.feedrate,
        )
        c.rapid_override = s.rapidrate
        # Per-axis load isn't available at the NML/stat level (it lives on
        # the HAL layer, which AbstractBackend deliberately excludes — see
        # its class docstring) — always the zeroed default.
        c.axis_loads = AxisLoads()

        c.flood = s.flood == linuxcnc.FLOOD_ON
        c.mist = s.mist == linuxcnc.MIST_ON
        c.lube = bool(s.lube)

        c.tool_in_spindle = s.tool_in_spindle
        c.spindle_brake = bool(s.spindle[0]["brake"])

    # ------------------------------------------------------------------
    # Machine state reads
    # ------------------------------------------------------------------

    def get_machine_state(self) -> MachineState:
        return self._cache.machine_state

    def is_homed(self) -> bool:
        return self._cache.homed

    def get_inpos(self) -> bool:
        return self._cache.inpos

    # ------------------------------------------------------------------
    # Program / interpreter reads
    # ------------------------------------------------------------------

    def get_program_state(self) -> ProgramState:
        return self._cache.program_state

    def get_loaded_file(self) -> str:
        return self._cache.loaded_file

    def get_current_line(self) -> int:
        return self._cache.current_line

    def get_distance_to_go(self) -> float:
        return self._cache.distance_to_go

    def get_optional_stop(self) -> bool:
        return self._cache.optional_stop

    def get_block_delete(self) -> bool:
        return self._cache.block_delete

    def get_feed_hold(self) -> bool:
        return self._cache.feed_hold

    # ------------------------------------------------------------------
    # Position / WCS reads
    # ------------------------------------------------------------------

    def get_position(self) -> Position:
        return self._cache.position

    def get_active_wcs(self) -> int:
        return self._cache.active_wcs

    def get_wcs_offset(self) -> Position:
        return self._cache.wcs_offset

    # ------------------------------------------------------------------
    # Feed / spindle reads
    # ------------------------------------------------------------------

    def get_feed_data(self) -> FeedData:
        return self._cache.feed_data

    def get_rapid_override(self) -> float:
        return self._cache.rapid_override

    def get_axis_loads(self) -> AxisLoads:
        return self._cache.axis_loads

    # ------------------------------------------------------------------
    # Coolant / auxiliary reads
    # ------------------------------------------------------------------

    def get_flood(self) -> bool:
        return self._cache.flood

    def get_mist(self) -> bool:
        return self._cache.mist

    def get_lube(self) -> bool:
        return self._cache.lube

    # ------------------------------------------------------------------
    # Tool reads
    # ------------------------------------------------------------------

    def get_tool_in_spindle(self) -> int:
        return self._cache.tool_in_spindle

    def get_spindle_brake(self) -> bool:
        return self._cache.spindle_brake

    # ------------------------------------------------------------------
    # Error channel
    # ------------------------------------------------------------------

    def get_error(self) -> MachineError | None:
        if not self._error_queue:
            return None
        return self._error_queue.popleft()

    # ------------------------------------------------------------------
    # Machine state commands
    # ------------------------------------------------------------------

    def estop(self) -> None:
        self._cmd.state(linuxcnc.STATE_ESTOP)

    def estop_reset(self) -> None:
        if self._cache.machine_state == MachineState.ESTOP:
            self._cmd.state(linuxcnc.STATE_ESTOP_RESET)

    def set_machine_on(self) -> None:
        if self._cache.machine_state == MachineState.ESTOP_RESET:
            self._cmd.state(linuxcnc.STATE_ON)

    def set_machine_off(self) -> None:
        if self._cache.machine_state == MachineState.ON:
            self._cmd.state(linuxcnc.STATE_OFF)

    # ------------------------------------------------------------------
    # Program commands
    # ------------------------------------------------------------------

    def run_program(self, gcode_path: str) -> None:
        if self._cache.machine_state != MachineState.ON:
            self._push_error("Cannot start program: machine is not ON.")
            return
        if not self._cache.homed:
            self._push_error("Cannot start program: machine is not homed.")
            return
        if self._cache.program_state == ProgramState.RUNNING:
            self._push_error("Program already running.", ErrorSeverity.INFO)
            return

        self._cmd.mode(linuxcnc.MODE_AUTO)
        self._cmd.wait_complete()
        self._cmd.program_open(gcode_path)
        self._cmd.auto(linuxcnc.AUTO_RUN, 0)

    def pause_program(self) -> None:
        if self._cache.program_state == ProgramState.RUNNING:
            self._cmd.auto(linuxcnc.AUTO_PAUSE)

    def resume_program(self) -> None:
        if self._cache.program_state == ProgramState.PAUSED:
            self._cmd.auto(linuxcnc.AUTO_RESUME)

    def stop_program(self) -> None:
        if self._cache.program_state in (ProgramState.RUNNING, ProgramState.PAUSED):
            self._cmd.abort()

    def auto_step(self) -> None:
        if self._cache.loaded_file and self._cache.program_state != ProgramState.RUNNING:
            self._cmd.auto(linuxcnc.AUTO_STEP)

    def set_single_block(self, enabled: bool) -> None:
        # No LinuxCNC NML call maps to this (base.py names none) — LinuxCNC
        # doesn't have a "single block" toggle distinct from AUTO_STEP.
        # Purely a local flag for the UI/controller to decide whether to
        # drive the program via auto_step() or run_program()/AUTO_RUN.
        self._single_block = enabled

    def get_single_block(self) -> bool:
        return getattr(self, "_single_block", False)

    def rewind_program(self) -> None:
        """
        Reset to the start of the loaded program without unloading it.

        base.py's docstring specifies the required behaviour (motion and
        spindle stop, line back to 0, file stays loaded) but — unlike
        every other command here — names no explicit LinuxCNC call
        sequence. Reopening the same file via program_open() is the
        standard way LinuxCNC GUIs reset the interpreter's line counter
        to 0 without changing which file is loaded; abort() first ensures
        any running motion is halted, and the spindle is stopped
        explicitly since abort() does not guarantee that on its own.
        """
        if not self._cache.loaded_file:
            return
        self._cmd.abort()
        self._cmd.spindle(linuxcnc.SPINDLE_OFF)
        self._cmd.program_open(self._cache.loaded_file)

    # ------------------------------------------------------------------
    # Interpreter commands
    # ------------------------------------------------------------------

    def set_optional_stop(self, enabled: bool) -> None:
        self._cmd.set_optional_stop(int(enabled))

    def set_block_delete(self, enabled: bool) -> None:
        self._cmd.set_block_delete(int(enabled))

    def set_feed_hold(self, enabled: bool) -> None:
        if self._cache.program_state == ProgramState.RUNNING:
            self._cmd.set_feed_hold(int(enabled))

    def reset_interpreter(self) -> None:
        self._cmd.reset_interpreter()

    # ------------------------------------------------------------------
    # Jog commands
    # ------------------------------------------------------------------

    def jog_continuous(self, axis: int, velocity: float) -> None:
        self._cmd.mode(linuxcnc.MODE_MANUAL)
        self._cmd.teleop_enable(False)
        self._cmd.jog(linuxcnc.JOG_CONTINUOUS, False, axis, velocity)

    def jog_increment(self, axis: int, velocity: float, distance: float) -> None:
        self._cmd.jog(linuxcnc.JOG_INCREMENT, False, axis, velocity, distance)

    def jog_stop(self, axis: int) -> None:
        self._cmd.jog(linuxcnc.JOG_STOP, False, axis)

    # ------------------------------------------------------------------
    # Homing
    # ------------------------------------------------------------------

    def home_joint(self, joint: int) -> None:
        self._cmd.home(joint)

    def home_all(self) -> None:
        if self._cache.machine_state == MachineState.ON:
            self._cmd.home(-1)

    def unhome_joint(self, joint: int) -> None:
        self._cmd.unhome(joint)

    # ------------------------------------------------------------------
    # Feed / speed overrides
    # ------------------------------------------------------------------

    def set_feed_override(self, value: float) -> None:
        self._cmd.feedrate(max(0.0, min(value, 2.0)))

    def set_rapid_override(self, value: float) -> None:
        self._cmd.rapidrate(max(0.0, min(value, 1.0)))

    def set_spindle_override(self, value: float) -> None:
        self._cmd.spindleoverride(value)

    def set_max_velocity(self, value: float) -> None:
        self._cmd.maxvel(max(0.1, value))

    # ------------------------------------------------------------------
    # Spindle
    # ------------------------------------------------------------------

    def spindle_on(self, rpm: float, forward: bool = True) -> None:
        direction = linuxcnc.SPINDLE_FORWARD if forward else linuxcnc.SPINDLE_REVERSE
        self._cmd.spindle(direction, rpm)

    def spindle_off(self) -> None:
        self._cmd.spindle(linuxcnc.SPINDLE_OFF)

    def spindle_brake(self, engage: bool) -> None:
        self._cmd.brake(linuxcnc.BRAKE_ENGAGE if engage else linuxcnc.BRAKE_RELEASE)

    # ------------------------------------------------------------------
    # Coolant
    # ------------------------------------------------------------------

    def flood_on(self) -> None:
        self._cmd.flood(linuxcnc.FLOOD_ON)

    def flood_off(self) -> None:
        self._cmd.flood(linuxcnc.FLOOD_OFF)

    def mist_on(self) -> None:
        self._cmd.mist(linuxcnc.MIST_ON)

    def mist_off(self) -> None:
        self._cmd.mist(linuxcnc.MIST_OFF)

    # ------------------------------------------------------------------
    # Limit override
    # ------------------------------------------------------------------

    def override_limits(self) -> None:
        self._cmd.override_limits()

    # ------------------------------------------------------------------
    # Tool management
    # ------------------------------------------------------------------

    def load_tool_table(self) -> None:
        self._cmd.load_tool_table()

    def set_tool_offset(
        self,
        tool_num: int,
        z_offset: float,
        x_offset: float,
        diameter: float,
        front_angle: float,
        back_angle: float,
        orientation: int,
    ) -> None:
        self._cmd.tool_offset(
            tool_num, z_offset, x_offset, diameter, front_angle, back_angle, orientation
        )

    # ------------------------------------------------------------------
    # MDI
    # ------------------------------------------------------------------

    def send_mdi(self, command: str) -> None:
        self._cmd.mode(linuxcnc.MODE_MDI)
        self._cmd.wait_complete()
        self._cmd.mdi(command)
