"""
sim/voxel/controller.py — VoxelSimController.

Coordinates the High-Water-Mark path dispatch, CPU carving, and GPU
texture upload.  No worker thread — all operations run synchronously on
the Qt main thread, which keeps the code simple and correct.

High-Water-Mark algorithm
--------------------------
``_max_processed_s`` tracks the furthest arc-length that has already been
carved.  Advancing past it queues new segments; rewinding does not re-carve
existing material (the tool cannot un-remove chips).

Call ``reset()`` to rebuild the stock from scratch (e.g. after the user
presses the Reset button or changes voxel_size).

Thread safety
-------------
All methods must be called from the Qt main thread.  The GPU upload
(``grid.upload_if_dirty()``) must be called with the GL context current —
the caller (``DatumSimWidget._tick``) is responsible for ``makeCurrent()``.
"""
from __future__ import annotations

import numpy as np

from controller.sim.voxel.gpu_grid   import GpuVoxelGrid
from controller.sim.voxel.carver     import VoxelCarver
from controller.sim.voxel.stock      import StockDefinition, BoundingBox
from controller.sim.simulation.tool_definition import ToolDefinition


class VoxelSimController:
    """
    Drives voxel material removal in sync with the simulation player.

    Parameters
    ----------
    grid :
        The shared GPU grid.  Owned externally; this controller only writes.
    carver :
        The carver that modifies ``grid``.
    path_points :
        (N, 3) float32 array of world-space path positions.
    path_arc_lengths :
        (N,) float32 array of cumulative arc lengths for each path point.
    tool :
        Initial tool.  Update with :meth:`update_tool` on T-commands.
    """

    def __init__(
        self,
        grid:             GpuVoxelGrid,
        carver:           VoxelCarver,
        path_points:      np.ndarray,
        path_arc_lengths: np.ndarray,
        path_feed_rates:  np.ndarray,
        tool:             ToolDefinition,
    ) -> None:
        self._grid            = grid
        self._carver          = carver
        self._pts             = path_points       # (N, 3) float32
        self._arc             = path_arc_lengths  # (N,)   float32
        self._feeds           = path_feed_rates   # (N,)   float32 — 0.0 = G0 rapid
        self._tool            = tool

        # High-Water-Mark: last carved arc-length and corresponding path index
        self._max_s:   float = 0.0
        self._max_idx: int   = 0

    # ── Properties ─────────────────────────────────────────────���──────────────

    @property
    def grid(self) -> GpuVoxelGrid:
        return self._grid

    # ── Playback interface ────────────────────────────────────────────────────

    def on_tick(self, current_s: float) -> bool:
        """
        Called every render tick with the player's current arc-length.

        Finds path segments between ``_max_s`` and ``current_s``, carves
        them, and returns True if at least one segment was carved (so the
        caller knows to call ``grid.upload_if_dirty()``).

        Rewinding (current_s < _max_s) is a no-op — material is not
        restored.  Call :meth:`reset` to rebuild from scratch.
        """
        if current_s <= self._max_s or len(self._pts) < 2:
            return False

        # Find the path index range [_max_idx, new_idx)
        new_idx = int(np.searchsorted(self._arc, current_s, side="right"))
        new_idx = min(new_idx, len(self._pts) - 1)

        if new_idx <= self._max_idx:
            return False

        carved_any = False
        for i in range(self._max_idx, new_idx):
            # Skip G0 rapid moves — feed_rate == 0.0 marks rapid segments.
            # path_buffer.py convention: feed_rates[k] is the feed that
            # ARRIVED at point k (the move from k-1 → k).  So the segment
            # from i → i+1 is rapid when feeds[i+1] == 0.0.
            if self._feeds[i + 1] == 0.0:
                continue
            self._carver.carve_segment(
                self._pts[i],
                self._pts[i + 1],
                self._tool,
            )
            carved_any = True

        self._max_s   = current_s
        self._max_idx = new_idx
        return carved_any

    def update_tool(self, tool: ToolDefinition) -> None:
        """Called when the simulation passes a T-command."""
        self._tool = tool

    def reset(self) -> None:
        """
        Rebuild the stock from scratch.

        Resets the material grid to fully solid, clears the High-Water-Mark,
        and marks the grid dirty so the next upload pushes the full reset.
        """
        self._grid.reset()
        self._max_s   = 0.0
        self._max_idx = 0
