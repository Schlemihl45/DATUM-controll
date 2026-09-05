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
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QWidget

from controller.ui.icon_loader import get_icon


class PreviewThumbnail(QLabel):
    """A square icon-sized placeholder. See module docstring."""

    def __init__(self, size: int = 56, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewThumbnail")
        self._size = size
        self._source = ""
        self._path: str | None = None
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

    def _render_fallback(self) -> None:
        icon_size = QSize(int(self._size * 0.6), int(self._size * 0.6))
        self.setPixmap(get_icon("workpieces", size=icon_size).pixmap(icon_size))
