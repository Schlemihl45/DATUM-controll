"""
ui/pages/tools_settings_panel.py — "Tools" settings section (ToolPage's
magazine size). Follows the exact widget/sync pattern
sim/ui/overlay/panels/sim_panel.py's tabs already use (write side: widget
signal -> setattr(s, ...); read side: AppSettings' _changed signal -> sync
the widget back), so this section behaves identically whether it's edited
here or — since AppSettings is a singleton — anywhere else that ever reads
tool_pocket_count. Kept as its own flat module (not nested under a
`settings/` subpackage) to match ui/pages/'s existing layout, which has no
such subpackage for settings_page.py's other sections either.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from controller.sim.core.settings import AppSettings

SECTION_ICONS: list[tuple[str, str]] = [
    ("tools", "Magazin"),
    ("file-3d", "Darstellung"),
]


def _sync_spin_int(spin: QSpinBox, value: int) -> None:
    if spin.value() != value:
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)


def _sync_combo_text(combo: QComboBox, text: str) -> None:
    idx = combo.findText(text)
    if idx >= 0 and idx != combo.currentIndex():
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = rgb
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


class _ColorSwatch(QWidget):
    """Small colored rectangle showing a hex color. Local copy of
    sim_panel.py's _ColorSwatch — not shared, per this module's own
    "each settings section is self-contained" convention (see docstring)."""

    def __init__(self, hex_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self._color = hex_color
        self._update_style()

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color
        self._update_style()

    def _update_style(self) -> None:
        self.setStyleSheet(
            f"background: {self._color}; border: 1px solid rgba(255,255,255,0.15);"
            f" border-radius: 4px;"
        )


class _ToolMagazineTab(QWidget):
    """Number of physical pockets ToolPage's pinned magazine bar shows."""

    def __init__(self, s: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hdr = QLabel("Werkzeugmagazin")
        hdr.setObjectName("CardTitle")
        root.addWidget(hdr)

        form = QFormLayout(); form.setSpacing(8)
        self._pocket_spin = QSpinBox()
        self._pocket_spin.setRange(1, 200)
        self._pocket_spin.setToolTip(
            "Anzahl der Magazinplätze (Pockets), die ToolPage's Magazin-Leiste "
            "anzeigt (P1..Pn)."
        )
        form.addRow("Magazinplätze", self._pocket_spin)
        root.addLayout(form)
        root.addStretch()

        # Load saved
        _sync_spin_int(self._pocket_spin, s.tool_pocket_count)

        # Write side
        self._pocket_spin.valueChanged.connect(lambda v: setattr(s, "tool_pocket_count", v))

        # Read side (cross-instance sync — see module docstring)
        s.tool_pocket_count_changed.connect(lambda v: _sync_spin_int(self._pocket_spin, v))


class _ToolCardAppearanceTab(QWidget):
    """Expanded-body colour for ToolCardWidget (ToolPage). Deliberately
    limited to the body — the header always keeps the plain Card look, see
    tool_card_widget.py's ToolCardWidget.__init__ docstring comment on
    self._body/_apply_body_color()."""

    def __init__(self, s: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._s = s

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hdr = QLabel("Werkzeugkarte")
        hdr.setObjectName("CardTitle")
        root.addWidget(hdr)

        form = QFormLayout(); form.setSpacing(8)

        self._body_color_combo  = QComboBox()
        self._body_color_swatch = _ColorSwatch("#292929")
        for name, rgb in AppSettings.TOOLCARD_BODY_COLORS.items():
            self._body_color_combo.addItem(name, userData=_rgb_to_hex(rgb))
        body_row = QHBoxLayout(); body_row.setSpacing(8)
        body_row.addWidget(self._body_color_swatch)
        body_row.addWidget(self._body_color_combo)
        form.addRow("Hintergrund (ausgeklappt)", body_row)

        root.addLayout(form)
        root.addStretch()

        # Load saved state
        _sync_combo_text(self._body_color_combo, s.toolcard_body_color)
        self._update_body_swatch()

        # Write side
        self._body_color_combo.currentIndexChanged.connect(self._on_body_color_changed)

        # Read side — bound method, not a lambda (see sim_panel.py's
        # _DisplayTab read-side comment for why: this tab lives in the
        # persistent SettingsPage today, but a bound method costs nothing
        # and keeps the pattern safe if it's ever reused elsewhere).
        s.toolcard_body_color_changed.connect(self._on_body_color_settings_changed)

    def _on_body_color_changed(self, _idx: int) -> None:
        self._s.toolcard_body_color = self._body_color_combo.currentText()
        self._update_body_swatch()

    def _on_body_color_settings_changed(self, v: str) -> None:
        _sync_combo_text(self._body_color_combo, v)
        self._update_body_swatch()

    def _update_body_swatch(self) -> None:
        hex_val = self._body_color_combo.currentData()
        if hex_val:
            self._body_color_swatch.set_color(hex_val)


def build_tools_sections(parent: QWidget | None = None) -> list[QWidget]:
    """Build a fresh set of section widgets, in SECTION_ICONS order — same
    factory-function shape as sim_panel.py's build_sections(), so
    settings_page.py wires this section in identically."""
    s = AppSettings.instance()
    return [_ToolMagazineTab(s, parent), _ToolCardAppearanceTab(s, parent)]
