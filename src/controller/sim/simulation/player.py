"""sim/simulation/player.py — SimulationPlayer: time-based position along an arc-length path.

speed_scale controls playback direction and rate:
  • +1.0  — real-time forward (feed-rate mm/min ÷ 60 × dt)
  • +N    — N× faster forward
  •  0.0  — frozen (running but not moving)
  • -1.0  — real-time reverse
  • -N    — N× faster reverse

Playback stops naturally at either end (s ≥ total_length or s ≤ 0).
Use is_finished (forward end) and is_at_start (reverse end) to detect each.
"""
import time
import numpy as np
from controller.sim.gcode.path_buffer import PathBuffer, DEFAULT_RAPID_FEED_MM_MIN


class SimulationPlayer:

    def __init__(self, path: PathBuffer) -> None:
        self._path       = path
        self._s          = 0.0        # current arc-length position
        self._running    = False
        self._last_t: float | None = None
        self.speed_scale = 1.0        # negative = reverse

    # ── Controls ──────────────────────────────────────────────────────────────

    def play(self) -> None:
        self._running = True
        self._last_t  = time.perf_counter()

    def pause(self) -> None:
        self._running = False

    def reset(self) -> None:
        """Stop and rewind to the beginning."""
        self._running = False
        self._s       = 0.0

    def seek(self, fraction: float) -> None:
        """Jump to a position given as a 0-1 fraction of total path length."""
        self._s = float(np.clip(fraction, 0.0, 1.0)) * self._path.total_length

    # ── Per-frame update ──────────────────────────────────────────────────────

    def tick(self) -> tuple[np.ndarray, int, float]:
        """Advance the simulation by one real-time step.

        Returns:
            (position_xyz, line_index, arc_length_s)

        Negative speed_scale steps backwards. Playback stops automatically
        when either end of the path is reached.
        """
        now = time.perf_counter()

        if self._running and self._last_t is not None:
            dt   = now - self._last_t
            feed = self._path.feed_at(self._s)
            if feed < 1e-6:
                feed = DEFAULT_RAPID_FEED_MM_MIN   # default rapid feed when no feed set

            ds       = (feed / 60.0) * dt * self.speed_scale
            self._s += ds

            # Natural stop at either end
            if self._s >= self._path.total_length:
                self._s      = self._path.total_length
                self._running = False
            elif self._s <= 0.0:
                self._s      = 0.0
                self._running = False

        self._last_t = now

        # Single _index_and_t lookup, re-used for position + line
        i, t  = self._path._index_and_t(self._s)
        pos   = self._path.points[i] + t * (self._path.points[i + 1] - self._path.points[i])
        line  = int(self._path.line_ids[i + 1])
        return pos, line, self._s

    # ── State accessors ───────────────────────────────────────────────────────

    def current_s(self) -> float:
        """Current arc-length (for Viewport.set_progress)."""
        return self._s

    def current_position(self) -> np.ndarray:
        return self._path.position_at(self._s)

    def current_line(self) -> int:
        return self._path.line_at(self._s)

    def progress(self) -> float:
        """0-1 fraction of total path length."""
        if self._path.total_length < 1e-9:
            return 0.0
        return self._s / self._path.total_length

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_finished(self) -> bool:
        """True when forward playback reached the end of the program."""
        return not self._running and self._s >= self._path.total_length - 1e-6

    @property
    def is_at_start(self) -> bool:
        """True when reverse playback reached the beginning of the program."""
        return not self._running and self._s <= 1e-6
