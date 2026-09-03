"""
sim/voxel/collision.py — read-only tool/material collision check.

Reuses the exact sweep-sampling technique VoxelCarver.carve_segment() uses
(½-voxel XY sampling, per-Z-slice profile-radius broadcast) but as a pure
read test against existing material instead of a write — see
check_segment()'s docstring for the actual rule.

Kept independent of GpuVoxelGrid/moderngl on purpose: check_segment() takes
a plain numpy material array + bbox/voxel_size, so it works identically
against the live grid's array (VoxelSimController.on_tick(), main/GL
thread) and against a disposable numpy copy (DatumSimWidget's pre-flight
whole-program check, background thread, see its docstring) with zero
GPU/context involvement either way.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.sim.simulation.tool_holder import HolderProfile
from controller.sim.voxel.stock import BoundingBox


@dataclass
class CollisionHit:
    """One detected collision. *line_number* is left at -1 by
    check_segment() itself (it has no notion of G-code lines) — callers
    that have that context (VoxelSimController.on_tick(), the pre-flight
    scan) fill it in before handing the hit onward."""
    point: np.ndarray
    kind: Literal["rapid", "shank", "holder"]
    line_number: int = -1


def _combined_radius_at_array(
    z_arr: np.ndarray, tool: ToolDefinition, holder: HolderProfile | None,
    total_len: float,
) -> np.ndarray:
    """Tool profile radius for z <= total_len, holder profile radius (or 0
    if there is no holder) for z > total_len — the combined outline of
    "everything physically attached to the spindle at this height"."""
    result = tool.profile_radius_at_array(np.minimum(z_arr, total_len))
    beyond = z_arr > total_len
    if np.any(beyond):
        result = result.copy()
        if holder is not None:
            result[beyond] = holder.radius_at_array(
                (z_arr[beyond] - total_len).astype("f4")
            )
        else:
            result[beyond] = 0.0
    return result


def check_segment(
    material:   np.ndarray,       # (Nz, Ny, Nx) uint8, 255 = solid material
    bbox:       BoundingBox,
    voxel_size: float,
    start:      np.ndarray,       # (3,) float32 — segment start, world mm
    end:        np.ndarray,       # (3,) float32 — segment end,   world mm
    tool:       ToolDefinition,
    is_rapid:   bool,
    holder:     HolderProfile | None = None,
) -> CollisionHit | None:
    """
    Check whether *tool* (+ *holder*, if given) moving from *start* to *end*
    touches existing material anywhere it isn't supposed to.

    Rule
    ----
    G0 rapid (is_rapid=True): the ENTIRE tool+holder profile, z in
        [0, total_length + holder.gauge_length], must not touch material —
        a rapid is only ever supposed to move through already-cleared air.
    G1/G2/G3 cutting move (is_rapid=False): only the defined cutting edge,
        z in [0, tool.cutting_length], is allowed to touch material (that's
        the normal carve). Everything above it — shank, and holder if
        present — must not: z in (tool.cutting_length, total_length +
        holder.gauge_length] is checked.

    Returns the first hit found (stops sampling immediately — this is a
    yes/no safety check, not a full collision map) or None if the segment
    is clear.
    """
    seg_vec = (end - start).astype("f4")
    vs = voxel_size

    total_len = tool.total_length if tool.total_length > 0.0 else (
        tool.cutting_length if tool.cutting_length > 0.0 else 999.0
    )
    holder_len = holder.gauge_length if holder is not None else 0.0
    full_len = total_len + holder_len
    z_check_lo = 0.0 if is_rapid else tool.cutting_length

    if full_len <= z_check_lo:
        return None  # nothing above the allowed cutting zone to check

    # ── Sample positions (same density rule as carve_segment) ─────────────
    xy_len = float(np.linalg.norm(seg_vec[:2]))
    z_len  = float(abs(seg_vec[2]))
    n_xy   = int(np.ceil(xy_len / (vs * 0.5))) + 1
    n_z    = int(np.ceil(z_len / max(full_len - z_check_lo, vs))) + 1
    n_samples = max(2, n_xy, n_z)
    ts  = np.linspace(0.0, 1.0, n_samples, dtype="f4")
    pts = start + ts[:, np.newaxis] * seg_vec

    # ── Sweep bounding box in voxel indices ────────────────────────────────
    origin = bbox.origin()
    nz, ny, nx = material.shape

    max_r = tool.radius
    if holder is not None and holder.profile:
        max_r = max(max_r, max(r for _, r in holder.profile))

    min_pt = pts.min(axis=0)
    max_pt = pts.max(axis=0)

    sweep_min = np.array([
        min_pt[0] - max_r - vs,
        min_pt[1] - max_r - vs,
        min_pt[2] + z_check_lo - vs,
    ], dtype="f4")
    sweep_max = np.array([
        max_pt[0] + max_r  + vs,
        max_pt[1] + max_r  + vs,
        max_pt[2] + full_len + vs,
    ], dtype="f4")

    ix0 = int(np.clip(np.floor((sweep_min[0] - origin[0]) / vs), 0, nx))
    ix1 = int(np.clip(np.ceil ((sweep_max[0] - origin[0]) / vs), 0, nx))
    iy0 = int(np.clip(np.floor((sweep_min[1] - origin[1]) / vs), 0, ny))
    iy1 = int(np.clip(np.ceil ((sweep_max[1] - origin[1]) / vs), 0, ny))
    iz0 = int(np.clip(np.floor((sweep_min[2] - origin[2]) / vs), 0, nz))
    iz1 = int(np.clip(np.ceil ((sweep_max[2] - origin[2]) / vs), 0, nz))

    if ix0 >= ix1 or iy0 >= iy1 or iz0 >= iz1:
        return None  # segment's checked envelope lies entirely outside the grid

    xs = origin[0] + (np.arange(ix0, ix1, dtype="f4") + 0.5) * vs
    ys = origin[1] + (np.arange(iy0, iy1, dtype="f4") + 0.5) * vs
    zs = origin[2] + (np.arange(iz0, iz1, dtype="f4") + 0.5) * vs

    mat_box = material[iz0:iz1, iy0:iy1, ix0:ix1]   # (lz, ly, lx) view

    for pt in pts:
        tx, ty, tz = float(pt[0]), float(pt[1]), float(pt[2])

        dx = xs - tx
        dy = ys - ty
        r2_xy = dx[np.newaxis, :] ** 2 + dy[:, np.newaxis] ** 2   # (ly, lx)

        iz_abs_lo = max(iz0, int(np.floor((tz + z_check_lo - origin[2]) / vs)))
        iz_abs_hi = min(iz1, int(np.ceil ((tz + full_len  - origin[2]) / vs)) + 1)
        if iz_abs_lo >= iz_abs_hi:
            continue

        iz_range = np.arange(iz_abs_lo, iz_abs_hi, dtype=np.int32)
        iz_local = iz_range - iz0
        local_zs = zs[iz_local] - tz

        valid = (local_zs >= z_check_lo) & (local_zs <= full_len)
        iz_local_v = iz_local[valid]
        local_zs_v = local_zs[valid]
        if len(iz_local_v) == 0:
            continue

        radii = _combined_radius_at_array(local_zs_v, tool, holder, total_len)
        r2_z  = (radii ** 2)[:, np.newaxis, np.newaxis]           # (n_valid, 1, 1)
        inside = r2_xy[np.newaxis] <= r2_z                        # (n_valid, ly, lx)

        mat_slices = mat_box[iz_local_v]                          # (n_valid, ly, lx)
        hit_mask = inside & (mat_slices == 255)
        if not hit_mask.any():
            continue

        zi, yi, xi = (int(v) for v in np.argwhere(hit_mask)[0])
        world_pt = np.array([xs[xi], ys[yi], zs[iz_local_v[zi]]], dtype="f4")
        kind: Literal["rapid", "shank", "holder"]
        if is_rapid:
            kind = "rapid"
        elif local_zs_v[zi] > total_len:
            kind = "holder"
        else:
            kind = "shank"
        return CollisionHit(point=world_pt, kind=kind)

    return None
