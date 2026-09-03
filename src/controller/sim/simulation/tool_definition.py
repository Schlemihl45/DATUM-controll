"""sim/simulation/tool_definition.py — ToolDefinition dataclass and ToolType enum."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from abc import ABC, abstractmethod
import numpy as np

# Pocket number meaning "not assigned to any magazine slot". Lives here
# (the domain-model module), not in a UI widget, so both the persistence
# layer (persistence/tool_db.py) and the UI (ui/widgets/tool_magazine_bar.py)
# can share one definition instead of each hard-coding -1 independently.
# The `pocket` column is a plain INTEGER with no CHECK constraint (see
# tool_db.py's schema), so this needs no migration — it's just a value
# convention, mirroring how LinuxCNC tool tables already use out-of-range
# pocket numbers loosely.
UNASSIGNED_POCKET = -1


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
        manufacturer, material, service_life_min, holder_preset

    Further expanded for the ToolPage UI (magazine list + detail editor):
        name, flute_count, clearance_angle, cutting_speed, feed_rate
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

    # ToolPage fields (added for the tool magazine/detail UI) — kept
    # separate from `remark` (which stays a free-text note field on the
    # detail page) and from the LinuxCNC-native fields above.
    name: str = ""                 # display name, e.g. "10mm Schaftfräser"
    flute_count: int = 0           # number of cutting edges
    clearance_angle: float = 0.0   # relief/clearance angle, degrees
    cutting_speed: float = 0.0     # vc, m/min
    feed_rate: float = 0.0         # mm/min

    # Name of a controller.sim.simulation.tool_holder.HolderProfile preset
    # this tool is mounted in, or None if unassigned. Resolved to the actual
    # HolderProfile via persistence.tool_db.ToolDatabase.get_holder() — kept
    # here as just the name (not the resolved object) so ToolDefinition
    # stays a plain, DB-row-shaped dataclass.
    holder_preset: str | None = None

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

    def profile_radius_at_array(self, z_arr: np.ndarray) -> np.ndarray:
        """
        Vectorised version of profile_radius_at.

        Parameters
        ----------
        z_arr : (n,) float32 array
            Heights above the tool tip (z=0 at tip, positive toward shank).

        Returns
        -------
        (n,) float32 array of tool radii at each height.
        No Python loop — uses numpy broadcast throughout.
        """
        r = np.float32(self.radius)
        # Default: full radius for z >= 0, 0 for z < 0
        result = np.where(z_arr < 0.0, np.float32(0.0), r).astype("f4")

        if self.tool_type == ToolType.ENDMILL:
            return result

        elif self.tool_type == ToolType.BALL_ENDMILL:
            in_ball = (z_arr >= 0.0) & (z_arr <= r)
            result[in_ball] = np.sqrt(
                np.maximum(np.float32(0.0), r ** 2 - (r - z_arr[in_ball]) ** 2)
            ).astype("f4")
            return result

        elif self.tool_type == ToolType.BULL_ENDMILL:
            cr = np.float32(min(self.corner_radius, self.radius))
            flat_r = r - cr
            in_torus = (z_arr >= 0.0) & (z_arr <= cr)
            result[in_torus] = (
                flat_r + np.sqrt(
                    np.maximum(np.float32(0.0), cr ** 2 - (cr - z_arr[in_torus]) ** 2)
                )
            ).astype("f4")
            return result

        elif self.tool_type == ToolType.CHAMFER:
            half = np.radians(self.tip_angle / 2.0)
            result = np.where(
                z_arr < 0.0, np.float32(0.0),
                np.minimum(z_arr * np.tan(half), r),
            )
            return result.astype("f4")

        elif self.tool_type == ToolType.DRILL:
            half = np.radians(self.tip_angle / 2.0)
            tip_h = r / np.tan(half)
            result = np.where(
                z_arr < 0.0, np.float32(0.0),
                np.where(z_arr <= tip_h, z_arr * np.tan(half), r),
            )
            return result.astype("f4")

        elif self.tool_type == ToolType.TAPER:
            tip_r = np.float32(0.5)
            result = np.where(
                z_arr < 0.0, np.float32(0.0),
                np.minimum(tip_r + z_arr * np.tan(np.radians(self.taper_angle)), r),
            )
            return result.astype("f4")

        return result
