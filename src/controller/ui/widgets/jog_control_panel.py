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
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from controller.core.machine.controller import MachineController
from controller.sim.core.settings import AppSettings
from controller.ui.icon_loader import get_icon

_STEP_SIZES_MM = [1.0, 0.1, 0.01]
_AXIS_ICON = {0: "x-coordinate", 1: "y-coordinate", 2: "z-coordinate"}
_ICON_SIZE = QSize(20, 20)
_BTN_SIZE = QSize(56, 56)

# Diagonal corner label, keyed by (sign_x, sign_y) — no dedicated diagonal
# icon asset exists, so these stay plain arrow glyphs (see module docstring
# on why the edge buttons DO get axis icons but corners don't).
_DIAGONAL_LABELS: dict[tuple[int, int], str] = {
    (1, 1): "↗", (1, -1): "↘", (-1, 1): "↖", (-1, -1): "↙",
}


def _axis_button(axis: int, sign: int) -> QPushButton:
    """Direction button for one axis (X/Y/Z, +/-): axis-colored icon
    (tint=False — preserves the existing red/green/blue X/Y/Z coding on
    x/y/z-coordinate.svg; see icon_loader.get_icon()'s own docstring on
    when NOT to tint) plus a plain "+"/"-" label, since no separate
    plus/minus icon asset exists per axis."""
    btn = QPushButton("+" if sign > 0 else "−")
    btn.setIcon(get_icon(_AXIS_ICON[axis], tint=False, size=_ICON_SIZE))
    btn.setIconSize(_ICON_SIZE)
    btn.setFixedSize(_BTN_SIZE)
    btn.setAutoRepeat(False)
    return btn


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
        self._movement_buttons: list[QPushButton] = []
        self._step_buttons: dict[QPushButton, float] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        root.addLayout(self._build_step_column())
        root.addLayout(self._build_matrix())
        root.addLayout(self._build_z_column())

    # ── Step column (left) ──────────────────────────────────────────────────

    def _build_step_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)
        hdr = QLabel("Schrittweite")
        hdr.setObjectName("CardTitle")
        col.addWidget(hdr)

        for mm in _STEP_SIZES_MM:
            btn = QPushButton(f"{mm:g} mm")
            btn.setObjectName("JogStepButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, b=btn: self._on_step_clicked(b, checked))
            self._step_buttons[btn] = mm
            col.addWidget(btn)
        col.addStretch(1)
        return col

    def _on_step_clicked(self, button: QPushButton, checked: bool) -> None:
        # Manual mutual exclusion (not QButtonGroup(exclusive=True)):
        # re-clicking the already-active button must be able to switch BACK
        # to continuous mode, which an exclusive QButtonGroup can't do (it
        # always keeps exactly one button checked).
        if checked:
            for other in self._step_buttons:
                if other is not button:
                    other.setChecked(False)
            self._active_step = self._step_buttons[button]
        else:
            self._active_step = None

    # ── 3x3 X/Y matrix (center) ─────────────────────────────────────────────

    def _build_matrix(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)
        hdr = QLabel("X / Y")
        hdr.setObjectName("CardTitle")
        col.addWidget(hdr)

        grid = QGridLayout()
        grid.setSpacing(4)

        # (row, col) -> [(axis, sign), ...] — one entry for an edge, two for
        # a diagonal corner. Conventional CNC jog-pad layout: Y+ top-middle,
        # X-/X+ left/right, Y- bottom-middle, diagonals in the corners.
        positions: dict[tuple[int, int], list[tuple[int, float]]] = {
            (0, 0): [(0, -1.0), (1, 1.0)],
            (0, 1): [(1, 1.0)],
            (0, 2): [(0, 1.0), (1, 1.0)],
            (1, 0): [(0, -1.0)],
            (1, 2): [(0, 1.0)],
            (2, 0): [(0, -1.0), (1, -1.0)],
            (2, 1): [(1, -1.0)],
            (2, 2): [(0, 1.0), (1, -1.0)],
        }
        for (row, grid_col), axes in positions.items():
            if len(axes) == 1:
                axis, sign = axes[0]
                btn = _axis_button(axis, int(sign))
            else:
                signs = (int(axes[0][1]), int(axes[1][1]))
                btn = QPushButton(_DIAGONAL_LABELS[signs])
                btn.setFixedSize(_BTN_SIZE)
                btn.setAutoRepeat(False)
            btn.pressed.connect(lambda a=axes: self._on_jog_press(a))
            btn.released.connect(lambda a=axes: self._on_jog_release(a))
            self._movement_buttons.append(btn)
            grid.addWidget(btn, row, grid_col)

        self._rapid_btn = QPushButton("Rapid")
        self._rapid_btn.setObjectName("RapidToggle")
        self._rapid_btn.setCheckable(True)
        self._rapid_btn.setFixedSize(_BTN_SIZE)
        self._rapid_btn.setToolTip(
            "Eilgang (Rapid) für Jog aktivieren/deaktivieren — betrifft nur "
            "die Jog-Geschwindigkeit (siehe Einstellungen -> Manuell), kein "
            "eigener Maschinen-Modus (das Backend kennt keinen separaten "
            "Rapid-Jog)."
        )
        grid.addWidget(self._rapid_btn, 1, 1)

        col.addLayout(grid)
        col.addStretch(1)
        return col

    # ── Z column (right) ────────────────────────────────────────────────────

    def _build_z_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)
        hdr = QLabel("Z")
        hdr.setObjectName("CardTitle")
        col.addWidget(hdr)
        col.addStretch(1)

        z_plus = _axis_button(2, 1)
        z_plus.pressed.connect(lambda: self._on_jog_press([(2, 1.0)]))
        z_plus.released.connect(lambda: self._on_jog_release([(2, 1.0)]))
        self._movement_buttons.append(z_plus)
        col.addWidget(z_plus)

        z_minus = _axis_button(2, -1)
        z_minus.pressed.connect(lambda: self._on_jog_press([(2, -1.0)]))
        z_minus.released.connect(lambda: self._on_jog_release([(2, -1.0)]))
        self._movement_buttons.append(z_minus)
        col.addWidget(z_minus)

        col.addStretch(1)
        return col

    # ── Jog press/release — see module docstring for step vs. continuous ──

    def _on_jog_press(self, axes: list[tuple[int, float]]) -> None:
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

    # ── Guards ───────────────────────────────────────────────────────────────

    def refresh_guards(self, can_jog: bool) -> None:
        """Enable/disable every movement button (edges/corners/Z). Step-size
        buttons and the Rapid toggle stay always enabled — they only select
        a mode, they never move anything by themselves."""
        for btn in self._movement_buttons:
            btn.setEnabled(can_jog)
