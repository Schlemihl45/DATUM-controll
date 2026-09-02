"""
sim/ui/main_widget.py — DatumSimWidget: 3D G-code viewer + sim player.

Composes:
  • Viewport  — ModernGL OpenGL widget (G-code path + tool)
  • SettingsPanel  — right-edge slide-out with functional sim settings
  • ControlHub — bottom-center playback bar + info labels

All three are positioned as overlays on top of the viewport via
_layout_overlays(), which is called from resizeEvent and open/close panel
actions so the geometry always stays correct.

Public interface (mirrors SimPlaceholder so machine_page.py can swap freely):
    set_file(path)          — load and compile a G-code file
    set_mode("SIM"|"MACHINE") — sim player vs. live machine follow
    set_state(state_str)    — string state label (passed through)
    set_position(x, y, z)  — push live machine position (MACHINE mode)
    set_line(line)          — highlight a G-code line
    sim_play/pause/reset/seek/set_speed — simulation playback controls
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from controller.sim.ui.viewport import Viewport, ToolMode, PathMode
from controller.sim.ui.overlay.settings_panel import SettingsPanel
from controller.sim.ui.overlay.control_hub import ControlHub
from controller.sim.gcode.compiler import GCodeCompiler
from controller.sim.simulation.player import SimulationPlayer
from controller.sim.simulation.tool_database import get_tool
from controller.sim.simulation.tool_definition import ToolDefinition
from controller.sim.core.settings import AppSettings

# Voxel controller and renderer — imported lazily (depend on C++ extension)
try:
    from controller.sim.voxel.controller import VoxelSimController
    from controller.sim.voxel.renderer import VoxelRenderer
    from controller.sim.voxel.stock import StockDefinition, BoundingBox
    _VOXEL_AVAILABLE = True
except ImportError:
    _VOXEL_AVAILABLE = False


class DatumSimWidget(QWidget):
    """Top-level 3D simulation widget — drop-in replacement for SimPlaceholder.

    After construction the widget is ready to use immediately. Load a G-code
    file with set_file() and the simulation player starts in paused state.
    """

    # String → enum maps for mode/path/tool strings coming from outside
    _TOOL_MAP = {
        "Endmill": ToolMode.CYLINDER,
        "Point":   ToolMode.POINT,
        "None":    ToolMode.NONE,
    }
    _PATH_MAP = {
        "Complete":    PathMode.FULL,
        "Progressive": PathMode.PROGRESSIVE,
        "None":        PathMode.NONE,
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Sub-widgets
        self.viewport    = Viewport(self)
        self.settings    = SettingsPanel(self)
        self.control_hub = ControlHub(self)
        self.compiler    = GCodeCompiler()

        # Internal state
        self._player:               SimulationPlayer | None = None
        self._state:                str                     = "IDLE"
        self._mode:                 str                     = "SIM"
        self._clean_lines:          list[str]               = []
        self._tool_changes:         list                    = []
        self._last_tool_change_idx: int                     = -1
        self._path_mode:            PathMode                = PathMode.FULL
        self._tool_mode:            ToolMode                = ToolMode.POINT

        # Voxel sim controller + renderer (None until set_file() + initializeGL)
        self._voxel_ctrl:     "VoxelSimController | None" = None
        self._voxel_renderer: "VoxelRenderer | None"      = None

        # Wire sim-panel → viewport
        self.settings.sim_panel.tool_mode_changed.connect(self.set_tool_mode)
        self.settings.sim_panel.path_mode_changed.connect(self.set_path_mode)

        # Voxel setting changes from sim panel → setup/teardown
        s = AppSettings.instance()
        s.voxel_size_changed.connect(self._on_voxel_size_changed)
        s.voxel_enabled_changed.connect(self._on_voxel_enabled_changed)

        # Render tick (~30 fps)
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(32)
        self._render_timer.timeout.connect(self._tick)
        self._render_timer.start()

        self._connect_control_hub()
        self._layout_overlays()

    # ── File loading ──────────────────────────────────────────────────────────

    def set_file(self, path: str) -> None:
        """Compile and load a G-code file, set up the simulation player."""
        # Tear down any previous voxel controller cleanly
        if self._voxel_ctrl is not None:
            self._voxel_ctrl.stop()
            self._voxel_ctrl = None

        program = self.compiler.load_file(path)
        self._clean_lines          = program.clean_lines
        self._player               = SimulationPlayer(program.path)
        self._tool_changes         = program.tool_changes
        self._last_tool_change_idx = -1
        self._last_program         = program   # kept for voxel re-init on toggle/resize

        self.viewport.set_path(program.path)

        # Apply saved display modes (order matters: mode before tool)
        s = AppSettings.instance()
        self.set_tool_mode(self._TOOL_MAP.get(s.tool_mode, ToolMode.CYLINDER))
        self.set_path_mode(self._PATH_MAP.get(s.path_mode, PathMode.PROGRESSIVE))

        # Tool at simulation start comes from the first T-command, or T1
        first_tool = (
            get_tool(program.tool_changes[0].tool_number)
            if program.tool_changes else get_tool(1)
        )
        self._apply_tool(first_tool)

        # Initialise voxel simulation (only when extension installed AND enabled)
        if _VOXEL_AVAILABLE and s.voxel_enabled:
            self._setup_voxel_sim(program, first_tool, s.voxel_size)

        # Tell the sim panel whether a sim is now running (for voxel size guard)
        self.settings.sim_panel.set_sim_running(True)

    # ── Voxel simulation setup ────────────────────────────────────────────────

    def _setup_voxel_sim(self, program, first_tool, voxel_size: float) -> None:
        """Create VoxelSimController and VoxelRenderer for the loaded program."""
        # Build stock bounding box from path + padding
        bbox = BoundingBox.from_path_buffer(program.path, margin_mm=5.0)
        stock = StockDefinition(bbox=bbox, voxel_size=voxel_size)

        self._voxel_ctrl = VoxelSimController(
            stock            = stock,
            path_points      = program.path.points,
            path_arc_lengths = program.path.arc_lengths,
            tool             = first_tool,
        )

        # Create VoxelRenderer lazily — Viewport's GL context must be ready.
        # We attach it after the first paintGL via a one-shot connection.
        if not hasattr(self.viewport, 'ctx'):
            # GL not ready yet — defer until after initializeGL fires
            self.viewport.installEventFilter(self)
            self._pending_voxel_renderer = True
        else:
            self._create_voxel_renderer()

    def _create_voxel_renderer(self) -> None:
        """Create and attach VoxelRenderer once the GL context is available."""
        if not _VOXEL_AVAILABLE:
            return
        self.viewport.makeCurrent()
        self._voxel_renderer = VoxelRenderer(self.viewport.ctx)
        self.viewport.doneCurrent()
        self.viewport.set_voxel_renderer(self._voxel_renderer)

    def eventFilter(self, obj, event) -> bool:
        """One-shot event filter: create VoxelRenderer after GL is initialised."""
        from PySide6.QtCore import QEvent
        if (obj is self.viewport
                and event.type() == QEvent.Type.Paint
                and getattr(self, '_pending_voxel_renderer', False)):
            self._pending_voxel_renderer = False
            self.viewport.removeEventFilter(self)
            self._create_voxel_renderer()
        return super().eventFilter(obj, event)

    def _teardown_voxel_sim(self) -> None:
        """Stop the voxel worker and clear the mesh from the viewport."""
        if self._voxel_ctrl is not None:
            self._voxel_ctrl.stop()
            self._voxel_ctrl = None
        # Tell the viewport to show nothing (pass empty arrays)
        if self._voxel_renderer is not None:
            import numpy as np
            self.viewport.upload_voxel_mesh(
                np.empty((0, 3), dtype='f4'),
                np.empty((0, 3), dtype='f4'),
                np.empty((0,),   dtype='u4'),
            )

    def _on_voxel_size_changed(self, voxel_size: float) -> None:
        """Voxel size changed in settings → reset the grid (no live resize)."""
        self._teardown_voxel_sim()
        # Re-create with new voxel size if a program is loaded
        if self._player is not None and hasattr(self, '_last_program'):
            self._setup_voxel_sim(self._last_program, self._current_tool, voxel_size)

    def _on_voxel_enabled_changed(self, enabled: bool) -> None:
        """Sim-panel checkbox toggled → start or stop voxel simulation."""
        if not _VOXEL_AVAILABLE:
            return
        if enabled:
            # Start sim if a program is already loaded
            if self._player is not None and hasattr(self, '_last_program'):
                s = AppSettings.instance()
                self._setup_voxel_sim(self._last_program, self._current_tool, s.voxel_size)
        else:
            self._teardown_voxel_sim()

    # ── Tool management (T-command driven) ───────────────────────────────────

    def _apply_tool(self, tool: ToolDefinition | None) -> None:
        """Push a tool definition to the viewport (no UI selector involved)."""
        if tool is None:
            return
        self._current_tool = tool
        self.viewport.set_tool_definition(tool)
        # Notify the sim panel's display (read-only label, no combo selection)
        self.settings.sim_panel.set_current_tool(tool.tool_number)
        # Update the voxel controller's tool profile for subsequent segments
        if self._voxel_ctrl is not None:
            self._voxel_ctrl.update_tool(tool)

    def _check_tool_change(self, current_line: int) -> None:
        """Auto-swap tool when the simulation passes a T-command line."""
        for tc in self._tool_changes:
            if tc.line_index <= current_line and tc.line_index > self._last_tool_change_idx:
                self._last_tool_change_idx = tc.line_index
                self._apply_tool(get_tool(tc.tool_number))

    # ── Machine interface (SimPlaceholder-compatible API) ─────────────────────

    def set_state(self, state: str) -> None:
        """Update the machine state label (passed through to display)."""
        self._state = state

    def set_position(self, x: float, y: float, z: float) -> None:
        """Push live machine position — only used in MACHINE mode."""
        if self._mode == "MACHINE":
            self.viewport.set_tool_position(np.array([x, y, z], dtype='f4'))

    def set_line(self, line: int) -> None:
        """Highlight a G-code line (works in both SIM and MACHINE mode)."""
        self.viewport.set_active_line(line)

    # ── Mode ──────────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        """Switch between SIM (player-driven) and MACHINE (controller-driven)."""
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

    # ── Simulation playback controls ─────────────────────────────────────────

    def sim_play(self) -> None:
        if self._player:
            self._player.play()

    def sim_pause(self) -> None:
        if self._player:
            self._player.pause()

    def sim_reset(self) -> None:
        if self._player:
            self._player.reset()
            self._last_tool_change_idx = -1
            self.control_hub.reset_play_state()
        # Reset the voxel grid to the initial blank state (rebuilds via worker)
        if self._voxel_ctrl is not None:
            self._voxel_ctrl.reset()

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
        """Called ~30 fps. Advances the simulation player and pushes state."""
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

            # Info bar updates
            if self._clean_lines and 0 <= line < len(self._clean_lines):
                self.control_hub.set_gcode(f"({line}) {self._clean_lines[line]}")

            feed = self._player._path.feed_at(s)
            self.control_hub.set_feedrate(feed)
            self.control_hub.set_datum(getattr(self.viewport, '_active_wcs', 1))
            if hasattr(self, '_current_tool') and self._current_tool:
                self.control_hub.set_tool(self._current_tool.tool_number)

            # Feed arc-length to voxel controller (High-Water-Mark logic inside)
            if self._voxel_ctrl is not None:
                self._voxel_ctrl.on_tick(s)

        # Check whether the worker thread produced a new mesh
        if self._voxel_ctrl is not None:
            mesh = self._voxel_ctrl.get_mesh_if_dirty()
            if mesh is not None:
                verts, normals, indices = mesh
                self.viewport.upload_voxel_mesh(verts, normals, indices)

        self.viewport.update()

    def _on_sim_finished(self) -> None:
        """Called when the simulation player reaches the end of the program."""
        self.control_hub.reset_play_state()

    # ── Overlay layout ────────────────────────────────────────────────────────

    def _layout_overlays(self) -> None:
        """Position all child overlays to fill/anchor within the widget."""
        W, H = self.width(), self.height()

        # Viewport fills the entire widget
        self.viewport.setGeometry(0, 0, W, H)

        # Settings panel anchored to the right edge
        sw = self.settings.width()
        self.settings.setGeometry(W - sw, 0, sw, H)

        # Control hub centered at the bottom
        cw, ch = self.control_hub.width(), self.control_hub.height()
        self.control_hub.setGeometry((W - cw) // 2, H - ch - 16, cw, ch)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._layout_overlays()
