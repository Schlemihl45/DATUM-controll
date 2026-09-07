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

ToolCardWidget's expanded-body appearance (light background, dark text) is
NOT configurable here — it's a fixed per-theme look defined directly in
dark.qss/light.qss (QWidget#ToolCardBody), not an AppSettings value. An
earlier iteration exposed it as a user preference here; that was reverted
as unnecessary complexity for what is just one widget's own styling.
"""
from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from controller.sim.core.settings import AppSettings

SECTION_ICONS: list[tuple[str, str]] = [
    ("tools", "Magazin"),
]


def _sync_spin_int(spin: QSpinBox, value: int) -> None:
    if spin.value() != value:
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)


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


def build_tools_sections(parent: QWidget | None = None) -> list[QWidget]:
    """Build a fresh set of section widgets, in SECTION_ICONS order — same
    factory-function shape as sim_panel.py's build_sections(), so
    settings_page.py wires this section in identically."""
    s = AppSettings.instance()
    return [_ToolMagazineTab(s, parent)]
