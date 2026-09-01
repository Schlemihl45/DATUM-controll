"""sim/simulation/player.py — SimulationPlayer: time-based position along an arc-length path."""
import time
import numpy as np
from controller.sim.gcode.path_buffer import PathBuffer

class SimulationPlayer:

    def __init__(self, path: PathBuffer):
        self._path       = path
        self._s          = 0.0
        self._running    = False
        self._last_t     = None
        self.speed_scale = 1.0

    def play(self):
        self._running = True
        self._last_t  = time.perf_counter()

    def pause(self):
        self._running = False

    def reset(self):
        self._running = False
        self._s       = 0.0

    def seek(self, fraction: float):
        self._s = float(np.clip(fraction, 0.0, 1.0)) * self._path.total_length

    def current_s(self) -> float:
        """Current arc-length for Viewport.set_progress()."""
        return self._s

    def tick(self) -> tuple[np.ndarray, int, float]:
        """Returns (position, line_index, arc_length_s) — one searchsorted."""
        now = time.perf_counter()
        if self._running and self._last_t is not None:
            dt = now - self._last_t
            feed = self._path.feed_at(self._s)
            if feed < 1e-6:
                feed = 5000.0
            self._s += (feed / 60.0) * dt * self.speed_scale
            self._s = min(self._s, self._path.total_length)
            if self._s >= self._path.total_length:
                self._running = False
        self._last_t = now

        # One lookup, triple use
        i, t = self._path._index_and_t(self._s)
        pos = self._path.points[i] + t * (self._path.points[i + 1] - self._path.points[i])
        line = int(self._path.line_ids[i + 1])
        return pos, line, self._s

    def current_position(self) -> np.ndarray:
        return self._path.position_at(self._s)

    def current_line(self) -> int:
        return self._path.line_at(self._s)

    def progress(self) -> float:
        if self._path.total_length < 1e-9:
            return 0.0
        return self._s / self._path.total_length

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_finished(self) -> bool:
        return not self._running and self._s >= self._path.total_length - 1e-6
