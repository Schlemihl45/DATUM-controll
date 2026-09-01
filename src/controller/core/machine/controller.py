from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from src.controller.core.backends.base import AbstractBackend
from src.controller.domain.models import (
    ErrorSeverity,
    FeedData,
    MachineError,
    MachineState,
    Position,
    ProgramState,
    AxisLoads,
)


class MachineController(QObject):
    """
    Heart of the application — must only be used from the main thread.

    Responsibilities
    -----------------
    1. 50 ms poll loop (QTimer -> _poll())
    2. Delta detection: signals fire only when a value actually changes
    3. Precondition checks against cached state before every command
    4. Command delegation to the backend

    Architecture rules (do not violate)
    -------------------------------------
    - Only call from the main thread (Qt constraint)
    - ui/ never imports core/ directly — only via signals/slots
    - The backend is fully swappable (SimulatedBackend <-> LinuxCNCBackend)
    - No business logic here — that belongs in domain/ and repositories/

    First poll
    ----------
    On the first _poll() all _last_* attributes are None.
    Every check therefore emits its signal immediately -> the UI
    initialises itself without an explicit refresh() call.
    """

    POLL_INTERVAL_MS: int = 50

    # ------------------------------------------------------------------
    # Signals — naming: <what changed>_changed, never <what happened>
    # ------------------------------------------------------------------

    # Machine state
    machine_state_changed   = Signal(MachineState)
    homed_changed           = Signal(bool)
    inpos_changed           = Signal(bool)

    # Program / interpreter
    program_state_changed   = Signal(ProgramState)
    file_changed            = Signal(str)
    line_changed            = Signal(int)
    distance_to_go_changed  = Signal(float)
    optional_stop_changed   = Signal(bool)
    block_delete_changed    = Signal(bool)
    feed_hold_changed       = Signal(bool)
    single_block_changed    = Signal(bool)

    # Position / WCS
    position_changed        = Signal(Position)
    wcs_changed             = Signal(int)       # active WCS index (1=G54 …)

    # Feed / spindle
    feed_changed            = Signal(FeedData)
    rapid_override_changed  = Signal(float)
    spindle_brake_changed   = Signal(bool)
    axis_loads_changed      = Signal(AxisLoads)

    # Coolant / auxiliary
    flood_changed           = Signal(bool)
    mist_changed            = Signal(bool)

    # Tool
    tool_changed            = Signal(int)       # T-number (0 = no tool)

    # Errors and warnings
    error_occurred          = Signal(MachineError)

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(
        self,
        backend: AbstractBackend,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend

        # Cached state — None means "not yet polled"
        self._last_machine_state:   MachineState | None = None
        self._last_homed:           bool | None         = None
        self._last_inpos:           bool | None         = None

        self._last_program_state:   ProgramState | None = None
        self._last_file:            str | None          = None
        self._last_line:            int | None          = None
        self._last_distance_to_go:  float | None        = None
        self._last_optional_stop:   bool | None         = None
        self._last_block_delete:    bool | None         = None
        self._last_feed_hold:       bool | None         = None
        self._last_single_block:    bool | None         = None

        self._last_position:        Position | None     = None
        self._last_wcs:             int | None          = None

        self._last_feed:            FeedData | None     = None
        self._last_rapid_override:  float | None        = None
        self._last_spindle_brake:   bool | None         = None
        self._last_axis_loads:      AxisLoads | None    = None

        self._last_flood:           bool | None         = None
        self._last_mist:            bool | None         = None

        self._last_tool:            int | None          = None

        # QTimer — fires repeatedly every 50 ms (single_shot=False by default)
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the 50 ms poll loop. Call once after app startup."""
        self._timer.start()

    def stop(self) -> None:
        """Stop the poll loop. Call before the application exits."""
        self._timer.stop()

    # ------------------------------------------------------------------
    # Poll loop (private — only called by QTimer)
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        """
        Called every 50 ms.

        Order is deliberate:
            1. Update backend cache (stat.poll() in LinuxCNC)
            2. Machine state first — all other checks depend on it
            3. Error channel last — state is already current when UI reacts
        """
        self._backend.poll()

        self._check_machine_state()
        self._check_homed()
        self._check_inpos()

        self._check_program_state()
        self._check_file()
        self._check_line()
        self._check_distance_to_go()
        self._check_optional_stop()
        self._check_block_delete()
        self._check_feed_hold()
        self._check_single_block()

        self._check_position()
        self._check_wcs()

        self._check_feed()
        self._check_rapid_override()
        self._check_spindle_brake()
        self._check_axis_loads()

        self._check_flood()
        self._check_mist()

        self._check_tool()

        self._check_errors()

    # --- check helpers ---

    def _check_machine_state(self) -> None:
        v = self._backend.get_machine_state()
        if v != self._last_machine_state:
            self._last_machine_state = v
            self.machine_state_changed.emit(v)

    def _check_homed(self) -> None:
        v = self._backend.is_homed()
        if v != self._last_homed:
            self._last_homed = v
            self.homed_changed.emit(v)

    def _check_inpos(self) -> None:
        v = self._backend.get_inpos()
        if v != self._last_inpos:
            self._last_inpos = v
            self.inpos_changed.emit(v)

    def _check_program_state(self) -> None:
        v = self._backend.get_program_state()
        if v != self._last_program_state:
            self._last_program_state = v
            self.program_state_changed.emit(v)

    def _check_file(self) -> None:
        v = self._backend.get_loaded_file()
        if v != self._last_file:
            self._last_file = v
            self.file_changed.emit(v)

    def _check_line(self) -> None:
        v = self._backend.get_current_line()
        if v != self._last_line:
            self._last_line = v
            self.line_changed.emit(v)

    def _check_distance_to_go(self) -> None:
        v = self._backend.get_distance_to_go()
        if v != self._last_distance_to_go:
            self._last_distance_to_go = v
            self.distance_to_go_changed.emit(v)

    def _check_optional_stop(self) -> None:
        v = self._backend.get_optional_stop()
        if v != self._last_optional_stop:
            self._last_optional_stop = v
            self.optional_stop_changed.emit(v)

    def _check_block_delete(self) -> None:
        v = self._backend.get_block_delete()
        if v != self._last_block_delete:
            self._last_block_delete = v
            self.block_delete_changed.emit(v)

    def _check_single_block(self) -> None:
        v = self._backend.get_single_block()
        if v != self._last_single_block:
            self._last_single_block = v
            self.single_block_changed.emit(v)

    def set_single_block(self, enabled: bool) -> None:
        self._backend.set_single_block(enabled)

    def _check_feed_hold(self) -> None:
        v = self._backend.get_feed_hold()
        if v != self._last_feed_hold:
            self._last_feed_hold = v
            self.feed_hold_changed.emit(v)

    def _check_position(self) -> None:
        # Position is a @dataclass -> __eq__ compares all fields
        v = self._backend.get_position()
        if v != self._last_position:
            self._last_position = v
            self.position_changed.emit(v)

    def _check_wcs(self) -> None:
        v = self._backend.get_active_wcs()
        if v != self._last_wcs:
            self._last_wcs = v
            self.wcs_changed.emit(v)

    def _check_feed(self) -> None:
        # FeedData is a @dataclass -> __eq__ compares all fields
        v = self._backend.get_feed_data()
        if v != self._last_feed:
            self._last_feed = v
            self.feed_changed.emit(v)

    def _check_rapid_override(self) -> None:
        v = self._backend.get_rapid_override()
        if v != self._last_rapid_override:
            self._last_rapid_override = v
            self.rapid_override_changed.emit(v)

    def _check_spindle_brake(self) -> None:
        v = self._backend.get_spindle_brake()
        if v != self._last_spindle_brake:
            self._last_spindle_brake = v
            self.spindle_brake_changed.emit(v)

    def _check_axis_loads(self) -> None:
        v = self._backend.get_axis_loads()
        if v != self._last_axis_loads:
            self._last_axis_loads = v
            self.axis_loads_changed.emit(v)

    def _check_flood(self) -> None:
        v = self._backend.get_flood()
        if v != self._last_flood:
            self._last_flood = v
            self.flood_changed.emit(v)

    def _check_mist(self) -> None:
        v = self._backend.get_mist()
        if v != self._last_mist:
            self._last_mist = v
            self.mist_changed.emit(v)

    def _check_tool(self) -> None:
        v = self._backend.get_tool_in_spindle()
        if v != self._last_tool:
            self._last_tool = v
            self.tool_changed.emit(v)

    def _check_errors(self) -> None:
        # get_error() returns once and clears (queue semantics)
        err = self._backend.get_error()
        if err is not None:
            self.error_occurred.emit(err)

    # ------------------------------------------------------------------
    # Properties — cached read access for the UI (no extra poll)
    # ------------------------------------------------------------------

    @property
    def machine_state(self) -> MachineState:
        return self._last_machine_state or MachineState.ESTOP

    @property
    def program_state(self) -> ProgramState:
        return self._last_program_state or ProgramState.IDLE

    @property
    def position(self) -> Position:
        return self._last_position or Position()

    @property
    def feed(self) -> FeedData:
        return self._last_feed or FeedData()

    @property
    def axis_loads(self) -> AxisLoads:
        return self._last_axis_loads or AxisLoads()

    @property
    def is_homed(self) -> bool:
        return bool(self._last_homed)

    @property
    def inpos(self) -> bool:
        return bool(self._last_inpos)

    @property
    def loaded_file(self) -> str:
        return self._last_file or ""

    @property
    def current_line(self) -> int:
        return self._last_line or 0

    @property
    def flood(self) -> bool:
        return bool(self._last_flood)

    @property
    def mist(self) -> bool:
        return bool(self._last_mist)

    @property
    def optional_stop(self) -> bool:
        return bool(self._last_optional_stop)

    @property
    def block_delete(self) -> bool:
        return bool(self._last_block_delete)

    @property
    def rapid_override(self) -> float:
        return self._last_rapid_override if self._last_rapid_override is not None else 1.0

    @property
    def active_wcs(self) -> int:
        return self._last_wcs or 1

    # ------------------------------------------------------------------
    # Preconditions (private — checked against cached state)
    # ------------------------------------------------------------------

    def _is_on(self) -> bool:
        return self._last_machine_state == MachineState.ON

    def _is_idle(self) -> bool:
        return self._last_program_state == ProgramState.IDLE

    def _can_run(self) -> bool:
        """Run program: ON + idle + homed."""
        return self._is_on() and self._is_idle() and bool(self._last_homed)

    def _can_jog(self) -> bool:
        """Jog: ON + idle. Homing not required (jog is used to reach switches)."""
        return self._is_on() and self._is_idle()

    def _can_mdi(self) -> bool:
        """MDI: ON + idle + homed."""
        return self._is_on() and self._is_idle() and bool(self._last_homed)

    def _warn(self, message: str) -> None:
        """Emit a WARNING through error_occurred without stopping operation."""
        self.error_occurred.emit(
            MachineError(message, ErrorSeverity.WARNING, source="Controller")
        )

    # ------------------------------------------------------------------
    # Machine state commands
    # ------------------------------------------------------------------

    def estop(self) -> None:
        """Trigger E-stop. Always allowed — no precondition."""
        self._backend.estop()

    def estop_reset(self) -> None:
        """Acknowledge E-stop. Only valid from ESTOP state."""
        if self._last_machine_state == MachineState.ESTOP:
            self._backend.estop_reset()

    def set_machine_on(self) -> None:
        """Enable drives. Only valid from ESTOP_RESET."""
        if self._last_machine_state == MachineState.ESTOP_RESET:
            self._backend.set_machine_on()

    def set_machine_off(self) -> None:
        """Disable drives. Only valid when ON."""
        if self._is_on():
            self._backend.set_machine_off()

    # ------------------------------------------------------------------
    # Program commands
    # ------------------------------------------------------------------

    def run_program(self, gcode_path: str) -> None:
        """Load a G-code file and start execution."""
        if not gcode_path:
            self._warn("No G-code path specified.")
            return
        if not self._can_run():
            self._warn(
                "Cannot start program: machine must be ON, homed, and idle."
            )
            return
        self._backend.run_program(gcode_path)

    def pause_program(self) -> None:
        """Pause program execution between blocks."""
        if self._last_program_state == ProgramState.RUNNING:
            self._backend.pause_program()

    def resume_program(self) -> None:
        """Resume a paused program."""
        if self._last_program_state == ProgramState.PAUSED:
            self._backend.resume_program()

    def stop_program(self) -> None:
        """Abort a running or paused program."""
        if self._last_program_state in (ProgramState.RUNNING, ProgramState.PAUSED):
            self._backend.stop_program()

    def auto_step(self) -> None:
        """Execute one G-code block then pause (single-step mode)."""
        if self._is_on() and self._last_homed:
            self._backend.auto_step()

    def rewind_program(self) -> None:
        """Reset to program start without unloading. Not allowed while
        RUNNING — pause first."""
        if self._last_program_state == ProgramState.RUNNING:
            self._warn("Cannot rewind: program is still running. Pause first.")
            return
        self._backend.rewind_program()

    # ------------------------------------------------------------------
    # Interpreter commands
    # ------------------------------------------------------------------

    def set_optional_stop(self, enabled: bool) -> None:
        """Enable/disable M1 optional stop behaviour."""
        self._backend.set_optional_stop(enabled)

    def set_block_delete(self, enabled: bool) -> None:
        """Enable/disable skipping of '/' prefixed blocks."""
        self._backend.set_block_delete(enabled)

    def set_feed_hold(self, enabled: bool) -> None:
        """
        Freeze/unfreeze motion without stopping the interpreter.
        Spindle continues running. Different from pause_program().
        """
        self._backend.set_feed_hold(enabled)

    def reset_interpreter(self) -> None:
        """Reset the G-code interpreter (recovery after error)."""
        if self._is_on():
            self._backend.reset_interpreter()

    # ------------------------------------------------------------------
    # Jog commands
    # ------------------------------------------------------------------

    def jog_continuous(self, axis: int, velocity: float) -> None:
        """
        Jog an axis continuously until jog_stop() is called.
        axis: 0=X 1=Y 2=Z 3=A 4=B 5=C  |  velocity: mm/s, sign = direction
        """
        if self._can_jog():
            self._backend.jog_continuous(axis, velocity)

    def jog_increment(self, axis: int, velocity: float, distance: float) -> None:
        """Jog an axis by a fixed increment (distance in mm, always positive)."""
        if self._can_jog():
            self._backend.jog_increment(axis, velocity, distance)

    def jog_stop(self, axis: int) -> None:
        """Stop a continuous jog. Allowed whenever the machine is ON."""
        if self._is_on():
            self._backend.jog_stop(axis)

    # ------------------------------------------------------------------
    # Homing
    # ------------------------------------------------------------------

    def home_all(self) -> None:
        """Home all joints following the INI HOME_SEQUENCE."""
        if self._is_on():
            self._backend.home_all()
        else:
            self._warn("Cannot home: machine is not ON.")

    def home_joint(self, joint: int) -> None:
        """Home a single joint."""
        if self._is_on():
            self._backend.home_joint(joint)

    def unhome_joint(self, joint: int) -> None:
        """Mark a joint as unhomed (recovery / re-homing)."""
        if self._is_on():
            self._backend.unhome_joint(joint)

    # ------------------------------------------------------------------
    # Feed / speed overrides
    # ------------------------------------------------------------------

    def set_feed_override(self, value: float) -> None:
        """Set feed override (0.0–2.0). Clamped silently."""
        self._backend.set_feed_override(max(0.0, min(value, 2.0)))

    def set_rapid_override(self, value: float) -> None:
        """Set rapid override (0.0–1.0). LinuxCNC caps rapid at 100%."""
        self._backend.set_rapid_override(max(0.0, min(value, 1.0)))

    def set_spindle_override(self, value: float) -> None:
        """Set spindle speed override (0.0–2.0)."""
        self._backend.set_spindle_override(max(0.0, min(value, 2.0)))

    def set_max_velocity(self, value: float) -> None:
        """Set runtime velocity cap in mm/s (does not persist across restarts)."""
        if value > 0:
            self._backend.set_max_velocity(value)

    # ------------------------------------------------------------------
    # Spindle
    # ------------------------------------------------------------------

    def spindle_on(self, rpm: float, forward: bool = True) -> None:
        """Start the spindle. Machine must be ON."""
        if self._is_on():
            self._backend.spindle_on(rpm, forward)

    def spindle_off(self) -> None:
        """Stop the spindle."""
        self._backend.spindle_off()

    def spindle_brake(self, engage: bool) -> None:
        """Engage or release the spindle brake."""
        self._backend.spindle_brake(engage)

    # ------------------------------------------------------------------
    # Coolant
    # ------------------------------------------------------------------

    def flood_on(self) -> None:
        """Turn on flood coolant."""
        if self._is_on():
            self._backend.flood_on()

    def flood_off(self) -> None:
        """Turn off flood coolant."""
        self._backend.flood_off()

    def toggle_flood(self) -> None:
        """Toggle flood coolant state."""
        if self._last_flood:
            self.flood_off()
        else:
            self.flood_on()

    def mist_on(self) -> None:
        """Turn on mist coolant."""
        if self._is_on():
            self._backend.mist_on()

    def mist_off(self) -> None:
        """Turn off mist coolant."""
        self._backend.mist_off()

    def toggle_mist(self) -> None:
        """Toggle mist coolant state."""
        if self._last_mist:
            self.mist_off()
        else:
            self.mist_on()

    # ------------------------------------------------------------------
    # Limit override
    # ------------------------------------------------------------------

    def override_limits(self) -> None:
        """
        Override soft limits for one move (recovery from limit trip).
        Use with caution — only for manual recovery, never in automation.
        """
        if self._is_on():
            self._backend.override_limits()

    # ------------------------------------------------------------------
    # Tool management
    # ------------------------------------------------------------------

    def load_tool_table(self) -> None:
        """Reload tool.tbl from disk (after external edits)."""
        if self._is_on():
            self._backend.load_tool_table()

    def set_tool_offset(
        self,
        tool_num: int,
        z_offset: float,
        x_offset: float = 0.0,
        diameter: float = 0.0,
        front_angle: float = 0.0,
        back_angle: float = 0.0,
        orientation: int = 0,
    ) -> None:
        """Write a tool offset (e.g. after tool probing)."""
        if self._is_on():
            self._backend.set_tool_offset(
                tool_num, z_offset, x_offset,
                diameter, front_angle, back_angle, orientation,
            )

    # ------------------------------------------------------------------
    # MDI
    # ------------------------------------------------------------------

    def send_mdi(self, command: str) -> None:
        """
        Send a single G-code command in MDI mode.
        Preconditions: ON + idle + homed.
        Use M64/M65 for digital outputs (lights, pumps, etc.).
        """
        if not command.strip():
            return
        if not self._can_mdi():
            self._warn("MDI not available: machine must be ON, homed, and idle.")
            return
        self._backend.send_mdi(command)