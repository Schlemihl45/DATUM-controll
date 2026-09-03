"""
ui/main_window.py — Application shell: StatusBar + machine info panel +
page stack (Home <-> MachinePage <-> SettingsPage) + quick-access button row.

Every button that represents a real machine command is wired through
MachineController, never directly to the backend (see MachineController's
docstring: ui/ only ever talks to core/ via signals/slots and methods so the
backend stays swappable behind AbstractBackend).

ThemeManager is accepted as a constructor argument and passed on to
SettingsPage so the General tab can apply theme switches app-wide.
The viewport is registered with ThemeManager inside MachinePage after
construction so corner-fill colours stay in sync with the active theme.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from controller.core.machine.controller import MachineController
from controller.domain.models import ProgramState
from controller.ui.icon_loader import get_icon
from controller.ui.pages.machine_page import MachinePage
from controller.ui.pages.settings_page import SettingsPage
from controller.ui.widgets.card_button import CardButton
from controller.ui.widgets.machine_info_cards import (
    AxisPositionCard,
    FeedrateCard,
    SpindleCard,
)
from controller.ui.widgets.status_bar import StatusBar

# Digital-output pin for the work light, sent via M64/M65 MDI commands
# (see AbstractBackend.send_mdi — "prefer M64/M65 via send_mdi() over
# raw set_digital_output() calls"). Placeholder: must be replaced with
# the real HAL pin number once the machine's INI/HAL config defines it.
_LIGHT_MDI_PIN = 0

_HOME_INDEX         = 0
_MACHINE_PAGE_INDEX = 1
_SETTINGS_INDEX     = 2

_NAV_ICON_SIZE = QSize(256, 256)


def _nav_button(icon_name: str) -> CardButton:
    """Big square icon-only button for the home-page navigation grid."""
    return CardButton(icon=get_icon(icon_name, size=_NAV_ICON_SIZE), icon_size=256)


class MainWindow(QMainWindow):

    def __init__(
        self,
        controller: MachineController,
        theme_manager=None,       # ThemeManager | None — None = no theme switching
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller    = controller
        self._theme_manager = theme_manager

        self.setWindowTitle("DATUM Control")
        self.resize(600, 900)
        self.setWindowIcon(get_icon("logo"))

        # -------------------------------------------------------
        # Status bar
        # -------------------------------------------------------
        status_bar = StatusBar(controller, self)

        # -------------------------------------------------------
        # Machine Info Panel
        # -------------------------------------------------------
        machine_info_area = QGridLayout()
        machine_info_area.setContentsMargins(0, 0, 0, 0)
        machine_info_area.setSpacing(6)
        machine_info_area.addWidget(AxisPositionCard(controller), 0, 0, 2, 1)
        machine_info_area.addWidget(FeedrateCard(controller), 0, 1)
        machine_info_area.addWidget(SpindleCard(controller), 1, 1)

        # -------------------------------------------------------
        # Stacked Widget: Home <-> MachinePage
        # -------------------------------------------------------
        self._stack = QStackedWidget(self)

        home_page = QWidget()
        home_grid = QGridLayout(home_page)
        home_grid.setContentsMargins(0, 0, 0, 0)
        home_grid.setSpacing(6)

        machine_page_btn = _nav_button("machine")

        # Pages that don't exist yet — disabled, not faked. See roadmap.
        tools_page_btn = _nav_button("tools")
        setup_page_btn = _nav_button("setup")
        programs_page_btn = _nav_button("workpieces")
        statistics_page_btn = _nav_button("statistics")
        settings_page_btn = _nav_button("settings")
        for btn in (tools_page_btn, setup_page_btn, programs_page_btn,
                    statistics_page_btn):
            btn.setEnabled(False)
            btn.setToolTip("Noch nicht implementiert")

        home_grid.addWidget(machine_page_btn, 0, 0)
        home_grid.addWidget(tools_page_btn, 0, 1)
        home_grid.addWidget(setup_page_btn, 1, 0)
        home_grid.addWidget(programs_page_btn, 1, 1)
        home_grid.addWidget(statistics_page_btn, 2, 0)
        home_grid.addWidget(settings_page_btn, 2, 1)

        machine_page_btn.clicked.connect(self._on_machine_btn_clicked)
        settings_page_btn.clicked.connect(self._on_settings_btn_clicked)

        # Build pages
        self._machine_page = MachinePage(controller, self)
        self._stack.addWidget(home_page)                        # _HOME_INDEX
        self._stack.addWidget(self._machine_page)               # _MACHINE_PAGE_INDEX

        self._settings_page = SettingsPage(
            theme_manager=theme_manager,
            parent=self,
        )
        self._stack.addWidget(self._settings_page)              # _SETTINGS_INDEX

        # Register viewport with ThemeManager for gradient sync
        if theme_manager is not None:
            try:
                theme_manager.register_viewport(self._machine_page._sim.viewport)
            except AttributeError:
                pass   # sim widget not the real one (SimPlaceholder)

        # -------------------------------------------------------
        # Quick Button Row — light, coolant, back
        # -------------------------------------------------------
        button_row = QHBoxLayout()
        button_row.setSpacing(6)

        self.light_btn = CardButton(icon=get_icon("light_off", size=QSize(48, 48)), icon_size=48)
        self.light_btn.setCheckable(True)
        self.light_btn.toggled.connect(self._on_light_toggled)
        self.light_btn.setFixedSize(100, 100)
        button_row.addWidget(self.light_btn)

        self.coolant_btn = CardButton(icon=get_icon("coolant_off"), icon_size=64)
        self.coolant_btn.setCheckable(True)
        self.coolant_btn.toggled.connect(self._on_coolant_toggled)
        self.coolant_btn.setFixedSize(100, 100)
        button_row.addWidget(self.coolant_btn)

        button_row.addStretch(1)

        # Feed Hold — reachable from this app-wide quick bar (outside
        # self._stack, so it survives page navigation) regardless of which
        # page is showing, per "von überall gestoppt werden kann". Only
        # shown while a program is actually RUNNING (see
        # _on_program_state_for_quickbar) — toggles the real
        # MachineController.set_feed_hold(), distinct from MachinePage's
        # own Pause button (pause_program(), a full resumable pause).
        # Placed left of Return: this bar has no separate Stop button, so
        # Feed Hold's QSS (dark.qss/light.qss) carries Stop's red as its own
        # default/warning color instead of the amber some other toggles use.
        self.feed_hold_btn = CardButton(
            "Feed Hold", icon=get_icon("player-pause", tint=True, size=QSize(40, 40)),
            icon_size=40,
        )
        self.feed_hold_btn.setCheckable(True)
        self.feed_hold_btn.setProperty("variant", "feed_hold")
        self.feed_hold_btn.setFixedSize(100, 100)
        self.feed_hold_btn.setVisible(False)
        self.feed_hold_btn.toggled.connect(self._controller.set_feed_hold)
        self._controller.feed_hold_changed.connect(self.feed_hold_btn.setChecked)
        self._controller.program_state_changed.connect(
            self._on_program_state_for_quickbar
        )
        button_row.addWidget(self.feed_hold_btn)

        self.return_btn = CardButton(icon=get_icon("return"), icon_size=48)
        self.return_btn.setFixedSize(100, 100)
        self.return_btn.clicked.connect(self._on_return_clicked)
        button_row.addWidget(self.return_btn)

        # -------------------------------------------------------
        # Layout
        # -------------------------------------------------------
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(6, 6, 6, 6)
        content_layout.setSpacing(6)
        content_layout.addLayout(machine_info_area)
        content_layout.addWidget(self._stack, stretch=1)
        content_layout.addLayout(button_row)

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(status_bar)
        root_layout.addLayout(content_layout, stretch=1)

        central = QWidget(self)
        central.setObjectName("CentralWidget")
        central.setLayout(root_layout)
        self.setCentralWidget(central)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_machine_btn_clicked(self) -> None:
        self._stack.setCurrentIndex(_MACHINE_PAGE_INDEX)

    def _on_settings_btn_clicked(self) -> None:
        self._stack.setCurrentIndex(_SETTINGS_INDEX)

    def _on_return_clicked(self) -> None:
        self._stack.setCurrentIndex(_HOME_INDEX)

    def _on_program_state_for_quickbar(self, state: ProgramState) -> None:
        """Feed Hold only makes sense — and is only shown — while a
        program is actually RUNNING; it stays out of the way otherwise."""
        self.feed_hold_btn.setVisible(state == ProgramState.RUNNING)

    # ------------------------------------------------------------------
    # Quick buttons -> AbstractBackend (via MachineController)
    # ------------------------------------------------------------------

    def _on_light_toggled(self, checked: bool) -> None:
        # No dedicated light command exists on AbstractBackend — per its
        # own docstring, digital outputs go through MDI M64/M65 rather
        # than a raw HAL call. send_mdi() already enforces the ON+homed+
        # idle precondition and reports a warning via the status bar if
        # it isn't met, so nothing extra to check here.
        mdi_command = f"M64 P{_LIGHT_MDI_PIN}" if checked else f"M65 P{_LIGHT_MDI_PIN}"
        self._controller.send_mdi(mdi_command)
        icon_name = "light_on" if checked else "light_off"
        self.light_btn.set_icon(get_icon(icon_name, size=QSize(48, 48)))

    def _on_coolant_toggled(self, checked: bool) -> None:
        if checked:
            self._controller.flood_on()
        else:
            self._controller.flood_off()
        # Optimistic icon update. flood_on()/flood_off() silently no-op
        # when the machine isn't ON (see MachineController) — same
        # caveat MachinePage's own buttons have; a future "reflect real
        # backend state" pass would need MachineController to warn on
        # rejected coolant commands the way it already does for MDI.
        self.coolant_btn.set_icon(get_icon("coolant_on" if checked else "coolant_off"))
