"""
sim/ui/overlay/panels/sim_panel.py — Functional simulation settings panel.

Shows simulation behavior settings:
  • Tool Display mode  — Endmill / Point / None
  • Path mode          — Complete / Progressive / None
  • Info label visibility toggles (WCS / G-code line / Tool / Feedrate)
  • Abtragssimulation  — enable/disable toggle + voxel size (mm)
  • Rohteil            — stock shape + dimensional overrides

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
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from controller.sim.core.settings import AppSettings
from controller.sim.ui.viewport import PathMode, ToolMode

# Check whether the voxel simulation modules are importable
# (requires numpy + moderngl, both already in dependencies)
try:
    from controller.sim.voxel.controller import VoxelSimController as _VSC  # noqa: F401
    _VOXEL_AVAILABLE = True
    del _VSC
except ImportError:
    _VOXEL_AVAILABLE = False

# Shape labels ↔ settings key
_SHAPE_LABELS = ["Boundary Box", "Rund"]
_SHAPE_KEYS   = ["bounding_box", "round"]


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

        # ── Abtragssimulation ─────────────────────────────────────────────────
        root.addWidget(_section_label("Abtragssimulation"))
        voxel_form = QFormLayout()
        voxel_form.setContentsMargins(0, 0, 0, 0)
        voxel_form.setSpacing(8)

        self._chk_voxel = QCheckBox()
        if _VOXEL_AVAILABLE:
            self._chk_voxel.setToolTip(
                "Aktiviert die voxelbasierte Materialabtragssimulation.\n"
                "Das Rohteil wird als 3D-Textur simuliert."
            )
        else:
            self._chk_voxel.setEnabled(False)
            self._chk_voxel.setToolTip(
                "Nicht verfügbar — die Voxel-Module konnten nicht importiert werden.\n"
                "Stelle sicher dass numpy und moderngl installiert sind."
            )
        voxel_form.addRow("Aktivieren", self._chk_voxel)

        if not _VOXEL_AVAILABLE:
            hint = QLabel("⚠ Voxel-Module nicht verfügbar")
            hint.setStyleSheet("color: #e0a040; font-size: 11px;")
            hint.setWordWrap(True)
            voxel_form.addRow("", hint)

        self._voxel_size_spin = QDoubleSpinBox()
        self._voxel_size_spin.setRange(0.05, 5.0)
        self._voxel_size_spin.setSingleStep(0.1)
        self._voxel_size_spin.setDecimals(2)
        self._voxel_size_spin.setSuffix(" mm")
        self._voxel_size_spin.setToolTip(
            "Kantenlänge eines Voxels in mm.\n"
            "Kleiner = feinere Details, mehr Speicher und langsamere Berechnung.\n"
            "Änderungen erfordern einen Reset der Simulation."
        )
        self._voxel_size_spin.blockSignals(True)
        self._voxel_size_spin.setValue(self._s.voxel_size)
        self._voxel_size_spin.blockSignals(False)
        voxel_form.addRow("Voxelgröße", self._voxel_size_spin)
        root.addLayout(voxel_form)

        # ── Rohteil (stock geometry) ──────────────────────────────────────────
        root.addWidget(_section_label("Rohteil"))
        stock_form = QFormLayout()
        stock_form.setContentsMargins(0, 0, 0, 0)
        stock_form.setSpacing(8)

        # Shape selector
        self._stock_shape_combo = QComboBox()
        self._stock_shape_combo.addItems(_SHAPE_LABELS)
        stock_form.addRow("Form", self._stock_shape_combo)

        # Z-Oberfläche: distance from Z=0 to stock top surface
        self._stock_z_offset_spin = QDoubleSpinBox()
        self._stock_z_offset_spin.setRange(-500.0, 500.0)
        self._stock_z_offset_spin.setSingleStep(0.5)
        self._stock_z_offset_spin.setDecimals(2)
        self._stock_z_offset_spin.setSuffix(" mm")
        self._stock_z_offset_spin.setToolTip(
            "Abstand vom Werkzeug-Nullpunkt (Z=0) zur Rohteiloberfläche.\n"
            "0 mm = Rohteiloberfläche liegt genau bei Z=0 (Standard).\n"
            "Positiv = Material ragt über den Nullpunkt."
        )
        stock_form.addRow("Z-Oberfläche", self._stock_z_offset_spin)

        # Höhe: stock height (0 = auto from path)
        self._stock_height_spin = QDoubleSpinBox()
        self._stock_height_spin.setRange(0.0, 2000.0)
        self._stock_height_spin.setSingleStep(1.0)
        self._stock_height_spin.setDecimals(1)
        self._stock_height_spin.setSuffix(" mm")
        self._stock_height_spin.setSpecialValueText("Auto")
        self._stock_height_spin.setToolTip(
            "Höhe des Rohteils in mm.\n"
            "0 (Auto) = Höhe wird aus dem G-code-Pfad abgeleitet."
        )
        stock_form.addRow("Höhe", self._stock_height_spin)

        # Radius: only for Round mode
        self._stock_radius_label = QLabel("Radius")
        self._stock_radius_spin  = QDoubleSpinBox()
        self._stock_radius_spin.setRange(1.0, 2000.0)
        self._stock_radius_spin.setSingleStep(5.0)
        self._stock_radius_spin.setDecimals(1)
        self._stock_radius_spin.setSuffix(" mm")
        self._stock_radius_spin.setToolTip(
            "Radius des zylindrischen Rohteils um den XY-Mittelpunkt\n"
            "der Schnittbewegungen."
        )
        stock_form.addRow(self._stock_radius_label, self._stock_radius_spin)

        root.addLayout(stock_form)
        root.addStretch()

        # Internal state
        self._sim_running: bool = False

        # ── Restore persisted values before connecting signals ────────────────
        self._load_saved()

        # ── Connect signals ───────────────────────────────────────────────────
        self._tool_combo.currentIndexChanged.connect(self._on_tool_changed)
        self._path_combo.currentIndexChanged.connect(self._on_path_changed)

        self._chk_datum.toggled.connect(self._on_datum_toggled)
        self._chk_gcode.toggled.connect(self._on_gcode_toggled)
        self._chk_tool.toggled.connect(self._on_tool_toggled)
        self._chk_feedrate.toggled.connect(self._on_feedrate_toggled)

        self._chk_voxel.toggled.connect(self._on_voxel_enabled_toggled)
        self._voxel_size_spin.valueChanged.connect(self._on_voxel_size_changed)

        self._stock_shape_combo.currentIndexChanged.connect(self._on_stock_shape_changed)
        self._stock_z_offset_spin.valueChanged.connect(self._on_stock_z_offset_changed)
        self._stock_height_spin.valueChanged.connect(self._on_stock_height_changed)
        self._stock_radius_spin.valueChanged.connect(self._on_stock_radius_changed)

        # Sync enabled states
        self._sync_voxel_size_state()
        self._sync_stock_controls()

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
        for chk, val in [
            (self._chk_datum,    self._s.show_datum),
            (self._chk_gcode,    self._s.show_gcode_line),
            (self._chk_tool,     self._s.show_tool),
            (self._chk_feedrate, self._s.show_feedrate),
            (self._chk_voxel,    self._s.voxel_enabled and _VOXEL_AVAILABLE),
        ]:
            chk.blockSignals(True)
            chk.setChecked(val)
            chk.blockSignals(False)

        # Stock settings
        try:
            shape_idx = _SHAPE_KEYS.index(self._s.stock_shape)
        except ValueError:
            shape_idx = 0
        self._stock_shape_combo.blockSignals(True)
        self._stock_shape_combo.setCurrentIndex(shape_idx)
        self._stock_shape_combo.blockSignals(False)

        for spin, val in [
            (self._stock_z_offset_spin, self._s.stock_z_offset_mm),
            (self._stock_height_spin,   self._s.stock_height_mm),
            (self._stock_radius_spin,   self._s.stock_round_radius_mm),
        ]:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

    def _sync_voxel_size_state(self) -> None:
        """Enable/disable voxel size spinbox based on checkbox + availability."""
        enabled = _VOXEL_AVAILABLE and self._chk_voxel.isChecked()
        self._voxel_size_spin.setEnabled(enabled)

    def _sync_stock_controls(self) -> None:
        """Show/hide the radius row based on the current stock shape."""
        is_round = (self._stock_shape_combo.currentIndex() == _SHAPE_KEYS.index("round"))
        self._stock_radius_label.setVisible(is_round)
        self._stock_radius_spin.setVisible(is_round)

        # Disable all stock controls when sim not available
        enabled = _VOXEL_AVAILABLE
        for w in (
            self._stock_shape_combo,
            self._stock_z_offset_spin,
            self._stock_height_spin,
            self._stock_radius_spin,
        ):
            w.setEnabled(enabled)

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

    def _on_voxel_enabled_toggled(self, checked: bool) -> None:
        self._s.voxel_enabled = checked
        self._sync_voxel_size_state()

    def _on_voxel_size_changed(self, value: float) -> None:
        if self._sim_running:
            QMessageBox.information(
                self,
                "Voxelgröße geändert",
                "Die Voxelgröße kann nicht während einer laufenden Simulation geändert werden.\n"
                "Bitte Reset drücken, um das Rohteil mit der neuen Auflösung neu aufzubauen.",
            )
            # Revert the spinbox to the saved value without triggering the signal
            self._voxel_size_spin.blockSignals(True)
            self._voxel_size_spin.setValue(self._s.voxel_size)
            self._voxel_size_spin.blockSignals(False)
            return
        self._s.voxel_size = value

    def _on_stock_shape_changed(self, index: int) -> None:
        if 0 <= index < len(_SHAPE_KEYS):
            self._s.stock_shape = _SHAPE_KEYS[index]
            self._sync_stock_controls()

    def _on_stock_z_offset_changed(self, value: float) -> None:
        self._s.stock_z_offset_mm = value

    def _on_stock_height_changed(self, value: float) -> None:
        self._s.stock_height_mm = value

    def _on_stock_radius_changed(self, value: float) -> None:
        self._s.stock_round_radius_mm = value

    def set_sim_running(self, running: bool) -> None:
        """Called by DatumSimWidget to tell the panel whether a sim is active.

        When running=True, voxel_size changes show an info dialog instead of
        applying immediately (Live-Resize is not supported — Level-Set resolution
        is fixed at grid creation time).
        """
        self._sim_running = running

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
