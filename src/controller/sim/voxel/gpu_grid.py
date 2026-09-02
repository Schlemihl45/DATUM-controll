"""
sim/voxel/gpu_grid.py — Dual-sided voxel material grid.

CPU side  :  np.uint8 array  (Nz, Ny, Nx)  —  255 = material, 0 = air.
GPU side  :  moderngl.Texture3D  r8  —  sampled as 0.0…1.0 in shaders.

The texture is kept in sync with the CPU array through a *dirty-region*
mechanism: carving operations mark the minimum changed AABB, and
``upload_if_dirty()`` uploads only that sub-volume to the GPU.

OpenGL 3.3 compatible — no compute shaders required.

Architecture / extension notes
-------------------------------
Future physics layers (temperature, stress, …) will be added as additional
``Texture3D`` fields here alongside ``_texture_material``.  The carver and
renderer will then bind extra image/sampler units as needed.
"""
from __future__ import annotations

import numpy as np
import moderngl

from controller.sim.voxel.stock import StockDefinition, BoundingBox


# Sentinel meaning "no dirty region exists yet"
_NO_DIRTY = None


class GpuVoxelGrid:
    """
    Manages the voxel material field on CPU (numpy) and GPU (Texture3D).

    Parameters
    ----------
    ctx :
        Active ModernGL context (must be current when constructing).
    stock :
        Geometry + resolution description.
    """

    def __init__(self, ctx: moderngl.Context, stock: StockDefinition) -> None:
        self._ctx        = ctx
        self._bbox       = stock.bbox
        self._voxel_size = stock.voxel_size

        nx, ny, nz = stock.grid_shape
        self._shape = (nx, ny, nz)   # (Nx, Ny, Nz)  — x is the fast axis in numpy

        # ── CPU material array ────────────────────────────────────────────────
        # Layout: _material[iz, iy, ix]  →  255 = workpiece, 0 = air
        self._material: np.ndarray = np.full((nz, ny, nx), 255, dtype=np.uint8)

        # ── GPU Texture3D (r8 — unsigned normalised, sampled as 0…1) ─────────
        # Texture3D size = (width, height, depth) = (Nx, Ny, Nz)
        self._tex = ctx.texture3d(
            size=(nx, ny, nz),
            components=1,
            data=self._material.tobytes(),
            dtype="f1",
        )
        self._tex.filter = (moderngl.NEAREST, moderngl.NEAREST)

        # ── Dirty region (voxel-index AABB) ──────────────────────────────────
        self._dirty: bool = False
        self._dx = [nx, 0]   # [x_min, x_max)
        self._dy = [ny, 0]
        self._dz = [nz, 0]

    # ── Read-only properties ──────────────────────────────────────────────────

    @property
    def shape(self) -> tuple[int, int, int]:
        """(Nx, Ny, Nz) — voxel counts per axis."""
        return self._shape

    @property
    def bbox(self) -> BoundingBox:
        return self._bbox

    @property
    def voxel_size(self) -> float:
        """Edge length of one voxel in mm."""
        return self._voxel_size

    @property
    def texture(self) -> moderngl.Texture3D:
        """GPU texture — bind this in the renderer."""
        return self._tex

    @property
    def material(self) -> np.ndarray:
        """CPU material array (Nz, Ny, Nx), uint8.  Read-only from outside."""
        return self._material

    # ── Carving ───────────────────────────────────────────────────────────────

    def carve(
        self,
        ix0: int, ix1: int,
        iy0: int, iy1: int,
        iz0: int, iz1: int,
        mask: np.ndarray,
    ) -> None:
        """
        Zero out material where *mask* is True in the sub-volume
        [ix0:ix1, iy0:iy1, iz0:iz1].

        Parameters
        ----------
        mask :
            Boolean array of shape ``(iz1-iz0, iy1-iy0, ix1-ix0)``.
        """
        self._material[iz0:iz1, iy0:iy1, ix0:ix1][mask] = 0
        self._dirty = True
        self._dx[0] = min(self._dx[0], ix0)
        self._dx[1] = max(self._dx[1], ix1)
        self._dy[0] = min(self._dy[0], iy0)
        self._dy[1] = max(self._dy[1], iy1)
        self._dz[0] = min(self._dz[0], iz0)
        self._dz[1] = max(self._dz[1], iz1)

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Refill with solid material and mark the entire grid as dirty."""
        self._material[:] = 255
        nx, ny, nz = self._shape
        self._dirty = True
        self._dx    = [0, nx]
        self._dy    = [0, ny]
        self._dz    = [0, nz]

    # ── GPU sync ──────────────────────────────────────────────────────────────

    def upload_if_dirty(self) -> bool:
        """
        Upload the dirty sub-volume of the CPU array to the GPU texture.

        Returns True if an upload was performed.

        **Must be called from the GL thread with the context current.**
        Typically called from ``DatumSimWidget._tick()`` after
        ``viewport.makeCurrent()``.
        """
        if not self._dirty:
            return False

        nx, ny, nz = self._shape
        x0, x1 = max(0, self._dx[0]), min(nx, self._dx[1])
        y0, y1 = max(0, self._dy[0]), min(ny, self._dy[1])
        z0, z1 = max(0, self._dz[0]), min(nz, self._dz[1])

        if x0 >= x1 or y0 >= y1 or z0 >= z1:
            self._dirty = False
            return False

        region = np.ascontiguousarray(self._material[z0:z1, y0:y1, x0:x1])
        self._tex.write(
            region.tobytes(),
            viewport=(x0, y0, z0, x1 - x0, y1 - y0, z1 - z0),
        )

        # Reset dirty tracking
        self._dirty = False
        self._dx    = [nx, 0]
        self._dy    = [ny, 0]
        self._dz    = [nz, 0]
        return True

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def release(self) -> None:
        """Release GPU resources.  Call when the grid is no longer needed."""
        self._tex.release()
