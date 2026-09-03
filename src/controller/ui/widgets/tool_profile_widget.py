"""
ui/widgets/tool_profile_widget.py — ToolProfileWidget: a stylised, live 2D
side-view silhouette of a tool (+ its holder, if assigned), for
ToolCardWidget's expanded body (tool_card_widget.py).

Pure QPainter/QPainterPath — no GL, no mesh. Reuses the exact same
geometry source sim/simulation/tool_mesh.py's 3D solid-of-revolution
already uses (ToolDefinition.profile_radius_at_array()), just projected
into 2D instead of revolved into a mesh, and the same cutting/shank/holder
color split tool_mesh.py's build_tool_mesh() bakes per-vertex (see its
COLOR_CUTTING/COLOR_SHANK/COLOR_HOLDER — this widget uses the identical
RGB values, just as QColor hex instead of float triples).

Orientation: per the spec, the holder/shank ("Aufspannung") is on the
LEFT, the cutting tip on the RIGHT — the mirror image of
ToolDefinition.profile_radius_at()'s own z convention (z=0 at the tip,
increasing toward the shank/holder), so z maps to DECREASING x here.

Scaling & anchoring: ONE uniform scale factor applies to both axes
(min(width-fit, height-fit)) — never independent X/Y scales, which would
distort the holder's true proportions. The holder's far/spindle end
(HolderProfile's z_local=gauge_length, see tool_holder.py's docstring —
z_local=0 is the tool-side attachment plane) is pinned to the LEFT margin
always, regardless of which scale limit binds; the tip grows to the right
from there. When height is the binding constraint, the whole drawing
shrinks — never just squeezed in X — and the holder stays anchored left
rather than the drawing being horizontally re-centred, matching "Halter
sitzt fest im linken Bereich".

Fill geometry: one closed QPainterPath per colour zone (cutting / shank /
[holder]), not one quad per sample pair — a per-quad approach (the
previous implementation) leaves faint antialiased seams between ~220
adjacent same-coloured polygons; a single continuous path per zone has no
internal seams. The cutting/shank boundary is closed with an exact,
shared point at z=min(tool.cutting_length, total_length) so the two zones
meet without a gap; the shank/holder boundary is a real, intentional edge
(the physical tool-to-holder interface), not an artifact.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.sim.simulation.tool_holder import HolderProfile

# Same values as sim/simulation/tool_mesh.py's build_tool_mesh() defaults
# (there as float 0-1 triples; here as the equivalent QColor hex) — keeps
# this 2D preview visually consistent with the 3D viewport's tool mesh.
_COLOR_CUTTING = QColor("#FFD700")   # gold
_COLOR_SHANK   = QColor("#808080")   # neutral grey
_COLOR_HOLDER  = QColor("#59616B")   # steel grey

_SAMPLES_TOOL   = 160
_SAMPLES_HOLDER = 60
_MARGIN_X = 24
_MARGIN_Y = 20


class ToolProfileWidget(QWidget):
    """Live 2D tool silhouette. Call set_tool() whenever the tool (or a
    transient preview built from unsaved form values — see
    ToolCardWidget._push_live_profile()) changes; no other signal wiring
    needed, it's a pure pull/redraw on each call."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tool: ToolDefinition | None = None
        self._holder: HolderProfile | None = None
        self.setMinimumHeight(180)

    def set_tool(self, tool: ToolDefinition | None, holder: HolderProfile | None = None) -> None:
        self._tool = tool
        self._holder = holder
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        tool = self._tool
        if tool is None:
            return

        total_len = tool.total_length if tool.total_length > 0.0 else max(tool.cutting_length, 1.0)
        holder = self._holder if (self._holder is not None and self._holder.profile) else None
        holder_len = holder.gauge_length if holder is not None else 0.0
        full_len = total_len + holder_len
        if full_len <= 0.0:
            return

        w, h = self.width(), self.height()
        avail_w = max(1, w - 2 * _MARGIN_X)
        avail_h = max(1, h - 2 * _MARGIN_Y)

        zs_tool = np.linspace(0.0, total_len, _SAMPLES_TOOL).astype("f4")
        rs_tool = tool.profile_radius_at_array(zs_tool)
        tool_pts: list[tuple[float, float]] = list(zip(
            (float(z) for z in zs_tool), (float(r) for r in rs_tool),
        ))

        holder_pts: list[tuple[float, float]] = []
        if holder is not None:
            zs_h_local = np.linspace(0.0, holder_len, _SAMPLES_HOLDER).astype("f4")
            rs_h = holder.radius_at_array(zs_h_local)
            holder_pts = [
                (total_len + float(zl), float(r)) for zl, r in zip(zs_h_local, rs_h)
            ]

        max_r = max((r for _, r in tool_pts + holder_pts), default=1e-6)
        max_r = max(max_r, 1e-6)
        # ONE scale for both axes — never independent X/Y (that's what
        # distorted the holder before). Whichever dimension is more
        # constraining (width-to-fit-length vs. height-to-fit-radius)
        # wins; the whole drawing shrinks together when needed.
        scale = min(avail_w / full_len, avail_h / (2.0 * max_r))
        cy = h / 2.0

        def x_of(z: float) -> float:
            # Holder's far/spindle end (z=full_len) pinned to the LEFT
            # margin; z=0 (tip) grows to the right from there.
            return _MARGIN_X + (full_len - z) * scale

        def y_of(r: float) -> float:
            return cy - r * scale

        def zone_path(points: list[tuple[float, float]]) -> QPainterPath:
            """One closed path per colour zone — upper contour forward,
            mirrored lower contour backward — instead of one quad per
            sample pair, so there are no antialiased seams within a zone
            (see module docstring)."""
            path = QPainterPath()
            if len(points) < 2:
                return path
            path.moveTo(x_of(points[0][0]), y_of(points[0][1]))
            for z, r in points[1:]:
                path.lineTo(x_of(z), y_of(r))
            for z, r in reversed(points):
                path.lineTo(x_of(z), 2 * cy - y_of(r))
            path.closeSubpath()
            return path

        # Split the tool samples into cutting/shank zones at an exact,
        # shared boundary point (not just the nearest existing sample) so
        # the two zones' paths meet without a gap or overlap.
        cut_len = min(tool.cutting_length, total_len)
        cutting_pts = [(z, r) for z, r in tool_pts if z <= cut_len]
        shank_pts   = [(z, r) for z, r in tool_pts if z >= cut_len]
        if cutting_pts and cutting_pts[-1][0] != cut_len:
            r_at_cut = float(tool.profile_radius_at(cut_len))
            cutting_pts.append((cut_len, r_at_cut))
            shank_pts.insert(0, (cut_len, r_at_cut))

        painter.setPen(Qt.PenStyle.NoPen)
        if cutting_pts:
            painter.setBrush(_COLOR_CUTTING)
            painter.drawPath(zone_path(cutting_pts))
        if shank_pts:
            painter.setBrush(_COLOR_SHANK)
            painter.drawPath(zone_path(shank_pts))
        if holder_pts:
            painter.setBrush(_COLOR_HOLDER)
            painter.drawPath(zone_path(holder_pts))

        # Faint centreline for orientation (horizontal — unrelated to the
        # vertical seam artifacts the per-zone paths above eliminate).
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(_MARGIN_X, cy), QPointF(w - _MARGIN_X, cy))
