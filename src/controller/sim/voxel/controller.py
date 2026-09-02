"""
sim/voxel/controller.py — VoxelSimController: Python orchestrator for
voxel-based material-removal simulation.

Architecture
────────────
A worker thread receives movement jobs (path-buffer segments) from a Queue and
calls the C++ subtract_segment() function on each. This keeps the GUI thread
free — the main thread only enqueues jobs and reads back mesh data.

                 main thread (GUI)                  worker thread
                 ─────────────────                  ─────────────
  on_tick(s) ──► check high-water-mark
                 enqueue new segments ──► Queue ──► subtract_segment(C++)
                                                    every N steps: get_mesh(C++)
                                                    _dirty.set() ─────────────────►
  get_mesh_if_dirty() ◄──────────────────────────── mesh numpy arrays

High-Water-Mark (max_processed_s)
──────────────────────────────────
Prevents double-subtraction when scrubbing backwards and replaying:
  • Advance past the watermark → enqueue segment, advance watermark.
  • Advance within the watermark (after a rewind) → no-op for CSG.
  • reset() → reinit grid, watermark = 0.

Thread safety
─────────────
  _queue         — stdlib Queue (thread-safe).
  _lock          — threading.Lock protecting _mesh_data and _dirty.
  _grid_handle   — only ever accessed from the worker thread after init.
"""
from __future__ import annotations

import threading
import queue
import logging
from typing import Optional

import numpy as np

from controller.sim.voxel.stock import StockDefinition, BoundingBox

logger = logging.getLogger(__name__)

# How many subtraction steps between mesh updates. Lower = more responsive but
# heavier (volumeToMesh is expensive). 5–10 is a good default.
_MESH_UPDATE_EVERY = 6

# Sentinel put into the queue to tell the worker to flush pending work and
# reinitialise the grid.
_SENTINEL_RESET = object()

# ── Try to import the C++ extension ──────────────────────────────────────────
try:
    from controller.sim.voxel import voxel_mod as _voxel_mod
    _VOXEL_AVAILABLE = True
except ImportError:
    _voxel_mod = None          # type: ignore[assignment]
    _VOXEL_AVAILABLE = False
    logger.warning(
        "voxel_mod C++ extension not found — "
        "voxel material removal will be disabled. "
        "Build it with: cd src/controller/sim/voxel/ext && cmake -B build . && cmake --build build"
    )


class VoxelSimController:
    """Manages voxel-based material removal for a single G-code program.

    Owns the worker thread and the OpenVDB grid handle.  The main thread
    interacts only via on_tick(), reset(), and get_mesh_if_dirty().

    Args:
        stock:       StockDefinition describing the raw blank.
        path_points: The PathBuffer.points array (Nx3 float32) — used to
                     look up segment endpoints by arc-length index.
        path_arc_lengths: The PathBuffer.arc_lengths array — parallel to points.
        tool:        ToolDefinition for the current tool.
    """

    def __init__(
        self,
        stock: StockDefinition,
        path_points:      np.ndarray,
        path_arc_lengths: np.ndarray,
        tool,                               # ToolDefinition
    ) -> None:
        self._stock            = stock
        self._path_points      = path_points      # (N, 3) float32
        self._path_arc_lengths = path_arc_lengths  # (N,)   float32
        self._tool             = tool

        # High-water mark: arc-length up to which CSG has already been applied
        self._max_processed_s: float = 0.0

        # Arc-length index corresponding to max_processed_s
        self._max_idx: int = 0

        # Worker thread
        self._queue:  queue.Queue = queue.Queue()
        self._worker: threading.Thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="voxel-worker"
        )

        # Shared mesh result (worker writes, main reads under lock)
        self._lock:      threading.Lock   = threading.Lock()
        self._mesh_data: Optional[tuple]  = None   # (verts, normals, indices) or None
        self._dirty:     bool             = False

        # Grid handle — lives entirely on the worker thread
        self._grid_handle = None

        self._worker.start()

        # Enqueue the initial grid construction
        self._enqueue_init()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True when the C++ voxel_mod extension is installed."""
        return _VOXEL_AVAILABLE

    def on_tick(self, current_s: float) -> None:
        """Called from the GUI thread ~30 fps with the current arc-length.

        Enqueues new CSG jobs for any path beyond the current high-water mark.
        Has no effect if current_s is within already-processed territory (rewind).

        Thread-safe (only touches self._max_processed_s and self._max_idx from
        the main thread; the queue is thread-safe).
        """
        if not _VOXEL_AVAILABLE:
            return
        if current_s <= self._max_processed_s:
            return   # rewind territory — no re-subtraction

        # Find path buffer indices from last watermark to current_s
        new_idx = int(
            np.searchsorted(self._path_arc_lengths, current_s, side='right')
        )
        new_idx = int(np.clip(new_idx, 0, len(self._path_points) - 1))

        # Enqueue each newly-covered path segment
        for i in range(self._max_idx, new_idx):
            start = self._path_points[i].tolist()
            end   = self._path_points[min(i + 1, len(self._path_points) - 1)].tolist()
            self._queue.put(("segment", start, end))

        self._max_processed_s = current_s
        self._max_idx         = new_idx

    def reset(self) -> None:
        """Reset the grid to the initial blank state and clear the watermark.

        Safe to call from the main thread at any time. Flushes pending jobs.
        """
        # Drop all pending work
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        self._max_processed_s = 0.0
        self._max_idx         = 0

        with self._lock:
            self._mesh_data = None
            self._dirty     = False

        self._enqueue_init()

    def get_mesh_if_dirty(self) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """Return (vertices, normals, indices) if the mesh was updated since last call.

        Clears the dirty flag. Returns None if no new mesh is available.
        Safe to call from the main thread; GPU upload is the caller's responsibility.
        """
        with self._lock:
            if not self._dirty or self._mesh_data is None:
                return None
            result      = self._mesh_data
            self._dirty = False
        return result

    def update_tool(self, tool) -> None:
        """Swap the tool used for subsequent CSG operations (T-command)."""
        self._tool = tool

    def stop(self) -> None:
        """Shut down the worker thread. Call when the simulation is closed."""
        self._queue.put(None)  # None = shutdown sentinel
        self._worker.join(timeout=2.0)

    # ── Worker thread ─────────────────────────────────────────────────────────

    def _enqueue_init(self) -> None:
        """Send an init job to the worker thread."""
        self._queue.put(("init", self._stock, self._tool))

    def _worker_loop(self) -> None:
        """Main loop of the worker thread.

        Processes jobs from the queue:
          ("init", stock, tool)          — reinitialise the grid
          ("segment", start, end)        — subtract one path segment
          None                           — shutdown
          _SENTINEL_RESET               — ignored (queue flush artifact)
        """
        step_count = 0

        while True:
            job = self._queue.get()

            if job is None:
                break   # clean shutdown

            if job is _SENTINEL_RESET:
                continue

            kind = job[0]

            if kind == "init":
                _, stock, tool = job
                self._worker_init_grid(stock, tool)
                step_count = 0

            elif kind == "segment":
                if self._grid_handle is None:
                    continue   # grid not ready yet
                _, start, end = job
                self._worker_subtract(start, end)
                step_count += 1

                # Throttle mesh updates to every _MESH_UPDATE_EVERY subtractions
                if step_count % _MESH_UPDATE_EVERY == 0:
                    self._worker_update_mesh()

    def _worker_init_grid(self, stock: StockDefinition, tool) -> None:
        """(Worker thread) Construct the initial OpenVDB grid."""
        try:
            from controller.sim.gcode.path_buffer import PathBuffer  # type guard
            bbox = stock.resolve()
        except Exception as exc:
            logger.error("VoxelSimController: failed to resolve stock bbox: %s", exc)
            self._grid_handle = None
            return

        try:
            self._grid_handle = _voxel_mod.init_stock(
                bbox.as_tuple(), stock.voxel_size
            )
            logger.debug("VoxelSimController: grid initialised  "
                         "bbox=%s  voxel=%.2fmm", bbox, stock.voxel_size)
        except Exception as exc:
            logger.error("VoxelSimController: init_stock failed: %s", exc)
            self._grid_handle = None
            return

        self._worker_update_mesh()

    def _worker_subtract(self, start: list, end: list) -> None:
        """(Worker thread) Subtract one path segment from the grid."""
        profile = self._build_tool_profile()
        try:
            _voxel_mod.subtract_segment(
                self._grid_handle,
                start, end,
                profile,
            )
        except Exception as exc:
            logger.warning("VoxelSimController: subtract_segment error: %s", exc)

    def _worker_update_mesh(self) -> None:
        """(Worker thread) Call get_mesh and store result for the main thread."""
        if self._grid_handle is None:
            return
        try:
            verts, normals, indices = _voxel_mod.get_mesh(self._grid_handle)
            with self._lock:
                self._mesh_data = (
                    np.array(verts,   dtype='f4'),
                    np.array(normals, dtype='f4'),
                    np.array(indices, dtype='u4'),
                )
                self._dirty = True
        except Exception as exc:
            logger.warning("VoxelSimController: get_mesh error: %s", exc)

    def _build_tool_profile(self):
        """Convert a Python ToolDefinition to a C++ ToolProfile object."""
        from controller.sim.simulation.tool_definition import ToolType as PToolType

        profile = _voxel_mod.ToolProfile()

        # Map Python ToolType enum → C++ int
        _type_map = {
            PToolType.ENDMILL:      _voxel_mod.ToolType.ENDMILL,
            PToolType.BALL_ENDMILL: _voxel_mod.ToolType.BALL_ENDMILL,
            PToolType.BULL_ENDMILL: _voxel_mod.ToolType.BULL_ENDMILL,
            PToolType.CHAMFER:      _voxel_mod.ToolType.CHAMFER,
            PToolType.DRILL:        _voxel_mod.ToolType.DRILL,
            PToolType.TAPER:        _voxel_mod.ToolType.TAPER,
        }
        profile.tool_type      = _type_map.get(self._tool.tool_type,
                                                _voxel_mod.ToolType.ENDMILL)
        profile.diameter       = float(self._tool.diameter)
        profile.corner_radius  = float(self._tool.corner_radius)
        profile.tip_angle      = float(self._tool.tip_angle)
        profile.taper_angle    = float(self._tool.taper_angle)
        profile.cutting_length = float(
            self._tool.cutting_length if self._tool.cutting_length > 0 else 20.0
        )
        return profile
