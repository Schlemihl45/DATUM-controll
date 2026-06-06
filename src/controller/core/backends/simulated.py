from __future__ import annotations

import math
import time

from domain.models import (
    ErrorSeverity,
    FeedData,
    MachineError,
    MachineState,
    Position,
    ProgramState,
)
from core.backends.base import AbstractBackend


class SimulatedBackend(AbstractBackend):
    """
    Fully in-memory simulated backend — no hardware required.

    State machine:
        ESTOP -> estop_reset() -> ESTOP_RESET -> set_machine_on() -> ON
          ^                                                           |
          +----------------------- estop() ---------------------------+

    set_machine_on() works ONLY from ESTOP_RESET, mirroring real machine
    behaviour. Calling it from any other state is silently ignored.

    When a program is running, position follows a Lissajous curve so
    the UI shows real changing numbers without any hardware.
    """

    _PROGRAM_DURATION: float = 30.0  # seconds for a simulated program run

    def __init__(self) -> None:
        # --- Machine state ---
        self._machine_state: MachineState = MachineState.ESTOP
        self._program_state: ProgramState = ProgramState.IDLE

        # --- Position & kinematics ---
        self._position: Position = Position()
        self._wcs_offset: Position = Position()
        self._active_wcs: int = 1            # G54
        self._t0: float = time.monotonic()

        # --- Feed / speed ---
        self._feed_override: float = 1.0
        self._rapid_override: float = 1.0
        self._max_velocity: float = 100.0    # mm/s (runtime cap)

        # --- Spindle ---
        self._spindle_rpm: float = 0.0
        self._spindle_target_rpm: float = 0.0
        self._spindle_brake: bool = False

        # --- Program ---
        self._loaded_file: str = ""
        self._current_line: int = 0
        self._program_start_time: float = 0.0
        self._distance_to_go: float = 0.0

        # --- Tool ---
        self._tool_in_spindle: int = 0

        # --- Homing ---
        self._homed: bool = False

        # --- Coolant / auxiliary ---
        self._flood: bool = False
        self._mist: bool = False
        self._lube: bool = False

        # --- Interpreter flags ---
        self._optional_stop: bool = False
        self._block_delete: bool = False
        self._feed_hold: bool = False

        # --- Error channel ---
        self._pending_error: MachineError | None = None

    # ------------------------------------------------------------------
    # poll()
    # ------------------------------------------------------------------

    def poll(self) -> None:
        """Advance simulation by one time step. Called every 50 ms."""
        if self._machine_state != MachineState.ON:
            return

        t = time.monotonic() - self._t0

        if self._program_state == ProgramState.RUNNING and not self._feed_hold:
            self._poll_running(t)

        self._poll_spindle()

    def _poll_running(self, t: float) -> None:
        elapsed = time.monotonic() - self._program_start_time
        progress = min(elapsed / self._PROGRAM_DURATION, 1.0)
        self._current_line = int(progress * 200)

        # Lissajous-like path — gives believable numbers in the UI
        self._position = Position(
            x=round(math.sin(t * 0.5) * 40.0, 3),
            y=round(math.cos(t * 0.3) * 25.0, 3),
            z=round(-5.0 + math.sin(t * 1.2) * 2.0, 3),
        )
        self._distance_to_go = round(abs(math.sin(t * 2.0)) * 15.0, 3)

        if progress >= 1.0:
            self._program_state = ProgramState.IDLE
            self._current_line = 0
            self._loaded_file = ""
            self._distance_to_go = 0.0

    def _poll_spindle(self) -> None:
        """Exponentially approach target RPM (simulated ramp-up/down)."""
        if self._spindle_brake:
            self._spindle_rpm = max(0.0, round(self._spindle_rpm * 0.5, 1))
            return
        if self._spindle_target_rpm != 0.0:
            diff = self._spindle_target_rpm - self._spindle_rpm
            self._spindle_rpm = round(self._spindle_rpm + diff * 0.1, 1)
        else:
            self._spindle_rpm = max(0.0, round(self._spindle_rpm * 0.85, 1))

    # ------------------------------------------------------------------
    # Machine state reads
    # ------------------------------------------------------------------

    def get_machine_state(self) -> MachineState:
        return self._machine_state

    def is_homed(self) -> bool:
        return self._homed

    def get_inpos(self) -> bool:
        # In simulation: in-position when not actively running a program
        return self._program_state != ProgramState.RUNNING

    # ------------------------------------------------------------------
    # Program / interpreter reads
    # ------------------------------------------------------------------

    def get_program_state(self) -> ProgramState:
        return self._program_state

    def get_loaded_file(self) -> str:
        return self._loaded_file

    def get_current_line(self) -> int:
        return self._current_line

    def get_distance_to_go(self) -> float:
        return self._distance_to_go

    def get_optional_stop(self) -> bool:
        return self._optional_stop

    def get_block_delete(self) -> bool:
        return self._block_delete

    def get_feed_hold(self) -> bool:
        return self._feed_hold

    # ------------------------------------------------------------------
    # Position / WCS reads
    # ------------------------------------------------------------------

    def get_position(self) -> Position:
        return self._position

    def get_active_wcs(self) -> int:
        return self._active_wcs

    def get_wcs_offset(self) -> Position:
        return self._wcs_offset

    # ------------------------------------------------------------------
    # Feed / spindle reads
    # ------------------------------------------------------------------

    def get_feed_data(self) -> FeedData:
        feed_actual = 0.0
        if self._program_state == ProgramState.RUNNING and not self._feed_hold:
            feed_actual = round(abs(math.sin(time.monotonic() - self._t0)) * 800, 1)
        return FeedData(
            feed_actual=feed_actual,
            spindle_rpm=self._spindle_rpm,
            feed_override=self._feed_override,
        )

    def get_rapid_override(self) -> float:
        return self._rapid_override

    # ------------------------------------------------------------------
    # Coolant / auxiliary reads
    # ------------------------------------------------------------------

    def get_flood(self) -> bool:
        return self._flood

    def get_mist(self) -> bool:
        return self._mist

    def get_lube(self) -> bool:
        return self._lube

    # ------------------------------------------------------------------
    # Tool reads
    # ------------------------------------------------------------------

    def get_tool_in_spindle(self) -> int:
        return self._tool_in_spindle

    def get_spindle_brake(self) -> bool:
        return self._spindle_brake

    # ------------------------------------------------------------------
    # Error channel
    # ------------------------------------------------------------------

    def get_error(self) -> MachineError | None:
        err = self._pending_error
        self._pending_error = None
        return err

    def _push_error(self, msg: str, severity: ErrorSeverity = ErrorSeverity.WARNING) -> None:
        self._pending_error = MachineError(msg, severity, source="SimulatedBackend")

    # ------------------------------------------------------------------
    # Machine state commands
    # ------------------------------------------------------------------

    def estop(self) -> None:
        self._machine_state = MachineState.ESTOP
        self._program_state = ProgramState.IDLE
        self._homed = False
        self._spindle_target_rpm = 0.0
        self._flood = False
        self._mist = False

    def estop_reset(self) -> None:
        if self._machine_state == MachineState.ESTOP:
            self._machine_state = MachineState.ESTOP_RESET

    def set_machine_on(self) -> None:
        if self._machine_state == MachineState.ESTOP_RESET:
            self._machine_state = MachineState.ON
            self._t0 = time.monotonic()

    def set_machine_off(self) -> None:
        if self._machine_state == MachineState.ON:
            self._machine_state = MachineState.OFF

    # ------------------------------------------------------------------
    # Program commands
    # ------------------------------------------------------------------

    def run_program(self, gcode_path: str) -> None:
        if self._machine_state != MachineState.ON:
            self._push_error("Cannot start program: machine is not ON.")
            return
        if not self._homed:
            self._push_error("Cannot start program: machine is not homed.")
            return
        if self._program_state == ProgramState.RUNNING:
            self._push_error("Program already running.", ErrorSeverity.INFO)
            return
        self._loaded_file = gcode_path
        self._program_state = ProgramState.RUNNING
        self._program_start_time = time.monotonic()
        self._t0 = time.monotonic()
        self._feed_hold = False

    def pause_program(self) -> None:
        if self._program_state == ProgramState.RUNNING:
            self._program_state = ProgramState.PAUSED

    def resume_program(self) -> None:
        if self._program_state == ProgramState.PAUSED:
            # Shift start time so progress does not jump
            elapsed = time.monotonic() - self._program_start_time
            self._program_start_time = time.monotonic() - elapsed
            self._program_state = ProgramState.RUNNING
            self._feed_hold = False

    def stop_program(self) -> None:
        if self._program_state in (ProgramState.RUNNING, ProgramState.PAUSED):
            self._program_state = ProgramState.IDLE
            self._current_line = 0
            self._loaded_file = ""
            self._distance_to_go = 0.0
            self._feed_hold = False

    def auto_step(self) -> None:
        """Advance program by one line then pause (single-step mode)."""
        if self._program_state == ProgramState.PAUSED and self._loaded_file:
            self._current_line += 1
            # Stay paused — caller must call auto_step() again for next line
        elif self._program_state == ProgramState.IDLE and self._loaded_file:
            self._program_state = ProgramState.PAUSED
            self._current_line = 1

    # ------------------------------------------------------------------
    # Interpreter commands
    # ------------------------------------------------------------------

    def set_optional_stop(self, enabled: bool) -> None:
        self._optional_stop = enabled

    def set_block_delete(self, enabled: bool) -> None:
        self._block_delete = enabled

    def set_feed_hold(self, enabled: bool) -> None:
        """Feed hold: freeze motion without stopping the interpreter."""
        if self._program_state == ProgramState.RUNNING:
            self._feed_hold = enabled

    def reset_interpreter(self) -> None:
        """Reset interpreter to idle state (recovery after error)."""
        self._program_state = ProgramState.IDLE
        self._current_line = 0
        self._loaded_file = ""
        self._distance_to_go = 0.0
        self._feed_hold = False

    # ------------------------------------------------------------------
    # Jog commands
    # ------------------------------------------------------------------

    def jog_continuous(self, axis: int, velocity: float) -> None:
        # Phase 1: no continuous motion tracking — UI can render buttons
        pass

    def jog_increment(self, axis: int, velocity: float, distance: float) -> None:
        """Shift position by a discrete step."""
        _ATTRS = ("x", "y", "z", "a", "b", "c")
        if 0 <= axis < len(_ATTRS):
            attr = _ATTRS[axis]
            delta = math.copysign(distance, velocity)
            setattr(self._position, attr, round(getattr(self._position, attr) + delta, 3))

    def jog_stop(self, axis: int) -> None:
        pass  # No continuous jog in Phase 1

    # ------------------------------------------------------------------
    # Homing
    # ------------------------------------------------------------------

    def home_joint(self, joint: int) -> None:
        # Phase 1: individual joint homing — no per-joint state tracked yet
        pass

    def home_all(self) -> None:
        """Simulate homing: move to machine zero and mark as homed."""
        if self._machine_state != MachineState.ON:
            return
        self._position = Position()
        self._homed = True

    def unhome_joint(self, joint: int) -> None:
        """Mark machine as unhomed (conservative: affects all joints)."""
        self._homed = False

    # ------------------------------------------------------------------
    # Feed / speed overrides
    # ------------------------------------------------------------------

    def set_feed_override(self, value: float) -> None:
        self._feed_override = max(0.0, min(value, 2.0))

    def set_rapid_override(self, value: float) -> None:
        self._rapid_override = max(0.0, min(value, 1.0))

    def set_spindle_override(self, value: float) -> None:
        # Phase 1: not tracked separately from target RPM
        pass

    def set_max_velocity(self, value: float) -> None:
        self._max_velocity = max(0.1, value)

    # ------------------------------------------------------------------
    # Spindle
    # ------------------------------------------------------------------

    def spindle_on(self, rpm: float, forward: bool = True) -> None:
        self._spindle_brake = False
        self._spindle_target_rpm = rpm if forward else -rpm

    def spindle_off(self) -> None:
        self._spindle_target_rpm = 0.0

    def spindle_brake(self, engage: bool) -> None:
        self._spindle_brake = engage
        if engage:
            self._spindle_target_rpm = 0.0

    # ------------------------------------------------------------------
    # Coolant
    # ------------------------------------------------------------------

    def flood_on(self) -> None:
        self._flood = True

    def flood_off(self) -> None:
        self._flood = False

    def mist_on(self) -> None:
        self._mist = True

    def mist_off(self) -> None:
        self._mist = False

    # ------------------------------------------------------------------
    # Limit override
    # ------------------------------------------------------------------

    def override_limits(self) -> None:
        pass  # Simulator has no soft limits to override

    # ------------------------------------------------------------------
    # Tool management
    # ------------------------------------------------------------------

    def load_tool_table(self) -> None:
        pass  # No tool table file in Phase 1 simulator

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
        pass  # No persistent tool table in Phase 1 simulator

    # ------------------------------------------------------------------
    # MDI
    # ------------------------------------------------------------------

    def send_mdi(self, command: str) -> None:
        """
        Minimal MDI simulation.
        Handles Tx tool-selection commands; all others are accepted silently.
        """
        cmd = command.strip().upper()
        # T1, T2, ... -> update tool in spindle
        if cmd.startswith("T") and cmd[1:].split()[0].isdigit():
            self._tool_in_spindle = int(cmd[1:].split()[0])