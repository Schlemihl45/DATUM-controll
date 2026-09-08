from __future__ import annotations

import math
import time
from dataclasses import replace

from controller.core.backends.base import AbstractBackend
from controller.domain.models import (
    AxisLoads,
    ErrorSeverity,
    FeedData,
    Load,
    MachineError,
    MachineState,
    Position,
    ProgramState,
)


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
    _MAX_SPINDLE_TORQUE_NM: float = 8.0
    _MAX_AXIS_TORQUE_NM: float = 3.0

    def __init__(self) -> None:

        # --- Machine state ---
        self._machine_state: MachineState = MachineState.ESTOP
        self._program_state: ProgramState = ProgramState.IDLE

        # --- Position & kinematics ---
        self._position: Position = Position()
        self._wcs_offset: Position = Position()
        self._active_wcs: int = 1            # G54
        self._t0: float = time.monotonic()

        # --- Continuous jog --- axis_index -> velocity mm/s (sign =
        # direction), applied each poll() tick — see jog_continuous()'s
        # docstring for why this dict exists at all.
        self._active_jogs: dict[int, float] = {}
        self._last_poll_time: float = time.monotonic()

        # --- Feed / speed ---
        self._feed_override: float = 1.0
        self._rapid_override: float = 1.0
        self._max_velocity: float = 100.0    # mm/s (runtime cap)
        self._axis_loads: AxisLoads = AxisLoads()

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
        self._single_block: bool = False

        # --- Error channel ---
        self._pending_error: MachineError | None = None

    # ------------------------------------------------------------------
    # poll()
    # ------------------------------------------------------------------

    def poll(self) -> None:
        """Advance simulation by one time step. Called every 50 ms."""
        now = time.monotonic()
        # dt tracked unconditionally (even while OFF/ESTOP) so a stale gap
        # never produces one huge jump the next time poll() actually runs
        # something with it — see _apply_continuous_jog()'s clamp below.
        dt = now - self._last_poll_time
        self._last_poll_time = now

        if self._machine_state != MachineState.ON:
            return

        if self._active_jogs and self._program_state != ProgramState.RUNNING:
            self._apply_continuous_jog(dt)

        t = time.monotonic() - self._t0

        if self._program_state == ProgramState.RUNNING and not self._feed_hold:
            self._poll_running(t)

        self._poll_spindle()

    def _apply_continuous_jog(self, dt: float) -> None:
        """Advance self._position for every axis currently jogging
        (jog_continuous()) by velocity * dt — the actual motion
        jog_continuous() itself only registers, matching a real
        controller's own jog loop advancing DRO position in real time
        until JOG_STOP. dt is clamped to the expected ~50ms poll tick so
        a delayed/late poll (e.g. right after set_machine_on()) can never
        produce a sudden large jump."""
        dt = min(dt, 0.2)
        _ATTRS = ("x", "y", "z", "a", "b", "c")
        for axis, velocity in self._active_jogs.items():
            if 0 <= axis < len(_ATTRS):
                attr = _ATTRS[axis]
                delta = velocity * dt
                setattr(self._position, attr, round(getattr(self._position, attr) + delta, 3))

    def _simulated_load(
        self, base: float, amplitude: float, freq: float, t: float, phase: float = 0.0
    ) -> Load:
        """Oszillierende Last: base ± amplitude, nie dauerhaft am Anschlag,
        solange amplitude + base < 100 gewählt wird."""
        percent = base + amplitude * abs(math.sin(t * freq + phase))
        percent = max(0.0, min(percent, 100.0))
        return Load(
            percent=round(percent, 1),
            torque_nm=round(percent / 100.0 * self._MAX_AXIS_TORQUE_NM, 3),
        )

    def _poll_running(self, t: float) -> None:
        if self._single_block:
            self._poll_running_single_block(t)
            return

        elapsed = time.monotonic() - self._program_start_time
        progress = min(elapsed / self._PROGRAM_DURATION, 1.0)
        self._current_line = int(progress * 200)

        self._position = Position(
            x=round(math.sin(t * 0.5) * 40.0, 3),
            y=round(math.cos(t * 0.3) * 25.0, 3),
            z=round(-5.0 + math.sin(t * 1.2) * 2.0, 3),
        )
        self._distance_to_go = round(abs(math.sin(t * 2.0)) * 15.0, 3)
        self._axis_loads = AxisLoads(
            x=self._simulated_load(base=25.0, amplitude=35.0, freq=1.3, t=t),
            y=self._simulated_load(base=20.0, amplitude=30.0, freq=0.9, t=t, phase=1.0),
            z=self._simulated_load(base=15.0, amplitude=45.0, freq=2.1, t=t, phase=2.3),
        )

        if progress >= 1.0:
            self._program_state = ProgramState.IDLE
            self._current_line = 0
            self._loaded_file = ""
            self._distance_to_go = 0.0
            self._axis_loads = AxisLoads()

    def _poll_running_single_block(self, t: float) -> None:
        """Im Einzelsatz-Modus: genau EINE Zeile pro Resume, unabhängig
        davon, wie lange pausiert wurde — kein Zeit-basierter Fortschritt."""
        self._current_line += 1
        progress = min(self._current_line / 200, 1.0)

        self._position = Position(
            x=round(math.sin(t * 0.5) * 40.0, 3),
            y=round(math.cos(t * 0.3) * 25.0, 3),
            z=round(-5.0 + math.sin(t * 1.2) * 2.0, 3),
        )
        self._distance_to_go = round(abs(math.sin(t * 2.0)) * 15.0, 3)
        self._axis_loads = AxisLoads(
            x=self._simulated_load(base=25.0, amplitude=35.0, freq=1.3, t=t),
            y=self._simulated_load(base=20.0, amplitude=30.0, freq=0.9, t=t, phase=1.0),
            z=self._simulated_load(base=15.0, amplitude=45.0, freq=2.1, t=t, phase=2.3),
        )

        self._program_state = ProgramState.PAUSED

        if progress >= 1.0:
            self._program_state = ProgramState.IDLE
            self._current_line = 0
            self._loaded_file = ""
            self._distance_to_go = 0.0
            self._axis_loads = AxisLoads()

    def _poll_spindle(self) -> None:
        if self._spindle_brake:
            self._spindle_rpm = max(0.0, round(self._spindle_rpm * 0.5, 1))
            return
        if self._spindle_target_rpm != 0.0:
            diff = self._spindle_target_rpm - self._spindle_rpm
            self._spindle_rpm = round(self._spindle_rpm + diff * 0.1, 1)
        else:
            self._spindle_rpm = max(0.0, round(self._spindle_rpm * 0.85, 1))
            if self._spindle_rpm < 1.0:  # <-- neu: Schwellenwert statt exaktem 0-Vergleich
                self._spindle_rpm = 0.0

    def set_single_block(self, enabled: bool) -> None:
        if self._single_block and not enabled:
            # Umschalten von Single-Block zurück auf Zeit-basiert:
            # program_start_time so setzen, dass sie zur aktuell erreichten
            # Zeile passt, sonst springt der nächste Tick wild.
            progress_at_current_line = self._current_line / 200
            elapsed_equivalent = progress_at_current_line * self._PROGRAM_DURATION
            self._program_start_time = time.monotonic() - elapsed_equivalent
        self._single_block = enabled

    def get_single_block(self) -> bool:
        return self._single_block

    def rewind_program(self) -> None:
        """Reset to program start without unloading. No-op if nothing
        is loaded."""
        if not self._loaded_file:
            return
        self._program_state = ProgramState.IDLE
        self._current_line = 0
        self._distance_to_go = 0.0
        self._feed_hold = False
        self._spindle_target_rpm = 0.0
        self._axis_loads = AxisLoads()
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
        # A COPY, not self._position itself: MachineController._check_position()
        # stores whatever this returns as _last_position and later compares
        # the NEXT get_position() against it to decide whether to emit
        # position_changed. jog_increment()/_apply_continuous_jog() mutate
        # self._position IN PLACE (setattr(self._position, attr, ...)) — if
        # this returned the live object by reference, _last_position would
        # silently alias that same mutating object, so the "changed?"
        # comparison would always see two references to the identical
        # already-mutated instance and never fire. That was the actual
        # reason Jog buttons never moved MachinePage's axis display, in
        # BOTH step and continuous mode (_poll_running() masked this for
        # program playback only, since it reassigns self._position to a
        # brand-new Position object each tick instead of mutating in place).
        return replace(self._position)

    def get_active_wcs(self) -> int:
        return self._active_wcs

    def get_wcs_offset(self) -> Position:
        return self._wcs_offset

    # ------------------------------------------------------------------
    # Feed / spindle reads
    # ------------------------------------------------------------------
    def get_axis_loads(self) -> AxisLoads:
        return self._axis_loads

    def get_feed_data(self) -> FeedData:
        feed_actual = 0.0
        if self._program_state == ProgramState.RUNNING and not self._feed_hold:
            feed_actual = round(abs(math.sin(time.monotonic() - self._t0)) * 800, 1)

        t = time.monotonic() - self._t0
        base_load = min(self._spindle_rpm / 12000.0 * 70.0, 70.0)  # RPM-abhängiger Sockel
        wobble = 15.0 * abs(math.sin(t * 1.7))  # Schwankung obendrauf
        spindle_load_percent = min(base_load + wobble, 100.0) if self._spindle_rpm > 0 else 0.0

        spindle_load = Load(
            percent=round(spindle_load_percent, 1),
            torque_nm=round(spindle_load_percent / 100.0 * self._MAX_SPINDLE_TORQUE_NM, 3),
        )

        return FeedData(
            feed_actual=feed_actual,
            spindle_rpm=self._spindle_rpm,
            feed_override=self._feed_override,
            spindle_load=spindle_load,
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
        self._active_jogs.clear()   # E-stop must halt any in-flight jog too

    def estop_reset(self) -> None:
        if self._machine_state == MachineState.ESTOP:
            self._machine_state = MachineState.ESTOP_RESET

    def set_machine_on(self) -> None:
        if self._machine_state == MachineState.ESTOP_RESET:
            self._machine_state = MachineState.ON
            self._t0 = time.monotonic()
            # Avoid a huge dt spike on the first poll() after being OFF for
            # a while feeding into _apply_continuous_jog() (no jog can
            # actually be active yet at this point, but this keeps poll()'s
            # dt bookkeeping honest regardless of how long ON was pending).
            self._last_poll_time = time.monotonic()

    def set_machine_off(self) -> None:
        if self._machine_state == MachineState.ON:
            self._machine_state = MachineState.OFF
            self._active_jogs.clear()

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
        self._spindle_target_rpm = 8000.0  # <-- neu: Simulation "schaltet Spindel ein"

    def stop_program(self) -> None:
        if self._program_state in (ProgramState.RUNNING, ProgramState.PAUSED):
            self._program_state = ProgramState.IDLE
            self._current_line = 0
            self._loaded_file = ""
            self._distance_to_go = 0.0
            self._feed_hold = False
            self._spindle_target_rpm = 0.0  # <-- neu: Spindel simuliert "abschalten"
            self._axis_loads = AxisLoads()

    def pause_program(self) -> None:
        if self._program_state == ProgramState.RUNNING:
            self._program_state = ProgramState.PAUSED

    def resume_program(self) -> None:
        if self._program_state == ProgramState.PAUSED:
            if self._single_block:
                # feuert EINMAL _poll_running_single_block, pausiert sofort wieder
                self._program_state = ProgramState.RUNNING
                self._spindle_target_rpm = 8000.0
                return
            elapsed = time.monotonic() - self._program_start_time
            self._program_start_time = time.monotonic() - elapsed
            self._program_state = ProgramState.RUNNING
            self._feed_hold = False
            self._spindle_target_rpm = 8000.0

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
        self._spindle_target_rpm = 0.0  # <-- neu
        self._axis_loads = AxisLoads()

    # ------------------------------------------------------------------
    # Jog commands
    # ------------------------------------------------------------------

    def jog_continuous(self, axis: int, velocity: float) -> None:
        """Start (or re-target) continuous jogging on *axis* — actual
        motion happens in poll()'s _apply_continuous_jog(), ticking every
        50ms exactly like a real controller's DRO would advance during a
        JOG_CONTINUOUS move, so the UI's axis display moves smoothly
        until jog_stop() is called. Previously a no-op stub, which is why
        pressing a Jog button never moved the position display at all."""
        self._active_jogs[axis] = velocity

    def jog_increment(self, axis: int, velocity: float, distance: float) -> None:
        """Shift position by a discrete step."""
        _ATTRS = ("x", "y", "z", "a", "b", "c")
        if 0 <= axis < len(_ATTRS):
            attr = _ATTRS[axis]
            delta = math.copysign(distance, velocity)
            setattr(self._position, attr, round(getattr(self._position, attr) + delta, 3))

    def jog_stop(self, axis: int) -> None:
        self._active_jogs.pop(axis, None)

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