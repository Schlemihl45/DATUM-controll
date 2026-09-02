"""
sim/ui/overlay/panels/sim_panel.py — Simulation settings panel.

Master-detail layout: a narrow left-hand column of checkable nav buttons
(CardButton, same component the rest of the app uses) picks which section
is shown in a QStackedWidget to the right — not a QTabWidget. All settings
that affect the sim widget live here (moved in from the old app-wide
SettingsPage, which now only holds the app theme):

  Darstellung   Tool display + cutting-edge color, path mode, info-bar
                toggles, viewport toggles + background color theme
  Simulation    Voxel enable/disable, voxel resolution
  Rohteil       Stock shape, Z-offset, height, radius, material color
"""
from __future__ import annotations

from PySide6.QtCore  import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QStackedWidget, QVBoxLayout, QWidget,
)

from controller.sim.core.settings import AppSettings
from controller.sim.ui.viewport   import PathMode, ToolMode
from controller.ui.icon_loader    import get_icon
from controller.ui.widgets.card_button import CardButton

try:
    from controller.sim.voxel.controller import VoxelSimController as _VSC  # noqa: F401
    _VOXEL_AVAILABLE = True
    del _VSC
except ImportError:
    _VOXEL_AVAILABLE = False

_SHAPE_LABELS = ["Boundary Box", "Rund"]
_SHAPE_KEYS   = ["bounding_box", "round"]


# ══════════════════════════════════════════════════════════════════════════════
# Reusable helpers
# ══════════════════════════════════════════════════════════════════════════════

class _ColorSwatch(QWidget):
    """Small colored rectangle showing a hex color."""

    def __init__(self, hex_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self._color = hex_color
        self._update_style()

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color
        self._update_style()

    def _update_style(self) -> None:
        self.setStyleSheet(
            f"background: {self._color}; border: 1px solid rgba(255,255,255,0.15);"
            f" border-radius: 4px;"
        )


def _hdr(text: str) -> QLabel:
    lbl = QLabel(text)
    font = lbl.font()
    font.setBold(True)
    lbl.setFont(font)
    return lbl


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = rgb
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Darstellung
# ══════════════════════════════════════════════════════════════════════════════

class _DisplayTab(QWidget):
    """Tool display + color, path mode, info-bar toggles, viewport toggles + bg theme."""

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

        # Tool display + cutting-edge color ────────────────────────────────
        root.addWidget(_hdr("Werkzeug"))
        tf = QFormLayout(); tf.setSpacing(8)
        self._tool_combo = QComboBox()
        self._tool_combo.addItems(["Endmill", "Point", "None"])
        tf.addRow("Anzeige", self._tool_combo)

        self._tool_color_combo  = QComboBox()
        self._tool_color_swatch = _ColorSwatch("#ffd600")
        for name, rgb in AppSettings.TOOL_COLORS.items():
            self._tool_color_combo.addItem(name, userData=_rgb_to_hex(rgb))
        tool_color_row = QHBoxLayout()
        tool_color_row.setSpacing(8)
        tool_color_row.addWidget(self._tool_color_swatch)
        tool_color_row.addWidget(self._tool_color_combo)
        tf.addRow("Schneidenfarbe", tool_color_row)
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
        self._chk_datum     = QCheckBox()
        self._chk_gcode     = QCheckBox()
        self._chk_tool      = QCheckBox()
        self._chk_feedrate  = QCheckBox()
        self._chk_part_time = QCheckBox()
        inf.addRow("WCS / Nullpunkt",  self._chk_datum)
        inf.addRow("G-code Zeile",     self._chk_gcode)
        inf.addRow("Werkzeug",         self._chk_tool)
        inf.addRow("Vorschub",         self._chk_feedrate)
        inf.addRow("Teilezeit",        self._chk_part_time)
        root.addLayout(inf)

        # Viewport ───────────────────────────────────────────────────────────
        root.addWidget(_hdr("Viewport"))
        vp = QFormLayout(); vp.setSpacing(8)
        self._chk_axes         = QCheckBox()
        self._chk_grid         = QCheckBox()
        self._chk_datum_symbol = QCheckBox()
        vp.addRow("Achsen (X/Y/Z)",    self._chk_axes)
        vp.addRow("Gitter (XY-Ebene)", self._chk_grid)
        vp.addRow("Nullpunkt-Symbol",  self._chk_datum_symbol)

        self._bg_combo  = QComboBox()
        self._bg_swatch = _ColorSwatch("#1c1c1c")
        for name, (inner, _outer) in AppSettings.BG_COLORS.items():
            self._bg_combo.addItem(name, userData=inner)
        bg_row = QHBoxLayout()
        bg_row.setSpacing(8)
        bg_row.addWidget(self._bg_swatch)
        bg_row.addWidget(self._bg_combo)
        vp.addRow("Hintergrund", bg_row)
        root.addLayout(vp)
        root.addStretch()

        # Load saved state (no signals yet)
        for chk, val in [
            (self._chk_datum,         s.show_datum),
            (self._chk_gcode,         s.show_gcode_line),
            (self._chk_tool,          s.show_tool),
            (self._chk_feedrate,      s.show_feedrate),
            (self._chk_part_time,     s.show_part_time),
            (self._chk_axes,          s.show_axes),
            (self._chk_grid,          s.show_grid),
            (self._chk_datum_symbol,  s.show_datum_symbol),
        ]:
            chk.blockSignals(True)
            chk.setChecked(val)
            chk.blockSignals(False)

        for combo, saved in [(self._tool_combo, s.tool_mode),
                              (self._path_combo, s.path_mode)]:
            idx = combo.findText(saved)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        tool_color_idx = self._tool_color_combo.findText(s.tool_cutting_color)
        self._tool_color_combo.setCurrentIndex(max(0, tool_color_idx))
        self._update_tool_color_swatch()

        bg_idx = self._bg_combo.findText(s.bg_theme_name())
        self._bg_combo.setCurrentIndex(max(0, bg_idx))
        self._update_bg_swatch()

        # Connect
        self._tool_combo.currentIndexChanged.connect(self._on_tool)
        self._path_combo.currentIndexChanged.connect(self._on_path)
        self._tool_color_combo.currentIndexChanged.connect(self._on_tool_color)
        self._bg_combo.currentIndexChanged.connect(self._on_bg_theme)
        self._chk_datum.toggled.connect(        lambda v: setattr(s, "show_datum",        v))
        self._chk_gcode.toggled.connect(        lambda v: setattr(s, "show_gcode_line",   v))
        self._chk_tool.toggled.connect(         lambda v: setattr(s, "show_tool",         v))
        self._chk_feedrate.toggled.connect(     lambda v: setattr(s, "show_feedrate",     v))
        self._chk_part_time.toggled.connect(    lambda v: setattr(s, "show_part_time",    v))
        self._chk_axes.toggled.connect(         lambda v: setattr(s, "show_axes",         v))
        self._chk_grid.toggled.connect(         lambda v: setattr(s, "show_grid",         v))
        self._chk_datum_symbol.toggled.connect( lambda v: setattr(s, "show_datum_symbol", v))

    def _on_tool(self, idx: int) -> None:
        if 0 <= idx < len(self._TOOL_MODES):
            self._s.tool_mode = self._tool_combo.itemText(idx)
            self.tool_mode_changed.emit(self._TOOL_MODES[idx])

    def _on_path(self, idx: int) -> None:
        if 0 <= idx < len(self._PATH_MODES):
            self._s.path_mode = self._path_combo.itemText(idx)
            self.path_mode_changed.emit(self._PATH_MODES[idx])

    def _on_tool_color(self, _idx: int) -> None:
        self._s.tool_cutting_color = self._tool_color_combo.currentText()
        self._update_tool_color_swatch()

    def _update_tool_color_swatch(self) -> None:
        hex_val = self._tool_color_combo.currentData()
        if hex_val:
            self._tool_color_swatch.set_color(hex_val)

    def _on_bg_theme(self, _idx: int) -> None:
        self._s.apply_bg_theme(self._bg_combo.currentText())
        self._update_bg_swatch()

    def _update_bg_swatch(self) -> None:
        hex_val = self._bg_combo.currentData()
        if hex_val:
            self._bg_swatch.set_color(hex_val)

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
# Section 2 — Simulation
# ══════════════════════════════════════════════════════════════════════════════

class _VoxelSimTab(QWidget):
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
# Section 3 — Rohteil
# ══════════════════════════════════════════════════════════════════════════════

class _StockTab(QWidget):
    """Workpiece shape, dimensional overrides, and material color."""

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

        # Material color
        self._color_combo  = QComboBox()
        self._color_swatch = _ColorSwatch("#f2ae1f")
        for name, rgb in AppSettings.VOXEL_COLORS.items():
            self._color_combo.addItem(name, userData=_rgb_to_hex(rgb))
        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        color_row.addWidget(self._color_swatch)
        color_row.addWidget(self._color_combo)
        form.addRow("Materialfarbe", color_row)

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

        color_idx = self._color_combo.findText(s.voxel_color)
        self._color_combo.setCurrentIndex(max(0, color_idx))
        self._update_color_swatch()

        # Connect
        self._shape_combo.currentIndexChanged.connect(self._on_shape)
        self._z_spin.valueChanged.connect(lambda v: setattr(s, "stock_z_offset_mm",    v))
        self._h_spin.valueChanged.connect(lambda v: setattr(s, "stock_height_mm",      v))
        self._r_spin.valueChanged.connect(lambda v: setattr(s, "stock_round_radius_mm", v))
        self._color_combo.currentIndexChanged.connect(self._on_color_changed)

    def _on_shape(self, idx: int) -> None:
        if 0 <= idx < len(_SHAPE_KEYS):
            self._s.stock_shape = _SHAPE_KEYS[idx]
            self._sync_radius_row()

    def _sync_radius_row(self) -> None:
        is_round = (self._shape_combo.currentIndex() == _SHAPE_KEYS.index("round"))
        self._r_label.setVisible(is_round)
        self._r_spin.setVisible(is_round)

    def _on_color_changed(self, _index: int) -> None:
        self._s.voxel_color = self._color_combo.currentText()
        self._update_color_swatch()

    def _update_color_swatch(self) -> None:
        hex_val = self._color_combo.currentData()
        if hex_val:
            self._color_swatch.set_color(hex_val)


# ══════════════════════════════════════════════════════════════════════════════
# Public widget — left nav + stacked content
# ══════════════════════════════════════════════════════════════════════════════

_NAV_ICON_SIZE = QSize(22, 22)
_NAV_BTN_SIZE  = QSize(78, 64)


class SimPanel(QWidget):
    """
    Simulation settings panel: left-hand section nav + stacked content.

    Sections:
      Darstellung — tool/path display, info-bar toggles, viewport + bg color
      Simulation  — voxel enable and resolution
      Rohteil     — workpiece shape/size overrides + material color
    """

    tool_mode_changed = Signal(object)
    path_mode_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        s = AppSettings.instance()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left nav column ─────────────────────────────────────────────────
        nav_col = QVBoxLayout()
        nav_col.setContentsMargins(6, 8, 6, 8)
        nav_col.setSpacing(4)
        nav_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._stack = QStackedWidget(self)

        self._display_tab  = _DisplayTab(s, self._stack)
        self._voxel_tab     = _VoxelSimTab(s, self._stack)
        self._stock_tab     = _StockTab(s, self._stack)

        self._stack.addWidget(self._display_tab)
        self._stack.addWidget(self._voxel_tab)
        self._stack.addWidget(self._stock_tab)

        self._nav_buttons: list[CardButton] = []
        for i, (icon_name, tooltip) in enumerate([
            ("settings",   "Darstellung"),
            ("scan-cube",  "Simulation"),
            ("workpieces", "Rohteil"),
        ]):
            btn = CardButton(icon=get_icon(icon_name, tint=True, size=_NAV_ICON_SIZE),
                              icon_size=_NAV_ICON_SIZE.width())
            btn.setToolTip(tooltip)
            btn.setFixedSize(_NAV_BTN_SIZE)
            btn.setCheckable(True)
            btn.setProperty("variant", "sim_nav")
            btn.clicked.connect(lambda _=False, idx=i: self._on_nav_clicked(idx))
            nav_col.addWidget(btn)
            self._nav_buttons.append(btn)

        nav_col.addStretch()

        nav_widget = QWidget(self)
        nav_widget.setLayout(nav_col)
        nav_widget.setFixedWidth(_NAV_BTN_SIZE.width() + 12)

        root.addWidget(nav_widget)
        root.addWidget(self._stack, stretch=1)

        self._on_nav_clicked(0)

        # Forward display-tab signals up
        self._display_tab.tool_mode_changed.connect(self.tool_mode_changed)
        self._display_tab.path_mode_changed.connect(self.path_mode_changed)

    def _on_nav_clicked(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)

    # ── API expected by DatumSimWidget ────────────────────────────────────────

    def set_tool_mode(self, mode: ToolMode) -> None:
        self._display_tab.set_tool_mode(mode)

    def set_path_mode(self, mode: PathMode) -> None:
        self._display_tab.set_path_mode(mode)

    def set_current_tool(self, tool_number: int) -> None:
        """No-op — tools are set exclusively via T-commands in G-code."""

    def set_sim_running(self, running: bool) -> None:  # noqa: ARG002
        """Kept for API compatibility — voxel size changes are now always live."""
