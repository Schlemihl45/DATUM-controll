"""
ui/pages/settings_page.py — Application settings page.

The "official" settings page, restructured into two thematic top-level
groups — **General** (app-wide, currently just Theme) and **Simulation**
(every sim-widget setting: Darstellung/Optik/Simulation/Rohteil, from
sim/ui/overlay/panels/sim_panel.py's build_sections()) — each its own
horizontal-nav + stacked-content master-detail panel (_NavStack), with
Simulation's own sub-nav one level inside General/Simulation's own level.

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
    QFrame,
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

_NAV_ICON_SIZE = QSize(20, 20)
_NAV_BTN_SIZE  = QSize(196, 44)   # horizontal: icon left, label right
_OUTER_ICON_SIZE = QSize(22, 22)
_OUTER_BTN_SIZE  = QSize(196, 48)


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


# ── Shared master-detail nav (left column of horizontal CardButtons +
#    QStackedWidget) — used for both SettingsPage's outer General/
#    Simulation level and Simulation's own inner sub-nav, so the two levels
#    stay visually and behaviourally identical without duplicating the
#    wiring twice. ──────────────────────────────────────────────────────────

class _NavStack(QWidget):
    def __init__(
        self,
        sections: list[tuple[str, str, QWidget]],   # (icon_name, label, content widget)
        btn_size: QSize = _NAV_BTN_SIZE,
        icon_size: QSize = _NAV_ICON_SIZE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        nav_col = QVBoxLayout()
        nav_col.setContentsMargins(10, 14, 10, 14)
        nav_col.setSpacing(6)
        nav_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._stack = QStackedWidget(self)

        self._nav_buttons: list[CardButton] = []
        for icon_name, label, widget in sections:
            self._stack.addWidget(widget)
            btn = CardButton(
                label=label,
                icon=get_icon(icon_name, tint=True, size=icon_size),
                icon_size=icon_size.width(),
                orientation=Qt.Orientation.Horizontal,
            )
            btn.setToolTip(label)
            btn.setFixedSize(btn_size)
            btn.setCheckable(True)
            btn.setProperty("variant", "sim_nav")
            idx = len(self._nav_buttons)
            btn.clicked.connect(lambda _=False, i=idx: self._on_nav_clicked(i))
            nav_col.addWidget(btn)
            self._nav_buttons.append(btn)

        nav_col.addStretch()

        # Themed background (matches the app's Card look, same rounding
        # convention settings_panel.py's left-docked overlay strip uses:
        # flat on the edge this column attaches to, rounded toward the
        # content it opens) instead of a bare, unstyled QWidget.
        nav_widget = QFrame(self)
        nav_widget.setObjectName("Card")
        nav_widget.setProperty("variant", "sim_overlay")
        nav_widget.setLayout(nav_col)
        nav_widget.setFixedWidth(btn_size.width() + 20)

        root.addWidget(nav_widget)
        root.addWidget(self._stack, stretch=1)

        if self._nav_buttons:
            self._on_nav_clicked(0)

    def _on_nav_clicked(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)


# ── SettingsPage ──────────────────────────────────────────────────────────────

class SettingsPage(QWidget):
    """Application settings page embedded in the main window stack.

    Two top-level thematic groups (General, Simulation), each its own
    _NavStack — General currently holds just Theme; Simulation holds every
    sim-widget section (Darstellung/Optik/Simulation/Rohteil).

    Args:
        theme_manager:  The application ThemeManager instance.
    """

    def __init__(
        self,
        theme_manager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        general_sections: list[tuple[str, str, QWidget]] = [
            ("light", "Theme", _ThemeTab(theme_manager, self)),
        ]
        general_page = _NavStack(general_sections, parent=self)

        if _SIM_SECTIONS_AVAILABLE:
            sim_sections = [
                (icon_name, label, widget)
                for (icon_name, label), widget in zip(SECTION_ICONS, build_sections(self))
            ]
        else:
            warn = QLabel(
                "3D simulation not available (moderngl not installed).",
                styleSheet="color: #8fa0ba; font-size: 12px;",
            )
            sim_sections = [("workpieces", "Simulation", warn)]
        simulation_page = _NavStack(sim_sections, parent=self)

        outer_sections: list[tuple[str, str, QWidget]] = [
            ("setup",     "General",    general_page),
            ("scan-cube", "Simulation", simulation_page),
        ]
        self._outer = _NavStack(
            outer_sections, btn_size=_OUTER_BTN_SIZE, icon_size=_OUTER_ICON_SIZE, parent=self,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._outer)
