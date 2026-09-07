"""
ui/pages/settings_page.py — Application settings page.

The "official" settings page, restructured into thematic top-level
groups — **General** (app-wide, currently just Theme), **Simulation**
(every sim-widget setting: Darstellung/Optik/Simulation/Rohteil, from
sim/ui/overlay/panels/sim_panel.py's build_sections()), and **Tools**
(ToolPage's magazine size, from tools_settings_panel.py's
build_tools_sections()) — each its own horizontal-nav + stacked-content
master-detail panel (_NavStack), with each group's own sub-nav one level
inside the outer level.

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
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from controller.sim.core.settings import AppSettings
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card_button import CardButton

logger = logging.getLogger(__name__)

try:
    from controller.sim.ui.overlay.panels.sim_panel import SECTION_ICONS, build_sections
    _SIM_SECTIONS_AVAILABLE = True
except ImportError:
    _SIM_SECTIONS_AVAILABLE = False

from controller.ui.pages.tools_settings_panel import (
    SECTION_ICONS as TOOLS_SECTION_ICONS,
    build_tools_sections,
)
from controller.ui.pages.workpieces_settings_panel import (
    SECTION_ICONS as WORKPIECES_SECTION_ICONS,
    build_workpieces_sections,
)

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


class _SafetyTab(QWidget):
    """App-wide safety settings — currently just whether Start pre-flights
    the loaded program with a whole-program collision scan.

    Deliberately separate from the "Kollisionserkennung" checkbox in the
    Simulation -> Simulation section (sim_panel.py's _VoxelSimTab): that one
    only gates the voxel engine's own live/simulation collision feedback
    (always informational, never stops anything — see machine_page.py's
    _on_live_collision()); this one decides whether MachinePage.Start runs
    presim_check_collisions() at all before launching the program. Enabling
    in-simulation collision detection does NOT imply this pre-start check
    should run too — they're independent switches."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        s = AppSettings.instance()
        self._s = s

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(_section_label("Sicherheit"))

        form = QFormLayout()
        form.setSpacing(10)

        self._chk_before_start = QCheckBox()
        self._chk_before_start.setToolTip(
            "Prüft den gesamten Werkzeugweg auf Kollisionen, bevor das "
            "Programm über Start tatsächlich losläuft. Eine erkannte "
            "Kollision blockiert Start hinter einer Bestätigung "
            "(\"Trotzdem starten\") statt automatisch loszulaufen.\n\n"
            "Unabhängig von der Kollisionserkennung in den "
            "Simulations-Einstellungen — die ist rein informativ und "
            "läuft unabhängig davon, ob dieser Vorab-Check aktiv ist."
        )
        self._chk_before_start.blockSignals(True)
        self._chk_before_start.setChecked(s.collision_check_before_start_enabled)
        self._chk_before_start.blockSignals(False)
        form.addRow("Kollisionserkennung vor Programmstart", self._chk_before_start)
        root.addLayout(form)

        root.addStretch()

        self._chk_before_start.toggled.connect(
            lambda v: setattr(s, "collision_check_before_start_enabled", v))
        s.collision_check_before_start_enabled_changed.connect(self._on_external_change)

    def _on_external_change(self, v: bool) -> None:
        if self._chk_before_start.isChecked() != v:
            self._chk_before_start.blockSignals(True)
            self._chk_before_start.setChecked(v)
            self._chk_before_start.blockSignals(False)


class _ManualTab(QWidget):
    """Jog speed presets used by ManualPage's JogControlPanel
    (ui/widgets/jog_control_panel.py) — no backend Rapid-jog mode exists,
    so Feed vs. Rapid there is purely these two velocity values, swapped by
    the jog pad's own Rapid toggle button."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        s = AppSettings.instance()
        self._s = s

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(_section_label("Jog-Geschwindigkeit"))

        form = QFormLayout()
        form.setSpacing(10)

        self._feed_spin = QDoubleSpinBox()
        self._feed_spin.setRange(0.1, 500.0)
        self._feed_spin.setDecimals(1)
        self._feed_spin.setSuffix(" mm/s")
        self._feed_spin.setToolTip(
            "Jog-Geschwindigkeit, solange der Eilgang-Umschalter (Rapid) "
            "im Jog-Pad NICHT aktiv ist."
        )
        form.addRow("Feed", self._feed_spin)

        self._rapid_spin = QDoubleSpinBox()
        self._rapid_spin.setRange(0.1, 2000.0)
        self._rapid_spin.setDecimals(1)
        self._rapid_spin.setSuffix(" mm/s")
        self._rapid_spin.setToolTip(
            "Jog-Geschwindigkeit, solange der Eilgang-Umschalter (Rapid) "
            "im Jog-Pad aktiv ist. Sicherheitsrelevant — mit Bedacht wählen."
        )
        form.addRow("Rapid", self._rapid_spin)

        root.addLayout(form)
        root.addStretch()

        for spin, val in (
            (self._feed_spin, s.jog_feed_velocity_mm_s),
            (self._rapid_spin, s.jog_rapid_velocity_mm_s),
        ):
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

        self._feed_spin.valueChanged.connect(
            lambda v: setattr(s, "jog_feed_velocity_mm_s", v))
        self._rapid_spin.valueChanged.connect(
            lambda v: setattr(s, "jog_rapid_velocity_mm_s", v))

        s.jog_feed_velocity_mm_s_changed.connect(self._on_feed_changed)
        s.jog_rapid_velocity_mm_s_changed.connect(self._on_rapid_changed)

    def _on_feed_changed(self, v: float) -> None:
        if abs(self._feed_spin.value() - v) > 1e-9:
            self._feed_spin.blockSignals(True)
            self._feed_spin.setValue(v)
            self._feed_spin.blockSignals(False)

    def _on_rapid_changed(self, v: float) -> None:
        if abs(self._rapid_spin.value() - v) > 1e-9:
            self._rapid_spin.blockSignals(True)
            self._rapid_spin.setValue(v)
            self._rapid_spin.blockSignals(False)


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
            ("scan-cube", "Sicherheit", _SafetyTab(self)),
            ("setup", "Manuell", _ManualTab(self)),
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

        tools_sections = [
            (icon_name, label, widget)
            for (icon_name, label), widget in zip(TOOLS_SECTION_ICONS, build_tools_sections(self))
        ]
        tools_page = _NavStack(tools_sections, parent=self)

        workpieces_sections = [
            (icon_name, label, widget)
            for (icon_name, label), widget in zip(
                WORKPIECES_SECTION_ICONS, build_workpieces_sections(self)
            )
        ]
        workpieces_page = _NavStack(workpieces_sections, parent=self)

        outer_sections: list[tuple[str, str, QWidget]] = [
            ("setup",      "General",    general_page),
            ("scan-cube",  "Simulation", simulation_page),
            ("tools",      "Tools",      tools_page),
            ("workpieces", "Workpieces", workpieces_page),
        ]
        self._outer = _NavStack(
            outer_sections, btn_size=_OUTER_BTN_SIZE, icon_size=_OUTER_ICON_SIZE, parent=self,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._outer)
