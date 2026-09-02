"""sim/core/settings.py — Per-user simulation settings, persisted via QSettings."""

from PySide6.QtCore import QSettings, QObject, Signal


class AppSettings(QObject):
    """
    Singleton. Settings are written to disk immediately on change.
    Signals fire when a value changes so the UI can react.

    Usage:
        from controller.sim.core.settings import AppSettings
        s = AppSettings.instance()
        s.zoom_speed        # read
        s.zoom_speed = 2.0  # write + auto-save
    """

    # Signals ──────────────────────────────────────────────────────────────────
    bg_color_changed        = Signal(str)
    bg_color_2_changed = Signal(str)

    zoom_speed_changed      = Signal(float)
    rotate_speed_changed    = Signal(float)
    pan_speed_changed       = Signal(float)

    invert_zoom_changed     = Signal(bool)
    invert_rotate_x_changed = Signal(bool)
    invert_rotate_y_changed = Signal(bool)
    invert_pan_x_changed    = Signal(bool)
    invert_pan_y_changed    = Signal(bool)
    show_grid_changed = Signal(bool)

    show_gcode_line_changed = Signal(bool)
    show_datum_changed = Signal(bool)
    show_tool_changed = Signal(bool)
    show_feedrate_changed = Signal(bool)

    tool_mode_changed = Signal(str)
    path_mode_changed = Signal(str)

    voxel_size_changed    = Signal(float)
    voxel_enabled_changed = Signal(bool)
    voxel_color_changed   = Signal(str)

    # Stock-shape signals (any change requires a sim rebuild)
    stock_shape_changed         = Signal(str)
    stock_z_offset_changed      = Signal(float)
    stock_height_changed        = Signal(float)
    stock_round_radius_changed  = Signal(float)

    # ── Voxel colour presets ───────────────────────────────────────────────────
    VOXEL_COLORS: dict[str, tuple[float, float, float]] = {
        "Orange-Gelb":  (0.95, 0.68, 0.12),
        "Koralle":      (0.92, 0.38, 0.25),
        "Hellblau":     (0.30, 0.62, 0.92),
        "Mintgrün":     (0.22, 0.78, 0.55),
        "Violett":      (0.58, 0.35, 0.85),
        "Aluminium":    (0.72, 0.65, 0.56),
        "Stahl":        (0.55, 0.57, 0.60),
        "Holz":         (0.76, 0.60, 0.35),
    }

    # Singleton ────────────────────────────────────────────────────────────────
    _instance: "AppSettings | None" = None

    @classmethod
    def instance(cls) -> "AppSettings":
        if cls._instance is None:
            cls._instance = AppSettings()
        return cls._instance

    def __init__(self):
        super().__init__()
        # Keys stay "DatumSim"/"DatumSim" so user prefs survive upgrades
        self._qs = QSettings("DatumSim", "DatumSim")

    BG_COLORS = {
        "Dark (Standard)": "#1c1c1c",
        "Black":           "#000000",
        "Dark Grey":        "#2d2d2d",
        "Grey":        "#4a4a4a",
        "Dark Blue":        "#0d1b2a",
        "DATUM": "#232a35" ,
    }

    @property
    def bg_color(self) -> str:
        return self._qs.value("camera/bg_color", "#1c1c1c", type=str)

    @bg_color.setter
    def bg_color(self, v: str):
        self._qs.setValue("camera/bg_color", v)
        self.bg_color_changed.emit(v)

    @property
    def bg_color_2(self) -> str:
        return self._qs.value("camera/bg_color_2", "#161b22", type=str)

    @bg_color_2.setter
    def bg_color_2(self, v: str):
        self._qs.setValue("camera/bg_color_2", v)
        self.bg_color_2_changed.emit(v)

    @property
    def zoom_speed(self) -> float:
        return self._qs.value("camera/zoom_speed", 1.0, type=float)

    @zoom_speed.setter
    def zoom_speed(self, v: float):
        self._qs.setValue("camera/zoom_speed", v)
        self.zoom_speed_changed.emit(v)

    @property
    def rotate_speed(self) -> float:
        return self._qs.value("camera/rotate_speed", 1.0, type=float)

    @rotate_speed.setter
    def rotate_speed(self, v: float):
        self._qs.setValue("camera/rotate_speed", v)
        self.rotate_speed_changed.emit(v)

    @property
    def pan_speed(self) -> float:
        return self._qs.value("camera/pan_speed", 1.0, type=float)

    @pan_speed.setter
    def pan_speed(self, v: float):
        self._qs.setValue("camera/pan_speed", v)
        self.pan_speed_changed.emit(v)

    @property
    def invert_zoom(self) -> bool:
        return self._qs.value("camera/invert_zoom", False, type=bool)

    @invert_zoom.setter
    def invert_zoom(self, v: bool):
        self._qs.setValue("camera/invert_zoom", v)
        self.invert_zoom_changed.emit(v)

    @property
    def invert_rotate_x(self) -> bool:
        return self._qs.value("camera/invert_rotate_x", False, type=bool)

    @invert_rotate_x.setter
    def invert_rotate_x(self, v: bool):
        self._qs.setValue("camera/invert_rotate_x", v)
        self.invert_rotate_x_changed.emit(v)

    @property
    def invert_rotate_y(self) -> bool:
        return self._qs.value("camera/invert_rotate_y", False, type=bool)

    @invert_rotate_y.setter
    def invert_rotate_y(self, v: bool):
        self._qs.setValue("camera/invert_rotate_y", v)
        self.invert_rotate_y_changed.emit(v)

    @property
    def invert_pan_x(self) -> bool:
        return self._qs.value("camera/invert_pan_x", False, type=bool)

    @invert_pan_x.setter
    def invert_pan_x(self, v: bool):
        self._qs.setValue("camera/invert_pan_x", v)
        self.invert_pan_x_changed.emit(v)

    @property
    def invert_pan_y(self) -> bool:
        return self._qs.value("camera/invert_pan_y", False, type=bool)

    @invert_pan_y.setter
    def invert_pan_y(self, v: bool):
        self._qs.setValue("camera/invert_pan_y", v)
        self.invert_pan_y_changed.emit(v)

    @property
    def show_grid(self) -> bool:
        return self._qs.value("camera/show_grid", True, type=bool)

    @show_grid.setter
    def show_grid(self, v: bool):
        self._qs.setValue("camera/show_grid", v)
        self.show_grid_changed.emit(v)

    @property
    def tool_mode(self) -> str:
        return self._qs.value("sim/tool_mode", "ENDMILL", type=str)

    @tool_mode.setter
    def tool_mode(self, v: str):
        self._qs.setValue("sim/tool_mode", v)
        self.tool_mode_changed.emit(v)

    @property
    def path_mode(self) -> str:
        return self._qs.value("sim/path_mode", "Full", type=str)

    @path_mode.setter
    def path_mode(self, v: str):
        self._qs.setValue("sim/path_mode", v)
        self.path_mode_changed.emit(v)

    @property
    def show_gcode_line(self) -> bool:
        return self._qs.value("controlhub/show_gcode_line", True, type=bool)

    @show_gcode_line.setter
    def show_gcode_line(self, v: bool):
        self._qs.setValue("controlhub/show_gcode_line", v)
        self.show_gcode_line_changed.emit(v)

    @property
    def show_datum(self) -> bool:
        return self._qs.value("controlhub/show_datum", True, type=bool)

    @show_datum.setter
    def show_datum(self, v: bool):
        self._qs.setValue("controlhub/show_datum", v)
        self.show_datum_changed.emit(v)

    @property
    def show_tool(self) -> bool:
        return self._qs.value("controlhub/show_tool", True, type=bool)

    @show_tool.setter
    def show_tool(self, v: bool):
        self._qs.setValue("controlhub/show_tool", v)
        self.show_tool_changed.emit(v)

    @property
    def show_feedrate(self) -> bool:
        return self._qs.value("controlhub/show_feedrate", True, type=bool)

    @show_feedrate.setter
    def show_feedrate(self, v: bool):
        self._qs.setValue("controlhub/show_feedrate", v)
        self.show_feedrate_changed.emit(v)

    @property
    def voxel_size(self) -> float:
        """Edge length of one voxel in mm. Default 0.5 mm."""
        return self._qs.value("sim/voxel_size", 0.5, type=float)

    @voxel_size.setter
    def voxel_size(self, v: float) -> None:
        v = float(max(0.05, v))   # prevent accidentally tiny grids
        self._qs.setValue("sim/voxel_size", v)
        self.voxel_size_changed.emit(v)

    @property
    def voxel_enabled(self) -> bool:
        """Whether the voxel material-removal simulation should run. Default True."""
        return self._qs.value("sim/voxel_enabled", True, type=bool)

    @voxel_enabled.setter
    def voxel_enabled(self, v: bool) -> None:
        self._qs.setValue("sim/voxel_enabled", bool(v))
        self.voxel_enabled_changed.emit(bool(v))

    @property
    def voxel_color(self) -> str:
        """Name key from VOXEL_COLORS. Default: 'Orange-Gelb'."""
        return self._qs.value("sim/voxel_color", "Orange-Gelb", type=str)

    @voxel_color.setter
    def voxel_color(self, name: str) -> None:
        self._qs.setValue("sim/voxel_color", name)
        self.voxel_color_changed.emit(name)

    def voxel_color_rgb(self) -> tuple[float, float, float]:
        """Current voxel colour as (r, g, b) floats 0–1."""
        return self.VOXEL_COLORS.get(self.voxel_color, (0.95, 0.68, 0.12))

    # ── Stock shape settings ───────────────────────────────────────────────────

    @property
    def stock_shape(self) -> str:
        """Stock shape: 'bounding_box' or 'round'. Default: 'bounding_box'."""
        return self._qs.value("sim/stock_shape", "bounding_box", type=str)

    @stock_shape.setter
    def stock_shape(self, v: str) -> None:
        self._qs.setValue("sim/stock_shape", v)
        self.stock_shape_changed.emit(v)

    @property
    def stock_z_offset_mm(self) -> float:
        """Distance from Z=0 to the stock top surface (mm). Default 0.0."""
        return self._qs.value("sim/stock_z_offset_mm", 0.0, type=float)

    @stock_z_offset_mm.setter
    def stock_z_offset_mm(self, v: float) -> None:
        self._qs.setValue("sim/stock_z_offset_mm", float(v))
        self.stock_z_offset_changed.emit(float(v))

    @property
    def stock_height_mm(self) -> float:
        """Stock height in mm. 0.0 = auto (derived from cutting path)."""
        return self._qs.value("sim/stock_height_mm", 0.0, type=float)

    @stock_height_mm.setter
    def stock_height_mm(self, v: float) -> None:
        self._qs.setValue("sim/stock_height_mm", float(v))
        self.stock_height_changed.emit(float(v))

    @property
    def stock_round_radius_mm(self) -> float:
        """Cylinder radius for ROUND stock (mm). Default 50.0."""
        return self._qs.value("sim/stock_round_radius_mm", 50.0, type=float)

    @stock_round_radius_mm.setter
    def stock_round_radius_mm(self, v: float) -> None:
        self._qs.setValue("sim/stock_round_radius_mm", float(v))
        self.stock_round_radius_changed.emit(float(v))
