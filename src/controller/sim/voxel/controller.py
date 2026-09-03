"""
sim/voxel/controller.py — VoxelSimController.

Coordinates the High-Water-Mark path dispatch and CPU carving; GPU texture
upload is the caller's responsibility (``grid.upload_if_dirty()``).

High-Water-Mark algorithm
--------------------------
``_max_s`` / ``_max_idx`` track the furthest arc-length (and corresponding
path index) already carved.  Advancing past it carves new segments;
rewinding does not re-carve existing material (the tool cannot un-remove
chips).

Call ``reset()`` to rebuild the stock from scratch (e.g. after the user
presses the Reset button or changes voxel_size).

Thread safety
-------------
``on_tick()`` mutates ``_max_s``/``_max_idx`` and is NOT safe to call
concurrently from two threads.  ``DatumSimWidget`` calls it either
synchronously from the Qt main thread (small per-tick backlogs) or from a
single background worker thread (large backlogs) — never both at once: a
new call is only ever dispatched once any previous background worker has
fully finished, so at any instant there is exactly one caller. The GPU
upload (``grid.upload_if_dirty()``) must be called with the GL context
current — the caller (``DatumSimWidget._tick`` / ``_drive_voxel_carve`` /
``_force_carve_catchup``) is responsible for ``makeCurrent()``.
"""
from __future__ import annotations

import numpy as np

from controller.sim.voxel.gpu_grid   import GpuVoxelGrid
from controller.sim.voxel.carver     import VoxelCarver
from controller.sim.voxel.collision  import check_segment, CollisionHit
from controller.sim.voxel.stock      import StockDefinition, BoundingBox
from controller.sim.simulation.tool_definition import ToolDefinition
from controller.sim.simulation.tool_holder import HolderProfile


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
        path_line_ids:    np.ndarray | None = None,
    ) -> None:
        self._grid            = grid
        self._carver          = carver
        self._pts             = path_points       # (N, 3) float32
        self._arc             = path_arc_lengths  # (N,)   float32
        self._feeds           = path_feed_rates   # (N,)   float32 — 0.0 = G0 rapid
        self._line_ids        = path_line_ids     # (N,)   int32 | None — PathBuffer.line_ids
        self._tool            = tool
        self._holder          = None   # HolderProfile | None — see update_holder()
        self._collision_enabled = True
        self._hit = None   # CollisionHit | None — see collision_hit/clear_collision()

        # High-Water-Mark: last carved arc-length and corresponding path index
        self._max_s:   float = 0.0
        self._max_idx: int   = 0

    # ── Properties ─────────────────────────────────────────────���──────────────

    @property
    def grid(self) -> GpuVoxelGrid:
        return self._grid

    @property
    def max_s(self) -> float:
        """High-water-mark: arc-length (mm) up to which material has been carved."""
        return self._max_s

    @property
    def collision_hit(self):
        """The first unacknowledged collision found, or None. Set by
        on_tick()/carve_move(); once set, on_tick() stops advancing until
        clear_collision() or reset() is called — see their docstrings."""
        return self._hit

    def set_collision_enabled(self, v: bool) -> None:
        self._collision_enabled = v

    def clear_collision(self) -> None:
        """Acknowledge/dismiss the current collision_hit (if any) without
        touching the carved material or the High-Water-Mark — e.g. after
        the user reviews it and manually seeks/resumes past it. reset()
        calls this too, as part of rebuilding the stock from scratch."""
        self._hit = None

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
        if self._hit is not None:
            return False  # frozen at a collision — see clear_collision()

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
            is_rapid = self._feeds[i + 1] == 0.0

            if self._collision_enabled:
                hit = check_segment(
                    self._grid.material, self._grid.bbox, self._grid.voxel_size,
                    self._pts[i], self._pts[i + 1], self._tool, is_rapid, self._holder,
                )
                if hit is not None:
                    if self._line_ids is not None:
                        hit.line_number = int(self._line_ids[i + 1])
                    self._hit = hit
                    # Freeze the High-Water-Mark just before the colliding
                    # segment rather than at current_s — a later seek/replay
                    # up to this same s must re-encounter the same hit
                    # instead of silently treating it as already carved.
                    self._max_s   = float(self._arc[i])
                    self._max_idx = i
                    return carved_any

            if is_rapid:
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

    def carve_move(
        self,
        start:       np.ndarray,
        end:         np.ndarray,
        tool:        ToolDefinition,
        feed_rate:   float = 1.0,
        line_number: int = -1,
    ) -> bool:
        """
        Carve one real-machine move (position-based, not arc-length based).

        Used in MACHINE mode where the controller feeds live position updates
        instead of a pre-computed toolpath.  Does not touch ``_max_s`` or
        ``_max_idx`` (those are SIM-mode high-water-mark state).

        Parameters
        ----------
        start, end :
            World-space segment endpoints in mm.
        tool :
            Tool geometry to use for the carved volume.
        feed_rate :
            Feed rate of the move (mm/min).  Pass 0.0 or negative to mark a
            G0 rapid move — still collision-checked (rapids must not touch
            material at all — see collision.check_segment), just not carved.
        line_number :
            Best-known current G-code line, for collision_hit reporting —
            MACHINE mode has no tessellated path to look this up from, so
            the caller (DatumSimWidget, which already tracks it for
            _check_tool_change()) passes its own last-known value.

        Returns
        -------
        bool
            True if the grid was modified (i.e. ``grid.is_dirty`` is set).
            False if nothing was carved — including when a collision was
            just detected (check collision_hit).
        """
        is_rapid = feed_rate <= 0.0

        if self._collision_enabled and self._hit is None:
            hit = check_segment(
                self._grid.material, self._grid.bbox, self._grid.voxel_size,
                start, end, tool, is_rapid, self._holder,
            )
            if hit is not None:
                hit.line_number = line_number
                self._hit = hit
                return False

        if is_rapid or self._hit is not None:
            return False
        self._carver.carve_segment(start, end, tool)
        return self._grid.is_dirty

    def update_tool(self, tool: ToolDefinition) -> None:
        """Called when the simulation passes a T-command."""
        self._tool = tool

    def update_holder(self, holder: "HolderProfile | None") -> None:
        """Called alongside update_tool() when the active tool's assigned
        holder changes (including to None, for a tool with none assigned).
        Factored into collision checks (see collision.check_segment) —
        purely bookkeeping here, no geometry itself."""
        self._holder = holder

    def reset(self) -> None:
        """
        Rebuild the stock from scratch.

        Resets the material grid to fully solid, clears the High-Water-Mark
        and any pending collision_hit, and marks the grid dirty so the next
        upload pushes the full reset.
        """
        self._grid.reset()
        self.clear_collision()
        self._max_s   = 0.0
        self._max_idx = 0
