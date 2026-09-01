"""
sim/ui/overlay/panels/sim_panel.py — Functional simulation settings panel.

Shows only the settings that control simulation behavior:
  • Tool Display mode  — Endmill / Point / None
  • Path mode          — Complete / Progressive / None

The tool-database selector was intentionally removed: tool changes happen
exclusively via T-commands in the G-code program, never through the UI.
Camera/viewport settings (background color, speeds, etc.) were moved to
the application-level SettingsPage so they stay persistent across sessions
and are consistent with the rest of the app's settings structure.
"""
from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from controller.sim.core.settings import AppSettings
from controller.sim.ui.viewport import PathMode, ToolMode


class SimPanel(QWidget):
    """Slide-out panel for simulation display settings (no tool/camera controls)."""

    # Emitted when the user changes the tool render mode
    tool_mode_changed = Signal(object)   # ToolMode
    # Emitted when the user changes path display mode
    path_mode_changed = Signal(object)   # PathMode

    # --- ordered lists drive the combo indices -----------
    _TOOL_MODES  = [ToolMode.CYLINDER, ToolMode.POINT, ToolMode.NONE]
    _PATH_MODES  = [PathMode.FULL, PathMode.PROGRESSIVE, PathMode.NONE]

    _TOOL_LABELS = {
        ToolMode.CYLINDER: "Endmill",
        ToolMode.POINT:    "Point",
        ToolMode.NONE:     "None",
    }
    _PATH_LABELS = {
        PathMode.FULL:        "Complete",
        PathMode.PROGRESSIVE: "Progressive",
        PathMode.NONE:        "None",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._s = AppSettings.instance()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        # ── Tool display mode ────────────────────────────────────────────────
        root.addWidget(QLabel("Tool Display"))
        tool_form = QFormLayout()
        tool_form.setContentsMargins(0, 0, 0, 0)
        tool_form.setSpacing(8)

        self._tool_combo = QComboBox()
        self._tool_combo.addItems(["Endmill", "Point", "None"])
        tool_form.addRow("Display", self._tool_combo)
        root.addLayout(tool_form)

        # ── Path display mode ────────────────────────────────────────────────
        root.addWidget(QLabel("Path Settings"))
        path_form = QFormLayout()
        path_form.setContentsMargins(0, 0, 0, 0)
        path_form.setSpacing(8)

        self._path_combo = QComboBox()
        self._path_combo.addItems(["Complete", "Progressive", "None"])
        path_form.addRow("Path", self._path_combo)
        root.addLayout(path_form)

        root.addStretch()

        # Restore persisted values before connecting signals
        self._load_saved()

        self._tool_combo.currentIndexChanged.connect(self._on_tool_changed)
        self._path_combo.currentIndexChanged.connect(self._on_path_changed)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_saved(self) -> None:
        """Restore combo selections from QSettings."""
        saved_tool = self._s.tool_mode   # e.g. "Endmill"
        saved_path = self._s.path_mode   # e.g. "Progressive"

        idx = self._tool_combo.findText(saved_tool)
        if idx >= 0:
            self._tool_combo.setCurrentIndex(idx)

        idx = self._path_combo.findText(saved_path)
        if idx >= 0:
            self._path_combo.setCurrentIndex(idx)

    # ── Signal callbacks ──────────────────────────────────────────────────────

    def _on_tool_changed(self, index: int) -> None:
        if 0 <= index < len(self._TOOL_MODES):
            mode = self._TOOL_MODES[index]
            self._s.tool_mode = self._tool_combo.itemText(index)
            self.tool_mode_changed.emit(mode)

    def _on_path_changed(self, index: int) -> None:
        if 0 <= index < len(self._PATH_MODES):
            mode = self._PATH_MODES[index]
            self._s.path_mode = self._path_combo.itemText(index)
            self.path_mode_changed.emit(mode)

    # ── External setters (called by DatumSimWidget) ───────────────────────────

    def set_tool_mode(self, mode: ToolMode) -> None:
        """Sync combo to the given mode without emitting signals."""
        label = self._TOOL_LABELS.get(mode)
        if label is None:
            return
        idx = self._tool_combo.findText(label)
        if idx >= 0:
            self._tool_combo.blockSignals(True)
            self._tool_combo.setCurrentIndex(idx)
            self._tool_combo.blockSignals(False)

    def set_path_mode(self, mode: PathMode) -> None:
        """Sync combo to the given mode without emitting signals."""
        label = self._PATH_LABELS.get(mode)
        if label is None:
            return
        idx = self._path_combo.findText(label)
        if idx >= 0:
            self._path_combo.blockSignals(True)
            self._path_combo.setCurrentIndex(idx)
            self._path_combo.blockSignals(False)

    def set_current_tool(self, tool_number: int) -> None:
        """No-op — tool display in the panel is now read-only (T-command only).

        Kept for API compatibility with DatumSimWidget._apply_tool().
        """
        pass
