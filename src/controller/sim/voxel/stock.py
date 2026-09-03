"""
sim/voxel/stock.py — Workpiece stock definition (pure Python / numpy).

Stock shapes
------------
BOUNDING_BOX
    Rectangular prism. XY extent is either explicit (``width_mm``/
    ``depth_mm`` set, positioned via ``x_offset_mm``/``y_offset_mm`` —
    distance from the work origin to the stock's near corner) or auto,
    derived from G1/G2/G3 cutting moves + margin (the historical/default
    behavior, when width/depth are left at 0.0). Z top at ``z_offset_mm``
    (default 0 = workpiece-zero surface); Z bottom either auto (path z_min
    − margin) or explicit ``height_mm`` — unaffected by the X/Y change,
    both shapes already had this.

ROUND
    Cylinder centred on the work origin (0, 0) — i.e. the workpiece
    coordinate system's own zero, not wherever the cutting moves happen to
    sit (the previous behavior). Same Z convention as BOUNDING_BOX.

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

    # ROUND: cylinder radius, centred on the work origin (0, 0).
    round_radius_mm:  float      = 50.0

    # BOUNDING_BOX explicit size: 0.0 (either, or both) means "auto" —
    # fall back to the historical path-derived XY extent below. Sizes are
    # independent (only a non-auto axis uses its offset; the other keeps
    # deriving from the path) so e.g. a fixed width with auto depth works.
    width_mm:         float      = 0.0   # X size
    depth_mm:         float      = 0.0   # Y size

    # BOUNDING_BOX explicit position: distance from the work origin to the
    # stock's near (lower) X/Y corner. Only meaningful together with a
    # non-auto width_mm/depth_mm on that axis — 0.0 puts the origin exactly
    # on that corner.
    x_offset_mm:      float      = 0.0
    y_offset_mm:      float      = 0.0

    # ── Derived (set by build_bbox) ───────────────────────────────────────────
    bbox: BoundingBox | None = field(default=None, repr=False)

    # ── Construction ─────────────────────────────────────────────────────────

    def build_bbox(self, path) -> "StockDefinition":
        """
        Compute and store the bounding box from the path and current settings.

        Only G1/G2/G3 cutting moves (feed_rate > 0) are used for auto XY
        extent; G0 rapids are excluded. An explicit width_mm/depth_mm (see
        their docstrings) overrides the corresponding axis instead of
        deriving it from the path. Returns self for chaining.
        """
        pts   = path.points       # (N, 3) float32
        feeds = path.feed_rates   # (N,)   float32

        mask        = feeds > 0.0
        cutting_pts = pts[mask] if mask.any() else pts
        margin = self.xy_margin_mm

        if self.shape == StockShape.ROUND:
            # Cylinder centred on the work origin (0, 0) — not the cutting
            # extent's centroid; round_radius_mm is the only size control.
            r = self.round_radius_mm
            x_min, x_max = -r, r
            y_min, y_max = -r, r
        else:
            if self.width_mm > 0.0:
                x_min = -self.x_offset_mm
                x_max = x_min + self.width_mm
            else:
                x_min = float(cutting_pts[:, 0].min()) - margin
                x_max = float(cutting_pts[:, 0].max()) + margin

            if self.depth_mm > 0.0:
                y_min = -self.y_offset_mm
                y_max = y_min + self.depth_mm
            else:
                y_min = float(cutting_pts[:, 1].min()) - margin
                y_max = float(cutting_pts[:, 1].max()) + margin

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
