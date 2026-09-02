"""
sim/voxel/carver.py — CPU-side numpy tool carver.

Implements material subtraction in a GpuVoxelGrid's CPU array.
No C++ or GPU compute required — compatible with OpenGL 3.3.

Algorithm
---------
For each path segment the swept volume is approximated by a series of
tool "stamps" spaced at ½-voxel intervals along the segment.  Each stamp
evaluates the tool profile at every z-slice of the candidate voxel box
and zeroes the voxels whose XY distance falls within the tool radius at
that depth.

The XY distance check is fully numpy-vectorised (broadcast over the
2-D XY plane); the z-loop is pure Python but is small (typically < 20
iterations for common tool heights).

Extension points (future)
-------------------------
Physics channels
    When temperature / stress fields are added to GpuVoxelGrid, the carver
    will also populate heat-source masks alongside material removal.  The
    per-voxel chip-thickness and contact-zone width needed for Kienzle-based
    heat flux calculations are derivable from the quantities already computed
    here (local_z, r_at_z, r2_xy).

Tool profile
    profile_radius_at(z) is called once per z-slice per sample —
    O(n_samples × n_z_slices) calls, typically < 100 per segment.
"""
from __future__ import annotations

import numpy as np

from controller.sim.voxel.gpu_grid import GpuVoxelGrid
from controller.sim.simulation.tool_definition import ToolDefinition


class VoxelCarver:
    """
    Carves tool-swept volumes into a GpuVoxelGrid's CPU material array.

    Usage
    -----
    After construction, call :meth:`carve_segment` for each new path
    segment.  The grid's dirty region is updated automatically.
    The caller must then call ``grid.upload_if_dirty()`` inside a valid
    GL context to push changes to the GPU.
    """

    def __init__(self, grid: GpuVoxelGrid) -> None:
        self._grid = grid

    # ── Public interface ──────────────────────────────────────────────────────

    def carve_segment(
        self,
        start: np.ndarray,     # (3,) float32 — segment start, world mm
        end:   np.ndarray,     # (3,) float32 — segment end,   world mm
        tool:  ToolDefinition,
    ) -> None:
        """
        Subtract the swept volume of *tool* moving from *start* to *end*.

        Samples the segment at ≤ half-voxel intervals and accumulates the
        union of all per-sample tool footprints before calling grid.carve().
        """
        seg_vec = (end - start).astype("f4")
        seg_len = float(np.linalg.norm(seg_vec))
        vs      = self._grid.voxel_size

        # ── Sample positions ─────────────────────────────────────────────────
        # At least 2 (start + end), then add intermediate at ½-voxel spacing
        n_samples = max(2, int(np.ceil(seg_len / (vs * 0.5))) + 1)
        ts  = np.linspace(0.0, 1.0, n_samples, dtype="f4")      # (n,)
        pts = start + ts[:, np.newaxis] * seg_vec                # (n, 3)

        # ── Effective cutting depth ───────────────────────────────────────────
        cut_len = tool.cutting_length
        if cut_len <= 0.0:
            cut_len = tool.total_length
        if cut_len <= 0.0:
            cut_len = 999.0     # fallback: treat as infinitely long

        # ── Sweep bounding box in voxel indices ───────────────────────────────
        bbox   = self._grid.bbox
        origin = bbox.origin()        # (3,) float32
        nx, ny, nz = self._grid.shape
        tip_r  = tool.radius

        min_pt = pts.min(axis=0)
        max_pt = pts.max(axis=0)

        # XY: tool radius defines the lateral margin.
        # Z:  the tool body extends CUT_LEN *above* the tip (toward the shank).
        #     Using tip_r here instead of cut_len would under-estimate sweep_max_z
        #     whenever cut_len > tip_r, leaving the upper flute slices outside the
        #     bounding box and silently skipping those voxels — the root cause of
        #     the "top layers not carved" bug.
        sweep_min = np.array([
            min_pt[0] - tip_r    - vs,
            min_pt[1] - tip_r    - vs,
            min_pt[2]            - vs,   # just below the lowest tip position
        ], dtype="f4")
        sweep_max = np.array([
            max_pt[0] + tip_r    + vs,
            max_pt[1] + tip_r    + vs,
            max_pt[2] + cut_len  + vs,   # above the highest flute-top position
        ], dtype="f4")

        ix0 = int(np.clip(np.floor((sweep_min[0] - origin[0]) / vs), 0, nx))
        ix1 = int(np.clip(np.ceil ((sweep_max[0] - origin[0]) / vs), 0, nx))
        iy0 = int(np.clip(np.floor((sweep_min[1] - origin[1]) / vs), 0, ny))
        iy1 = int(np.clip(np.ceil ((sweep_max[1] - origin[1]) / vs), 0, ny))
        iz0 = int(np.clip(np.floor((sweep_min[2] - origin[2]) / vs), 0, nz))
        iz1 = int(np.clip(np.ceil ((sweep_max[2] - origin[2]) / vs), 0, nz))

        if ix0 >= ix1 or iy0 >= iy1 or iz0 >= iz1:
            return  # segment lies entirely outside the grid

        lx, ly, lz = ix1 - ix0, iy1 - iy0, iz1 - iz0

        # ── Voxel centre coordinates for the candidate box ────────────────────
        xs = origin[0] + (np.arange(ix0, ix1, dtype="f4") + 0.5) * vs  # (lx,)
        ys = origin[1] + (np.arange(iy0, iy1, dtype="f4") + 0.5) * vs  # (ly,)
        zs = origin[2] + (np.arange(iz0, iz1, dtype="f4") + 0.5) * vs  # (lz,)

        # ── Accumulate union of all sample tool volumes ───────────────────────
        inside = np.zeros((lz, ly, lx), dtype=bool)

        for pt in pts:
            tx, ty, tz = float(pt[0]), float(pt[1]), float(pt[2])

            # XY distance² from tool axis (broadcast: (ly, lx))
            dx = xs - tx                                        # (lx,)
            dy = ys - ty                                        # (ly,)
            r2_xy = dx[np.newaxis, :] ** 2 + dy[:, np.newaxis] ** 2   # (ly, lx)

            # Per-sample Z-range: restrict to [tz, tz + cut_len] in voxel
            # index space.  Vectorise over all Z slices at once with
            # profile_radius_at_array() — eliminates the Python z-loop and
            # replaces O(n_z) scalar calls with a single numpy broadcast.
            iz_abs_lo = max(iz0, int(np.floor((tz           - origin[2]) / vs)))
            iz_abs_hi = min(iz1, int(np.ceil ((tz + cut_len - origin[2]) / vs)) + 1)
            if iz_abs_lo >= iz_abs_hi:
                continue

            iz_range  = np.arange(iz_abs_lo, iz_abs_hi, dtype=np.int32)
            iz_local  = iz_range - iz0                     # (n_z,) relative to box
            local_zs  = zs[iz_local] - tz                  # (n_z,) height above tip

            valid     = (local_zs >= 0.0) & (local_zs <= cut_len)
            iz_local_v = iz_local[valid]
            local_zs_v = local_zs[valid]
            if len(iz_local_v) == 0:
                continue

            # profile_radius_at_array: (n_valid,) → no Python loop
            radii = tool.profile_radius_at_array(local_zs_v)
            r2_z  = (radii ** 2)[:, np.newaxis, np.newaxis]   # (n_valid, 1, 1)
            # Broadcast: r2_xy is (ly, lx), r2_z is (n_valid, 1, 1)
            # Result: (n_valid, ly, lx) — fancy-index into inside
            inside[iz_local_v] |= (r2_xy[np.newaxis] <= r2_z)

        # ── Write to grid ─────────────────────────────────────────────────────
        self._grid.carve(ix0, ix1, iy0, iy1, iz0, iz1, inside)
