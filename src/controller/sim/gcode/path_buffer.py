"""sim/gcode/path_buffer.py — Arc-length parameterized path representation."""
from __future__ import annotations
import numpy as np
from controller.sim.gcode.motion_planner import (
    MotionSegment, LinearSegment, ArcSegment, HelixSegment
)


class PathBuffer:

    def __init__(self, segments: list[MotionSegment], arc_tess_deg: float = 1.0):
        # Local lists — not retained after conversion
        positions:   list[np.ndarray] = [np.zeros(3)]
        arc_lengths: list[float]      = [0.0]
        feed_rates:  list[float]      = [0.0]
        line_ids:    list[int]        = [0]
        total_s = 0.0

        max_linear_step = 1.0

        for seg in segments:
            pts, feeds = PathBuffer._tessellate(seg, arc_tess_deg)

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

    @staticmethod
    def _tessellate(seg, tess_deg):
        if isinstance(seg, LinearSegment):
            return PathBuffer._tessellate_linear(seg)
        if isinstance(seg, ArcSegment):
            return PathBuffer._tessellate_arc(seg, tess_deg)
        if isinstance(seg, HelixSegment):
            return PathBuffer._tessellate_helix(seg, tess_deg)
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
    def _tessellate_arc(seg: ArcSegment, tess_deg: float):
        a_s = np.arctan2(seg.start[1] - seg.center[1], seg.start[0] - seg.center[0])
        a_e = np.arctan2(seg.end[1]   - seg.center[1], seg.end[0]   - seg.center[0])
        if seg.clockwise:
            if a_e >= a_s: a_e -= 2 * np.pi
        else:
            if a_e <= a_s: a_e += 2 * np.pi
        n      = max(2, int(np.degrees(abs(a_e - a_s)) / tess_deg))
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
    def _tessellate_helix(seg: HelixSegment, tess_deg: float):
        a_s = np.arctan2(seg.start[1] - seg.center[1], seg.start[0] - seg.center[0])
        a_e = np.arctan2(seg.end[1]   - seg.center[1], seg.end[0]   - seg.center[0])
        if seg.clockwise:
            if a_e >= a_s: a_e -= 2 * np.pi
        else:
            if a_e <= a_s: a_e += 2 * np.pi
        n      = max(2, int(np.degrees(abs(a_e - a_s)) / tess_deg))
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
