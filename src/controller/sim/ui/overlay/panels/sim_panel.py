"""
sim/ui/overlay/panels/sim_panel.py — Simulation settings panel with tabs.

Three thematic tabs keep the panel from overflowing:

  Darstellung   Tool display mode, path mode, info-bar toggles
  Simulation    Voxel enable/disable, voxel resolution
  Rohteil       Stock shape, Z-offset, height, (radius for round)
"""
from __future__ import annotations

from PySide6.QtCore  import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QLabel, QTabWidget, QVBoxLayout, QWidget,
)

from controller.sim.core.settings import AppSettings
from controller.sim.ui.viewport   import PathMode, ToolMode

try:
    from controller.sim.voxel.controller import VoxelSimController as _VSC  # noqa: F401
    _VOXEL_AVAILABLE = True
    del _VSC
except ImportError:
    _VOXEL_AVAILABLE = False

_SHAPE_LABELS = ["Boundary Box", "Rund"]
_SHAPE_KEYS   = ["bounding_box", "round"]


# ══════════════════════════════════════════════════════════════════════════════
# Tab 1 — Darstellung
# ══════════════════════════════════════════════════════════════════════════════

class _DisplayTab(QWidget):
    """Tool display, path mode, info-bar label toggles."""

    tool_mode_changed = Signal(object)
    path_mode_changed = Signal(object)

    _TOOL_MODES  = [ToolMode.CYLINDER, ToolMode.POINT, ToolMode.NONE]
    _PATH_MODES  = [PathMode.FULL, PathMode.PROGRESSIVE, PathMode.NONE]
    _TOOL_LABELS = {ToolMode.CYLINDER: "Endmill",
                    ToolMode.POINT:    "Point",
                    ToolMode.NONE:     "None"}
    _PATH_LABELS = {PathMode.FULL:        "Complete",
                    PathMode.PROGRESSIVE: "Progressive",
                    PathMode.NONE:        "None"}

    def __init__(self, s: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._s = s

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Tool display ──────────────────────────────────────────────────────
        root.addWidget(_hdr("Werkzeug"))
        tf = QFormLayout(); tf.setSpacing(8)
        self._tool_combo = QComboBox()
        self._tool_combo.addItems(["Endmill", "Point", "None"])
        tf.addRow("Anzeige", self._tool_combo)
        root.addLayout(tf)

        # Path mode ─────────────────────────────────────────────────────────
        root.addWidget(_hdr("Pfad"))
        pf = QFormLayout(); pf.setSpacing(8)
        self._path_combo = QComboBox()
        self._path_combo.addItems(["Complete", "Progressive", "None"])
        pf.addRow("Modus", self._path_combo)
        root.addLayout(pf)

        # Info bar ──────────────────────────────────────────────────────────
        root.addWidget(_hdr("Info-Leiste"))
        inf = QFormLayout(); inf.setSpacing(8)
        self._chk_datum    = QCheckBox()
        self._chk_gcode    = QCheckBox()
        self._chk_tool     = QCheckBox()
        self._chk_feedrate = QCheckBox()
        inf.addRow("WCS / Nullpunkt",  self._chk_datum)
        inf.addRow("G-code Zeile",     self._chk_gcode)
        inf.addRow("Werkzeug",         self._chk_tool)
        inf.addRow("Vorschub",         self._chk_feedrate)
        root.addLayout(inf)
        root.addStretch()

        # Load saved state (no signals yet)
        for chk, val in [
            (self._chk_datum,    s.show_datum),
            (self._chk_gcode,    s.show_gcode_line),
            (self._chk_tool,     s.show_tool),
            (self._chk_feedrate, s.show_feedrate),
        ]:
            chk.blockSignals(True)
            chk.setChecked(val)
            chk.blockSignals(False)

        for combo, saved in [(self._tool_combo, s.tool_mode),
                              (self._path_combo, s.path_mode)]:
            idx = combo.findText(saved)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        # Connect
        self._tool_combo.currentIndexChanged.connect(self._on_tool)
        self._path_combo.currentIndexChanged.connect(self._on_path)
        self._chk_datum.toggled.connect(   lambda v: setattr(s, "show_datum",      v))
        self._chk_gcode.toggled.connect(   lambda v: setattr(s, "show_gcode_line", v))
        self._chk_tool.toggled.connect(    lambda v: setattr(s, "show_tool",       v))
        self._chk_feedrate.toggled.connect(lambda v: setattr(s, "show_feedrate",   v))

    def _on_tool(self, idx: int) -> None:
        if 0 <= idx < len(self._TOOL_MODES):
            self._s.tool_mode = self._tool_combo.itemText(idx)
            self.tool_mode_changed.emit(self._TOOL_MODES[idx])

    def _on_path(self, idx: int) -> None:
        if 0 <= idx < len(self._PATH_MODES):
            self._s.path_mode = self._path_combo.itemText(idx)
            self.path_mode_changed.emit(self._PATH_MODES[idx])

    # External setters -------------------------------------------------------
    def set_tool_mode(self, mode: ToolMode) -> None:
        label = self._TOOL_LABELS.get(mode)
        if label:
            self._tool_combo.blockSignals(True)
            self._tool_combo.setCurrentIndex(self._tool_combo.findText(label))
            self._tool_combo.blockSignals(False)

    def set_path_mode(self, mode: PathMode) -> None:
        label = self._PATH_LABELS.get(mode)
        if label:
            self._path_combo.blockSignals(True)
            self._path_combo.setCurrentIndex(self._path_combo.findText(label))
            self._path_combo.blockSignals(False)


# ══════════════════════════════════════════════════════════════════════════════
# Tab 2 — Simulation
# ══════════════════════════════════════════════════════════════════════════════

class _SimTab(QWidget):
    """Voxel simulation enable switch and resolution."""

    def __init__(self, s: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._s = s

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(_hdr("Abtragssimulation"))
        form = QFormLayout(); form.setSpacing(8)

        self._chk = QCheckBox()
        if not _VOXEL_AVAILABLE:
            self._chk.setEnabled(False)
            self._chk.setToolTip(
                "Nicht verfügbar — numpy / moderngl nicht importierbar."
            )
        form.addRow("Aktivieren", self._chk)

        if not _VOXEL_AVAILABLE:
            warn = QLabel("⚠ Voxel-Module nicht verfügbar")
            warn.setStyleSheet("color: #e0a040; font-size: 11px;")
            warn.setWordWrap(True)
            form.addRow("", warn)

        self._size_spin = QDoubleSpinBox()
        self._size_spin.setRange(0.05, 5.0)
        self._size_spin.setSingleStep(0.1)
        self._size_spin.setDecimals(2)
        self._size_spin.setSuffix(" mm")
        self._size_spin.setToolTip(
            "Kantenlänge eines Voxels.\n"
            "Kleiner = feiner, aber mehr Speicher + langsamer.\n"
            "Änderung baut die Simulation sofort neu auf."
        )
        form.addRow("Voxelgröße", self._size_spin)
        root.addLayout(form)
        root.addStretch()

        # Load saved
        self._chk.blockSignals(True)
        self._chk.setChecked(s.voxel_enabled and _VOXEL_AVAILABLE)
        self._chk.blockSignals(False)

        self._size_spin.blockSignals(True)
        self._size_spin.setValue(s.voxel_size)
        self._size_spin.blockSignals(False)

        self._sync_size_state()

        self._chk.toggled.connect(self._on_enabled)
        self._size_spin.valueChanged.connect(self._on_size)

    def _sync_size_state(self) -> None:
        self._size_spin.setEnabled(_VOXEL_AVAILABLE and self._chk.isChecked())

    def _on_enabled(self, checked: bool) -> None:
        self._s.voxel_enabled = checked
        self._sync_size_state()

    def _on_size(self, value: float) -> None:
        self._s.voxel_size = value


# ══════════════════════════════════════════════════════════════════════════════
# Tab 3 — Rohteil
# ══════════════════════════════════════════════════════════════════════════════

class _StockTab(QWidget):
    """Workpiece shape and dimensional overrides."""

    def __init__(self, s: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._s = s

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(_hdr("Rohteil"))
        form = QFormLayout(); form.setSpacing(8)

        # Shape
        self._shape_combo = QComboBox()
        self._shape_combo.addItems(_SHAPE_LABELS)
        form.addRow("Form", self._shape_combo)

        # Z-Oberfläche
        self._z_spin = QDoubleSpinBox()
        self._z_spin.setRange(-500.0, 500.0)
        self._z_spin.setSingleStep(0.5)
        self._z_spin.setDecimals(2)
        self._z_spin.setSuffix(" mm")
        self._z_spin.setToolTip(
            "Abstand vom Werkzeug-Nullpunkt (Z=0) zur Rohteiloberfläche.\n"
            "0 mm = Oberfläche liegt bei Z=0 (Standard)."
        )
        form.addRow("Z-Oberfläche", self._z_spin)

        # Höhe
        self._h_spin = QDoubleSpinBox()
        self._h_spin.setRange(0.0, 2000.0)
        self._h_spin.setSingleStep(1.0)
        self._h_spin.setDecimals(1)
        self._h_spin.setSuffix(" mm")
        self._h_spin.setSpecialValueText("Auto")
        self._h_spin.setToolTip("Rohteilhöhe. 0 = Auto aus G-code-Pfad.")
        form.addRow("Höhe", self._h_spin)

        # Radius (Round only)
        self._r_label = QLabel("Radius")
        self._r_spin  = QDoubleSpinBox()
        self._r_spin.setRange(1.0, 2000.0)
        self._r_spin.setSingleStep(5.0)
        self._r_spin.setDecimals(1)
        self._r_spin.setSuffix(" mm")
        self._r_spin.setToolTip("Zylinderradius um den XY-Mittelpunkt.")
        form.addRow(self._r_label, self._r_spin)

        root.addLayout(form)
        root.addStretch()

        # Disable all when voxel unavailable
        if not _VOXEL_AVAILABLE:
            for w in (self._shape_combo, self._z_spin, self._h_spin, self._r_spin):
                w.setEnabled(False)

        # Load saved
        try:
            shape_idx = _SHAPE_KEYS.index(s.stock_shape)
        except ValueError:
            shape_idx = 0
        self._shape_combo.blockSignals(True)
        self._shape_combo.setCurrentIndex(shape_idx)
        self._shape_combo.blockSignals(False)

        for spin, val in [
            (self._z_spin, s.stock_z_offset_mm),
            (self._h_spin, s.stock_height_mm),
            (self._r_spin, s.stock_round_radius_mm),
        ]:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

        self._sync_radius_row()

        # Connect
        self._shape_combo.currentIndexChanged.connect(self._on_shape)
        self._z_spin.valueChanged.connect(lambda v: setattr(s, "stock_z_offset_mm",    v))
        self._h_spin.valueChanged.connect(lambda v: setattr(s, "stock_height_mm",      v))
        self._r_spin.valueChanged.connect(lambda v: setattr(s, "stock_round_radius_mm", v))

    def _on_shape(self, idx: int) -> None:
        if 0 <= idx < len(_SHAPE_KEYS):
            self._s.stock_shape = _SHAPE_KEYS[idx]
            self._sync_radius_row()

    def _sync_radius_row(self) -> None:
        is_round = (self._shape_combo.currentIndex() == _SHAPE_KEYS.index("round"))
        self._r_label.setVisible(is_round)
        self._r_spin.setVisible(is_round)


# ══════════════════════════════════════════════════════════════════════════════
# Public widget
# ══════════════════════════════════════════════════════════════════════════════

class SimPanel(QWidget):
    """
    Tabbed simulation settings panel.

    Tabs:
      Darstellung — tool/path display and info-bar toggles
      Simulation  — voxel enable and resolution
      Rohteil     — workpiece shape and size overrides
    """

    tool_mode_changed = Signal(object)
    path_mode_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        s = AppSettings.instance()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabs = QTabWidget(self)
        tabs.setDocumentMode(True)       # compact tab bar

        self._display_tab = _DisplayTab(s, tabs)
        self._sim_tab     = _SimTab(s, tabs)
        self._stock_tab   = _StockTab(s, tabs)

        tabs.addTab(self._display_tab, "Darstellung")
        tabs.addTab(self._sim_tab,     "Simulation")
        tabs.addTab(self._stock_tab,   "Rohteil")

        root.addWidget(tabs)

        # Forward display-tab signals up
        self._display_tab.tool_mode_changed.connect(self.tool_mode_changed)
        self._display_tab.path_mode_changed.connect(self.path_mode_changed)

    # ── API expected by DatumSimWidget ────────────────────────────────────────

    def set_tool_mode(self, mode: ToolMode) -> None:
        self._display_tab.set_tool_mode(mode)

    def set_path_mode(self, mode: PathMode) -> None:
        self._display_tab.set_path_mode(mode)

    def set_current_tool(self, tool_number: int) -> None:
        """No-op — tools are set exclusively via T-commands in G-code."""

    def set_sim_running(self, running: bool) -> None:  # noqa: ARG002
        """Kept for API compatibility — voxel size changes are now always live."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hdr(text: str) -> QLabel:
    lbl = QLabel(text)
    font = lbl.font()
    font.setBold(True)
    lbl.setFont(font)
    return lbl
