"""
sim/voxel/stock.py — Workpiece stock definition (pure Python / numpy).

Stock shapes
------------
BOUNDING_BOX
    Rectangular prism.  XY extent derived from G1/G2/G3 cutting moves +
    margin.  Z top at ``z_offset_mm`` (default 0 = workpiece-zero surface);
    Z bottom either auto (path z_min − margin) or explicit ``height_mm``.

ROUND
    Cylinder centred on the XY centroid of the cutting moves.
    Same Z convention as BOUNDING_BOX.

All settings default to "auto from path" so the simulation works
out-of-the-box even when no stock parameters are configured.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

import numpy as np


class StockShape(Enum):
    BOUNDING_BOX = "bounding_box"
    ROUND        = "round"


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned bounding box in world-space millimetres."""

    x_min: float;  x_max: float
    y_min: float;  y_max: float
    z_min: float;  z_max: float

    # ── Geometry queries ──────────────────────────────────────────────────────

    def grid_shape(self, voxel_size: float) -> tuple[int, int, int]:
        """Return (Nx, Ny, Nz) voxel counts for the given voxel_size (mm)."""
        nx = max(1, int(np.ceil((self.x_max - self.x_min) / voxel_size)))
        ny = max(1, int(np.ceil((self.y_max - self.y_min) / voxel_size)))
        nz = max(1, int(np.ceil((self.z_max - self.z_min) / voxel_size)))
        return nx, ny, nz

    def origin(self) -> np.ndarray:
        """(x_min, y_min, z_min) corner as float32 array."""
        return np.array([self.x_min, self.y_min, self.z_min], dtype="f4")

    def size(self) -> np.ndarray:
        """(width, depth, height) extents as float32 array."""
        return np.array(
            [self.x_max - self.x_min, self.y_max - self.y_min, self.z_max - self.z_min],
            dtype="f4",
        )

    def xy_center(self) -> tuple[float, float]:
        return (self.x_min + self.x_max) * 0.5, (self.y_min + self.y_max) * 0.5


@dataclass
class StockDefinition:
    """
    Complete stock specification: shape + voxel resolution + dimensional overrides.

    Build workflow (called by DatumSimWidget._create_voxel_sim):
        stock = StockDefinition(...)
        stock.build_bbox(path)          # derives bbox from path + settings
        grid = GpuVoxelGrid(ctx, stock) # creates texture, applies shape mask
    """

    # ── Shape and resolution ──────────────────────────────────────────────────
    shape:            StockShape = StockShape.BOUNDING_BOX
    voxel_size:       float      = 0.5    # mm per voxel edge

    # ── Dimensional overrides ─────────────────────────────────────────────────
    # XY: margin around cutting-move extents (BOUNDING_BOX only)
    xy_margin_mm:     float      = 5.0

    # Z: stock top surface.  0.0 = workpiece zero (the typical case when
    # Z=0 is set on the workpiece surface in the CNC program).
    z_offset_mm:      float      = 0.0   # distance from Z=0 to stock top ↑

    # Stock height.  0.0 means "auto": derive from path z_min + margin below.
    height_mm:        float      = 0.0

    # ROUND: cylinder radius from the XY centroid of the cutting extent.
    round_radius_mm:  float      = 50.0

    # ── Derived (set by build_bbox) ───────────────────────────────────────────
    bbox: BoundingBox | None = field(default=None, repr=False)

    # ── Construction ─────────────────────────────────────────────────────────

    def build_bbox(self, path) -> "StockDefinition":
        """
        Compute and store the bounding box from the path and current settings.

        Only G1/G2/G3 cutting moves (feed_rate > 0) are used for XY extent;
        G0 rapids are excluded.  Returns self for chaining.
        """
        pts   = path.points       # (N, 3) float32
        feeds = path.feed_rates   # (N,)   float32

        mask        = feeds > 0.0
        cutting_pts = pts[mask] if mask.any() else pts

        # XY extents
        margin = self.xy_margin_mm
        x_min = float(cutting_pts[:, 0].min()) - margin
        x_max = float(cutting_pts[:, 0].max()) + margin
        y_min = float(cutting_pts[:, 1].min()) - margin
        y_max = float(cutting_pts[:, 1].max()) + margin

        if self.shape == StockShape.ROUND:
            # Cylinder centred on cutting extent centroid
            cx   = (x_min + x_max) * 0.5
            cy   = (y_min + y_max) * 0.5
            r    = self.round_radius_mm
            x_min, x_max = cx - r, cx + r
            y_min, y_max = cy - r, cy + r

        # Z extents
        z_top = self.z_offset_mm   # stock top = workpiece zero by default
        if self.height_mm > 0.0:
            z_bot = z_top - self.height_mm
        else:
            z_bot = float(cutting_pts[:, 2].min()) - margin

        self.bbox = BoundingBox(x_min, x_max, y_min, y_max, z_bot, z_top)
        return self

    # ── Convenience ───────────────────────────────────────────────────────────

    @property
    def grid_shape(self) -> tuple[int, int, int]:
        """(Nx, Ny, Nz) — requires build_bbox() to have been called."""
        if self.bbox is None:
            raise RuntimeError("Call build_bbox(path) before accessing grid_shape")
        return self.bbox.grid_shape(self.voxel_size)
