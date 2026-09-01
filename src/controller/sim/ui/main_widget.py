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

        # Wire sim-panel → viewport
        self.settings.sim_panel.tool_mode_changed.connect(self.set_tool_mode)
        self.settings.sim_panel.path_mode_changed.connect(self.set_path_mode)

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
        program = self.compiler.load_file(path)
        self._clean_lines          = program.clean_lines
        self._player               = SimulationPlayer(program.path)
        self._tool_changes         = program.tool_changes
        self._last_tool_change_idx = -1

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

    # ── Tool management (T-command driven) ───────────────────────────────────

    def _apply_tool(self, tool: ToolDefinition | None) -> None:
        """Push a tool definition to the viewport (no UI selector involved)."""
        if tool is None:
            return
        self._current_tool = tool
        self.viewport.set_tool_definition(tool)
        # Notify the sim panel's display (read-only label, no combo selection)
        self.settings.sim_panel.set_current_tool(tool.tool_number)

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
