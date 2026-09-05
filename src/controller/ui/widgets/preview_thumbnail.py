"""
ui/widgets/preview_thumbnail.py — PreviewThumbnail: a small, fixed-size
placeholder for a workpiece/operation preview image.

Today this only ever renders a static fallback icon — no rendering of an
actual simulation snapshot or a .step-derived thumbnail exists yet (see
domain.models.Operation.preview_source's docstring). The reason this lives
in its own widget already, before any real rendering exists, is the
INTERFACE: set_preview_source() is the one thing every caller (workpiece
cards, operation cards, ProgramDetailPage's header) needs to know about.
When real rendering lands (a simulation-rendered image for source="sim", a
.step-file thumbnail for source="step"), only this widget's internals
change — no caller needs touching.

set_material_hint() is a SEPARATE, optional call some callers (workpiece
cards/header — see WorkpiecesPage/WorkpieceDetailPage) make in addition,
so the fallback icon is at least "werkstückabhängig" (material-dependent)
rather than always identical — a subtle colour tint keyed off the
workpiece's material text, reusing a couple of the same material-ish
names AppSettings.VOXEL_COLORS already uses. It's deliberately not folded
into set_preview_source()'s signature: that interface is the one specified
for the future real-rendering hookup and must stay exactly
(source, path).
"""
from __future__ import annotations

import zlib

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from controller.ui.icon_loader import get_icon_pixmap

_RADIUS = 8  # same corner radius convention as gcode_viewer.py's _RADIUS

# A handful of recognizable material keywords, matched case-insensitively
# as substrings of Workpiece.material (free text) — colours roughly echo
# AppSettings.VOXEL_COLORS' own "Aluminium"/"Stahl"/"Holz" presets so a
# material reads consistently across the sim and this fallback icon.
_MATERIAL_COLORS: dict[str, str] = {
    "aluminium": "#B8AC90",
    "alu":       "#B8AC90",
    "stahl":     "#8C9198",
    "steel":     "#8C9198",
    "edelstahl": "#9AA3AC",
    "holz":      "#C29958",
    "wood":      "#C29958",
    "messing":   "#C9A227",
    "brass":     "#C9A227",
    "kunststoff":"#6FA8DC",
    "plastic":   "#6FA8DC",
    "pom":       "#E8E4D8",
    "vhm":       "#8C9198",
}
# Deterministic fallback palette for a material string that matches none
# of the above — still visually distinct from one unrecognized material
# to another, via a stable hash rather than Python's per-process hash().
_UNKNOWN_MATERIAL_PALETTE = ["#7C5CBF", "#4C8BF5", "#3AA76D", "#E0954C", "#D8546E", "#3E9C9C"]
_NEUTRAL_COLOR = "#6B7280"


class PreviewThumbnail(QLabel):
    """A square icon-sized placeholder. See module docstring."""

    def __init__(self, size: int = 56, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewThumbnail")
        self._size = size
        self._source = ""
        self._path: str | None = None
        self._material = ""
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._render_fallback()

    def set_preview_source(self, source: str, path: str | None = None) -> None:
        """source: "" (no preview), "sim" (future: rendered from the
        simulation), "step" (future: thumbnail derived from a .step file).
        path: the file/asset the source refers to — unused today. Neither
        non-empty value renders anything real yet (see module docstring);
        every value falls back to the same static icon until that lands."""
        self._source = source
        self._path = path
        self._render_fallback()

    def set_material_hint(self, material: str) -> None:
        """Optional: tint the fallback icon's background based on a
        workpiece's material text (see module docstring). Purely
        cosmetic — never affects set_preview_source()'s own contract."""
        self._material = material or ""
        self._render_fallback()

    def _render_fallback(self) -> None:
        pixmap = QPixmap(self._size, self._size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self._size, self._size), _RADIUS, _RADIUS)
        painter.setClipPath(path)

        color = QColor(self._tint_color())
        color.setAlphaF(0.22)
        painter.fillRect(pixmap.rect(), color)

        icon_size = QSize(int(self._size * 0.55), int(self._size * 0.55))
        icon_pixmap = get_icon_pixmap("preview_placeholder", size=icon_size, tint=True)
        x = (self._size - icon_size.width()) // 2
        y = (self._size - icon_size.height()) // 2
        painter.drawPixmap(x, y, icon_pixmap)
        painter.end()

        self.setPixmap(pixmap)

    def _tint_color(self) -> str:
        key = self._material.strip().lower()
        if not key:
            return _NEUTRAL_COLOR
        for keyword, hex_color in _MATERIAL_COLORS.items():
            if keyword in key:
                return hex_color
        index = zlib.crc32(key.encode("utf-8")) % len(_UNKNOWN_MATERIAL_PALETTE)
        return _UNKNOWN_MATERIAL_PALETTE[index]
