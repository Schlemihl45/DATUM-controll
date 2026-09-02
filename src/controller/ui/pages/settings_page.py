"""
ui/pages/settings_page.py — Application settings page.

The "official" settings page: a left-hand section nav (CardButton, same
component used everywhere else in the app) + stacked content, covering the
app theme AND every sim-widget setting (Darstellung/Optik/Simulation/
Rohteil — sim/ui/overlay/panels/sim_panel.py's build_sections()).

The sim sections are NOT a separate copy of the settings: each widget here
is a fresh instance bound to the same AppSettings singleton the sim
widget's own overlay panel uses, and both directions are wired (widget ->
AppSettings on user interaction, AppSettings -> widget on change from
anywhere) — so a setting changed here is reflected immediately in the sim
widget's own panel, and vice versa, without this page needing to know the
sim widget instance exists at all.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card_button import CardButton

logger = logging.getLogger(__name__)

try:
    from controller.sim.ui.overlay.panels.sim_panel import SECTION_ICONS, build_sections
    _SIM_SECTIONS_AVAILABLE = True
except ImportError:
    _SIM_SECTIONS_AVAILABLE = False

_NAV_ICON_SIZE = QSize(22, 22)
_NAV_BTN_SIZE  = QSize(96, 72)


# ── Theme section ────────────────────────────────────────────────────────────

class _ThemeTab(QWidget):
    """App-wide appearance: theme selection. The only setting that isn't
    sim-widget-specific, so it stays defined here rather than in
    sim_panel.py."""

    def __init__(self, theme_manager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme_manager = theme_manager
        self._theme_combo: QComboBox | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(_section_label("Appearance"))

        if theme_manager is not None:
            form = QFormLayout()
            form.setSpacing(10)

            self._theme_combo = QComboBox()
            for key in theme_manager.available_themes():
                self._theme_combo.addItem(theme_manager.display_name(key), userData=key)

            current = theme_manager.current_theme
            for i in range(self._theme_combo.count()):
                if self._theme_combo.itemData(i) == current:
                    self._theme_combo.setCurrentIndex(i)
                    break

            form.addRow("Theme", self._theme_combo)
            root.addLayout(form)

            self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
            theme_manager.theme_changed.connect(self._on_external_theme_changed)
        else:
            root.addWidget(QLabel(
                "Theme manager not available.",
                styleSheet="color: #8fa0ba; font-size: 12px;",
            ))

        root.addStretch()

    def _on_theme_changed(self, index: int) -> None:
        key = self._theme_combo.itemData(index)
        if key:
            self._theme_manager.apply_theme(key)

    def _on_external_theme_changed(self, key: str) -> None:
        for i in range(self._theme_combo.count()):
            if self._theme_combo.itemData(i) == key and i != self._theme_combo.currentIndex():
                self._theme_combo.blockSignals(True)
                self._theme_combo.setCurrentIndex(i)
                self._theme_combo.blockSignals(False)
                break


def _section_label(text: str) -> QLabel:
    """Styled section heading label."""
    lbl = QLabel(text)
    lbl.setObjectName("CardTitle")
    return lbl


# ── SettingsPage ──────────────────────────────────────────────────────────────

class SettingsPage(QWidget):
    """Application settings page embedded in the main window stack.

    Left-hand section nav + stacked content: Theme, then every sim-widget
    section (Darstellung/Optik/Simulation/Rohteil), when the sim module is
    available.

    Args:
        theme_manager:  The application ThemeManager instance.
    """

    def __init__(
        self,
        theme_manager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        nav_col = QVBoxLayout()
        nav_col.setContentsMargins(12, 16, 12, 16)
        nav_col.setSpacing(6)
        nav_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._stack = QStackedWidget(self)

        sections: list[tuple[str, str, QWidget]] = [
            ("light", "Theme", _ThemeTab(theme_manager, self._stack)),
        ]
        if _SIM_SECTIONS_AVAILABLE:
            for (icon_name, tooltip), widget in zip(SECTION_ICONS, build_sections(self._stack)):
                sections.append((icon_name, tooltip, widget))
        else:
            warn = QLabel(
                "3D simulation not available (moderngl not installed).",
                styleSheet="color: #8fa0ba; font-size: 12px;",
            )
            sections.append(("workpieces", "Simulation", warn))

        self._nav_buttons: list[CardButton] = []
        for icon_name, label, widget in sections:
            self._stack.addWidget(widget)
            btn = CardButton(label=label, icon=get_icon(icon_name, tint=True, size=_NAV_ICON_SIZE),
                              icon_size=_NAV_ICON_SIZE.width())
            btn.setToolTip(label)
            btn.setFixedSize(_NAV_BTN_SIZE)
            btn.setCheckable(True)
            btn.setProperty("variant", "sim_nav")
            idx = len(self._nav_buttons)
            btn.clicked.connect(lambda _=False, i=idx: self._on_nav_clicked(i))
            nav_col.addWidget(btn)
            self._nav_buttons.append(btn)

        nav_col.addStretch()

        nav_widget = QWidget(self)
        nav_widget.setLayout(nav_col)
        nav_widget.setFixedWidth(_NAV_BTN_SIZE.width() + 24)

        root.addWidget(nav_widget)
        root.addWidget(self._stack, stretch=1)

        self._on_nav_clicked(0)

    def _on_nav_clicked(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
