from __future__ import annotations

from abc import ABC, abstractmethod

from src.controller.domain.models import (
    FeedData,
    MachineError,
    MachineState,
    Position,
    ProgramState,
    AxisLoads,
)


class AbstractBackend(ABC):
    """
    Contract that every backend must fulfill.

    The MachineController talks exclusively to this interface —
    it never knows whether a simulator or LinuxCNC is behind it.

    Method groups map directly to LinuxCNC Python-API objects:
        stat   -> all get_*() reads    (polled every 50 ms)
        cmd    -> all command methods  (write path)
        err_ch -> get_error()          (error channel, queue semantics)

    poll() is called every 50 ms. All get_*() methods return the
    value cached during the last poll() call — they never perform
    their own IO.

    Deliberately excluded (see comments on each group):
        teleop_enable()       -- LinuxCNCBackend jog implementation detail
        traj_mode()           -- internal motion controller mode
        debug()               -- LinuxCNC internal logging
        display/error_msg()   -- LinuxCNC's own UI messaging; we use signals
        set_digital_output()  -- HAL layer; use send_mdi("M64 Px") instead
        ain/aout/din/dout     -- HAL layer, not application layer
        set_adaptive_feed()   -- plasma/laser feature, not relevant for mills
        set_min/max_limit()   -- dangerous; belongs in INI configuration
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def poll(self) -> None:
        """
        Update the backend's internal state cache.
        Called every 50 ms by the MachineController.

        LinuxCNCBackend: calls stat.poll() and drains error_channel
        SimulatedBackend: advances simulation by one time step

        MUST NOT BLOCK. No wait_complete() here.
        """
        ...

    # ------------------------------------------------------------------
    # Machine state reads
    # ------------------------------------------------------------------

    @abstractmethod
    def get_machine_state(self) -> MachineState:
        """
        Overall machine state: ESTOP / ESTOP_RESET / OFF / ON.

        LinuxCNC: stat.task_state
            STATE_ESTOP       -> MachineState.ESTOP
            STATE_ESTOP_RESET -> MachineState.ESTOP_RESET
            STATE_OFF         -> MachineState.OFF
            STATE_ON          -> MachineState.ON
        """
        ...

    @abstractmethod
    def is_homed(self) -> bool:
        """
        True when all configured joints are homed.

        LinuxCNC: all(stat.homed[:stat.joints])
        """
        ...

    @abstractmethod
    def get_inpos(self) -> bool:
        """
        True when the machine has reached the commanded position
        (trajectory planner queue drained and motion settled).

        LinuxCNC: stat.inpos
        Useful for: disabling buttons that require the machine to be still.
        """
        ...

    # ------------------------------------------------------------------
    # Program / interpreter reads
    # ------------------------------------------------------------------

    @abstractmethod
    def get_program_state(self) -> ProgramState:
        """
        G-code interpreter state: IDLE / RUNNING / PAUSED / ERROR.

        LinuxCNC: stat.interp_state + stat.task_paused
            INTERP_IDLE              -> ProgramState.IDLE
            INTERP_READING / WAITING -> ProgramState.RUNNING
            INTERP_PAUSED            -> ProgramState.PAUSED
        """
        ...

    @abstractmethod
    def get_loaded_file(self) -> str:
        """
        Path to the currently loaded G-code file.

        LinuxCNC: stat.file  (empty string when no file is loaded)
        """
        ...

    @abstractmethod
    def get_current_line(self) -> int:
        """
        Line number currently being executed.

        LinuxCNC: stat.current_line  (0 when idle)
        """
        ...

    @abstractmethod
    def get_distance_to_go(self) -> float:
        """
        Remaining distance of the current move in mm,
        as reported by the trajectory planner.

        LinuxCNC: stat.distance_to_go
        Useful for: progress indicators on individual moves.
        """
        ...

    @abstractmethod
    def get_optional_stop(self) -> bool:
        """
        Current state of the optional stop flag (M1 behaviour).

        When True: program pauses at every M1 block.
        LinuxCNC: stat.optional_stop
        """
        ...

    @abstractmethod
    def get_block_delete(self) -> bool:
        """
        Current state of the block delete flag.

        When True: lines starting with '/' are skipped.
        LinuxCNC: stat.block_delete
        """
        ...

    @abstractmethod
    def get_feed_hold(self) -> bool:
        """
        True when feed hold is active (motion paused, spindle still running).

        Different from pause_program() which stops the interpreter.
        Feed hold only stops motion; the interpreter keeps its position.
        LinuxCNC: stat.feed_hold_enabled
        """
        ...

    # ------------------------------------------------------------------
    # Position / WCS reads
    # ------------------------------------------------------------------

    @abstractmethod
    def get_position(self) -> Position:
        """
        Current machine position in machine coordinates (mm / deg).

        LinuxCNC: stat.actual_position
            Tuple (x, y, z, a, b, c, u, v, w) -> Position(x, y, z, a, b, c)
            u/v/w are ignored (not relevant for a 3/4/5-axis mill)
        """
        ...

    @abstractmethod
    def get_active_wcs(self) -> int:
        """
        Currently active work coordinate system index.

        LinuxCNC: stat.g5x_index
            G54 = 1, G55 = 2, G56 = 3, G57 = 4, G58 = 5, G59 = 6
        """
        ...

    @abstractmethod
    def get_wcs_offset(self) -> Position:
        """
        Offset of the currently active WCS relative to machine zero.

        LinuxCNC: stat.g5x_offset  (9-tuple, same mapping as actual_position)
        """
        ...

    # ------------------------------------------------------------------
    # Feed / spindle reads
    # ------------------------------------------------------------------

    @abstractmethod
    def get_feed_data(self) -> FeedData:
        """
        Current feed rate, spindle RPM, and feed override factor.

        LinuxCNC:
            stat.current_vel         -> feed_actual  (mm/s, NOT mm/min!)
            stat.spindle[0]['speed'] -> spindle_rpm
            stat.feedrate            -> feed_override (1.0 = 100%)
        """
        ...

    @abstractmethod
    def get_rapid_override(self) -> float:
        """
        Rapid traverse override factor.

        Separate from feed override — G0 moves use this, G1/G2/G3 use feedrate.
        LinuxCNC: stat.rapidrate  (1.0 = 100%)
        """
        ...

    @abstractmethod
    def get_axis_loads(self) -> AxisLoads:
        """Current per-axis load (percent + torque), wherever available."""
        ...

    # ------------------------------------------------------------------
    # Coolant / auxiliary reads
    # ------------------------------------------------------------------

    @abstractmethod
    def get_flood(self) -> bool:
        """
        True when flood coolant is on.

        LinuxCNC: stat.flood == linuxcnc.FLOOD_ON
        """
        ...

    @abstractmethod
    def get_mist(self) -> bool:
        """
        True when mist coolant is on.

        LinuxCNC: stat.mist == linuxcnc.MIST_ON
        """
        ...

    @abstractmethod
    def get_lube(self) -> bool:
        """
        True when the lubrication system is active.

        LinuxCNC: stat.lube  (driven by iocontrol.0.lube)
        """
        ...

    # ------------------------------------------------------------------
    # Tool reads
    # ------------------------------------------------------------------

    @abstractmethod
    def get_tool_in_spindle(self) -> int:
        """
        T-number of the currently loaded tool.

        LinuxCNC: stat.tool_in_spindle  (0 = no tool)
        """
        ...

    @abstractmethod
    def get_spindle_brake(self) -> bool:
        """
        True when the spindle brake is engaged.

        LinuxCNC: stat.spindle[0]['brake']
        """
        ...

    # ------------------------------------------------------------------
    # Error channel
    # ------------------------------------------------------------------

    @abstractmethod
    def get_error(self) -> MachineError | None:
        """
        Returns one pending error message and removes it from the queue.
        Returns None if no error is pending.

        LinuxCNC: error_channel.poll()
            Returns (kind, text) or None.
            kind: NML_ERROR / OPERATOR_ERROR -> ErrorSeverity.ERROR
                  NML_TEXT / OPERATOR_TEXT   -> ErrorSeverity.INFO

        WARNING: The NML error channel is a queue — whoever reads first
        deletes the message for all other readers. Only one reader per setup!
        """
        ...

    # ------------------------------------------------------------------
    # Machine state commands
    # ------------------------------------------------------------------

    @abstractmethod
    def estop(self) -> None:
        """
        Trigger E-stop. Always allowed — no precondition check.

        LinuxCNC: cmd.state(linuxcnc.STATE_ESTOP)
        """
        ...

    @abstractmethod
    def estop_reset(self) -> None:
        """
        Acknowledge E-stop -> state becomes ESTOP_RESET.

        LinuxCNC: cmd.state(linuxcnc.STATE_ESTOP_RESET)
        Precondition: task_state == STATE_ESTOP
        """
        ...

    @abstractmethod
    def set_machine_on(self) -> None:
        """
        Enable servo drives -> state becomes ON.

        LinuxCNC: cmd.state(linuxcnc.STATE_ON)
        Precondition: task_state == STATE_ESTOP_RESET
        Silently ignored if precondition is not met.
        """
        ...

    @abstractmethod
    def set_machine_off(self) -> None:
        """
        Disable servo drives -> state becomes OFF.
        Not an E-stop; re-enable without quitting is possible.

        LinuxCNC: cmd.state(linuxcnc.STATE_OFF)
        """
        ...

    # ------------------------------------------------------------------
    # Program commands
    # ------------------------------------------------------------------

    @abstractmethod
    def run_program(self, gcode_path: str) -> None:
        """
        Open a G-code file and start execution.

        LinuxCNC sequence (order matters):
            1. cmd.mode(linuxcnc.MODE_AUTO)
            2. cmd.wait_complete()        -- blocks; only allowed here
            3. cmd.program_open(path)
            4. cmd.auto(linuxcnc.AUTO_RUN, 0)   -- 0 = from line 1

        Preconditions: STATE_ON, all joints homed, INTERP_IDLE
        """
        ...

    @abstractmethod
    def pause_program(self) -> None:
        """
        Pause program execution (interpreter stops between blocks).

        LinuxCNC: cmd.auto(linuxcnc.AUTO_PAUSE)
        Precondition: interp_state == INTERP_READING or INTERP_WAITING
        """
        ...

    @abstractmethod
    def resume_program(self) -> None:
        """
        Resume a paused program.

        LinuxCNC: cmd.auto(linuxcnc.AUTO_RESUME)
        Precondition: task_paused == True
        """
        ...

    @abstractmethod
    def stop_program(self) -> None:
        """
        Abort the running or paused program.

        LinuxCNC: cmd.abort()
        """
        ...

    @abstractmethod
    def auto_step(self) -> None:
        """
        Execute one G-code block then pause (single-step mode).
        Essential for debugging G-code programs line by line.

        LinuxCNC: cmd.auto(linuxcnc.AUTO_STEP)
        Precondition: MODE_AUTO, program loaded
        """
        ...

    @abstractmethod
    def set_single_block(self, enabled: bool) -> None: ...

    @abstractmethod
    def get_single_block(self) -> bool: ...

    @abstractmethod
    def rewind_program(self) -> None:
        """Reset to the start of the currently loaded program without
        unloading it. Motion/spindle stop, line back to 0, file stays
        loaded — unlike stop_program()/reset_interpreter()."""
        ...

    # ------------------------------------------------------------------
    # Interpreter commands
    # ------------------------------------------------------------------

    @abstractmethod
    def set_optional_stop(self, enabled: bool) -> None:
        """
        Enable or disable the optional stop flag (M1 behaviour).

        LinuxCNC: cmd.set_optional_stop(int)
        """
        ...

    @abstractmethod
    def set_block_delete(self, enabled: bool) -> None:
        """
        Enable or disable block delete (skip lines starting with '/').

        LinuxCNC: cmd.set_block_delete(int)
        """
        ...

    @abstractmethod
    def set_feed_hold(self, enabled: bool) -> None:
        """
        Enable or disable feed hold.

        Feed hold stops motion without stopping the interpreter.
        Different from pause_program(): the spindle keeps running.
        LinuxCNC: cmd.set_feed_hold(int)
        """
        ...

    @abstractmethod
    def reset_interpreter(self) -> None:
        """
        Reset the RS274NGC interpreter to a known state.
        Use for recovery after an interpreter error.

        LinuxCNC: cmd.reset_interpreter()
        """
        ...

    # ------------------------------------------------------------------
    # Jog commands
    # ------------------------------------------------------------------

    @abstractmethod
    def jog_continuous(self, axis: int, velocity: float) -> None:
        """
        Jog an axis continuously until jog_stop() is called.

        axis:     0=X, 1=Y, 2=Z, 3=A, 4=B, 5=C  (XYZABCUVW index)
        velocity: mm/s; negative value = reverse direction

        LinuxCNC:
            cmd.mode(MODE_MANUAL)
            cmd.teleop_enable(False)   -- joint jog, not Cartesian
            cmd.jog(JOG_CONTINUOUS, False, axis, velocity)

        Preconditions: STATE_ON, INTERP_IDLE
        """
        ...

    @abstractmethod
    def jog_increment(self, axis: int, velocity: float, distance: float) -> None:
        """
        Jog an axis by a fixed increment.

        distance: step size in mm (always positive)
                  direction is determined by sign of velocity

        LinuxCNC: cmd.jog(JOG_INCREMENT, False, axis, velocity, distance)
        """
        ...

    @abstractmethod
    def jog_stop(self, axis: int) -> None:
        """
        Stop a continuous jog on one axis.

        LinuxCNC: cmd.jog(JOG_STOP, False, axis)
        """
        ...

    # ------------------------------------------------------------------
    # Homing
    # ------------------------------------------------------------------

    @abstractmethod
    def home_joint(self, joint: int) -> None:
        """
        Home a single joint.

        LinuxCNC: cmd.home(joint)
        With cia402_homecomp: the drive handles the homing sequence internally.
        Preconditions: STATE_ON, MODE_MANUAL
        """
        ...

    @abstractmethod
    def home_all(self) -> None:
        """
        Home all joints in the sequence defined by HOME_SEQUENCE in the INI.

        LinuxCNC: cmd.home(-1)   -- -1 = all joints
        """
        ...

    @abstractmethod
    def unhome_joint(self, joint: int) -> None:
        """
        Mark a joint as unhomed without moving it.
        Used for recovery or re-homing a single joint.

        LinuxCNC: cmd.unhome(joint)
        """
        ...

    # ------------------------------------------------------------------
    # Feed / speed overrides
    # ------------------------------------------------------------------

    @abstractmethod
    def set_feed_override(self, value: float) -> None:
        """
        Set feed rate override factor.

        value: 0.0–2.0  (1.0 = 100%, 0.5 = 50%, 2.0 = 200%)
        LinuxCNC: cmd.feedrate(value)
        """
        ...

    @abstractmethod
    def set_rapid_override(self, value: float) -> None:
        """
        Set rapid traverse override factor.

        Separate from feed override — only affects G0 moves.
        value: 0.0–1.0  (LinuxCNC clamps rapid to max 100%)
        LinuxCNC: cmd.rapidrate(value)
        """
        ...

    @abstractmethod
    def set_spindle_override(self, value: float) -> None:
        """
        Set spindle speed override factor.

        value: 0.0–2.0  (1.0 = 100%)
        LinuxCNC: cmd.spindleoverride(value)
        """
        ...

    @abstractmethod
    def set_max_velocity(self, value: float) -> None:
        """
        Set the current maximum velocity in mm/s.
        Does not persist across restarts (runtime limit only).

        LinuxCNC: cmd.maxvel(value)
        """
        ...

    # ------------------------------------------------------------------
    # Spindle
    # ------------------------------------------------------------------

    @abstractmethod
    def spindle_on(self, rpm: float, forward: bool = True) -> None:
        """
        Start the spindle at the given speed.

        LinuxCNC:
            cmd.spindle(SPINDLE_FORWARD, rpm)  when forward=True
            cmd.spindle(SPINDLE_REVERSE, rpm)  when forward=False
        Note: subsequent MDI S-words will override this command.
        """
        ...

    @abstractmethod
    def spindle_off(self) -> None:
        """
        Stop the spindle.

        LinuxCNC: cmd.spindle(linuxcnc.SPINDLE_OFF)
        """
        ...

    @abstractmethod
    def spindle_brake(self, engage: bool) -> None:
        """
        Engage or release the spindle brake.

        engage=True  -> LinuxCNC: cmd.brake(linuxcnc.BRAKE_ENGAGE)
        engage=False -> LinuxCNC: cmd.brake(linuxcnc.BRAKE_RELEASE)
        """
        ...

    # ------------------------------------------------------------------
    # Coolant
    # ------------------------------------------------------------------

    @abstractmethod
    def flood_on(self) -> None:
        """
        Turn on flood coolant.

        LinuxCNC: cmd.flood(linuxcnc.FLOOD_ON)
        """
        ...

    @abstractmethod
    def flood_off(self) -> None:
        """
        Turn off flood coolant.

        LinuxCNC: cmd.flood(linuxcnc.FLOOD_OFF)
        """
        ...

    @abstractmethod
    def mist_on(self) -> None:
        """
        Turn on mist coolant.

        LinuxCNC: cmd.mist(linuxcnc.MIST_ON)
        """
        ...

    @abstractmethod
    def mist_off(self) -> None:
        """
        Turn off mist coolant.

        LinuxCNC: cmd.mist(linuxcnc.MIST_OFF)
        """
        ...

    # ------------------------------------------------------------------
    # Limit override
    # ------------------------------------------------------------------

    @abstractmethod
    def override_limits(self) -> None:
        """
        Override axis soft limits for one move (recovery from limit trip).
        The override is automatically cleared after the next move completes.

        LinuxCNC: cmd.override_limits()
        Use with caution — only for manual recovery, never in automation.
        """
        ...

    # ------------------------------------------------------------------
    # Tool management
    # ------------------------------------------------------------------

    @abstractmethod
    def load_tool_table(self) -> None:
        """
        Reload the tool table from disk (tool.tbl).
        Call after manually editing tool offsets outside of the HMI.

        LinuxCNC: cmd.load_tool_table()
        """
        ...

    @abstractmethod
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
        """
        Write a tool offset entry directly from the HMI (e.g. after probing).

        LinuxCNC: cmd.tool_offset(tool_num, z_offset, x_offset,
                                  diameter, front_angle, back_angle, orientation)
        Automatically triggers a load_tool_table() internally in LinuxCNC.
        """
        ...

    # ------------------------------------------------------------------
    # MDI
    # ------------------------------------------------------------------

    @abstractmethod
    def send_mdi(self, command: str) -> None:
        """
        Send a single G-code command in MDI mode.

        LinuxCNC sequence:
            1. cmd.mode(linuxcnc.MODE_MDI)
            2. cmd.wait_complete()
            3. cmd.mdi(command)      -- max 254 chars

        Preconditions: STATE_ON, all joints homed, INTERP_IDLE

        Typical uses: tool change (T1 M6), set WCS origin (G10 L20 ...),
                      probe cycle, digital output (M64 P0).
        Note on lights/IO: prefer M64/M65 via send_mdi() over raw
        set_digital_output() calls — M-codes are motion-synchronized.
        """
        ...