"""
sim/voxel/gpu_grid.py — Tile-based sparse voxel material grid.

CPU side  :  tile dictionary  {(tz,ty,tx): np.uint8 (tz,ty,tx)}  — 255 solid, 0 air.
GPU side  :  moderngl.Texture3D  r8  — sampled as 0.0…1.0 in shaders.

Architecture
------------
Instead of allocating the full ``(Nz, Ny, Nx)`` material array up front, the
grid is divided into cubic tiles of ``TILE`` voxels per axis.  A tile that has
never been carved is *implicitly* solid (255 everywhere) — no RAM is allocated
for it.  Only tiles that have been modified by ``carve()`` are allocated
(``MIXED`` state).

For a 100×100×50 mm workpiece at 0.1 mm resolution that would normally require
500 MB as a flat array, this scheme starts at ~0 MB and allocates only the
tiles that actually get carved — typically 1–5 MB for a complete milling run.

GPU texture
-----------
The full ``Texture3D`` is allocated at construction time (required by the GPU)
and initially uploaded with the correct stock shape (all 255 for BOUNDING_BOX,
cylinder mask applied for ROUND).  Thereafter only the dirty tiles are
re-uploaded — each ``upload_if_dirty()`` call uploads at most one sub-volume
per dirty tile.

Thread safety
-------------
All methods must be called from the Qt main thread.  ``upload_if_dirty()`` must
be called with the GL context current.

OpenGL 3.3 compatible — no compute shaders required.
"""
from __future__ import annotations

import numpy as np
import moderngl

from controller.sim.voxel.stock import StockDefinition, StockShape, BoundingBox


class GpuVoxelGrid:
    """
    Manages the voxel material field via tile-based sparse CPU storage and a
    full-resolution GPU Texture3D.

    Parameters
    ----------
    ctx :
        Active ModernGL context (must be current when constructing).
    stock :
        Geometry + resolution description.  ``stock.bbox`` must already be
        set (call ``stock.build_bbox(path)`` before constructing the grid).
    """

    # Voxels per tile axis.  A 16³ tile = 4096 bytes of uint8 — small enough
    # that uploading a handful of dirty tiles per frame is negligible.
    TILE: int = 16

    def __init__(self, ctx: moderngl.Context, stock: StockDefinition) -> None:
        self._ctx        = ctx
        self._stock      = stock
        self._bbox       = stock.bbox
        self._voxel_size = stock.voxel_size

        nx, ny, nz = stock.grid_shape
        self._shape = (nx, ny, nz)   # (Nx, Ny, Nz) — x is the fast axis

        TILE = GpuVoxelGrid.TILE

        # ── Tile bookkeeping ──────────────────────────────────────────────────
        self._ntx = (nx + TILE - 1) // TILE
        self._nty = (ny + TILE - 1) // TILE
        self._ntz = (nz + TILE - 1) // TILE

        # 0 = SOLID (implicitly all 255, not yet allocated)
        # 1 = MIXED (partially carved — tile data lives in _tiles)
        self._tile_state = np.zeros((self._ntz, self._nty, self._ntx), dtype=np.uint8)

        # Allocated tile data (indexed by (tz_i, ty_i, tx_i))
        self._tiles: dict[tuple[int, int, int], np.ndarray] = {}

        # Dirty tracking — set of tile indices pending GPU upload
        self._dirty:       bool              = False
        self._dirty_tiles: set[tuple[int, int, int]] = set()

        # ── Round-stock precomputed mask ──────────────────────────────────────
        # (Ny, Nx) bool array: True = voxel column is OUTSIDE the cylinder.
        # None for BOUNDING_BOX stock.
        self._round_mask: np.ndarray | None = None
        if stock.shape == StockShape.ROUND:
            self._round_mask = self._compute_round_mask()

        # ── GPU Texture3D (r8 — unsigned normalised, sampled as 0…1) ─────────
        # Texture3D size = (width, height, depth) = (Nx, Ny, Nz)
        init_data = self._build_initial_gpu_data()
        self._tex = ctx.texture3d(
            size=(nx, ny, nz),
            components=1,
            data=init_data,
            dtype="f1",
        )
        self._tex.filter = (moderngl.NEAREST, moderngl.NEAREST)

    # ── Initialisation helpers ────────────────────────────────────────────────

    def _compute_round_mask(self) -> np.ndarray:
        """
        Build a (Ny, Nx) bool array: True where the voxel column is OUTSIDE
        the cylinder defined by stock.round_radius_mm around the XY centre.
        """
        bbox   = self._bbox
        vs     = self._voxel_size
        nx, ny, _nz = self._shape
        origin = bbox.origin()
        cx, cy = bbox.xy_center()
        radius = self._stock.round_radius_mm

        xs = origin[0] + (np.arange(nx, dtype="f4") + 0.5) * vs
        ys = origin[1] + (np.arange(ny, dtype="f4") + 0.5) * vs
        dx = xs - cx
        dy = ys - cy
        r2 = dx[np.newaxis, :] ** 2 + dy[:, np.newaxis] ** 2   # (Ny, Nx)
        return r2 > (radius * radius)   # True = outside

    def _build_initial_gpu_data(self) -> bytes:
        """
        Return the full initial texture data as bytes (all 255, or cylinder
        shape for ROUND stock).  Called once at construction and again for
        reset().
        """
        nx, ny, nz = self._shape
        data = np.full((nz, ny, nx), 255, dtype=np.uint8)
        if self._round_mask is not None:
            data[:, self._round_mask] = 0
        return data.tobytes()

    def ensure_tile(self, tz_i: int, ty_i: int, tx_i: int) -> np.ndarray:
        """
        Return the tile data array for tile ``(tz_i, ty_i, tx_i)``.

        If the tile has not been allocated yet (SOLID state), a new
        255-filled array is created and the round mask is applied if needed.
        Always marks the tile as MIXED after this call.

        Parameters
        ----------
        tz_i, ty_i, tx_i :
            Tile indices (z, y, x).  Must be in range.

        Returns
        -------
        np.ndarray of shape (tile_nz, tile_ny, tile_nx) uint8.
        """
        key = (tz_i, ty_i, tx_i)
        if key not in self._tiles:
            TILE = GpuVoxelGrid.TILE
            nx, ny, nz = self._shape
            iz0 = tz_i * TILE; iz1 = min(iz0 + TILE, nz)
            iy0 = ty_i * TILE; iy1 = min(iy0 + TILE, ny)
            ix0 = tx_i * TILE; ix1 = min(ix0 + TILE, nx)
            tile = np.full((iz1 - iz0, iy1 - iy0, ix1 - ix0), 255, dtype=np.uint8)
            if self._round_mask is not None:
                # Apply cylinder mask: columns outside the cylinder → 0
                outside = self._round_mask[iy0:iy1, ix0:ix1]   # (ty, tx)
                tile[:, outside] = 0
            self._tiles[key] = tile
            self._tile_state[tz_i, ty_i, tx_i] = 1   # MIXED
        return self._tiles[key]

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
        """
        Full CPU material array (Nz, Ny, Nx), uint8, assembled on demand.

        For most uses the GPU texture is the authoritative source; this
        property is provided for debugging and serialisation.  Assembling
        a large grid may be slow — do not call in a render loop.
        """
        nx, ny, nz = self._shape
        mat = np.full((nz, ny, nx), 255, dtype=np.uint8)
        if self._round_mask is not None:
            mat[:, self._round_mask] = 0
        TILE = GpuVoxelGrid.TILE
        for (tz_i, ty_i, tx_i), tile in self._tiles.items():
            iz0 = tz_i * TILE; iy0 = ty_i * TILE; ix0 = tx_i * TILE
            mat[iz0:iz0 + tile.shape[0],
                iy0:iy0 + tile.shape[1],
                ix0:ix0 + tile.shape[2]] = tile
        return mat

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
        """
        TILE = GpuVoxelGrid.TILE

        tz_lo = iz0 // TILE;  tz_hi = (iz1 - 1) // TILE
        ty_lo = iy0 // TILE;  ty_hi = (iy1 - 1) // TILE
        tx_lo = ix0 // TILE;  tx_hi = (ix1 - 1) // TILE

        for tz_i in range(tz_lo, tz_hi + 1):
            for ty_i in range(ty_lo, ty_hi + 1):
                for tx_i in range(tx_lo, tx_hi + 1):
                    tiz0 = tz_i * TILE
                    tiy0 = ty_i * TILE
                    tix0 = tx_i * TILE

                    # Voxel-index overlap between the carve region and this tile
                    # (absolute tile size can be shorter on the last tile)
                    # We don't know tile.shape yet, so compute from _shape.
                    nx, ny, nz = self._shape
                    tile_nz = min(tiz0 + TILE, nz) - tiz0
                    tile_ny = min(tiy0 + TILE, ny) - tiy0
                    tile_nx = min(tix0 + TILE, nx) - tix0

                    a_iz0 = max(iz0, tiz0);  a_iz1 = min(iz1, tiz0 + tile_nz)
                    a_iy0 = max(iy0, tiy0);  a_iy1 = min(iy1, tiy0 + tile_ny)
                    a_ix0 = max(ix0, tix0);  a_ix1 = min(ix1, tix0 + tile_nx)

                    if a_iz0 >= a_iz1 or a_iy0 >= a_iy1 or a_ix0 >= a_ix1:
                        continue

                    # Corresponding slice in the input mask
                    m_iz0 = a_iz0 - iz0;  m_iz1 = a_iz1 - iz0
                    m_iy0 = a_iy0 - iy0;  m_iy1 = a_iy1 - iy0
                    m_ix0 = a_ix0 - ix0;  m_ix1 = a_ix1 - ix0

                    sub_mask = mask[m_iz0:m_iz1, m_iy0:m_iy1, m_ix0:m_ix1]
                    if not np.any(sub_mask):
                        continue   # no voxels actually removed in this tile

                    # Allocate tile on first carve
                    tile = self.ensure_tile(tz_i, ty_i, tx_i)

                    # Tile-local slice
                    l_iz0 = a_iz0 - tiz0;  l_iz1 = a_iz1 - tiz0
                    l_iy0 = a_iy0 - tiy0;  l_iy1 = a_iy1 - tiy0
                    l_ix0 = a_ix0 - tix0;  l_ix1 = a_ix1 - tix0

                    tile[l_iz0:l_iz1, l_iy0:l_iy1, l_ix0:l_ix1][sub_mask] = 0
                    self._dirty = True
                    self._dirty_tiles.add((tz_i, ty_i, tx_i))

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """
        Refill with solid material (respecting stock shape) and flush to GPU.

        Clears all tile data and re-uploads the full initial texture so the
        GPU reflects the pristine stock state.
        """
        self._tiles.clear()
        self._tile_state[:] = 0
        self._dirty_tiles.clear()
        self._dirty = False
        self._tex.write(self._build_initial_gpu_data())

    # ── GPU sync ──────────────────────────────────────────────────────────────

    def upload_if_dirty(self) -> bool:
        """
        Upload all dirty tiles to the GPU Texture3D.

        Returns True if at least one tile was uploaded.

        **Must be called from the GL thread with the context current.**
        Typically called from ``DatumSimWidget._tick()`` after
        ``viewport.makeCurrent()``.
        """
        if not self._dirty:
            return False

        TILE = GpuVoxelGrid.TILE
        for (tz_i, ty_i, tx_i) in self._dirty_tiles:
            tile = self._tiles.get((tz_i, ty_i, tx_i))
            if tile is None:
                continue
            ix0 = tx_i * TILE
            iy0 = ty_i * TILE
            iz0 = tz_i * TILE
            # tile.shape = (tile_nz, tile_ny, tile_nx)
            # viewport = (x, y, z, width=x_size, height=y_size, depth=z_size)
            self._tex.write(
                np.ascontiguousarray(tile).tobytes(),
                viewport=(ix0, iy0, iz0,
                          tile.shape[2], tile.shape[1], tile.shape[0]),
            )

        self._dirty_tiles.clear()
        self._dirty = False
        return True

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def release(self) -> None:
        """Release GPU resources.  Call when the grid is no longer needed."""
        self._tex.release()
