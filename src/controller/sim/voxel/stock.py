"""
sim/voxel/stock.py — Workpiece stock definition (pure Python / numpy).

No C++ or OpenVDB required.  All geometry is derived from the G-code path
bounding box plus a configurable margin.

Classes
-------
BoundingBox
    Axis-aligned box in world-space mm.  Knows how to compute the voxel-grid
    dimensions for a given voxel_size.

StockDefinition
    Binds a BoundingBox with a voxel_size and exposes grid_shape.

Extension points (future)
-------------------------
GCODE_EXTRACTED
    Automatically derive the stock outline from the G-code moves rather than
    using a rectangular bounding box.  Stub — raises NotImplementedError.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np


class SourceType(Enum):
    BOUNDING_BOX    = auto()   # implemented
    GCODE_EXTRACTED = auto()   # stub, not yet implemented


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned bounding box in world-space millimetres."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    # ── Construction helpers ──────────────────────────────────────────────────

    @staticmethod
    def from_path_buffer(path, margin_mm: float = 5.0) -> "BoundingBox":
        """Derive bbox from cutting moves only (G1/G2/G3 — feed_rate > 0).

        G0 rapid moves are excluded: they travel above the workpiece and
        would inflate the bounding box with air-clearance positions.
        Falls back to all points if no cutting move is found.
        """
        pts   = path.points      # (N, 3) float32
        feeds = path.feed_rates  # (N,)   float32  — 0.0 = rapid (G0)

        mask = feeds > 0.0
        cutting_pts = pts[mask] if mask.any() else pts   # fallback: all

        return BoundingBox(
            x_min=float(cutting_pts[:, 0].min()) - margin_mm,
            x_max=float(cutting_pts[:, 0].max()) + margin_mm,
            y_min=float(cutting_pts[:, 1].min()) - margin_mm,
            y_max=float(cutting_pts[:, 1].max()) + margin_mm,
            z_min=float(cutting_pts[:, 2].min()) - margin_mm,
            z_max=float(cutting_pts[:, 2].max()) + margin_mm,
        )

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


@dataclass
class StockDefinition:
    """Binds geometry (BoundingBox) with simulation resolution (voxel_size)."""

    bbox:       BoundingBox
    voxel_size: float       = 0.5   # mm per voxel edge
    source_type: SourceType = SourceType.BOUNDING_BOX

    @property
    def grid_shape(self) -> tuple[int, int, int]:
        """(Nx, Ny, Nz) — number of voxels in each axis."""
        return self.bbox.grid_shape(self.voxel_size)

    def resolve(self, program=None):
        """Return self for BOUNDING_BOX; raise for stubs."""
        if self.source_type == SourceType.BOUNDING_BOX:
            return self
        if self.source_type == SourceType.GCODE_EXTRACTED:
            raise NotImplementedError(
                "GCODE_EXTRACTED stock derivation is not yet implemented. "
                "Use SourceType.BOUNDING_BOX for now."
            )
        raise ValueError(f"Unknown source type: {self.source_type!r}")
