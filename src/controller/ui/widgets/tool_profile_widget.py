"""
ui/widgets/tool_profile_widget.py — ToolProfileWidget: a stylised, live 2D
side-view silhouette of a tool (+ its holder, if assigned), for
ToolDetailPage's centre panel.

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
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
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
    ToolDetailPage._push_live_profile()) changes; no other signal wiring
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
        scale_x = avail_w / full_len

        zs_tool = np.linspace(0.0, total_len, _SAMPLES_TOOL).astype("f4")
        rs_tool = tool.profile_radius_at_array(zs_tool)
        points: list[tuple[float, float]] = list(zip(
            (float(z) for z in zs_tool), (float(r) for r in rs_tool),
        ))

        if holder is not None:
            zs_h_local = np.linspace(0.0, holder_len, _SAMPLES_HOLDER).astype("f4")
            rs_h = holder.radius_at_array(zs_h_local)
            points += [
                (total_len + float(zl), float(r)) for zl, r in zip(zs_h_local, rs_h)
            ]

        max_r = max((r for _, r in points), default=1e-6)
        max_r = max(max_r, 1e-6)
        scale_y = (avail_h / 2) / max_r
        cy = h / 2.0

        def x_of(z: float) -> float:
            # z=0 (tip) at the right edge; increasing z moves LEFT.
            return (w - _MARGIN_X) - z * scale_x

        def y_of(r: float) -> float:
            return cy - r * scale_y

        painter.setPen(Qt.PenStyle.NoPen)
        for (z0, r0), (z1, r1) in zip(points, points[1:]):
            z_mid = (z0 + z1) / 2.0
            if z_mid <= tool.cutting_length:
                color = _COLOR_CUTTING
            elif z_mid <= total_len:
                color = _COLOR_SHANK
            else:
                color = _COLOR_HOLDER

            x0, x1 = x_of(z0), x_of(z1)
            y0u, y1u = y_of(r0), y_of(r1)
            y0l, y1l = 2 * cy - y0u, 2 * cy - y1u
            poly = QPolygonF([
                QPointF(x0, y0u), QPointF(x1, y1u),
                QPointF(x1, y1l), QPointF(x0, y0l),
            ])
            painter.setBrush(color)
            painter.drawPolygon(poly)

        # Faint centreline for orientation.
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(_MARGIN_X, cy), QPointF(w - _MARGIN_X, cy))
