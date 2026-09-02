"""
sim/voxel/gpu_grid.py — Dual-sided voxel material grid.

CPU side  :  np.uint8 array  (Nz, Ny, Nx)  —  255 = material, 0 = air.
GPU side  :  moderngl.Texture3D  r8  —  sampled as 0.0…1.0 in shaders.

The texture is kept in sync with the CPU array through a *tile-based* dirty
tracking mechanism: ``carve()`` performs a single numpy slice operation (fast)
then marks which 16³-voxel tiles were touched.  ``upload_if_dirty()`` uploads
only those tiles to the GPU instead of the entire dirty AABB, keeping the
GPU transfer small even for long toolpaths.

OpenGL 3.3 compatible — no compute shaders required.

Thread safety
-------------
``carve()`` is called from the background carve thread; ``upload_if_dirty()``
is called from the Qt main (GL) thread.  The two methods share ``_dirty_tiles``
(a Python set).  The fix: ``upload_if_dirty()`` snapshots the set with
``list()`` before iterating, then removes only the processed keys afterwards.
New tiles added by ``carve()`` while the upload loop is running will stay in
the set and be uploaded on the next tick.

Architecture / extension notes
-------------------------------
Future physics layers (temperature, stress, …) will be added as additional
``Texture3D`` fields here alongside ``_texture_material``.  The carver and
renderer will then bind extra image/sampler units as needed.
"""
from __future__ import annotations

import numpy as np
import moderngl

from controller.sim.voxel.stock import StockDefinition, StockShape, BoundingBox


class GpuVoxelGrid:
    """
    Manages the voxel material field on CPU (numpy) and GPU (Texture3D).

    Parameters
    ----------
    ctx :
        Active ModernGL context (must be current when constructing).
    stock :
        Geometry + resolution description.  ``stock.bbox`` must already be
        set (call ``stock.build_bbox(path)`` before constructing the grid).
    """

    # Voxels per tile axis.  A 16³ tile = 4096 bytes — small enough that a
    # handful of dirty tiles per frame is a negligible GPU transfer, yet large
    # enough to amortise the tile-loop overhead in carve().
    TILE: int = 16

    def __init__(self, ctx: moderngl.Context, stock: StockDefinition) -> None:
        self._ctx        = ctx
        self._stock      = stock
        self._bbox       = stock.bbox
        self._voxel_size = stock.voxel_size

        nx, ny, nz = stock.grid_shape
        self._shape = (nx, ny, nz)   # (Nx, Ny, Nz)  — x is the fast axis in numpy

        # ── CPU material array ────────────────────────────────────────────────
        # Layout: _material[iz, iy, ix]  →  255 = workpiece, 0 = air
        self._material: np.ndarray = np.empty((nz, ny, nx), dtype=np.uint8)

        # Populate with the correct shape (fills _material in-place)
        self._init_material()

        # ── GPU Texture3D (r8 — unsigned normalised, sampled as 0…1) ─────────
        # Texture3D size = (width, height, depth) = (Nx, Ny, Nz)
        self._tex = ctx.texture3d(
            size=(nx, ny, nz),
            components=1,
            data=self._material.tobytes(),
            dtype="f1",
        )
        self._tex.filter = (moderngl.NEAREST, moderngl.NEAREST)

        # ── Tile-based dirty tracking ─────────────────────────────────────────
        # Instead of tracking a single dirty AABB (which can span the whole
        # toolpath for a long horizontal pass), we track which 16³-voxel tiles
        # have been modified.  Each tile upload is a small, bounded GPU call.
        self._dirty:       bool                         = False
        self._dirty_tiles: set[tuple[int, int, int]]    = set()

    # ── Initialisation helpers ────────────────────────────────────────────────

    def _init_material(self) -> None:
        """
        Fill ``self._material`` with the correct initial stock shape.

        BOUNDING_BOX → all 255 (fully solid rectangular block).
        ROUND        → voxels inside the cylinder = 255, outside = 0.
        """
        self._material[:] = 255

        if self._stock.shape == StockShape.ROUND:
            cx, cy = self._bbox.xy_center()
            self._apply_round_mask(cx, cy, self._stock.round_radius_mm)

    def _apply_round_mask(self, cx: float, cy: float, radius: float) -> None:
        """
        Zero out all voxels whose XY centre is outside the cylinder
        ``(cx, cy, radius)``.  The Z extent is already set by the bbox.
        """
        bbox   = self._bbox
        vs     = self._voxel_size
        nx, ny, nz = self._shape
        origin = bbox.origin()

        # World-space XY centre of each voxel column
        xs = origin[0] + (np.arange(nx, dtype="f4") + 0.5) * vs   # (Nx,)
        ys = origin[1] + (np.arange(ny, dtype="f4") + 0.5) * vs   # (Ny,)

        # Squared distance from cylinder axis — broadcast to (Ny, Nx)
        dx = xs - cx
        dy = ys - cy
        r2 = dx[np.newaxis, :] ** 2 + dy[:, np.newaxis] ** 2       # (Ny, Nx)

        # Boolean mask: True where the voxel column is OUTSIDE the cylinder
        outside = r2 > (radius * radius)                            # (Ny, Nx)

        # Apply across all Z slices — _material[iz, iy, ix]
        self._material[:, outside] = 0

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

    @property
    def is_dirty(self) -> bool:
        """True if there is unsynchronised carved data waiting for GPU upload."""
        return self._dirty

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

        Thread safety
        -------------
        May be called from the background carve thread.  The numpy write is
        GIL-protected; the set additions are GIL-protected (each .add() is
        atomic).  ``upload_if_dirty()`` snapshots the set before iterating so
        concurrent adds during the upload loop are harmless.
        """
        # ── Fast numpy write (identical to original approach) ─────────────────
        self._material[iz0:iz1, iy0:iy1, ix0:ix1][mask] = 0
        self._dirty = True

        # ── Mark overlapping tiles as dirty ───────────────────────────────────
        # The triple loop only marks *which* tiles to re-upload; no per-tile
        # sub-masking, no allocations.  Typically 1–4 iterations per carve call.
        TILE = GpuVoxelGrid.TILE
        for tz_i in range(iz0 // TILE, (iz1 - 1) // TILE + 1):
            for ty_i in range(iy0 // TILE, (iy1 - 1) // TILE + 1):
                for tx_i in range(ix0 // TILE, (ix1 - 1) // TILE + 1):
                    self._dirty_tiles.add((tz_i, ty_i, tx_i))

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """
        Refill with solid material (respecting stock shape) and mark the
        entire grid as dirty for a full GPU re-upload.
        """
        self._init_material()
        self._dirty_tiles.clear()
        self._dirty = False
        # Full re-upload for reset (infrequent operation)
        self._tex.write(self._material.tobytes())

    # ── GPU sync ──────────────────────────────────────────────────────────────

    def upload_if_dirty(self) -> bool:
        """
        Upload all dirty tiles of the CPU array to the GPU texture.

        Returns True if at least one tile was uploaded.

        **Must be called from the GL thread with the context current.**
        Typically called from ``DatumSimWidget._tick()`` after
        ``viewport.makeCurrent()``.

        Thread safety
        -------------
        ``carve()`` may add to ``_dirty_tiles`` concurrently from the carve
        thread.  We snapshot the set with ``list()`` before iterating to avoid
        "RuntimeError: Set changed size during iteration".  After uploading
        the snapshot, we remove only those entries (not clear()), so tiles
        added during the upload loop remain dirty for the next tick.
        """
        if not self._dirty:
            return False

        TILE = GpuVoxelGrid.TILE
        nx, ny, nz = self._shape

        # Snapshot — safe to iterate, carve() can still add to _dirty_tiles
        dirty_snapshot = list(self._dirty_tiles)

        for (tz_i, ty_i, tx_i) in dirty_snapshot:
            ix0 = tx_i * TILE;  ix1 = min(ix0 + TILE, nx)
            iy0 = ty_i * TILE;  iy1 = min(iy0 + TILE, ny)
            iz0 = tz_i * TILE;  iz1 = min(iz0 + TILE, nz)
            region = np.ascontiguousarray(self._material[iz0:iz1, iy0:iy1, ix0:ix1])
            self._tex.write(
                region.tobytes(),
                viewport=(ix0, iy0, iz0, ix1 - ix0, iy1 - iy0, iz1 - iz0),
            )

        # Remove only the entries we just processed (new ones stay dirty)
        self._dirty_tiles -= set(dirty_snapshot)
        self._dirty = bool(self._dirty_tiles)
        return True

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def release(self) -> None:
        """Release GPU resources.  Call when the grid is no longer needed."""
        self._tex.release()
