"""
ui/pages/manual_page.py — ManualPage: manual machine operation (Jog,
Homing, MDI, and a guided Touch-off/WCS-zero helper built on top of MDI).

Reached from the home screen's "Manuell" button (main_window.py's
manual_page_btn — the renamed, previously-disabled setup_page_btn slot),
registered in the QStackedWidget analogous to ToolPage.

Deliberately out of scope here (see the Workpieces-page follow-up prompt,
Abschnitt B): tool length/diameter measurement ("Teach"). That needs real
touch-probing hardware to make any sense — tool_card_widget.py's "Measure"
stub stays a stub, and this page adds no second one.

Every one of Jog/Homing/MDI/Touch-off has its own precondition (machine
ON, not RUNNING, homed where relevant) — rather than re-deriving "ON +
idle [+ homed]" four separate times, this page reads it once per group
from MachineController's public can_jog/can_home/can_mdi guard
properties (added alongside this page specifically so other future
callers get the same single source of truth) and re-evaluates them
whenever machine_state_changed/homed_changed/program_state_changed fire.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScroller,
    QVBoxLayout,
    QWidget,
)

from controller.core.machine.controller import MachineController
from controller.sim.core.settings import AppSettings
from controller.ui.widgets.card import Card

# axis name -> jog_continuous()/jog_increment()'s axis index (0=X 1=Y 2=Z
# 3=A 4=B 5=C, per MachineController.jog_continuous's own docstring).
_LINEAR_AXES = [("X", 0), ("Y", 1), ("Z", 2)]
_ROTARY_AXES = [("A", 3), ("B", 4), ("C", 5)]

_JOG_VELOCITY_MM_S = 10.0
_INCREMENTS_MM = [0.01, 0.1, 1.0, 10.0]


class ManualPage(QWidget):

    def __init__(
        self, controller: MachineController, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = AppSettings.instance()

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        QScroller.grabGesture(scroll.viewport(), QScroller.ScrollerGestureType.TouchGesture)

        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(10)

        col.addWidget(self._build_jog_card())
        col.addWidget(self._build_homing_card())
        col.addWidget(self._build_mdi_card())
        col.addWidget(self._build_touch_off_card())
        col.addStretch(1)

        scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        self._controller.machine_state_changed.connect(lambda _s: self._refresh_guards())
        self._controller.homed_changed.connect(lambda _h: self._refresh_guards())
        self._controller.program_state_changed.connect(lambda _p: self._refresh_guards())
        self._refresh_guards()

    # ------------------------------------------------------------------
    # Jog
    # ------------------------------------------------------------------

    def _build_jog_card(self) -> Card:
        card = Card(title="Jog")
        grid = QGridLayout()
        grid.setSpacing(6)

        self._jog_buttons: list[QPushButton] = []

        self._increment_combo = QComboBox()
        for mm in _INCREMENTS_MM:
            self._increment_combo.addItem(f"{mm:g} mm", mm)
        self._increment_combo.setCurrentIndex(1)   # 0.1 mm default

        axes = list(_LINEAR_AXES)
        if self._settings.has_rotary_axes:
            axes += _ROTARY_AXES

        for row, (label, axis_index) in enumerate(axes):
            grid.addWidget(QLabel(label), row, 0)

            minus_btn = QPushButton("−")
            minus_btn.setAutoRepeat(False)
            minus_btn.pressed.connect(
                lambda a=axis_index: self._controller.jog_continuous(a, -_JOG_VELOCITY_MM_S)
            )
            minus_btn.released.connect(lambda a=axis_index: self._controller.jog_stop(a))
            grid.addWidget(minus_btn, row, 1)
            self._jog_buttons.append(minus_btn)

            step_minus_btn = QPushButton("− Schritt")
            step_minus_btn.clicked.connect(
                lambda _c=False, a=axis_index: self._jog_step(a, -1)
            )
            grid.addWidget(step_minus_btn, row, 2)
            self._jog_buttons.append(step_minus_btn)

            step_plus_btn = QPushButton("+ Schritt")
            step_plus_btn.clicked.connect(
                lambda _c=False, a=axis_index: self._jog_step(a, 1)
            )
            grid.addWidget(step_plus_btn, row, 3)
            self._jog_buttons.append(step_plus_btn)

            plus_btn = QPushButton("+")
            plus_btn.setAutoRepeat(False)
            plus_btn.pressed.connect(
                lambda a=axis_index: self._controller.jog_continuous(a, _JOG_VELOCITY_MM_S)
            )
            plus_btn.released.connect(lambda a=axis_index: self._controller.jog_stop(a))
            grid.addWidget(plus_btn, row, 4)
            self._jog_buttons.append(plus_btn)

        card.content_layout.addLayout(grid)

        increment_row = QHBoxLayout()
        increment_row.addWidget(QLabel("Schrittweite:"))
        increment_row.addWidget(self._increment_combo)
        increment_row.addStretch(1)
        card.content_layout.addLayout(increment_row)

        return card

    def _jog_step(self, axis_index: int, sign: int) -> None:
        distance = self._increment_combo.currentData()
        self._controller.jog_increment(axis_index, sign * _JOG_VELOCITY_MM_S, distance)

    # ------------------------------------------------------------------
    # Homing
    # ------------------------------------------------------------------

    def _build_homing_card(self) -> Card:
        card = Card(title="Referenzieren")
        row = QHBoxLayout()

        self._home_all_btn = QPushButton("Alle referenzieren")
        self._home_all_btn.clicked.connect(self._controller.home_all)
        row.addWidget(self._home_all_btn)

        self._home_axis_buttons: list[QPushButton] = []
        axes = list(_LINEAR_AXES)
        if self._settings.has_rotary_axes:
            axes += _ROTARY_AXES
        for label, axis_index in axes:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _c=False, a=axis_index: self._controller.home_joint(a))
            row.addWidget(btn)
            self._home_axis_buttons.append(btn)

        card.content_layout.addLayout(row)
        return card

    # ------------------------------------------------------------------
    # MDI
    # ------------------------------------------------------------------

    def _build_mdi_card(self) -> Card:
        card = Card(title="MDI")
        row = QHBoxLayout()

        self._mdi_edit = QLineEdit()
        self._mdi_edit.setPlaceholderText("G-Code-Befehl …")
        self._mdi_edit.returnPressed.connect(self._on_mdi_send)
        row.addWidget(self._mdi_edit, stretch=1)

        self._mdi_send_btn = QPushButton("Senden")
        self._mdi_send_btn.clicked.connect(self._on_mdi_send)
        row.addWidget(self._mdi_send_btn)

        card.content_layout.addLayout(row)
        return card

    def _on_mdi_send(self) -> None:
        command = self._mdi_edit.text().strip()
        if not command:
            return
        self._controller.send_mdi(command)
        self._mdi_edit.clear()

    # ------------------------------------------------------------------
    # Touch-off / WCS zero-point — a thin guided wrapper around send_mdi(),
    # not a new backend method: builds the same "G10 L20 P<wcs> <axis>0"
    # MDI command a manual entry would, just without requiring the operator
    # to type it out (or know the syntax) themselves.
    # ------------------------------------------------------------------

    def _build_touch_off_card(self) -> Card:
        card = Card(title="Nullpunkt setzen")
        row = QHBoxLayout()

        self._touch_off_buttons: list[QPushButton] = []
        for label, axis_letter in (("X", "X"), ("Y", "Y"), ("Z", "Z")):
            btn = QPushButton(f"{label} = 0")
            btn.clicked.connect(lambda _c=False, a=axis_letter: self._touch_off(a))
            row.addWidget(btn)
            self._touch_off_buttons.append(btn)

        card.content_layout.addLayout(row)
        return card

    def _touch_off(self, axis_letter: str) -> None:
        wcs = self._controller.active_wcs
        self._controller.send_mdi(f"G10 L20 P{wcs} {axis_letter}0")

    # ------------------------------------------------------------------
    # Shared guard refresh — see module docstring.
    # ------------------------------------------------------------------

    def _refresh_guards(self) -> None:
        can_jog = self._controller.can_jog
        can_home = self._controller.can_home
        can_mdi = self._controller.can_mdi

        for btn in self._jog_buttons:
            btn.setEnabled(can_jog)

        self._home_all_btn.setEnabled(can_home)
        for btn in self._home_axis_buttons:
            btn.setEnabled(can_home)

        self._mdi_edit.setEnabled(can_mdi)
        self._mdi_send_btn.setEnabled(can_mdi)

        # Touch-off is MDI under the hood — same precondition.
        for btn in self._touch_off_buttons:
            btn.setEnabled(can_mdi)
