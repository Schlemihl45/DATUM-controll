"""
ui/icon_loader.py — Loads SVG icons.

tint=False (default): uses Qt's native SVG icon engine directly
(QIcon(path)) — re-renders the vector at whatever size/DPI is
requested, automatically sharp, no manual DPR math needed. This is
the Qt-recommended approach.

tint=True: falls back to manual QSvgRenderer + QPainter compositing,
because flat recoloring needs pixel-level control that the icon
engine doesn't expose.

Known limitation, unrelated to DPI: Qt's SVG engine implements a
restricted SVG profile. Icons exported with <mask>-based inside-
stroke techniques (a common Figma export pattern) may not render
correctly via either path — if that happens, re-export as outlined/
flattened paths rather than switching rendering approach again.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

logger = logging.getLogger(__name__)

_ICONS_DIR = Path(__file__).parent / "resources" / "icons"
_ICON_COLOR = QColor("#D6D6D6")
_DEFAULT_SIZE = QSize(64, 64)


def _device_pixel_ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return screen.devicePixelRatio() if screen else 1.0


def _resolve_path(label: str) -> Path | None:
    filename = label.lower().replace(" ", "_") + ".svg"
    path = _ICONS_DIR / filename
    if not path.exists():
        logger.warning("missing icon: %s", path)
        return None
    return path


def get_icon(
    label: str,
    color: QColor | None = None,
    size: QSize | None = None,
    tint: bool = False,
) -> QIcon:
    """
    Load an icon by button label.

    tint=False: native QIcon(path) — Qt's SVG icon engine handles
    scaling/DPI automatically on every repaint.
    tint=True: manually re-rendered and flat-colored (needs a fixed
    size, since the result is a baked pixmap, not a live vector icon).
    """
    path = _resolve_path(label)
    if path is None:
        return QIcon()

    if not tint:
        return QIcon(str(path))  # native SVG icon engine — scales natively

    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        logger.warning("invalid/unsupported SVG: %s", path)
        return QIcon()

    target_size = size or _DEFAULT_SIZE
    dpr = _device_pixel_ratio()
    physical_size = QSize(
        max(1, int(target_size.width() * dpr)),
        max(1, int(target_size.height() * dpr)),
    )

    pixmap = QPixmap(physical_size)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color or _ICON_COLOR)
    painter.end()

    return QIcon(pixmap)


def get_icon_pixmap(
    label: str, size: QSize | None = None, color: QColor | None = None, tint: bool = False
) -> QPixmap:
    """
    For QLabel.setPixmap() — uses QIcon.pixmap(size, devicePixelRatio),
    the Qt-native way to get a correctly scaled pixmap from a QIcon,
    instead of manual DPR pre-multiplication.
    """
    target_size = size or _DEFAULT_SIZE
    icon = get_icon(label, color=color, size=size, tint=tint)
    if icon.isNull():
        return QPixmap()
    return icon.pixmap(target_size, _device_pixel_ratio())