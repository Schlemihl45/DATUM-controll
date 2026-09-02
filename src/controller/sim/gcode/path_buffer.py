"""sim/gcode/path_buffer.py — Arc-length parameterized path representation."""
from __future__ import annotations
import numpy as np
from controller.sim.gcode.motion_planner import (
    MotionSegment, LinearSegment, ArcSegment, HelixSegment
)


class PathBuffer:

    def __init__(self, segments: list[MotionSegment], max_step_mm: float = 1.0):
        # Local lists — not retained after conversion
        positions:   list[np.ndarray] = [np.zeros(3)]
        arc_lengths: list[float]      = [0.0]
        feed_rates:  list[float]      = [0.0]
        line_ids:    list[int]        = [0]
        total_s = 0.0

        for seg in segments:
            pts, feeds = PathBuffer._tessellate(seg, max_step_mm)

            for p, f in zip(pts, feeds):
                step     = float(np.linalg.norm(p - positions[-1]))
                total_s += step
                positions.append(p)
                arc_lengths.append(total_s)
                feed_rates.append(f)
                line_ids.append(seg.line_index)   # line_index, not line_number

        # Only numpy arrays remain — no doubled RAM usage
        self.points:        np.ndarray = np.array(positions,   dtype='f4')
        self.arc_lengths:   np.ndarray = np.array(arc_lengths, dtype='f4')
        self.feed_rates:    np.ndarray = np.array(feed_rates,  dtype='f4')
        self.line_ids:      np.ndarray = np.array(line_ids,    dtype='i4')
        self.total_length:  float      = total_s

    # ── Queries ───────────────────────────────────────────────────────────────

    def position_at(self, s: float) -> np.ndarray:
        i, t = self._index_and_t(s)
        return self.points[i] + t * (self.points[i + 1] - self.points[i])

    def feed_at(self, s: float) -> float:
        i, _ = self._index_and_t(s)
        return float(self.feed_rates[i + 1])

    def line_at(self, s: float) -> int:
        i, _ = self._index_and_t(s)
        return int(self.line_ids[i + 1])

    def find_nearest(self, pos: np.ndarray) -> tuple[float, int]:
        starts  = self.points[:-1]
        ends    = self.points[1:]
        segs    = ends - starts
        lens_sq = np.einsum('ij,ij->i', segs, segs)
        t       = np.einsum('ij,ij->i', pos - starts, segs)
        t      /= np.maximum(lens_sq, 1e-12)
        t       = np.clip(t, 0.0, 1.0)
        nearest = starts + t[:, None] * segs
        dist_sq = np.einsum('ij,ij->i', pos - nearest, pos - nearest)
        i       = int(np.argmin(dist_sq))
        s       = self.arc_lengths[i] + t[i] * float(np.sqrt(lens_sq[i]))
        return float(s), int(self.line_ids[i])

    # ── Internal ──────────────────────────────────────────────────────────────

    def _index_and_t(self, s: float) -> tuple[int, float]:
        s   = float(np.clip(s, 0.0, self.total_length))
        i   = int(np.searchsorted(self.arc_lengths, s, side='right')) - 1
        i   = int(np.clip(i, 0, len(self.points) - 2))
        seg = self.arc_lengths[i + 1] - self.arc_lengths[i]
        t   = (s - self.arc_lengths[i]) / seg if seg > 1e-9 else 0.0
        return i, float(t)

    # ── Tessellation ──────────────────────────────────────────────────────────

    # Arcs/helices get at least one point every this many degrees, regardless
    # of how coarse max_step_mm alone would allow for a small radius.
    # Without this floor, a very small-radius full circle (e.g. a 0.25 mm
    # helical-drilling relief cut) would tessellate to as few as 2 points,
    # whose *chord* sum can undershoot the true arc length by a large margin
    # (a 2-point circle: chord sum = 4r vs. true circumference 2*pi*r, a 36%
    # deficit) — and PathBuffer.total_length (built from those chords) is
    # exactly what SimulationPlayer paces wall-clock playback against, so a
    # large deficit there would desync tool speed from the programmed feed
    # rate. 30 degrees keeps that error under ~1% (n=12) at negligible extra
    # cost — still a ~30x reduction from the old fixed-1deg-step count for a
    # tiny circle, while barely changing anything for larger arcs (see
    # _tessellate_arc/_tessellate_helix below).
    _ARC_ANGLE_FLOOR_DEG = 30.0

    @staticmethod
    def _tessellate(seg, max_step_mm):
        if isinstance(seg, LinearSegment):
            return PathBuffer._tessellate_linear(seg, max_step_mm)
        if isinstance(seg, ArcSegment):
            return PathBuffer._tessellate_arc(seg, max_step_mm)
        if isinstance(seg, HelixSegment):
            return PathBuffer._tessellate_helix(seg, max_step_mm)
        return [], []

    @staticmethod
    def _tessellate_linear(seg: LinearSegment, max_step_mm: float = 1.0):
        vec = seg.end - seg.start
        length = float(np.linalg.norm(vec))
        f = 0.0 if seg.is_rapid else seg.feed_rate

        if length < 1e-9:
            return [seg.end.copy()], [f]

        # One new point every max_step_mm
        n = max(1, int(np.ceil(length / max_step_mm)))
        ts = np.linspace(0.0, 1.0, n + 1)[1:]  # exclude t=0 (start already present)
        points = [seg.start + t * vec for t in ts]

        return points, [f] * len(points)

    @staticmethod
    def _arc_point_count(radius: float, sweep: float, max_step_mm: float) -> int:
        """
        Number of tessellation points for a bogen/helix sweep of *sweep*
        radians at *radius*.

        Sized from PHYSICAL arc length (radius * sweep), mirroring
        _tessellate_linear's max_step_mm — not from a fixed angular step.
        A fixed-degree step (the old scheme) makes physical step size
        proportional to radius, so a small-radius arc (e.g. a 0.25 mm
        helical-drilling relief circle) gets exactly as many points as a
        large one despite being physically tiny: a full circle at 1 degree
        steps is always 360 points, whether its radius is 0.25 mm or 250 mm.
        For a 0.25 mm circle that's a physical step of ~0.0044 mm — far
        finer than any voxel resolution in use — and each of those 360
        points becomes a separate carve_segment() call downstream
        (VoxelSimController.on_tick), each paying the same fixed per-call
        overhead regardless of how little segment it actually covers. Basing
        n on arc length instead collapses that to a handful of points for
        small arcs while leaving large-radius arcs essentially unchanged
        (their physical-length-driven point count was already comparable to
        the old fixed-degree scheme). The _ARC_ANGLE_FLOOR_DEG term bounds
        the chord-length approximation error for very coarse cases (see its
        docstring above).
        """
        arc_length = radius * sweep
        return max(
            2,
            int(np.ceil(arc_length / max_step_mm)),
            int(np.ceil(np.degrees(sweep) / PathBuffer._ARC_ANGLE_FLOOR_DEG)),
        )

    @staticmethod
    def _tessellate_arc(seg: ArcSegment, max_step_mm: float):
        a_s = np.arctan2(seg.start[1] - seg.center[1], seg.start[0] - seg.center[0])
        a_e = np.arctan2(seg.end[1]   - seg.center[1], seg.end[0]   - seg.center[0])
        if seg.clockwise:
            if a_e >= a_s: a_e -= 2 * np.pi
        else:
            if a_e <= a_s: a_e += 2 * np.pi
        n      = PathBuffer._arc_point_count(seg.radius, abs(a_e - a_s), max_step_mm)
        angles = np.linspace(a_s, a_e, n + 1)[1:]
        points = []
        for a in angles:
            p    = seg.center.copy()
            p[0] += seg.radius * np.cos(a)
            p[1] += seg.radius * np.sin(a)
            p[2]  = seg.start[2]
            points.append(p)
        return points, [seg.feed_rate] * len(points)

    @staticmethod
    def _tessellate_helix(seg: HelixSegment, max_step_mm: float):
        a_s = np.arctan2(seg.start[1] - seg.center[1], seg.start[0] - seg.center[0])
        a_e = np.arctan2(seg.end[1]   - seg.center[1], seg.end[0]   - seg.center[0])
        if seg.clockwise:
            if a_e >= a_s: a_e -= 2 * np.pi
        else:
            if a_e <= a_s: a_e += 2 * np.pi
        n      = PathBuffer._arc_point_count(seg.radius, abs(a_e - a_s), max_step_mm)
        angles = np.linspace(a_s, a_e, n + 1)[1:]
        z_vals = np.linspace(seg.start[2], seg.end[2], n + 1)[1:]
        points = []
        for a, z in zip(angles, z_vals):
            p    = seg.center.copy()
            p[0] += seg.radius * np.cos(a)
            p[1] += seg.radius * np.sin(a)
            p[2]  = z
            points.append(p)
        return points, [seg.feed_rate] * len(points)
