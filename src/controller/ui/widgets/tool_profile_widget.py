"""
ui/widgets/tool_profile_widget.py — ToolProfileWidget: a stylised, live 2D
side-view silhouette of a tool (+ its holder, if assigned), for
ToolCardWidget's expanded body (tool_card_widget.py).

Pure QPainter/QPainterPath — no GL, no mesh. Reuses the exact same
geometry source sim/simulation/tool_mesh.py's 3D solid-of-revolution
already uses (ToolDefinition.profile_radius_at_array()), just projected
into 2D instead of revolved into a mesh, and the same cutting/shank/holder
color split tool_mesh.py's build_tool_mesh() bakes per-vertex.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.sim.simulation.tool_holder import HolderProfile

_SAMPLES_TOOL   = 160
_SAMPLES_HOLDER = 60
_MARGIN_X = 24
_MARGIN_Y = 20


class ToolProfileWidget(QWidget):
    """Live 2D tool silhouette with metallic 3D gradients."""

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

        scale = min(avail_w / full_len, avail_h / (2.0 * max_r))
        cy = h / 2.0

        def x_of(z: float) -> float:
            return _MARGIN_X + (full_len - z) * scale

        def y_of(r: float) -> float:
            return cy - r * scale

        def zone_path(points: list[tuple[float, float]]) -> QPainterPath:
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

        # ── Neu: Zylindrischer Verlauf für 3D-Metall-Optik ──────────────────
        def create_metallic_gradient(base_hex: str, highlight_hex: str = "#FFFFFF") -> QLinearGradient:
            grad = QLinearGradient(0, cy - max_r * scale, 0, cy + max_r * scale)
            base = QColor(base_hex)
            dark = base.darker(170)
            mid_dark = base.darker(125)
            highlight = QColor(highlight_hex)

            grad.setColorAt(0.0, dark)            # Obere Schattenkante
            grad.setColorAt(0.2, mid_dark)        # Übergang
            grad.setColorAt(0.38, highlight)      # Helle Glanzkante (Light-Strip)
            grad.setColorAt(0.55, base)           # Grundfarbe
            grad.setColorAt(1.0, dark)            # Untere Schattenkante
            return grad

        # Split cutting/shank zones
        cut_len = min(tool.cutting_length, total_len)
        cutting_pts = [(z, r) for z, r in tool_pts if z <= cut_len]
        shank_pts   = [(z, r) for z, r in tool_pts if z >= cut_len]
        if cutting_pts and cutting_pts[-1][0] != cut_len:
            r_at_cut = float(tool.profile_radius_at(cut_len))
            cutting_pts.append((cut_len, r_at_cut))
            shank_pts.insert(0, (cut_len, r_at_cut))

        painter.setPen(Qt.PenStyle.NoPen)

        # Schneide (Gold / TiN)
        if cutting_pts:
            painter.setBrush(create_metallic_gradient("#FFD700", "#FFF8D0"))
            painter.drawPath(zone_path(cutting_pts))

        # Schaft (Neutraler Metall-Schaft)
        if shank_pts:
            painter.setBrush(create_metallic_gradient("#808080", "#F0F4F8"))
            painter.drawPath(zone_path(shank_pts))

        # Halter (Werkzeugaufnahme)
        if holder_pts:
            painter.setBrush(create_metallic_gradient("#59616B", "#B0B8C0"))
            painter.drawPath(zone_path(holder_pts))

        # Faint centreline
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(_MARGIN_X, cy), QPointF(w - _MARGIN_X, cy))