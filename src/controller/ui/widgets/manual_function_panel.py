"""
ui/widgets/manual_function_panel.py — ManualFunctionPanel: the mid-section
of ManualPage (see manual_page.py's __init__), a horizontal row of two
independent function sections — Spindle and Homing — separated by a
vertical divider, replacing the old plain "Referenzieren" card (Spindle
control did not exist on ManualPage before this).

Controller-injected, same convention as JogControlPanel/manual_page.py —
see jog_control_panel.py's module docstring for the reasoning.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from controller.core.machine.controller import MachineController
from controller.domain.models import FeedData

# axis name -> jog/home axis index (0=X 1=Y 2=Z), same mapping as
# MachineController.jog_continuous()/home_joint(). No A/B/C here — the
# redesigned ManualPage covers X/Y/Z only (see the approved plan).
_LINEAR_AXES = [("X", 0), ("Y", 1), ("Z", 2)]


def _vline() -> QFrame:
    """Vertical section divider — same ParamSeparator look tool_card_widget.py
    already uses between its geometry/flutes/specific/lifecycle groups."""
    line = QFrame()
    line.setObjectName("ParamSeparator")
    line.setFixedWidth(1)
    return line


class ManualFunctionPanel(QWidget):
    """See module docstring."""

    def __init__(self, controller: MachineController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        root.addLayout(self._build_spindle_section(), stretch=1)
        root.addWidget(_vline())
        root.addLayout(self._build_homing_section(), stretch=1)

        self._controller.feed_changed.connect(self._on_feed_changed)

    # ── Spindle ──────────────────────────────────────────────────────────────

    def _build_spindle_section(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)
        hdr = QLabel("Spindle")
        hdr.setObjectName("CardTitle")
        col.addWidget(hdr)

        row = QHBoxLayout()
        row.setSpacing(8)

        self._rpm_spin = QDoubleSpinBox()
        self._rpm_spin.setObjectName("SpindleRpmSpin")
        self._rpm_spin.setRange(0.0, 24000.0)
        self._rpm_spin.setDecimals(0)
        self._rpm_spin.setSingleStep(100.0)
        self._rpm_spin.setSuffix(" RPM")
        self._rpm_spin.setValue(1000.0)
        row.addWidget(self._rpm_spin, stretch=1)

        self._spindle_btn = QPushButton("Spindle")
        self._spindle_btn.setCheckable(True)
        self._spindle_btn.toggled.connect(self._on_spindle_toggled)
        row.addWidget(self._spindle_btn)

        col.addLayout(row)
        col.addStretch(1)
        return col

    def _on_spindle_toggled(self, checked: bool) -> None:
        if checked:
            self._controller.spindle_on(self._rpm_spin.value())
        else:
            self._controller.spindle_off()

    def _on_feed_changed(self, feed: FeedData) -> None:
        # No dedicated spindle_running signal exists (see
        # MachineController's own docstrings) — derive it from feed_changed's
        # FeedData.spindle_rpm instead. blockSignals() to avoid re-triggering
        # _on_spindle_toggled() and re-issuing a spindle_on()/spindle_off()
        # call for a state change that already happened on the backend.
        running = feed.spindle_rpm != 0
        if self._spindle_btn.isChecked() != running:
            self._spindle_btn.blockSignals(True)
            self._spindle_btn.setChecked(running)
            self._spindle_btn.blockSignals(False)

    # ── Homing ───────────────────────────────────────────────────────────────

    def _build_homing_section(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)
        hdr = QLabel("Referenzieren")
        hdr.setObjectName("CardTitle")
        col.addWidget(hdr)

        self._home_all_btn = QPushButton("Home All Axis")
        self._home_all_btn.clicked.connect(self._controller.home_all)
        col.addWidget(self._home_all_btn)

        axis_row = QHBoxLayout()
        axis_row.setSpacing(6)
        self._home_axis_buttons: list[QPushButton] = []
        for label, axis_index in _LINEAR_AXES:
            btn = QPushButton(f"Home {label}")
            btn.clicked.connect(lambda _c=False, a=axis_index: self._controller.home_joint(a))
            axis_row.addWidget(btn)
            self._home_axis_buttons.append(btn)
        col.addLayout(axis_row)

        col.addStretch(1)
        return col

    # ── Guards ───────────────────────────────────────────────────────────────

    def refresh_guards(self, can_jog: bool, can_home: bool) -> None:
        """Spindle controls gated on can_jog (same ON+idle precondition as
        jog — see MachineController.spindle_on()'s own gate), Homing on
        can_home (ON only)."""
        self._rpm_spin.setEnabled(can_jog)
        self._spindle_btn.setEnabled(can_jog)

        self._home_all_btn.setEnabled(can_home)
        for btn in self._home_axis_buttons:
            btn.setEnabled(can_home)
