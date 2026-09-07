"""
ui/main_window.py — Application shell: StatusBar + machine info panel +
page stack (Home <-> MachinePage <-> SettingsPage <-> ToolPage) +
quick-access button row.

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

from PySide6.QtCore import QSize, QTimer
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
from controller.ui.pages.manual_page import ManualPage
from controller.ui.pages.settings_page import SettingsPage
from controller.ui.pages.tool_page import ToolPage
from controller.ui.pages.workpiece_browser_page import WorkpiecesSection
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
_TOOLS_PAGE_INDEX   = 3
_WORKPIECES_INDEX   = 4
_MANUAL_PAGE_INDEX  = 5

_NAV_ICON_SIZE = QSize(256, 256)


def _nav_button(icon_name: str) -> CardButton:
    """Big square icon-only button for the home-page navigation grid.

    Explicit tint=True (not the get_icon() default — see its own docstring
    for why the default itself is True): every nav icon (machine/tools/
    setup/workpieces/statistics/settings) is a plain two-tone grey/white
    SVG, verified safe to flatten to the theme's icon color, unlike e.g.
    logo.svg's branded blue.
    """
    return CardButton(icon=get_icon(icon_name, tint=True, size=_NAV_ICON_SIZE), icon_size=256)


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
        # Explicit tint=False (against get_icon()'s own True default): the
        # logo is multi-color (brand blue #08407D) — tinting it would flatten
        # it to a single flat color and destroy the brand look.
        self.setWindowIcon(get_icon("logo", tint=False))

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
        tools_page_btn = _nav_button("tools")

        # "setup" icon (crosshair/target pictogram) reused here rather than
        # a new asset — visually distinct enough from settings.svg's gear
        # that "Manuell" and "Einstellungen" stay tellable apart at a
        # glance in the same grid.
        manual_page_btn = _nav_button("setup")
        manual_page_btn.setToolTip("Manuelle Bedienung")
        programs_page_btn = _nav_button("workpieces")
        # Pages that don't exist yet — disabled, not faked. See roadmap.
        statistics_page_btn = _nav_button("statistics")
        settings_page_btn = _nav_button("settings")
        for btn in (statistics_page_btn,):
            btn.setEnabled(False)
            btn.setToolTip("Noch nicht implementiert")

        home_grid.addWidget(machine_page_btn, 0, 0)
        home_grid.addWidget(tools_page_btn, 0, 1)
        home_grid.addWidget(manual_page_btn, 1, 0)
        home_grid.addWidget(programs_page_btn, 1, 1)
        home_grid.addWidget(statistics_page_btn, 2, 0)
        home_grid.addWidget(settings_page_btn, 2, 1)

        machine_page_btn.clicked.connect(self._on_machine_btn_clicked)
        tools_page_btn.clicked.connect(self._on_tools_btn_clicked)
        settings_page_btn.clicked.connect(self._on_settings_btn_clicked)
        programs_page_btn.clicked.connect(self._on_workpieces_btn_clicked)
        manual_page_btn.clicked.connect(self._on_manual_btn_clicked)

        # Build pages
        self._machine_page = MachinePage(controller, self)
        self._stack.addWidget(home_page)                        # _HOME_INDEX
        self._stack.addWidget(self._machine_page)               # _MACHINE_PAGE_INDEX

        self._settings_page = SettingsPage(
            theme_manager=theme_manager,
            parent=self,
        )
        self._stack.addWidget(self._settings_page)              # _SETTINGS_INDEX

        self._tools_page = ToolPage(self)
        self._stack.addWidget(self._tools_page)                 # _TOOLS_PAGE_INDEX

        self._workpieces_section = WorkpiecesSection(self)
        self._stack.addWidget(self._workpieces_section)         # _WORKPIECES_INDEX

        self._manual_page = ManualPage(controller, self)
        self._stack.addWidget(self._manual_page)                # _MANUAL_PAGE_INDEX

        # MachinePage's "Datei laden" button no longer loads a fixed file —
        # it sends the user to the Workpieces page to pick a real program;
        # a ProgramDetailPage's "In Maschine laden" button closes the loop
        # by loading that file back into MachinePage and switching to it.
        self._machine_page.open_workpieces_requested.connect(self._on_workpieces_btn_clicked)
        self._workpieces_section.load_in_machine_requested.connect(
            self._on_load_in_machine_requested
        )

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

        self.light_btn = CardButton(
            icon=get_icon("light_off", tint=True, size=QSize(48, 48)), icon_size=48,
        )
        self.light_btn.setCheckable(True)
        self.light_btn.toggled.connect(self._on_light_toggled)
        self.light_btn.setFixedSize(100, 100)
        button_row.addWidget(self.light_btn)

        self.coolant_btn = CardButton(icon=get_icon("coolant_off", tint=True), icon_size=64)
        self.coolant_btn.setCheckable(True)
        self.coolant_btn.toggled.connect(self._on_coolant_toggled)
        self.coolant_btn.setFixedSize(100, 100)
        button_row.addWidget(self.coolant_btn)

        button_row.addStretch(1)

        # Feed Hold — reachable from this app-wide quick bar (outside
        # self._stack, so it survives page navigation) regardless of which
        # page is showing, per "von überall gestoppt werden kann". Mirrors
        # MachinePage's own Feed-Hold button (both engage the same
        # controller state) so the control is reachable from anywhere, not
        # just while MachinePage is showing. ONE-WAY trigger, not a toggle
        # — pressing it only ever engages feed-hold; the button disappears
        # the instant it's engaged (see _sync_feed_hold_quickbar()) rather
        # than staying visible in a "checked" state, since there is no
        # quick-bar Start button to release it from — releasing a
        # quick-bar-engaged feed-hold means going to MachinePage (Start or
        # Stop), an accepted consequence of this bar having no resume
        # control of its own.
        # Placed left of Return: this bar has no separate Stop button
        # (that lives only on MachinePage's own control column now), so
        # Feed Hold's QSS (dark.qss/light.qss) carries Stop's red as its own
        # default/warning color instead of the amber some other toggles use.
        self.feed_hold_btn = CardButton(
            "Feed Hold", icon=get_icon("player-pause", tint=True, size=QSize(40, 40)),
            icon_size=40,
        )
        self.feed_hold_btn.setProperty("variant", "feed_hold")
        self.feed_hold_btn.setFixedSize(100, 100)
        self.feed_hold_btn.setVisible(False)
        self.feed_hold_btn.clicked.connect(self._on_feed_hold_clicked)
        self._controller.program_state_changed.connect(
            self._on_program_state_for_quickbar
        )
        self._controller.feed_hold_changed.connect(self._on_feed_hold_for_quickbar)
        button_row.addWidget(self.feed_hold_btn)

        # No separate quick-bar "Halt"/hold_axes_and_spindle button anymore
        # — the quick bar carries Feed Hold only, per the current control
        # layout: MachinePage's own control column now has the full
        # Start/Feed-Hold/Stop/Reset/Single-Step set (including its own
        # Stop button wired to hold_axes_and_spindle()), so a duplicate
        # entry point for the same action in the quick bar would just be
        # two controls for one thing, and — as the prior version of this
        # button demonstrated — a second stop-like control next to Feed
        # Hold risks being mixed up with it under time pressure.

        self.return_btn = CardButton(icon=get_icon("return", tint=True), icon_size=48)
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

    def _on_tools_btn_clicked(self) -> None:
        self._stack.setCurrentIndex(_TOOLS_PAGE_INDEX)

    def _on_manual_btn_clicked(self) -> None:
        self._stack.setCurrentIndex(_MANUAL_PAGE_INDEX)

    def _on_workpieces_btn_clicked(self) -> None:
        # Always enter fresh at the workpiece list — see
        # WorkpiecesSection.reset(), called on the way back out below, so
        # this reset() here is mostly a defensive no-op for entries that
        # somehow bypassed the return button.
        self._workpieces_section.reset()
        self._stack.setCurrentIndex(_WORKPIECES_INDEX)

    def _on_return_clicked(self) -> None:
        # The Workpieces section owns its own navigation stack (list ->
        # WorkpieceDetailPage -> ProgramDetailPage, ...) — while it's not
        # at its base page, Return steps back one level in THAT stack
        # instead of jumping straight to Home. Every other page still
        # jumps to Home directly, exactly as before.
        if (
            self._stack.currentWidget() is self._workpieces_section
            and self._workpieces_section.can_pop()
        ):
            self._workpieces_section.pop()
            return
        self._workpieces_section.reset()
        self._stack.setCurrentIndex(_HOME_INDEX)

    def _on_load_in_machine_requested(self, gcode_path: str) -> None:
        """A ProgramDetailPage's "Ausführen" button fired (see
        WorkpiecesSection.load_in_machine_requested) — switch to
        MachinePage and load that file into it, closing the loop
        MachinePage's own "Datei laden" button opened
        (open_workpieces_requested, connected above).

        Switches the page FIRST, and only THEN loads the file, on a
        QTimer.singleShot(0, ...) — one event-loop turn later — rather
        than the reverse (load, then switch), which is what this used to
        do: MachinePage.load_file() runs G-code compilation and (via
        DatumSimWidget.set_file()) kicks off the voxel-grid rebuild, and
        the click that triggers this handler must never sit there
        waiting on either before the UI visibly reacts. Ordering it this
        way means the page transition itself is instant — the operator
        sees MachinePage appear immediately — and the load runs a moment
        later without blocking that transition. This does not by itself
        make load_file() cheaper (see DatumSimWidget._schedule_voxel_sim()
        for the actual voxel-build-off-the-GUI-thread fix); it only
        prevents the OLD page (the one this click was made on) from
        freezing while load_file() is running.
        """
        self._stack.setCurrentIndex(_MACHINE_PAGE_INDEX)
        # `self` as the context object — see machine_page.py's identical
        # pattern for why (dropped instead of run against an already-torn-
        # down window if this fires after destruction).
        QTimer.singleShot(0, self, lambda: self._machine_page.load_file(gcode_path))

    def _on_feed_hold_clicked(self) -> None:
        self._controller.set_feed_hold(True)

    def _on_program_state_for_quickbar(self, state: ProgramState) -> None:
        self._sync_feed_hold_quickbar(state, self._controller.feed_hold)

    def _on_feed_hold_for_quickbar(self, held: bool) -> None:
        # feed_hold is orthogonal to ProgramState (see MachineController.
        # feed_hold's docstring) — program_state_changed alone would never
        # fire when only this flag changes, so this connection is required.
        self._sync_feed_hold_quickbar(self._controller.program_state, held)

    def _sync_feed_hold_quickbar(self, state: ProgramState, held: bool) -> None:
        """Feed Hold only makes sense, and is only shown, while a program
        is RUNNING and not already held — it disappears the instant it's
        engaged (one-way trigger, not a toggle; see its construction
        comment) and stays out of the way otherwise."""
        self.feed_hold_btn.setVisible(state == ProgramState.RUNNING and not held)

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
        self.light_btn.set_icon(get_icon(icon_name, tint=True, size=QSize(48, 48)))

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
        self.coolant_btn.set_icon(
            get_icon("coolant_on" if checked else "coolant_off", tint=True)
        )
