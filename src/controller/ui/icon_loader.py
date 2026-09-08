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
import re
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

logger = logging.getLogger(__name__)

_ICONS_DIR = Path(__file__).parent / "resources" / "icons"
_STYLES_DIR = Path(__file__).parent / "resources" / "styles"
_ICON_COLOR = QColor("#D6D6D6")
_DEFAULT_SIZE = QSize(64, 64)

# Theme-aware icon tinting: tint=True icons are colored by reading a
# `color:` rule straight out of the currently active .qss file (see
# extract_color_from_qss() below), so a tinted icon matches whichever
# theme's stylesheet is actually applied instead of a single hardcoded
# color. _DEFAULT_QSS_PATH is the fallback used before ThemeManager has
# run at all (the very first icons loaded during app construction, before
# apply_theme() has had a chance to call set_active_theme_path() — see
# that function's docstring and ThemeManager.apply_theme()).
_DEFAULT_QSS_PATH = _STYLES_DIR / "baseline.qss"
_active_qss_path: Path = _DEFAULT_QSS_PATH


def set_active_theme_path(path: Path) -> None:
    """Point tint=True icon loading at *path* (a theme's .qss file) from
    now on. Called by ThemeManager.apply_theme() whenever the app-wide
    stylesheet changes, so newly loaded icons pick up the new theme's
    tint color.

    Deliberately does NOT retint any QIcon/QPixmap already created and
    handed out — those were baked (tint=True renders to a fixed pixmap,
    see this module's own docstring) at load time and stay as they were.
    Making a live theme switch retint every already-visible icon would
    mean touching every icon-holding widget to reload on
    ThemeManager.theme_changed, which is out of scope here; the visible
    effect of this function is correct icon tinting from the next app
    start (or the next time a widget re-fetches its icon) onward.
    """
    global _active_qss_path
    _active_qss_path = path


# Matches "color: #rrggbb;" (or a named/rgba() value) inside a selector's
# rule block — NOT "background-color", hence the negative lookbehind.
_COLOR_PROPERTY_RE = re.compile(r"(?<![-\w])color\s*:\s*([^;]+);")


def extract_color_from_qss(
    qss_path: Path, selector: str = "QLabel#CardButtonIcon",
) -> QColor | None:
    """Read *qss_path* and return the `color` value of the first rule
    block for *selector* (e.g. "QLabel#CardButtonIcon" or
    "QLabel#InfoIcon"), or None if the file is unreadable, the selector
    isn't found, or it has no `color` property.

    Deliberately simple regex-based parsing, not a real CSS/QSS parser:
    every rule this needs to read is a plain, single, non-nested
    `Selector { ... color: #rrggbb; ... }` block, same as every other
    color rule already in dark.qss/light.qss.
    """
    try:
        text = qss_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read QSS file %s: %s", qss_path, exc)
        return None

    block_match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text)
    if block_match is None:
        return None

    color_match = _COLOR_PROPERTY_RE.search(block_match.group(1))
    if color_match is None:
        return None

    color = QColor(color_match.group(1).strip())
    if not color.isValid():
        logger.warning(
            "Unparseable color %r for selector %r in %s",
            color_match.group(1).strip(), selector, qss_path,
        )
        return None
    return color


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
    tint: bool = True,
    qss_path: Path | None = None,
    selector: str = "QLabel#CardButtonIcon",
) -> QIcon:
    """
    Load an icon by button label.

    tint=True (default): manually re-rendered and flat-colored (needs a
    fixed size, since the result is a baked pixmap, not a live vector
    icon). An explicit *color* wins outright; otherwise the tint color is
    read from *qss_path* (or the app's currently active theme file — see
    set_active_theme_path()) via extract_color_from_qss(), using
    *selector* to pick which rule's `color` applies (e.g. the default
    QLabel#CardButtonIcon for dark quick-bar/nav buttons vs.
    QLabel#InfoIcon for the light info cards in machine_info_cards.py).
    Falls back to the hardcoded _ICON_COLOR if that lookup finds nothing.
    tint=False: native QIcon(path) — Qt's SVG icon engine handles
    scaling/DPI automatically on every repaint, original SVG colors kept
    as-is. Pass this explicitly for genuinely multi-color/branded icons
    (e.g. logo.svg) or icons whose own colors carry meaning that a flat
    tint would destroy (e.g. the red/green/blue X/Y/Z axis icons).
    """
    path = _resolve_path(label)
    if path is None:
        return QIcon()

    if not tint:
        return QIcon(str(path))  # native SVG icon engine — scales natively

    if color is None:
        color = extract_color_from_qss(qss_path or _active_qss_path, selector=selector)

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

    # Seitenverhältnis beibehalten und SVG zentrieren
    svg_size = renderer.defaultSize()
    if not svg_size.isEmpty():
        # Auf Zielgröße skalieren unter Beibehaltung des Seitenverhältnisses
        scaled_size = svg_size.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio)
        x = (target_size.width() - scaled_size.width()) / 2.0
        y = (target_size.height() - scaled_size.height()) / 2.0
        dest_rect = QRectF(x, y, scaled_size.width(), scaled_size.height())
        renderer.render(painter, dest_rect)
    else:
        renderer.render(painter)

    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color or _ICON_COLOR)
    painter.end()

    return QIcon(pixmap)


def get_icon_pixmap(
    label: str,
    size: QSize | None = None,
    color: QColor | None = None,
    tint: bool = True,
    qss_path: Path | None = None,
    selector: str = "QLabel#CardButtonIcon",
) -> QPixmap:
    """
    For QLabel.setPixmap() — uses QIcon.pixmap(size, devicePixelRatio),
    the Qt-native way to get a correctly scaled pixmap from a QIcon,
    instead of manual DPR pre-multiplication. qss_path/selector: see
    get_icon().
    """
    target_size = size or _DEFAULT_SIZE
    icon = get_icon(
        label, color=color, size=size, tint=tint, qss_path=qss_path, selector=selector,
    )
    if icon.isNull():
        return QPixmap()
    return icon.pixmap(target_size, _device_pixel_ratio())