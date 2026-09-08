"""
ui/widgets/jog_control_panel.py — JogControlPanel: the CNC-style jog pad
used by ManualPage's top section (see manual_page.py's __init__), replacing
the old plain text-button Jog card.

Layout (three columns in one QHBoxLayout — see the approved plan):
  1. Step-size column: three toggle buttons (1 / 0.1 / 0.01 mm), at most one
     checked at a time. NONE checked -> continuous jog (the default).
  2. 3x3 X/Y matrix: edges = X+/X-/Y+/Y-, corners = the four diagonals,
     center = a Feed/Rapid toggle button.
  3. Z column: Z+/Z- buttons, vertically centered against the matrix.

Every button is built explicitly, one at a time — no loop/dict-driven
generation. Every button except the three step-size buttons carries its
own dedicated icon (no shared axis icon + "+"/"-" text overlay anymore);
step buttons stay text-only ("1 mm" etc.), per explicit request.

CardButton is used throughout instead of QPushButton, for the same visual
language as the rest of the app (Card/CardButton). This required CardButton
to gain pressed/released signals (see card_button.py's updated docstring)
— QFrame has neither built in, and the old QPushButton-based version relied
on exactly those two events for hold-to-jog behavior. Do not swap the
movement buttons for a widget that only offers `clicked`: `clicked` fires
on press with no matching release event, so jog_stop() would never be
called and an axis could jog indefinitely.

Icon assets assumed but not yet confirmed to exist — create/rename as
needed: "jog-x-plus"/"jog-x-minus"/"jog-y-plus"/"jog-y-minus" (axis-colored,
tint=False, same convention as the old x/y/z-coordinate icons — direction
now baked into the icon itself instead of a separate "+"/"-" label),
"jog-z-plus"/"jog-z-minus" (same), "jog-diagonal-ne"/"jog-diagonal-nw"/
"jog-diagonal-se"/"jog-diagonal-sw" (generic, tinted — a diagonal mixes two
axis colors, so no single axis color applies), "rapid" (generic, tinted).

Controller-injected (not signal-based) — consistent with the existing
manual_page.py/override_panel.py convention in this module cluster: this
widget calls MachineController directly rather than emitting its own
signals for ManualPage to relay. There is no second consumer of this
widget that would justify a signal-decoupled API.

No backend Rapid-jog mode exists — jog_continuous()/jog_increment() just
take a velocity (mm/s, sign = direction; see MachineController's own
docstrings). Feed vs. Rapid here is purely two AppSettings-configurable
velocity presets (jog_feed_velocity_mm_s / jog_rapid_velocity_mm_s),
swapped by the center toggle button — no separate machine-side mode.

No vector/diagonal jog in the backend either: a diagonal button presses
BOTH axes via two jog_continuous() calls and releases both via two
jog_stop() calls, with velocity normalized by 1/sqrt(2) per axis so the
resulting vector speed doesn't exceed the configured feed/rapid speed.

Step mode vs. continuous mode:
  - Step mode (a step-size button is checked): press fires jog_increment()
    immediately for every axis involved — no release handling needed, the
    move is already a fixed, self-terminating distance.
  - Continuous mode (no step button checked): press fires jog_continuous(),
    release IMMEDIATELY fires jog_stop() — never left running.
"""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget, QPushButton

from controller.core.machine.controller import MachineController
from controller.sim.core.settings import AppSettings
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card_button import CardButton

_ICON_SIZE = QSize(64, 64)
_BTN_SIZE = QSize(124, 124)
_STEP_BTN_SIZE = QSize(64, 64)


class JogControlPanel(QWidget):
    """See module docstring."""

    def __init__(
        self,
        controller: MachineController,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = settings
        self._active_step: float | None = None
        self._movement_buttons: list[CardButton] = []
        self._step_buttons: dict[QPushButton, float] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        # Linke Seite
        left_layout = QHBoxLayout()
        left_layout.addStretch(1)
        left_layout.addLayout(self._build_step_column())

        # Rechte Seite
        right_layout = QHBoxLayout()
        right_layout.addLayout(self._build_z_column())
        right_layout.addStretch(1)

        # Zusammenfügen mit der Matrix in der exakten Mitte
        root.addLayout(left_layout, 1)
        root.addLayout(self._build_matrix(), 0)  # Matrix in der Mitte ohne flexiblen Stretch
        root.addLayout(right_layout, 1)

    # ── Step column (left) — text-only, no icons, per explicit request ─────

    def _build_step_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addStretch(1)

        step_1 = QPushButton("1 mm")
        step_1.setFixedSize(_STEP_BTN_SIZE)
        step_1.setProperty("variant", "jog_step")
        step_1.setCheckable(True)
        step_1.clicked.connect(lambda: self._on_step_clicked(step_1))
        self._step_buttons[step_1] = 1.0
        col.addWidget(step_1)

        step_01 = QPushButton("0.1 mm")
        step_01.setFixedSize(_STEP_BTN_SIZE)
        step_01.setProperty("variant", "jog_step")
        step_01.setCheckable(True)
        step_01.clicked.connect(lambda: self._on_step_clicked(step_01))
        self._step_buttons[step_01] = 0.1
        col.addWidget(step_01)

        step_001 = QPushButton("0.01 mm")
        step_001.setFixedSize(_STEP_BTN_SIZE)
        step_001.setProperty("variant", "jog_step")
        step_001.setCheckable(True)
        step_001.clicked.connect(lambda: self._on_step_clicked(step_001))
        self._step_buttons[step_001] = 0.01
        col.addWidget(step_001)

        col.addStretch(1)
        return col

    def _on_step_clicked(self, button: QPushButton) -> None:
        # Manual mutual exclusion (not QButtonGroup(exclusive=True)):
        # re-clicking the already-active button must be able to switch BACK
        # to continuous mode, which an exclusive QButtonGroup can't do (it
        # always keeps exactly one button checked). CardButton.toggle()
        # already ran (see mousePressEvent) by the time this fires, so
        # button.isChecked() reflects the NEW state here.
        if button.isChecked():
            for other in self._step_buttons:
                if other is not button:
                    other.setChecked(False)
            self._active_step = self._step_buttons[button]
        else:
            self._active_step = None

    # ── 3x3 X/Y matrix (center) — every position built explicitly ──────────

    def _build_matrix(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)

        grid = QGridLayout()
        grid.setSpacing(12)


        nw_btn = CardButton(icon=get_icon("arrow_tl", tint=True, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        nw_btn.setFixedSize(_BTN_SIZE)
        nw_btn.pressed.connect(lambda: self._on_jog_press([(0, -1.0), (1, 1.0)]))
        nw_btn.released.connect(lambda: self._on_jog_release([(0, -1.0), (1, 1.0)]))
        self._movement_buttons.append(nw_btn)
        grid.addWidget(nw_btn, 0, 0)

        y_plus_btn = CardButton(icon=get_icon("arrow_up", tint=False, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        y_plus_btn.setFixedSize(_BTN_SIZE)
        y_plus_btn.pressed.connect(lambda: self._on_jog_press([(1, 1.0)]))
        y_plus_btn.released.connect(lambda: self._on_jog_release([(1, 1.0)]))
        self._movement_buttons.append(y_plus_btn)
        grid.addWidget(y_plus_btn, 0, 1)

        ne_btn = CardButton(icon=get_icon("arrow_tr", tint=True, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        ne_btn.setFixedSize(_BTN_SIZE)
        ne_btn.pressed.connect(lambda: self._on_jog_press([(0, 1.0), (1, 1.0)]))
        ne_btn.released.connect(lambda: self._on_jog_release([(0, 1.0), (1, 1.0)]))
        self._movement_buttons.append(ne_btn)
        grid.addWidget(ne_btn, 0, 2)

        x_minus_btn = CardButton(icon=get_icon("arrow_left", tint=False, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        x_minus_btn.setFixedSize(_BTN_SIZE)
        x_minus_btn.pressed.connect(lambda: self._on_jog_press([(0, -1.0)]))
        x_minus_btn.released.connect(lambda: self._on_jog_release([(0, -1.0)]))
        self._movement_buttons.append(x_minus_btn)
        grid.addWidget(x_minus_btn, 1, 0)

        self._rapid_btn = CardButton("Rapid", icon=get_icon("rapid", tint=True, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        self._rapid_btn.setProperty("variant", "rapid_toggle")
        self._rapid_btn.setCheckable(True)
        self._rapid_btn.setFixedSize(_BTN_SIZE)
        self._rapid_btn.clicked.connect(lambda: self._on_rapid_clicked())
        grid.addWidget(self._rapid_btn, 1, 1)

        x_plus_btn = CardButton(icon=get_icon("arrow_right", tint=False, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        x_plus_btn.setFixedSize(_BTN_SIZE)
        x_plus_btn.pressed.connect(lambda: self._on_jog_press([(0, 1.0)]))
        x_plus_btn.released.connect(lambda: self._on_jog_release([(0, 1.0)]))
        self._movement_buttons.append(x_plus_btn)
        grid.addWidget(x_plus_btn, 1, 2)

        sw_btn = CardButton(icon=get_icon("arrow_bl", tint=True, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        sw_btn.setFixedSize(_BTN_SIZE)
        sw_btn.pressed.connect(lambda: self._on_jog_press([(0, -1.0), (1, -1.0)]))
        sw_btn.released.connect(lambda: self._on_jog_release([(0, -1.0), (1, -1.0)]))
        self._movement_buttons.append(sw_btn)
        grid.addWidget(sw_btn, 2, 0)

        y_minus_btn = CardButton(icon=get_icon("arrow_down", tint=False, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        y_minus_btn.setFixedSize(_BTN_SIZE)
        y_minus_btn.pressed.connect(lambda: self._on_jog_press([(1, -1.0)]))
        y_minus_btn.released.connect(lambda: self._on_jog_release([(1, -1.0)]))
        self._movement_buttons.append(y_minus_btn)
        grid.addWidget(y_minus_btn, 2, 1)

        se_btn = CardButton(icon=get_icon("arrow_br", tint=True, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        se_btn.setFixedSize(_BTN_SIZE)
        se_btn.pressed.connect(lambda: self._on_jog_press([(0, 1.0), (1, -1.0)]))
        se_btn.released.connect(lambda: self._on_jog_release([(0, 1.0), (1, -1.0)]))
        self._movement_buttons.append(se_btn)
        grid.addWidget(se_btn, 2, 2)

        for btn in [x_plus_btn, x_minus_btn, y_plus_btn, y_minus_btn, ne_btn, nw_btn, se_btn, sw_btn]:
            btn.setProperty("variant", "jog_matrix_btn")

        col.addLayout(grid)
        col.addStretch(1)
        return col

    # ── Z column (right) — built explicitly, each with its own icon ────────

    def _build_z_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addStretch(1)

        z_plus = CardButton(icon=get_icon("arrow_up", tint=False, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        z_plus.setFixedSize(_BTN_SIZE)
        z_plus.pressed.connect(lambda: self._on_jog_press([(2, 1.0)]))
        z_plus.released.connect(lambda: self._on_jog_release([(2, 1.0)]))
        z_plus.setProperty("variant", "jog_matrix_btn")
        self._movement_buttons.append(z_plus)
        col.addWidget(z_plus)

        z_minus = CardButton(icon=get_icon("arrow_down", tint=False, size=_ICON_SIZE), icon_size=_ICON_SIZE)
        z_minus.setFixedSize(_BTN_SIZE)
        z_minus.pressed.connect(lambda: self._on_jog_press([(2, -1.0)]))
        z_minus.released.connect(lambda: self._on_jog_release([(2, -1.0)]))
        z_minus.setProperty("variant", "jog_matrix_btn")
        self._movement_buttons.append(z_minus)
        col.addWidget(z_minus)

        col.addStretch(1)
        return col

    # ── Jog press/release — see module docstring for step vs. continuous ──

    def _on_jog_press(self, axes: list[tuple[int, float]]) -> None:
        print(axes)
        velocity = (
            self._settings.jog_rapid_velocity_mm_s if self._rapid_btn.isChecked()
            else self._settings.jog_feed_velocity_mm_s
        )
        if self._active_step is not None:
            for axis, sign in axes:
                self._controller.jog_increment(axis, sign * velocity, self._active_step)
            return
        # Diagonal: normalize per-axis speed so the resulting vector speed
        # doesn't exceed the configured feed/rapid velocity.
        scale = (2 ** -0.5) if len(axes) > 1 else 1.0
        for axis, sign in axes:
            self._controller.jog_continuous(axis, sign * velocity * scale)

    def _on_jog_release(self, axes: list[tuple[int, float]]) -> None:
        if self._active_step is not None:
            return   # step mode is self-terminating — nothing to stop
        for axis, _sign in axes:
            self._controller.jog_stop(axis)

    def _on_rapid_clicked(self):
        if self._rapid_btn.isChecked():
            self._rapid_btn.set_icon(get_icon("feedrate_iso", tint=False, size=_ICON_SIZE))
        else:
            self._rapid_btn.set_icon(get_icon("rapid", tint=False, size=_ICON_SIZE))
    # ── Guards ───────────────────────────────────────────────────────────────

    def refresh_guards(self, can_jog: bool) -> None:
        """Enable/disable every movement button (edges/corners/Z). Step-size
        buttons and the Rapid toggle stay always enabled — they only select
        a mode, they never move anything by themselves."""
        for btn in self._movement_buttons:
            btn.setEnabled(can_jog)