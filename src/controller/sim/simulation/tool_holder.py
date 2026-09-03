"""
sim/simulation/tool_holder.py — HolderProfile: tool-holder ("Werkzeugaufnahme")
geometry as a simple piecewise-linear solid of revolution.

Sits directly above a tool's non-cutting geometry in the same Z convention
ToolDefinition.profile_radius_at() uses (z=0 at the tool tip, positive
toward the spindle) — but with its OWN local z=0 at the point where it
starts, i.e. z_local = z_global - tool.total_length. A HolderProfile is
purely a shape description; which tool it is currently attached to is a
per-tool assignment stored in the tool database (tools.holder_preset), not
part of this dataclass.

Unlike ToolDefinition.profile_radius_at() (closed-form curves per tool
type), a holder's outline is stored as a handful of (z, radius) control
points and linearly interpolated between them — good enough for the coarse,
mostly-cylindrical/conical outlines of collet chucks and steep-taper
holders, and trivially editable/extensible later from a future Toolpage
without touching code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class HolderProfile:
    """A tool-holder outline: named preset + piecewise-linear radius profile.

    Parameters
    ----------
    name :
        Preset name, e.g. "ER32", "SK40" — primary key in the tool_holders
        DB table and the value stored in tools.holder_preset.
    kind :
        Coarse category, e.g. "ER_COLLET", "SK", "BT" — informational, not
        used by the geometry/collision code (only `profile` is).
    gauge_length :
        Overall length of the holder from its attachment plane (z_local=0)
        to its far (spindle-side) end, mm. Informational + the natural
        upper bound for `profile`'s z range.
    profile :
        (z_local, radius) control points, mm, sorted by ascending z_local.
        z_local=0 is where the holder starts (tool.total_length in the
        combined tool+holder mesh/collision geometry); radius is the
        holder's outer radius at that height. Must have at least one point.
    """
    name: str
    kind: str
    gauge_length: float
    profile: list[tuple[float, float]] = field(default_factory=list)

    def radius_at(self, z_local: float) -> float:
        """Radius at *z_local* mm above the attachment plane.

        0 below the first control point, the last control point's radius
        beyond the last one (flat cap) — same "extend the last known value"
        convention ToolDefinition.profile_radius_at() uses past its own
        defined range.
        """
        if not self.profile:
            return 0.0
        zs = [p[0] for p in self.profile]
        rs = [p[1] for p in self.profile]
        return float(np.interp(z_local, zs, rs, left=0.0, right=rs[-1]))

    def radius_at_array(self, z_arr: np.ndarray) -> np.ndarray:
        """Vectorised version of radius_at() — see its docstring."""
        if not self.profile:
            return np.zeros_like(z_arr, dtype="f4")
        zs = np.array([p[0] for p in self.profile], dtype="f4")
        rs = np.array([p[1] for p in self.profile], dtype="f4")
        return np.interp(z_arr, zs, rs, left=0.0, right=float(rs[-1])).astype("f4")


# ── Standard presets ──────────────────────────────────────────────────────────
# Representative, coarse dimensions (mm) — a simple neck-then-flare/flange
# taper, good enough for display + collision purposes. Not exact ISO/DIN
# figures; refine per-holder later once a Toolpage exists to edit these.
#
# z_local=0 is the tool-side end (attaches at tool.total_length); z_local
# increases toward the spindle.
STANDARD_HOLDERS: dict[str, HolderProfile] = {
    "ER16": HolderProfile("ER16", "ER_COLLET", gauge_length=45.0, profile=[
        (0.0, 8.0), (15.0, 10.0), (18.0, 17.0), (45.0, 17.0),
    ]),
    "ER20": HolderProfile("ER20", "ER_COLLET", gauge_length=50.0, profile=[
        (0.0, 10.0), (18.0, 12.5), (22.0, 21.0), (50.0, 21.0),
    ]),
    "ER25": HolderProfile("ER25", "ER_COLLET", gauge_length=55.0, profile=[
        (0.0, 12.0), (20.0, 14.5), (25.0, 26.0), (55.0, 26.0),
    ]),
    "ER32": HolderProfile("ER32", "ER_COLLET", gauge_length=65.0, profile=[
        (0.0, 15.0), (25.0, 17.5), (30.0, 33.0), (65.0, 33.0),
    ]),
    "SK30": HolderProfile("SK30", "SK", gauge_length=50.0, profile=[
        (0.0, 11.0), (35.0, 21.0), (42.0, 23.0), (46.0, 23.0), (50.0, 15.0),
    ]),
    "SK40": HolderProfile("SK40", "SK", gauge_length=68.0, profile=[
        (0.0, 13.0), (48.0, 28.5), (58.0, 31.5), (63.0, 31.5), (68.0, 20.0),
    ]),
    "BT30": HolderProfile("BT30", "BT", gauge_length=48.5, profile=[
        (0.0, 11.0), (33.0, 21.0), (40.0, 23.0), (44.5, 23.0), (48.5, 15.0),
    ]),
    "BT40": HolderProfile("BT40", "BT", gauge_length=65.5, profile=[
        (0.0, 13.0), (46.0, 28.5), (56.0, 31.5), (61.0, 31.5), (65.5, 20.0),
    ]),
}
