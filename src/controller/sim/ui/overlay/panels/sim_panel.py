"""
sim/ui/overlay/panels/sim_panel.py — Functional simulation settings panel.

Shows simulation behavior settings:
  • Tool Display mode  — Endmill / Point / None
  • Path mode          — Complete / Progressive / None
  • Info label visibility toggles (WCS / G-code line / Tool / Feedrate)

The tool-database selector was intentionally removed: tool changes happen
exclusively via T-commands in the G-code program, never through the UI.
Camera/viewport settings (background color, speeds, etc.) were moved to
the application-level SettingsPage so they stay persistent across sessions.
"""
from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from controller.sim.core.settings import AppSettings
from controller.sim.ui.viewport import PathMode, ToolMode


class SimPanel(QWidget):
    """Slide-out panel for simulation display settings."""

    # Emitted when the user changes the tool render mode
    tool_mode_changed = Signal(object)   # ToolMode
    # Emitted when the user changes path display mode
    path_mode_changed = Signal(object)   # PathMode

    # Ordered lists drive the combo box indices
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
        root.addWidget(_section_label("Tool Display"))
        tool_form = QFormLayout()
        tool_form.setContentsMargins(0, 0, 0, 0)
        tool_form.setSpacing(8)

        self._tool_combo = QComboBox()
        self._tool_combo.addItems(["Endmill", "Point", "None"])
        tool_form.addRow("Display", self._tool_combo)
        root.addLayout(tool_form)

        # ── Path display mode ────────────────────────────────────────────────
        root.addWidget(_section_label("Path Settings"))
        path_form = QFormLayout()
        path_form.setContentsMargins(0, 0, 0, 0)
        path_form.setSpacing(8)

        self._path_combo = QComboBox()
        self._path_combo.addItems(["Complete", "Progressive", "None"])
        path_form.addRow("Path", self._path_combo)
        root.addLayout(path_form)

        # ── Info label visibility toggles ────────────────────────────────────
        root.addWidget(_section_label("Info Bar"))
        info_form = QFormLayout()
        info_form.setContentsMargins(0, 0, 0, 0)
        info_form.setSpacing(8)

        self._chk_datum    = QCheckBox()
        self._chk_gcode    = QCheckBox()
        self._chk_tool     = QCheckBox()
        self._chk_feedrate = QCheckBox()

        info_form.addRow("WCS / Nullpunkt",  self._chk_datum)
        info_form.addRow("G-code Zeile",     self._chk_gcode)
        info_form.addRow("Werkzeug",         self._chk_tool)
        info_form.addRow("Vorschub",         self._chk_feedrate)
        root.addLayout(info_form)

        root.addStretch()

        # Restore persisted values before connecting signals
        self._load_saved()

        self._tool_combo.currentIndexChanged.connect(self._on_tool_changed)
        self._path_combo.currentIndexChanged.connect(self._on_path_changed)

        self._chk_datum.toggled.connect(self._on_datum_toggled)
        self._chk_gcode.toggled.connect(self._on_gcode_toggled)
        self._chk_tool.toggled.connect(self._on_tool_toggled)
        self._chk_feedrate.toggled.connect(self._on_feedrate_toggled)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_saved(self) -> None:
        """Restore combo selections and checkbox states from QSettings."""
        saved_tool = self._s.tool_mode
        saved_path = self._s.path_mode

        idx = self._tool_combo.findText(saved_tool)
        if idx >= 0:
            self._tool_combo.setCurrentIndex(idx)

        idx = self._path_combo.findText(saved_path)
        if idx >= 0:
            self._path_combo.setCurrentIndex(idx)

        # Checkboxes: block signals while restoring to avoid double-setting
        self._chk_datum.blockSignals(True)
        self._chk_gcode.blockSignals(True)
        self._chk_tool.blockSignals(True)
        self._chk_feedrate.blockSignals(True)

        self._chk_datum.setChecked(self._s.show_datum)
        self._chk_gcode.setChecked(self._s.show_gcode_line)
        self._chk_tool.setChecked(self._s.show_tool)
        self._chk_feedrate.setChecked(self._s.show_feedrate)

        self._chk_datum.blockSignals(False)
        self._chk_gcode.blockSignals(False)
        self._chk_tool.blockSignals(False)
        self._chk_feedrate.blockSignals(False)

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

    def _on_datum_toggled(self, checked: bool) -> None:
        self._s.show_datum = checked

    def _on_gcode_toggled(self, checked: bool) -> None:
        self._s.show_gcode_line = checked

    def _on_tool_toggled(self, checked: bool) -> None:
        self._s.show_tool = checked

    def _on_feedrate_toggled(self, checked: bool) -> None:
        self._s.show_feedrate = checked

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


def _section_label(text: str) -> QLabel:
    """Styled section heading label for the settings panel."""
    lbl = QLabel(text)
    font = lbl.font()
    font.setBold(True)
    lbl.setFont(font)
    return lbl
