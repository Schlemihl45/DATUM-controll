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
