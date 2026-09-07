"""
ui/pages/manual_page.py — ManualPage: manual machine operation (Jog,
Spindle, Homing, MDI, and a guided Touch-off/WCS-zero helper built on top
of MDI).

Reached from the home screen's "Manuell" button (main_window.py's
manual_page_btn — the renamed, previously-disabled setup_page_btn slot),
registered in the QStackedWidget analogous to ToolPage.

The top (Jog) and mid (Spindle/Homing) sections are the dedicated
JogControlPanel/ManualFunctionPanel widgets (ui/widgets/jog_control_panel.py,
ui/widgets/manual_function_panel.py) — see their own module docstrings for
layout and interaction details. MDI and Touch-off stay simple Cards below
them, unchanged from before this redesign.

Deliberately out of scope here (see the Workpieces-page follow-up prompt,
Abschnitt B): tool length/diameter measurement ("Teach"). That needs real
touch-probing hardware to make any sense — tool_card_widget.py's "Measure"
stub stays a stub, and this page adds no second one.

Every one of Jog/Spindle/Homing/MDI/Touch-off has its own precondition
(machine ON, not RUNNING, homed where relevant) — rather than re-deriving
"ON + idle [+ homed]" repeatedly, this page reads it once per group from
MachineController's public can_jog/can_home/can_mdi guard properties (added
alongside this page specifically so other future callers get the same
single source of truth) and re-evaluates them whenever
machine_state_changed/homed_changed/program_state_changed fire.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
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
from controller.ui.widgets.jog_control_panel import JogControlPanel
from controller.ui.widgets.manual_function_panel import ManualFunctionPanel


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

        self._jog_panel = JogControlPanel(self._controller, self._settings)
        self._function_panel = ManualFunctionPanel(self._controller)
        col.addWidget(self._jog_panel)
        col.addWidget(self._function_panel)
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

        self._jog_panel.refresh_guards(can_jog)
        self._function_panel.refresh_guards(can_jog, can_home)

        self._mdi_edit.setEnabled(can_mdi)
        self._mdi_send_btn.setEnabled(can_mdi)

        # Touch-off is MDI under the hood — same precondition.
        for btn in self._touch_off_buttons:
            btn.setEnabled(can_mdi)
