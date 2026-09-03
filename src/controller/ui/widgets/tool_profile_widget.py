"""
ui/widgets/tool_profile_widget.py — ToolProfileWidget: a stylised, live 2D
side-view silhouette of a tool (+ its holder, if assigned).
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.sim.simulation.tool_holder import HolderProfile

_COLOR_CUTTING = QColor("#FFD700")   # gold
_COLOR_SHANK   = QColor("#808080")   # neutral grey
_COLOR_HOLDER  = QColor("#59616B")   # steel grey

_SAMPLES_TOOL   = 160
_SAMPLES_HOLDER = 60

# Dynamische Ränder
_PADDING_RIGHT = 30.0   # Sicherheitsabstand am rechten Rand
_MARGIN_Y      = 20.0   # Sicherheitsabstand oben/unten
_REF_LENGTH    = 100.0  # Referenz-Gesamtlänge für Standard-Skalierung (mm)


class ToolProfileWidget(QWidget):
    """Live 2D tool silhouette with fixed holder anchor and proportional scaling."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tool: ToolDefinition | None = None
        self._holder: HolderProfile | None = None
        self.setMinimumHeight(180)

    def set_tool(self, tool: ToolDefinition | None, holder: HolderProfile | None = None) -> None:
        self._tool = tool
        self._holder = holder
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
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

        w, h = float(self.width()), float(self.height())

        # 1. Halter-Ankerpunkt fest auf 2/5 (40%) der Widget-Breite von links setzen
        x_anchor = w * 0.40

        # Verfügbarer Platz für das Werkzeug nach rechts (3/5 der Breite abzüglich Sicherheitsrand)
        avail_w_right = max(1.0, (w - x_anchor) - _PADDING_RIGHT)
        avail_h = max(1.0, h - 2.0 * _MARGIN_Y)

        # 2. Geometrie-Punkte generieren
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

        # 3. Einheitlichen Skalierungsfaktor berechnen
        # Nutzt eine Referenz-Länge (_REF_LENGTH), damit kleinere Fräser nicht künstlich riesig gezogen werden,
        # sondern erst herunter-skalieren, wenn das Werkzeug wirklich über den rechten Rand ragt.
        effective_len_for_scale = max(full_len, _REF_LENGTH)
        scale_x = avail_w_right / effective_len_for_scale
        scale_y = avail_h / (2.0 * max_r)

        # Strikte relationale Skalierung für beide Achsen (Verbindet kein Verzerrungsrisiko)
        scale = min(scale_x, scale_y)
        cy = h / 2.0

        def x_of(z: float) -> float:
            # Spindel-Ende (z=full_len) liegt genau bei x_anchor.
            # Werkzeugspitze (z=0) wächst nach rechts.
            return x_anchor + (full_len - z) * scale

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
                path.lineTo(x_of(z), 2.0 * cy - y_of(r))
            path.closeSubpath()
            return path

        # 4. Zonen-Schnittpunkt exakt ermitteln (Schneide vs. Schaft)
        cut_len = min(tool.cutting_length, total_len)
        cutting_pts = [(z, r) for z, r in tool_pts if z <= cut_len]
        shank_pts   = [(z, r) for z, r in tool_pts if z >= cut_len]
        if cutting_pts and cutting_pts[-1][0] != cut_len:
            r_at_cut = float(tool.profile_radius_at(cut_len))
            cutting_pts.append((cut_len, r_at_cut))
            shank_pts.insert(0, (cut_len, r_at_cut))

        # 5. Zeichnen
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

        """# Dezente Mittellinie
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(10.0, cy), QPointF(w - 10.0, cy))"""