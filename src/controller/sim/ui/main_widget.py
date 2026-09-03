"""
sim/ui/main_widget.py — DatumSimWidget: 3D G-code viewer + sim player.

Composes:
  • Viewport       — ModernGL OpenGL widget (G-code path + tool)
  • SettingsPanel  — right-edge slide-out with functional sim settings
  • ControlHub     — bottom-centre playback bar + info labels

All three are positioned as overlays on top of the viewport via
_layout_overlays(), which is called from resizeEvent and open/close panel
actions so the geometry always stays correct.

Voxel material-removal simulation
----------------------------------
When the C++ extension is gone, the voxel sim is implemented entirely in
Python + numpy (carver.py / gpu_grid.py) with a GLSL raymarching renderer
(renderer.py).  All objects (GpuVoxelGrid, VoxelCarver, VoxelSimController,
VoxelRenderer) require an active GL context and are therefore created lazily
after the viewport's initializeGL fires.

Public interface (mirrors SimPlaceholder so machine_page.py can swap freely):
    set_file(path)              — load and compile a G-code file
    set_mode("SIM"|"MACHINE")  — sim player vs. live machine follow
    set_state(state_str)        — string state label (passed through)
    set_position(x, y, z)      — push live machine position (MACHINE mode)
    set_line(line)              — highlight a G-code line
    sim_play/pause/reset/seek/set_speed — simulation playback controls
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget

from controller.sim.ui.viewport        import Viewport, ToolMode, PathMode
from controller.sim.ui.overlay.settings_panel import SettingsPanel
from controller.sim.ui.overlay.control_hub    import ControlHub
from controller.sim.gcode.compiler            import GCodeCompiler
from controller.sim.simulation.player         import SimulationPlayer
from controller.sim.simulation.tool_database  import get_tool
from controller.sim.simulation.tool_definition import ToolDefinition
from controller.persistence.tool_db           import ToolDatabase
from controller.persistence.workpiece_db      import WorkpieceDatabase
from controller.sim.core.settings             import AppSettings

# Voxel sim — pure Python/numpy + GLSL raymarching (no C++ required)
try:
    from controller.sim.voxel.stock      import StockDefinition, StockShape
    from controller.sim.voxel.gpu_grid   import GpuVoxelGrid, solid_material
    from controller.sim.voxel.carver     import VoxelCarver
    from controller.sim.voxel.controller import VoxelSimController
    from controller.sim.voxel.renderer   import VoxelRenderer
    from controller.sim.voxel.collision  import check_segment, CollisionHit
    _VOXEL_AVAILABLE = True
except ImportError:
    _VOXEL_AVAILABLE = False

logger = logging.getLogger(__name__)

# Backlog (mm of arc-length still needing to be carved) below which _tick()
# carves synchronously, in-tick, on the main/GL thread instead of handing it
# to the background worker. Derived from the worst plausible single-tick
# advance: max speed-slider multiplier (20x, see control_hub.py _SPEED_MAX)
# x a generous cutting-feed ceiling (5000 mm/min) / 60 x the 32ms tick
# interval ~= 53mm; the highest cutting feed actually used in
# workpieces/Gcode.cnc (1828.8 mm/min) gives ~= 19.5mm at 20x, so 40mm leaves
# real margin either way. This is only a soft performance/UX threshold —
# exceeding it never produces a wrong result, it just routes to the (already
# correct) chunked background path instead of carving inline. It relies on
# PathBuffer capping tessellated-point density to ~1-2 points/mm (see
# path_buffer.py's max_step_mm) — without that bound this threshold would not
# actually bound per-tick carve cost.
_SYNC_CARVE_MAX_MM = 40.0


class DatumSimWidget(QWidget):
    """Top-level 3D simulation widget — drop-in replacement for SimPlaceholder."""

    # Emitted whenever the estimated part/cycle time is (re)computed — a
    # float in seconds, or None if no program is loaded. The sim widget owns
    # the computation (it has the PathBuffer + current overrides); the
    # display lives elsewhere (ProgramInfoCard on MachinePage), pushed-state
    # style like set_position()/set_line().
    part_time_changed = Signal(object)

    # Emitted whenever the active tool changes (T-command crossed, program
    # (re)loaded, or reset) — a ToolDefinition. Lets MachinePage's
    # ToolInfoCard reflect the tool called out in the running program,
    # including during a real MACHINE-mode run.
    tool_changed = Signal(object)

    # Emitted the first time a new VoxelSimController.collision_hit
    # (collision.CollisionHit) is observed — never re-emitted for the same
    # hit on subsequent ticks. See _handle_collision_state().
    collision_detected = Signal(object)

    # Internal plumbing for presim_check_collisions()'s background->main
    # thread handoff — see that method's docstring for why this (a Qt
    # signal) is used instead of QTimer.singleShot() from the worker
    # thread.
    _presim_collision_done = Signal(object)

    _TOOL_MAP = {
        "Endmill":    ToolMode.CYLINDER,
        "Point":      ToolMode.POINT,
        "None":       ToolMode.NONE,
    }
    _PATH_MAP = {
        "Complete":    PathMode.FULL,
        "Progressive": PathMode.PROGRESSIVE,
        "None":        PathMode.NONE,
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.viewport    = Viewport(self)
        self.settings    = SettingsPanel(self)
        self.control_hub = ControlHub(self)
        self.compiler    = GCodeCompiler()

        # ── Internal state ────────────────────────────────────────────────────
        self._player:               SimulationPlayer | None = None
        self._state:                str                     = "IDLE"
        self._mode:                 str                     = "SIM"
        self._clean_lines:          list[str]               = []
        self._raw_lines:            list[str]               = []
        self._tool_changes:         list                    = []
        self._last_tool_change_idx: int                     = -1
        self._path_mode:            PathMode                = PathMode.FULL
        self._tool_mode:            ToolMode                = ToolMode.POINT
        self._current_tool:         ToolDefinition | None   = None
        self._last_program                                  = None
        self._current_workpiece                             = None   # domain.models.Workpiece | None
        self._current_line:         int                     = -1
        # Whether the viewport/control_hub are CURRENTLY showing the
        # collision-active state — toggled to match ctrl.collision_hit's
        # live on/off value each tick, so viewport.set_collision()/
        # control_hub.set_collision() are only called on actual transitions
        # (cheap to call _handle_collision_state() unconditionally).
        self._collision_active = False

        # Feed/rapid overrides (1.0 = 100%), pushed in from MACHINE mode via
        # set_feed_override()/set_rapid_override() — drive the estimated
        # part-time display; SIM mode simply leaves them at 1.0.
        self._feed_override:  float = 1.0
        self._rapid_override: float = 1.0

        # Voxel objects (all None until _create_voxel_sim() runs after initializeGL)
        self._voxel_ctrl:     VoxelSimController | None = None
        self._voxel_renderer: VoxelRenderer | None      = None
        self._pending_voxel_setup: bool = False

        # Carving runs in a background thread so the main-thread controller UI
        # is never blocked by long numpy operations (deep plunges etc.).
        self._carve_thread: threading.Thread | None = None
        self._carve_done   = threading.Event()   # set by worker when grid is dirty
        self._carve_abort  = threading.Event()   # set to cancel in-flight carving

        # MACHINE-mode carving: track consecutive positions for segment carving.
        # Written and read exclusively on the main thread — no lock required.
        self._last_machine_pos:       np.ndarray | None                    = None
        self._pending_machine_carve:  tuple[np.ndarray, np.ndarray] | None = None

        # ── Signal connections ────────────────────────────────────────────────
        # Tool/path display mode are read straight from AppSettings rather
        # than forwarded through a specific settings-widget instance: the
        # same setting can now be edited from either the sim widget's own
        # overlay panel or the app-wide SettingsPage (see sim_panel.py's
        # build_sections()), so AppSettings is the single source of truth
        # both listen to and write through.
        s = AppSettings.instance()
        s.tool_mode_changed.connect(
            lambda name: self.set_tool_mode(self._TOOL_MAP.get(name, ToolMode.CYLINDER)))
        s.path_mode_changed.connect(
            lambda name: self.set_path_mode(self._PATH_MAP.get(name, PathMode.PROGRESSIVE)))

        s.voxel_size_changed.connect(self._on_voxel_size_changed)
        s.voxel_enabled_changed.connect(self._on_voxel_enabled_changed)
        # Rebuild the sim whenever stock geometry settings change
        s.stock_shape_changed.connect(self._on_stock_settings_changed)
        s.stock_z_offset_changed.connect(self._on_stock_settings_changed)
        s.stock_height_changed.connect(self._on_stock_settings_changed)
        s.stock_round_radius_changed.connect(self._on_stock_settings_changed)
        s.stock_width_changed.connect(self._on_stock_settings_changed)
        s.stock_depth_changed.connect(self._on_stock_settings_changed)
        s.stock_x_offset_changed.connect(self._on_stock_settings_changed)
        s.stock_y_offset_changed.connect(self._on_stock_settings_changed)
        s.collision_detection_enabled_changed.connect(self._on_collision_enabled_changed)

        # ── Render tick (~30 fps) ─────────────────────────────────────────────
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(32)
        self._render_timer.timeout.connect(self._tick)
        self._render_timer.start()

        self._connect_control_hub()
        # Re-centre the hub whenever its height changes (info row toggled)
        self.control_hub.layout_changed.connect(self._layout_overlays)
        self._layout_overlays()

    # ── File loading ──────────────────────────────────────────────────────────

    def set_file(self, path: str) -> None:
        """Compile and load a G-code file, set up the simulation player."""
        self._teardown_voxel_sim()

        s = AppSettings.instance()
        # The program's first move is implicitly a rapid FROM this position
        # (see GCodeCompiler.load_file()'s docstring) — work origin + a safe
        # Z clearance, not the work origin itself, so that rapid doesn't
        # read as a false-positive plunge into the stock right at tick 1.
        start_position = np.array([0.0, 0.0, s.start_safe_z_mm], dtype='f4')
        program = self.compiler.load_file(path, start_position=start_position)
        self._clean_lines          = program.clean_lines
        self._raw_lines            = program.raw_lines   # for collision log line text
        self._player               = SimulationPlayer(program.path)
        self._tool_changes         = program.tool_changes
        self._last_tool_change_idx = -1
        self._last_program         = program   # kept for voxel re-init

        # Resolve/create this file's Workpiece record — see
        # persistence.workpiece_db.WorkpieceDatabase and
        # domain.models.Workpiece's collision_detection_enabled docstring.
        # Purely a per-workpiece override lookup today (_effective_
        # collision_enabled()) — no UI anywhere reads/writes it yet.
        self._current_workpiece = WorkpieceDatabase.instance().get_or_create_by_path(path)

        self.viewport.set_path(program.path)

        self.set_tool_mode(self._TOOL_MAP.get(s.tool_mode, ToolMode.CYLINDER))
        self.set_path_mode(self._PATH_MAP.get(s.path_mode, PathMode.PROGRESSIVE))

        self._apply_initial_tool()
        self._recompute_part_time()

        if _VOXEL_AVAILABLE and s.voxel_enabled:
            self._schedule_voxel_sim()

    # ── Voxel sim lifecycle ───────────────────────────────────────────────────

    def _schedule_voxel_sim(self) -> None:
        """Create voxel objects now if GL is ready, or defer until first paint."""
        if hasattr(self.viewport, "ctx"):
            self._create_voxel_sim()
        else:
            self._pending_voxel_setup = True
            self.viewport.installEventFilter(self)

    def _create_voxel_sim(self) -> None:
        """
        Instantiate GpuVoxelGrid, VoxelCarver, VoxelSimController, VoxelRenderer.

        Must be called with the GL context current (or makeCurrent is called here).
        """
        if not _VOXEL_AVAILABLE or self._last_program is None:
            return

        s = AppSettings.instance()

        self.viewport.makeCurrent()
        try:
            # Build stock definition from persisted settings, then derive bbox
            # from the cutting-move extent of the loaded program.
            try:
                shape = StockShape(s.stock_shape)
            except ValueError:
                shape = StockShape.BOUNDING_BOX

            stock = StockDefinition(
                shape           = shape,
                voxel_size      = s.voxel_size,
                z_offset_mm     = s.stock_z_offset_mm,
                height_mm       = s.stock_height_mm,
                round_radius_mm = s.stock_round_radius_mm,
                width_mm        = s.stock_width_mm,
                depth_mm        = s.stock_depth_mm,
                x_offset_mm     = s.stock_x_offset_mm,
                y_offset_mm     = s.stock_y_offset_mm,
            )
            stock.build_bbox(self._last_program.path)

            grid  = GpuVoxelGrid(self.viewport.ctx, stock)
            carver = VoxelCarver(grid)

            active_tool = self._current_tool or get_tool(1)
            self._voxel_ctrl = VoxelSimController(
                grid             = grid,
                carver           = carver,
                path_points      = self._last_program.path.points,
                path_arc_lengths = self._last_program.path.arc_lengths,
                path_feed_rates  = self._last_program.path.feed_rates,
                tool             = active_tool,
                path_line_ids    = self._last_program.path.line_ids,
            )
            self._voxel_ctrl.set_collision_enabled(self._effective_collision_enabled())
            if active_tool is not None:
                self._voxel_ctrl.update_holder(
                    ToolDatabase.instance().get_holder(active_tool.holder_preset)
                )
            self._collision_active = False

            self._voxel_renderer = VoxelRenderer(self.viewport.ctx, grid)
        finally:
            self.viewport.doneCurrent()

        self.viewport.set_voxel_renderer(self._voxel_renderer)

    def _teardown_voxel_sim(self) -> None:
        """Stop the controller and remove the renderer from the viewport."""
        # Signal the carving thread to abort and wait briefly for it to exit.
        # This prevents the thread from writing to the (about-to-be-freed) grid.
        self._carve_abort.set()
        if self._carve_thread is not None and self._carve_thread.is_alive():
            self._carve_thread.join(timeout=0.3)
        self._carve_abort.clear()
        self._carve_done.clear()
        self._carve_thread = None

        self._voxel_ctrl = None          # carving stops immediately

        if self._voxel_renderer is not None:
            # Release GPU shader objects
            self.viewport.makeCurrent()
            try:
                self._voxel_renderer.release()
            finally:
                self.viewport.doneCurrent()
            self._voxel_renderer = None

        self.viewport.set_voxel_renderer(None)   # viewport stops rendering it

    def eventFilter(self, obj, event) -> bool:
        """One-shot: create voxel sim after initializeGL fires (first Paint)."""
        from PySide6.QtCore import QEvent
        if (
            obj is self.viewport
            and event.type() == QEvent.Type.Paint
            and self._pending_voxel_setup
        ):
            self._pending_voxel_setup = False
            self.viewport.removeEventFilter(self)
            self._create_voxel_sim()
        return super().eventFilter(obj, event)

    # ── AppSettings callbacks ─────────────────────────────────────────────────

    def _on_voxel_size_changed(self, voxel_size: float) -> None:
        """Voxel size changed → rebuild grid from scratch (no live resize)."""
        if not _VOXEL_AVAILABLE:
            return
        self._teardown_voxel_sim()
        if self._last_program is not None and AppSettings.instance().voxel_enabled:
            self._schedule_voxel_sim()

    def _on_voxel_enabled_changed(self, enabled: bool) -> None:
        """Voxel enable/disable checkbox toggled."""
        if not _VOXEL_AVAILABLE:
            return
        if enabled:
            if self._last_program is not None:
                self._schedule_voxel_sim()
        else:
            self._teardown_voxel_sim()

    def _on_collision_enabled_changed(self, _enabled: bool) -> None:
        # Re-resolve rather than trust the raw global value directly — a
        # per-workpiece override (if one is set) must keep taking
        # precedence over a global-setting change.
        if self._voxel_ctrl is not None:
            self._voxel_ctrl.set_collision_enabled(self._effective_collision_enabled())

    def _effective_collision_enabled(self) -> bool:
        """AppSettings.collision_detection_enabled, unless the current
        workpiece has an explicit override (see domain.models.Workpiece's
        collision_detection_enabled docstring) — used both for the live
        VoxelSimController and presim_check_collisions()."""
        wp = self._current_workpiece
        if wp is not None and wp.collision_detection_enabled is not None:
            return wp.collision_detection_enabled
        return AppSettings.instance().collision_detection_enabled

    def _on_stock_settings_changed(self, *_args) -> None:
        """Any stock geometry setting changed → tear down and rebuild the sim."""
        if not _VOXEL_AVAILABLE:
            return
        self._teardown_voxel_sim()
        if self._last_program is not None and AppSettings.instance().voxel_enabled:
            self._schedule_voxel_sim()

    # ── Tool management ───────────────────────────────────────────────────────

    def _apply_tool(self, tool: ToolDefinition | None) -> None:
        if tool is None:
            return
        self._current_tool = tool
        self.viewport.set_tool_definition(tool)
        # Resolve+push the tool's assigned holder (if any) too — a single
        # DB lookup per tool change, not per frame. viewport.set_tool_holder()
        # only actually renders it while AppSettings.show_tool_holder is on;
        # the voxel controller always factors it into collision checks (its
        # own on/off switch is AppSettings.collision_detection_enabled).
        holder = ToolDatabase.instance().get_holder(tool.holder_preset)
        self.viewport.set_tool_holder(holder)
        # Push to the info pill directly rather than waiting for the next
        # SIM-mode _tick() to refresh it — _tick()'s tool-label refresh only
        # ran while self._mode == "SIM", so MACHINE-mode tool changes (via
        # set_line() -> _check_tool_change()) and sim_reset() never reached
        # the display until (if ever) SIM mode ticked again.
        self.control_hub.set_tool(tool.tool_number)
        self.tool_changed.emit(tool)
        if self._voxel_ctrl is not None:
            self._voxel_ctrl.update_tool(tool)
            self._voxel_ctrl.update_holder(holder)

    def _check_tool_change(self, current_line: int) -> None:
        for tc in self._tool_changes:
            if tc.line_index <= current_line and tc.line_index > self._last_tool_change_idx:
                self._last_tool_change_idx = tc.line_index
                self._apply_tool(get_tool(tc.tool_number))

    def _apply_initial_tool(self) -> None:
        """Apply the program's first tool and reset tool-change tracking.

        Shared by set_file() (new program loaded) and sim_reset() (program
        restarted from the beginning) so the tool info field always shows
        the correct start-of-program tool rather than whatever tool was
        last active before the reload/reset.
        """
        if self._last_program is None:
            return
        first_tool = (
            get_tool(self._last_program.tool_changes[0].tool_number)
            if self._last_program.tool_changes else get_tool(1)
        )
        self._apply_tool(first_tool)

    # ── Feed/rapid override + part-time estimate ──────────────────────────────

    def set_feed_override(self, value: float) -> None:
        """Push the machine's current feed override (1.0 = 100%) in from
        outside — mirrors the set_position()/set_line() "pushed state"
        style already used for MACHINE mode. Recomputes the displayed
        part-time estimate."""
        self._feed_override = value
        self._recompute_part_time()

    def set_rapid_override(self, value: float) -> None:
        """Push the machine's current rapid override (1.0 = 100%) in from
        outside. Recomputes the displayed part-time estimate."""
        self._rapid_override = value
        self._recompute_part_time()

    def _recompute_part_time(self) -> None:
        if self._last_program is None:
            self.part_time_changed.emit(None)
            return
        seconds = self._last_program.path.estimated_time_s(
            self._feed_override, self._rapid_override,
        )
        self.part_time_changed.emit(seconds)

    # ── Machine interface (SimPlaceholder-compatible API) ─────────────────────

    def set_state(self, state: str) -> None:
        self._state = state

    def set_position(self, x: float, y: float, z: float) -> None:
        pos = np.array([x, y, z], dtype="f4")
        if self._mode == "MACHINE":
            self.viewport.set_tool_position(pos)
            if self._voxel_ctrl is not None:
                if self._last_machine_pos is not None:
                    # Overwrite any pending carve — only the latest endpoint
                    # matters; the worker always uses the newest position.
                    self._pending_machine_carve = (
                        self._last_machine_pos.copy(),
                        pos.copy(),
                    )
                self._last_machine_pos = pos.copy()

    def set_line(self, line: int) -> None:
        self._current_line = line
        self.viewport.set_active_line(line)
        # Keep the tool info field correct during a real machine run too —
        # previously only the SIM-mode _tick() loop applied tool changes,
        # so the info field never moved past the program's first tool while
        # MACHINE mode pushed lines in from the controller.
        self._check_tool_change(line)

    # ── Mode ──────────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        assert mode in ("SIM", "MACHINE"), f"Unknown mode: {mode!r}"
        self._mode = mode
        self.control_hub.set_mode(mode)
        if self._player:
            self._player.reset()
        # Reset machine-mode position tracking on every transition so we never
        # accidentally carve a phantom segment from the last known position.
        self._last_machine_pos      = None
        self._pending_machine_carve = None
        # Reset tool-change tracking so a MACHINE run always re-evaluates T
        # changes from the start of the program, independent of how far SIM
        # playback had already progressed before switching modes.
        self._last_tool_change_idx = -1
        self._apply_initial_tool()

    def set_path_mode(self, mode: PathMode) -> None:
        self._path_mode = mode
        self.viewport.set_path_mode(mode)

    def set_tool_mode(self, mode: ToolMode) -> None:
        self._tool_mode = mode
        self.viewport.set_tool_mode(mode)

    # ── Playback controls ─────────────────────────────────────────────────────

    def sim_play(self) -> None:
        if self._player:
            self._player.play()

    def sim_pause(self) -> None:
        if self._player:
            self._player.pause()
        self._force_carve_catchup()

    def sim_reset(self) -> None:
        # Stop any in-flight carving thread and WAIT for it to actually
        # exit before touching the grid. A timed-out join here used to drop
        # the Python reference while the OS thread kept running in the
        # background — free to keep calling carve_segment()/grid.carve()
        # against the SAME grid right after _voxel_ctrl.reset() re-solidified
        # it, silently re-carving material back out and making Reset look
        # like it hadn't cleared the stock. The worker loop (_carve_worker)
        # already checks _carve_abort at the top of every 5mm chunk, so a
        # plain join() here only ever waits out however long the chunk that
        # was already in flight takes to finish — milliseconds in practice.
        self._carve_abort.set()
        if self._carve_thread is not None and self._carve_thread.is_alive():
            self._carve_thread.join()
        self._carve_abort.clear()
        self._carve_done.clear()
        self._carve_thread = None

        if self._player:
            self._player.reset()
            self._last_tool_change_idx = -1
            self._apply_initial_tool()
            self.control_hub.reset_play_state()
        if self._voxel_ctrl is not None:
            # VoxelSimController.reset() -> GpuVoxelGrid.reset() does its own
            # immediate, full self._tex.write(...) (not the incremental
            # upload_if_dirty() path) and then clears the dirty flag itself
            # — so the GL context must already be current for THIS call, not
            # after it. It used to be called before makeCurrent(), which
            # made that write execute with no context bound (a no-op on
            # most drivers rather than a crash); reset() then leaving
            # `_dirty = False` meant the upload_if_dirty() call that
            # followed had nothing left to do either, so the CPU-side grid
            # went back to solid but the actually-rendered GPU texture
            # never did — Reset looked like it hadn't cleared the stock.
            self.viewport.makeCurrent()
            try:
                self._voxel_ctrl.reset()   # also clears collision_hit — see its docstring
            finally:
                self.viewport.doneCurrent()
        self._collision_active = False
        self.viewport.set_collision(False)
        self.control_hub.clear_collision()

    def sim_seek(self, fraction: float) -> None:
        if self._player:
            self._player.seek(fraction)

    def seek_to_point(self, point: np.ndarray) -> None:
        """Seek playback to the path position closest to *point* (world
        mm) — used to jump the sim view to a collision location for
        inspection after the user cancels a pre-flight-blocked Start."""
        if self._player is None:
            return
        path = self._player._path
        if path.total_length < 1e-9:
            return
        s, _line = path.find_nearest(np.asarray(point, dtype="f4"))
        self._player.seek(s / path.total_length)

    def sim_set_speed(self, speed: float) -> None:
        if self._player:
            self._player.speed_scale = speed

    def presim_to_s(
        self,
        target_s: float,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Fast-forward the voxel grid to *target_s* in a background thread.

        Intended for "start program from line X" — pre-carves all material
        that would have been removed before that line, so the stock state is
        correct when the program starts.

        Usage::

            s = path.arc[np.searchsorted(path.line_ids, start_line)]
            widget.presim_to_s(s, on_done=lambda: start_machine())

        The *on_done* callback is invoked on the main thread via
        ``QTimer.singleShot(0, ...)`` when the background thread completes.
        """
        from PySide6.QtCore import QTimer

        if self._voxel_ctrl is None:
            if on_done:
                on_done()
            return

        # Abort any running carve first
        self._carve_abort.set()
        if self._carve_thread is not None and self._carve_thread.is_alive():
            self._carve_thread.join(timeout=0.5)
        self._carve_abort.clear()
        self._carve_done.clear()

        ctrl = self._voxel_ctrl

        def _worker() -> None:
            # Single call — fastest path, no chunking needed for a one-shot
            # fast-forward.  The UI shows a "preparing" state during this.
            ctrl.on_tick(target_s)
            self._carve_done.set()
            if on_done and not self._carve_abort.is_set():
                QTimer.singleShot(0, on_done)

        self._carve_thread = threading.Thread(
            target=_worker, daemon=True, name="presim"
        )
        self._carve_thread.start()

    def presim_check_collisions(
        self,
        on_done: Callable[["CollisionHit | None"], None],
    ) -> None:
        """Background, whole-program collision pre-flight check ("Vorab-
        Check") — run before a real MACHINE-mode Start, not tied to
        playback speed or the live voxel state.

        Replays the ENTIRE loaded program from a fresh, fully-solid
        scratch copy of the stock (never the live grid — running this must
        not touch what's currently on screen), applying T-command tool
        changes and checking every segment with collision.check_segment()
        exactly like VoxelSimController.on_tick() does live. Stops at the
        first hit. Cutting moves are also carved into the scratch array
        (via a disposable, GPU-free VoxelCarver target) so later segments
        see material already removed by earlier ones, same as real
        playback would.

        on_done(hit_or_None) is invoked on the main/GUI thread once the
        background thread finishes, via a Qt signal (_presim_collision_done)
        rather than QTimer.singleShot() called from the worker thread —
        singleShot's timer would belong to the worker thread's (nonexistent)
        event loop and never fire; a signal's queued cross-thread delivery
        to a slot living on the GUI thread works regardless. Calls on_done
        with None immediately, synchronously, no thread spun up, if there's
        no program loaded or collision detection is disabled.
        """
        s = AppSettings.instance()
        if (
            not _VOXEL_AVAILABLE
            or self._last_program is None
            or not self._effective_collision_enabled()
        ):
            on_done(None)
            return

        program = self._last_program
        path    = program.path
        if len(path.points) < 2:
            on_done(None)
            return

        try:
            shape = StockShape(s.stock_shape)
        except ValueError:
            shape = StockShape.BOUNDING_BOX
        stock = StockDefinition(
            shape=shape, voxel_size=s.voxel_size, z_offset_mm=s.stock_z_offset_mm,
            height_mm=s.stock_height_mm, round_radius_mm=s.stock_round_radius_mm,
            width_mm=s.stock_width_mm, depth_mm=s.stock_depth_mm,
            x_offset_mm=s.stock_x_offset_mm, y_offset_mm=s.stock_y_offset_mm,
        )
        stock.build_bbox(path)
        material = solid_material(stock)
        bbox, voxel_size = stock.bbox, s.voxel_size

        pts, feeds, line_ids = path.points, path.feed_rates, path.line_ids
        tool_changes = program.tool_changes
        initial_tool = (
            get_tool(tool_changes[0].tool_number) if tool_changes else get_tool(1)
        )
        db = ToolDatabase.instance()

        class _CpuMaterialSink:
            """Minimal duck-typed carve target — implements just what
            VoxelCarver.carve_segment() actually calls on its grid
            (.voxel_size/.bbox/.shape/.carve()), writing straight into the
            scratch numpy array. No GPU, no GpuVoxelGrid instance, so this
            check never needs a GL context or touches the live grid."""

            def __init__(self, mat: np.ndarray) -> None:
                self._mat = mat
                nz, ny, nx = mat.shape
                self.shape = (nx, ny, nz)
                self.bbox = bbox
                self.voxel_size = voxel_size

            def carve(self, ix0, ix1, iy0, iy1, iz0, iz1, mask) -> None:
                self._mat[iz0:iz1, iy0:iy1, ix0:ix1][mask] = 0

        def _worker() -> None:
            carver = VoxelCarver(_CpuMaterialSink(material))
            current_tool = initial_tool
            current_holder = (
                db.get_holder(current_tool.holder_preset) if current_tool else None
            )
            tc_idx = 0
            hit: "CollisionHit | None" = None

            for i in range(len(pts) - 1):
                while (
                    tc_idx < len(tool_changes)
                    and tool_changes[tc_idx].line_index <= line_ids[i]
                ):
                    current_tool = get_tool(tool_changes[tc_idx].tool_number) or current_tool
                    current_holder = (
                        db.get_holder(current_tool.holder_preset) if current_tool else None
                    )
                    tc_idx += 1
                if current_tool is None:
                    continue

                is_rapid = feeds[i + 1] == 0.0
                hit = check_segment(
                    material, bbox, voxel_size, pts[i], pts[i + 1],
                    current_tool, is_rapid, current_holder,
                )
                if hit is not None:
                    hit.line_number = int(line_ids[i + 1])
                    break
                if not is_rapid:
                    carver.carve_segment(pts[i], pts[i + 1], current_tool)

            self._presim_collision_done.emit(hit)

        def _deliver(hit) -> None:
            self._presim_collision_done.disconnect(_deliver)
            on_done(hit)

        self._presim_collision_done.connect(_deliver)
        threading.Thread(
            target=_worker, daemon=True, name="presim-collision-check",
        ).start()

    # ── ControlHub wiring ─────────────────────────────────────────────────────

    def _connect_control_hub(self) -> None:
        self.control_hub.play_clicked.connect(self.sim_play)
        self.control_hub.pause_clicked.connect(self.sim_pause)
        self.control_hub.stop_clicked.connect(self.sim_reset)
        self.control_hub.speed_changed.connect(self.sim_set_speed)
        self.control_hub.skip_forward_clicked.connect(
            lambda: self.sim_seek(min(self._player.progress() + 0.05, 1.0))
            if self._player else None
        )
        self.control_hub.skip_backward_clicked.connect(
            lambda: self.sim_seek(max(self._player.progress() - 0.05, 0.0))
            if self._player else None
        )

    # ── Render tick ───────────────────────────────────────────────────────────

    def _tick(self) -> None:
        """Called ~30 fps.  Advances the player and drives voxel carving."""
        if self._mode == "SIM":
            if self._player is None:
                self.viewport.update()
                return

            pos, line, s = self._player.tick()

            if self._player.is_finished or self._player.is_at_start:
                self._on_sim_finished()

            self._check_tool_change(line)

            self.viewport.set_tool_position(pos)
            self.viewport.set_active_line(line)
            self.viewport.set_progress(s)

            if self._clean_lines and 0 <= line < len(self._clean_lines):
                self.control_hub.set_gcode(f"({line}) {self._clean_lines[line]}")

            feed = self._player._path.feed_at(s)
            self.control_hub.set_feedrate(feed)
            self.control_hub.set_datum(getattr(self.viewport, "_active_wcs", 1))
            # (tool pill is now pushed directly from _apply_tool(), not
            # refreshed here — see its docstring)

            # Voxel carving: small backlogs are carved synchronously right
            # here so the tool marker and the carved material are always for
            # the same `s`; only large backlogs (seeks/skips) go through the
            # background worker so the UI thread never stalls. See
            # _drive_voxel_carve()'s docstring.
            if self._voxel_ctrl is not None:
                ctrl = self._voxel_ctrl   # capture; teardown may null self._voxel_ctrl
                self._drive_voxel_carve(ctrl, s)

        elif self._mode == "MACHINE" and self._voxel_ctrl is not None:
            # MACHINE mode: carve from real machine positions delivered via
            # set_position().  Uses the same _carve_done / _carve_abort events
            # so _teardown_voxel_sim() correctly aborts both worker types.
            if self._carve_done.is_set():
                self._carve_done.clear()
                self.viewport.makeCurrent()
                self._voxel_ctrl.grid.upload_if_dirty()
                self.viewport.doneCurrent()

            if (
                self._pending_machine_carve is not None
                and (self._carve_thread is None or not self._carve_thread.is_alive())
            ):
                start, end = self._pending_machine_carve
                self._pending_machine_carve = None
                tool = self._current_tool
                if tool is not None:
                    ctrl = self._voxel_ctrl
                    self._carve_thread = threading.Thread(
                        target=self._machine_carve_worker,
                        args=(ctrl, start, end, tool),
                        daemon=True,
                        name="machine-carver",
                    )
                    self._carve_thread.start()

        self._handle_collision_state()
        self.viewport.update()

    def _handle_collision_state(self) -> None:
        """Non-blocking collision feedback, called unconditionally every
        tick — NEVER pauses or stops the simulation (see collision.py /
        VoxelSimController's docstrings: a hit is diagnostic here, not a
        safety interlock).

        Two independent things happen:
        - Every NEW hit (ctrl.drain_new_hits()) is logged exactly once,
          with its G-code line number and source text, and still emits the
          public collision_detected signal — MachinePage's live-machine
          handler reacts to that only while an actual MACHINE-mode program
          is RUNNING, so this firing repeatedly during plain SIM playback
          is harmless (see its docstring; unchanged by this rewrite).
        - The viewport tool tint / control_hub pill are synced to whether
          the tool is IN a collision RIGHT NOW (ctrl.collision_hit, live —
          clears itself the moment a clean segment follows), so the red
          highlight lasts exactly as long as the collision itself.
        """
        ctrl = self._voxel_ctrl
        if ctrl is None:
            return

        for hit in ctrl.drain_new_hits():
            self._log_collision(hit)
            self.collision_detected.emit(hit)

        active = ctrl.collision_hit is not None
        if active == self._collision_active:
            return
        self._collision_active = active
        if active:
            hit = ctrl.collision_hit
            self.viewport.set_collision(True, hit.point)
            self.control_hub.set_collision(hit)
        else:
            self.viewport.set_collision(False)
            self.control_hub.clear_collision()

    def _log_collision(self, hit: "CollisionHit") -> None:
        line_text = ""
        if 0 <= hit.line_number < len(self._raw_lines):
            line_text = self._raw_lines[hit.line_number].strip()
        logger.warning(
            "Kollision erkannt: Zeile %d (%s): %s",
            hit.line_number, hit.kind, line_text or "?",
        )

    def _drive_voxel_carve(self, ctrl: "VoxelSimController", s: float) -> None:
        """Advance voxel carving to *s*, keeping the displayed tool position
        and the carved material provably in sync for normal playback.

        Small backlogs (`s - ctrl.max_s`) are carved synchronously, in this
        call, on the calling (Qt main/GL) thread — so the grid upload below
        always reflects the exact same `s` the tool marker was just set to
        this frame. Large backlogs (a seek/skip jump, or any unexpectedly
        heavy stretch of path) fall back to the existing chunked background
        worker so the UI thread is never blocked for long. The two paths are
        mutually exclusive by construction: a new synchronous or background
        pass is only ever started once any previous background worker has
        fully finished (`is_alive()` is False only after its target callable
        has returned), so on_tick()/carve_segment() are never called from two
        threads at once against the same controller/grid.
        """
        # Flush anything a still/previously-running background worker produced.
        if self._carve_done.is_set():
            self._carve_done.clear()
            self.viewport.makeCurrent()
            ctrl.grid.upload_if_dirty()
            self.viewport.doneCurrent()

        if self._carve_thread is not None and self._carve_thread.is_alive():
            return  # background catch-up still running; re-check next tick

        backlog = s - ctrl.max_s
        if backlog <= 0.0:
            return  # rewinding, or already caught up

        if backlog <= _SYNC_CARVE_MAX_MM:
            if ctrl.on_tick(s):
                self.viewport.makeCurrent()
                ctrl.grid.upload_if_dirty()
                self.viewport.doneCurrent()
        else:
            self._carve_thread = threading.Thread(
                target=self._carve_worker,
                args=(ctrl, s),
                daemon=True,
                name="voxel-carver",
            )
            self._carve_thread.start()

    def _force_carve_catchup(self) -> None:
        """Guarantee the voxel state is fully caught up to the player's
        current position right now — call whenever the tool visibly stops
        (paused, or playback naturally finished/reversed to start).

        If a background worker is still chewing through a large backlog at
        that exact moment, wait for it (bounded) rather than leaving it to
        trickle across future frames, then run one final synchronous
        on_tick() pass for whatever (if anything) remains.
        """
        if self._voxel_ctrl is None or self._player is None:
            return
        ctrl = self._voxel_ctrl

        if self._carve_thread is not None and self._carve_thread.is_alive():
            # Bounded: background chunks are 5mm each (_carve_worker's
            # CHUNK_MM), so this returns well under the timeout in all but
            # truly pathological cases.
            self._carve_thread.join(timeout=2.0)

        if self._carve_done.is_set():
            self._carve_done.clear()
            self.viewport.makeCurrent()
            ctrl.grid.upload_if_dirty()
            self.viewport.doneCurrent()

        # Only safe to carve synchronously if no thread is still alive (the
        # join above could in principle time out on a pathological backlog).
        if self._carve_thread is None or not self._carve_thread.is_alive():
            if ctrl.on_tick(self._player.current_s()):
                self.viewport.makeCurrent()
                ctrl.grid.upload_if_dirty()
                self.viewport.doneCurrent()

    def _carve_worker(self, ctrl: "VoxelSimController", s_target: float) -> None:
        """Background thread: advance HWM carving to *s_target* in 5 mm chunks.

        Chunking lets the main thread upload and display partial results while
        the worker is still running, eliminating the visual lag between tool
        position and carved material at high playback speeds (5×/10×).
        """
        CHUNK_MM = 5.0
        s = ctrl.max_s
        while s < s_target and not self._carve_abort.is_set():
            s_next = min(s + CHUNK_MM, s_target)
            carved = ctrl.on_tick(s_next)
            if carved and not self._carve_abort.is_set():
                self._carve_done.set()
            s = s_next

    def _machine_carve_worker(
        self,
        ctrl:  "VoxelSimController",
        start: "np.ndarray",
        end:   "np.ndarray",
        tool:  "ToolDefinition",
    ) -> None:
        """Background thread: carve one real-machine move segment.

        Called in MACHINE mode with consecutive position pairs delivered by
        the machine controller.  No arc-length concept — uses carve_move()
        directly.
        """
        if self._carve_abort.is_set():
            return
        # feed_rate=1.0 means "it is a cutting move" — G0 rapid filtering will
        # be added when MachineController exposes the current feed rate.
        # Same limitation applies to collision checking here: every MACHINE-
        # mode move is checked with is_rapid=False (shank/holder-only), so a
        # real rapid that plows into material isn't caught by this path —
        # only by the pre-flight whole-program check before Start, which
        # tessellates from the loaded file and knows real G0/G1 moves apart.
        carved = ctrl.carve_move(
            start, end, tool, feed_rate=1.0, line_number=self._current_line,
        )
        if carved and not self._carve_abort.is_set():
            self._carve_done.set()

    def _on_sim_finished(self) -> None:
        self.control_hub.reset_play_state()
        self._force_carve_catchup()

    # ── Overlay layout ────────────────────────────────────────────────────────

    def _layout_overlays(self) -> None:
        W, H = self.width(), self.height()
        self.viewport.setGeometry(0, 0, W, H)

        sw = self.settings.width()
        self.settings.setGeometry(W - sw, 0, sw, H)

        cw, ch = self.control_hub.width(), self.control_hub.height()
        self.control_hub.setGeometry((W - cw) // 2, H - ch - 16, cw, ch)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._layout_overlays()
