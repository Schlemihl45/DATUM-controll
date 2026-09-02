"""
sim/voxel/stock.py — Stock (workpiece blank) definition.

StockDefinition describes how to create the initial raw-material volume:
  • BOUNDING_BOX   — Explicit min/max extents supplied directly (implemented).
  • GCODE_EXTRACTED — Derive extents from the G-code program (stub, see below).

BoundingBox is a plain data object used both here and as the argument type for
the C++ voxel_mod.init_stock() function.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    # Avoid a heavy import at module level — PathBuffer is only used in type hints.
    from controller.sim.gcode.path_buffer import PathBuffer


class SourceType(Enum):
    """How the stock bounding box is obtained."""

    BOUNDING_BOX    = auto()
    """Explicitly specified min/max corners (currently the only active source)."""

    GCODE_EXTRACTED = auto()
    """Extracted automatically from the G-code program.
    Not yet implemented — see extract_stock_from_gcode() below.
    """


@dataclass
class BoundingBox:
    """Axis-aligned bounding box in machine coordinates (mm)."""

    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def size(self) -> tuple[float, float, float]:
        return (self.max_x - self.min_x,
                self.max_y - self.min_y,
                self.max_z - self.min_z)

    @property
    def center(self) -> tuple[float, float, float]:
        return ((self.min_x + self.max_x) / 2,
                (self.min_y + self.max_y) / 2,
                (self.min_z + self.max_z) / 2)

    def padded(self, margin_mm: float) -> "BoundingBox":
        """Return a copy expanded by *margin_mm* on every face."""
        return BoundingBox(
            self.min_x - margin_mm, self.min_y - margin_mm, self.min_z - margin_mm,
            self.max_x + margin_mm, self.max_y + margin_mm, self.max_z + margin_mm,
        )

    @classmethod
    def from_path_buffer(cls, path: "PathBuffer", margin_mm: float = 5.0) -> "BoundingBox":
        """Derive a bounding box from the path points, padded by *margin_mm*.

        This is used as a fallback approximation when no explicit stock size is
        given. It is not the same as the actual stock dimensions — the path may
        not reach the stock boundary — but it is a reasonable safe default.
        """
        pts = path.points  # (N, 3) float32
        return cls(
            min_x=float(pts[:, 0].min()) - margin_mm,
            min_y=float(pts[:, 1].min()) - margin_mm,
            min_z=float(pts[:, 2].min()) - margin_mm,
            max_x=float(pts[:, 0].max()) + margin_mm,
            max_y=float(pts[:, 1].max()) + margin_mm,
            max_z=float(pts[:, 2].max()) + margin_mm,
        )

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        """Flat (min_x, min_y, min_z, max_x, max_y, max_z) — passed to C++."""
        return (self.min_x, self.min_y, self.min_z,
                self.max_x, self.max_y, self.max_z)


@dataclass
class StockDefinition:
    """Describes the workpiece blank used to initialise the voxel grid.

    Args:
        source_type: Where the bounding box comes from.
        bbox:        Explicit bounding box. Required when source_type is
                     BOUNDING_BOX; ignored for GCODE_EXTRACTED (resolved at
                     runtime).
        voxel_size:  Edge length of one voxel in mm. Smaller = finer detail
                     but more memory and slower CSG. Cannot be changed after
                     the grid is initialised without a full reset.
        padding_mm:  Extra margin added around the extracted bounding box.
                     Ignored when bbox is supplied explicitly.
    """

    source_type: SourceType = SourceType.BOUNDING_BOX
    bbox:        BoundingBox | None = None
    voxel_size:  float = 0.5    # mm
    padding_mm:  float = 5.0

    def resolve(self, program=None) -> BoundingBox:
        """Return the concrete bounding box for this stock definition.

        For BOUNDING_BOX: returns self.bbox directly (must not be None).
        For GCODE_EXTRACTED: delegates to extract_stock_from_gcode().
        """
        if self.source_type == SourceType.BOUNDING_BOX:
            if self.bbox is None:
                raise ValueError(
                    "StockDefinition.bbox must be set when source_type is BOUNDING_BOX."
                )
            return self.bbox

        if self.source_type == SourceType.GCODE_EXTRACTED:
            if program is None:
                raise ValueError(
                    "A GCodeProgram must be passed to resolve() when "
                    "source_type is GCODE_EXTRACTED."
                )
            return extract_stock_from_gcode(program)

        raise ValueError(f"Unknown SourceType: {self.source_type!r}")


# ── Stub ──────────────────────────────────────────────────────────────────────

def extract_stock_from_gcode(program) -> BoundingBox:
    """Extract the stock bounding box from a loaded G-code program.

    STUB — not yet implemented. See TODO below.

    Args:
        program: A GCodeProgram instance (from GCodeCompiler.load_file()).

    Returns:
        BoundingBox enclosing the stock material.

    TODO — the data source for this function is not yet determined.
    Three candidates are under consideration:

    1. Toolpath bounding box (PathBuffer.from_path_buffer):
       Simple approximation.  Does not capture stock that extends beyond the
       cutting area (most real parts).  Useful as a conservative fallback.

    2. Post-processor comments in the G-code header:
       Common in commercial CAM software (Fusion 360, Mastercam, etc.).
       Format is vendor-specific and not standardised.

    3. G10 / G28.1 lines:
       Some post-processors emit workpiece-origin offsets via G10 L2 Px.
       Would need the modal-state parser extended to track these.

    Until one of these sources is validated on real files, this function is
    intentionally left as a stub so callers receive a clear error rather than
    silently computing a wrong bounding box.
    """
    raise NotImplementedError(
        "extract_stock_from_gcode() is not yet implemented. "
        "Use SourceType.BOUNDING_BOX with an explicit BoundingBox, or call "
        "BoundingBox.from_path_buffer() for a path-derived approximation."
    )
