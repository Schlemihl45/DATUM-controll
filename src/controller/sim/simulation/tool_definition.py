"""sim/simulation/tool_definition.py — ToolDefinition dataclass and ToolType enum."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from abc import ABC, abstractmethod
import numpy as np

class ToolType(Enum):
    ENDMILL    = auto()
    BALL_ENDMILL    = auto()
    BULL_ENDMILL    = auto()
    CHAMFER         = auto()
    DRILL           = auto()
    TAPER           = auto()

@dataclass
class ToolDefinition:
    """
    LinuxCNC Tool Table 2.4.x+ compatible, expanded by geometry-data

    LinuxCNC:
        T = tool_number
        P = pocket
        D = diameter
        Z = z_offset
        X,Y = x_offset, y_offset

    Expanded by:
        tool_type, cutting_length, shank_diameter, corner_radius,
        tip_angle, taper_angle,
        manufacturer, material, service_life_min
    """
    # LinuxCNC Tool Table
    tool_number: int
    pocket: int
    diameter: float
    z_offset: float = 0.0
    x_offset: float = 0.0
    y_offset: float = 0.0
    remark: str = ""

    # Geometry
    tool_type: ToolType = ToolType.ENDMILL
    flute_length: float = 0.0
    cutting_length: float = 0.0
    shank_diameter: float = 0.0
    total_length: float = 0.0

    # Type specific
    corner_radius: float = 0.0
    tip_angle: float = 0.0
    taper_angle: float = 0.0

    manufacturer: str = ""
    material: str = ""
    service_life_min: float = 0.0
    used_min: float = 0.0

    @property
    def radius(self) -> float:
        return self.diameter / 2

    @property
    def remaining_life_min(self) -> float:
        if self.service_life_min <= 0:
            return float("inf")
        return max(0, int(self.service_life_min-self.used_min))

    def profile_radius_at(self, z: float) -> float:
        """
        Radius of the tool at height z.
        z=0: tool tip (contact point)
        z>0: toward the shank

        Called by the voxel engine per slice:
            for z in z_levels:
                r = tool.profile_radius_at(z)
                subtract_circle(center_xy, r, z)
        """
        if z < 0:
            return 0.0

        r = self.radius

        if self.tool_type == ToolType.ENDMILL:
            return r

        elif self.tool_type == ToolType.BALL_ENDMILL:
            if z <= r:
                # Hemisphere: Pythagorean cross-section of sphere
                return float(np.sqrt(max(0.0, r ** 2 - (r - z) ** 2)))
            return r

        elif self.tool_type == ToolType.BULL_ENDMILL:
            cr = min(self.corner_radius, r)
            flat_r = r - cr
            if z <= cr:
                # Torus cross-section
                return float(flat_r + np.sqrt(max(0.0, cr ** 2 - (cr - z) ** 2)))
            return r

        elif self.tool_type == ToolType.CHAMFER:
            half = np.radians(self.tip_angle / 2.0)
            return float(min(z * np.tan(half), r))

        elif self.tool_type == ToolType.DRILL:
            half = np.radians(self.tip_angle / 2.0)
            tip_h = r / np.tan(half)
            if z <= tip_h:
                return float(z * np.tan(half))
            return r

        elif self.tool_type == ToolType.TAPER:
            tip_r = 0.5  # minimum tip diameter
            return float(min(tip_r + z * np.tan(np.radians(self.taper_angle)), r))

        return r
