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

import threading

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from controller.sim.ui.viewport        import Viewport, ToolMode, PathMode
from controller.sim.ui.overlay.settings_panel import SettingsPanel
from controller.sim.ui.overlay.control_hub    import ControlHub
from controller.sim.gcode.compiler            import GCodeCompiler
from controller.sim.simulation.player         import SimulationPlayer
from controller.sim.simulation.tool_database  import get_tool
from controller.sim.simulation.tool_definition import ToolDefinition
from controller.sim.core.settings             import AppSettings

# Voxel sim — pure Python/numpy + GLSL raymarching (no C++ required)
try:
    from controller.sim.voxel.stock      import StockDefinition, StockShape
    from controller.sim.voxel.gpu_grid   import GpuVoxelGrid
    from controller.sim.voxel.carver     import VoxelCarver
    from controller.sim.voxel.controller import VoxelSimController
    from controller.sim.voxel.renderer   import VoxelRenderer
    _VOXEL_AVAILABLE = True
except ImportError:
    _VOXEL_AVAILABLE = False


class DatumSimWidget(QWidget):
    """Top-level 3D simulation widget — drop-in replacement for SimPlaceholder."""

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
        self._tool_changes:         list                    = []
        self._last_tool_change_idx: int                     = -1
        self._path_mode:            PathMode                = PathMode.FULL
        self._tool_mode:            ToolMode                = ToolMode.POINT
        self._current_tool:         ToolDefinition | None   = None
        self._last_program                                  = None

        # Voxel objects (all None until _create_voxel_sim() runs after initializeGL)
        self._voxel_ctrl:     VoxelSimController | None = None
        self._voxel_renderer: VoxelRenderer | None      = None
        self._pending_voxel_setup: bool = False

        # Carving runs in a background thread so the main-thread controller UI
        # is never blocked by long numpy operations (deep plunges etc.).
        self._carve_thread: threading.Thread | None = None
        self._carve_done   = threading.Event()   # set by worker when grid is dirty
        self._carve_abort  = threading.Event()   # set to cancel in-flight carving

        # ── Signal connections ────────────────────────────────────────────────
        self.settings.sim_panel.tool_mode_changed.connect(self.set_tool_mode)
        self.settings.sim_panel.path_mode_changed.connect(self.set_path_mode)

        s = AppSettings.instance()
        s.voxel_size_changed.connect(self._on_voxel_size_changed)
        s.voxel_enabled_changed.connect(self._on_voxel_enabled_changed)
        # Rebuild the sim whenever stock geometry settings change
        s.stock_shape_changed.connect(self._on_stock_settings_changed)
        s.stock_z_offset_changed.connect(self._on_stock_settings_changed)
        s.stock_height_changed.connect(self._on_stock_settings_changed)
        s.stock_round_radius_changed.connect(self._on_stock_settings_changed)

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

        program = self.compiler.load_file(path)
        self._clean_lines          = program.clean_lines
        self._player               = SimulationPlayer(program.path)
        self._tool_changes         = program.tool_changes
        self._last_tool_change_idx = -1
        self._last_program         = program   # kept for voxel re-init

        self.viewport.set_path(program.path)

        s = AppSettings.instance()
        self.set_tool_mode(self._TOOL_MAP.get(s.tool_mode, ToolMode.CYLINDER))
        self.set_path_mode(self._PATH_MAP.get(s.path_mode, PathMode.PROGRESSIVE))

        first_tool = (
            get_tool(program.tool_changes[0].tool_number)
            if program.tool_changes else get_tool(1)
        )
        self._apply_tool(first_tool)

        if _VOXEL_AVAILABLE and s.voxel_enabled:
            self._schedule_voxel_sim()

        self.settings.sim_panel.set_sim_running(True)

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
            )
            stock.build_bbox(self._last_program.path)

            grid  = GpuVoxelGrid(self.viewport.ctx, stock)
            carver = VoxelCarver(grid)

            self._voxel_ctrl = VoxelSimController(
                grid             = grid,
                carver           = carver,
                path_points      = self._last_program.path.points,
                path_arc_lengths = self._last_program.path.arc_lengths,
                path_feed_rates  = self._last_program.path.feed_rates,
                tool             = self._current_tool or get_tool(1),
            )

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
        self.settings.sim_panel.set_current_tool(tool.tool_number)
        if self._voxel_ctrl is not None:
            self._voxel_ctrl.update_tool(tool)

    def _check_tool_change(self, current_line: int) -> None:
        for tc in self._tool_changes:
            if tc.line_index <= current_line and tc.line_index > self._last_tool_change_idx:
                self._last_tool_change_idx = tc.line_index
                self._apply_tool(get_tool(tc.tool_number))

    # ── Machine interface (SimPlaceholder-compatible API) ─────────────────────

    def set_state(self, state: str) -> None:
        self._state = state

    def set_position(self, x: float, y: float, z: float) -> None:
        if self._mode == "MACHINE":
            self.viewport.set_tool_position(np.array([x, y, z], dtype="f4"))

    def set_line(self, line: int) -> None:
        self.viewport.set_active_line(line)

    # ── Mode ──────────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        assert mode in ("SIM", "MACHINE"), f"Unknown mode: {mode!r}"
        self._mode = mode
        self.control_hub.set_mode(mode)
        if self._player:
            self._player.reset()

    def set_path_mode(self, mode: PathMode) -> None:
        self._path_mode = mode
        self.viewport.set_path_mode(mode)
        self.settings.sim_panel.set_path_mode(mode)

    def set_tool_mode(self, mode: ToolMode) -> None:
        self._tool_mode = mode
        self.viewport.set_tool_mode(mode)
        self.settings.sim_panel.set_tool_mode(mode)

    # ── Playback controls ─────────────────────────────────────────────────────

    def sim_play(self) -> None:
        if self._player:
            self._player.play()

    def sim_pause(self) -> None:
        if self._player:
            self._player.pause()

    def sim_reset(self) -> None:
        # Stop any in-flight carving thread first to avoid writing to the grid
        # while we reset it.
        self._carve_abort.set()
        if self._carve_thread is not None and self._carve_thread.is_alive():
            self._carve_thread.join(timeout=0.3)
        self._carve_abort.clear()
        self._carve_done.clear()
        self._carve_thread = None

        if self._player:
            self._player.reset()
            self._last_tool_change_idx = -1
            self.control_hub.reset_play_state()
        if self._voxel_ctrl is not None:
            self._voxel_ctrl.reset()
            # Upload the reset (fully solid) grid immediately
            self.viewport.makeCurrent()
            self._voxel_ctrl.grid.upload_if_dirty()
            self.viewport.doneCurrent()

    def sim_seek(self, fraction: float) -> None:
        if self._player:
            self._player.seek(fraction)

    def sim_set_speed(self, speed: float) -> None:
        if self._player:
            self._player.speed_scale = speed

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
            if self._current_tool:
                self.control_hub.set_tool(self._current_tool.tool_number)

            # Voxel carving runs in a background thread so the main-thread
            # controller UI is never blocked by long numpy operations.
            if self._voxel_ctrl is not None:
                # If the worker flagged new carved data, upload to GPU now.
                if self._carve_done.is_set():
                    self._carve_done.clear()
                    self.viewport.makeCurrent()
                    self._voxel_ctrl.grid.upload_if_dirty()
                    self.viewport.doneCurrent()

                # Launch a new worker frame if the previous one is done.
                if self._carve_thread is None or not self._carve_thread.is_alive():
                    ctrl = self._voxel_ctrl   # capture; teardown may null self._voxel_ctrl
                    self._carve_thread = threading.Thread(
                        target=self._carve_worker,
                        args=(ctrl, s),
                        daemon=True,
                        name="voxel-carver",
                    )
                    self._carve_thread.start()

        self.viewport.update()

    def _carve_worker(self, ctrl, s: float) -> None:
        """Background thread: advance HWM carving up to arc-length *s*.

        Runs entirely off the main thread — no Qt or GL calls allowed here.
        Sets ``_carve_done`` so ``_tick()`` knows to upload the dirty region
        to the GPU on the next main-thread frame.
        """
        if self._carve_abort.is_set():
            return
        carved = ctrl.on_tick(s)
        if carved and not self._carve_abort.is_set():
            self._carve_done.set()

    def _on_sim_finished(self) -> None:
        self.control_hub.reset_play_state()

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
