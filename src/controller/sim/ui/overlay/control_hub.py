"""
sim/ui/overlay/control_hub.py — Floating playback-control bar.

Overlaid at the bottom-center of DatumSimWidget. Contains:
  • Skip-backward / play-pause / skip-forward / stop buttons
  • Speed slider with a floating value label above the handle
  • Info row: WCS label | G-code line (syntax-highlighted) | tool label | feedrate

In MACHINE mode the playback buttons are hidden — the real machine drives
the display and simulation controls would conflict with it.
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QSize, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from controller.sim.core.settings import AppSettings

# Icons shipped with this package
_ICONS_DIR = Path(__file__).resolve().parents[3] / "assets" / "icons"

# Lookup from WCS index → G-word name
_WCS_NAMES = {
    1: "G54", 2: "G55", 3: "G56", 4: "G57", 5: "G58",
    6: "G59", 7: "G59.1", 8: "G59.2", 9: "G59.3",
}

# ── Shared widget styles (self-contained; intentionally not in .qss so the
#    overlay keeps its dark look regardless of the host app theme) ─────────────

_LABEL_STYLE = """
QLabel {
    background: rgba(24, 24, 26, 200);
    border: 1px solid rgba(255, 255, 255, 12%);
    border-radius: 6px;
    color: #E2E8F0;
    padding-left: 8px;
    padding-right: 8px;
    font-size: 13px;
    font-family: Consolas;
}
"""

_BTN_STYLE = """
QPushButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}
QPushButton:hover {
    background: rgba(255, 255, 255, 8%);
    border: 1px solid rgba(255, 255, 255, 10%);
}
QPushButton:pressed {
    background: rgba(255, 255, 255, 15%);
    border: 1px solid rgba(255, 255, 255, 20%);
}
"""

_SPEED_SLIDER_STYLE = """
QSlider { background: transparent; }
QSlider::groove:horizontal {
    height: 4px;
    background: rgba(255, 255, 255, 15%);
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #E2E8F0;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #E2E8F0;
    width: 12px; height: 12px;
    margin-top: -4px; margin-bottom: -4px;
    border-radius: 6px;
}
QSlider::handle:horizontal:hover { background: #FFFFFF; }
"""


# ── Helper widgets ────────────────────────────────────────────────────────────

class GCodeLine(QLabel):
    """Expandable label that syntax-highlights a G-code fragment."""

    # Letter → color for syntax coloring
    _COLORS = {
        "G": "#E06C75", "M": "#E06C75",
        "F": "#E5C07B", "S": "#C678DD",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        font = QFont("Consolas", 12)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)

    def set_gcode(self, raw_text: str) -> None:
        """Apply per-letter syntax coloring via inline HTML spans."""
        def _replace(m: re.Match) -> str:
            letter = m.group(1).upper()
            color  = self._COLORS.get(letter, "#E0E0E0")
            return f'<span style="color:{color};">{letter}{m.group(2)}</span>'

        text = re.sub(r'([A-Za-z])([-+]?\d*\.?\d+)', _replace, raw_text)
        text = re.sub(r'(\(.*?\))', r'<span style="color:#7F848E;">\1</span>', text)
        self.setText(text)


class SpeedSlider(QSlider):
    """Horizontal speed slider that shows a floating label above the handle while dragging."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Horizontal, parent)

        # Qt.ToolTip flag prevents the label being clipped by the slider boundary
        self._popup = QLabel(self, Qt.ToolTip)
        self._popup.setAlignment(Qt.AlignCenter)
        self._popup.setStyleSheet("""
            QLabel {
                background: rgba(30, 30, 30, 230);
                color: #E2E8F0;
                border: 1px solid rgba(255, 255, 255, 20%);
                border-radius: 4px;
                padding: 4px 8px;
                font-family: Consolas;
                font-size: 12px;
            }
        """)
        self._popup.hide()

        # Snap zone: values within ±10/+50 of 100 snap to 100 (1×)
        self._snap_target          = 100
        self._snap_threshold_neg   = 10
        self._snap_threshold_pos   = 50

        self.sliderPressed.connect(self._on_pressed)
        self.sliderReleased.connect(self._popup.hide)
        self.valueChanged.connect(self._on_value_changed)

    def _on_pressed(self) -> None:
        self._update_popup()
        self._popup.show()

    def _on_value_changed(self, val: int) -> None:
        lower = self._snap_target - self._snap_threshold_neg
        upper = self._snap_target + self._snap_threshold_pos
        if lower <= val <= upper and val != self._snap_target:
            self.setValue(self._snap_target)
            return
        if self._popup.isVisible():
            self._update_popup()

    def _update_popup(self) -> None:
        speed = self.value() / 100.0
        self._popup.setText(f"{speed:.2f}×")
        self._popup.adjustSize()

        opt_min = self.minimum()
        opt_max = self.maximum()
        span    = opt_max - opt_min
        hx      = self.width() // 2 if span == 0 else (
            6 + int((self.value() - opt_min) / span * (self.width() - 12))
        )

        global_pos = self.mapToGlobal(QPoint(hx, 0))
        self._popup.move(
            global_pos.x() - self._popup.width() // 2,
            global_pos.y() - self._popup.height() - 6,
        )


# ── ControlHub ────────────────────────────────────────────────────────────────

class ControlHub(QWidget):
    """Bottom-center floating playback and info bar for DatumSimWidget."""

    play_clicked          = Signal()
    pause_clicked         = Signal()
    stop_clicked          = Signal()
    skip_forward_clicked  = Signal()
    skip_backward_clicked = Signal()
    speed_changed         = Signal(float)   # 0.0 … 20.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(500, 100)
        self._state = 0    # 0=stopped/paused, 1=playing
        self._s     = AppSettings.instance()

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── Playback controls row ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_skip_backward = QPushButton(self)
        self.btn_play_pause    = QPushButton(self)
        self.btn_skip_forward  = QPushButton(self)
        self.btn_stop          = QPushButton(self)

        self.btn_play_pause.setIcon(   QIcon(str(_ICONS_DIR / "player-play.svg")))
        self.btn_stop.setIcon(         QIcon(str(_ICONS_DIR / "player-stop.svg")))
        self.btn_skip_backward.setIcon(QIcon(str(_ICONS_DIR / "player-skip-back.svg")))
        self.btn_skip_forward.setIcon( QIcon(str(_ICONS_DIR / "player-skip-forward.svg")))

        for btn in (self.btn_skip_backward, self.btn_play_pause,
                    self.btn_skip_forward, self.btn_stop):
            btn.setFixedSize(40, 40)
            btn.setIconSize(QSize(24, 24))
            btn.setStyleSheet(_BTN_STYLE)
            btn_row.addWidget(btn)

        btn_row.addStretch()

        self.slider_speed = SpeedSlider(self)
        self.slider_speed.setRange(0, 2000)
        self.slider_speed.setValue(100)
        self.slider_speed.setFixedWidth(250)
        self.slider_speed.setStyleSheet(_SPEED_SLIDER_STYLE)
        btn_row.addWidget(self.slider_speed)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Info row ──────────────────────────────────────────────────────────
        info_row = QHBoxLayout()
        info_row.setSpacing(6)

        # WCS label (G54 …)
        self._datum_lbl = QLabel("G54", self)
        self._datum_lbl.setFixedWidth(54)
        self._datum_lbl.setAlignment(Qt.AlignCenter)
        self._datum_lbl.setStyleSheet(_LABEL_STYLE)

        # G-code line (expandable) or invisible spacer
        self._gcode_line    = GCodeLine(self)
        self._gcode_line.setStyleSheet(_LABEL_STYLE)
        self._gcode_spacer  = QWidget(self)
        self._gcode_spacer.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._gcode_stack   = QStackedWidget(self)
        self._gcode_stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._gcode_stack.addWidget(self._gcode_line)    # index 0
        self._gcode_stack.addWidget(self._gcode_spacer)  # index 1

        # Tool number label
        self._tool_lbl = QLabel("T1", self)
        self._tool_lbl.setFixedWidth(40)
        self._tool_lbl.setAlignment(Qt.AlignCenter)
        self._tool_lbl.setStyleSheet(_LABEL_STYLE)

        # Feedrate label
        self._feed_lbl = QLabel("F  0", self)
        self._feed_lbl.setFixedWidth(90)
        self._feed_lbl.setAlignment(Qt.AlignCenter)
        self._feed_lbl.setStyleSheet(_LABEL_STYLE)

        info_row.addWidget(self._datum_lbl)
        info_row.addWidget(self._gcode_stack)
        info_row.addWidget(self._tool_lbl)
        info_row.addWidget(self._feed_lbl)
        root.addLayout(info_row)

        # Initial visibility from settings
        self._apply_visibility(
            self._s.show_gcode_line, self._s.show_datum,
            self._s.show_tool, self._s.show_feedrate,
        )

        # Connect signals
        self.btn_play_pause.clicked.connect(self._play_pause_clicked)
        self.btn_stop.clicked.connect(self._stop_clicked)
        self.btn_skip_forward.clicked.connect(self.skip_forward_clicked)
        self.btn_skip_backward.clicked.connect(self.skip_backward_clicked)
        self.slider_speed.valueChanged.connect(
            lambda v: self.speed_changed.emit(v / 100.0)
        )

        self._s.show_gcode_line_changed.connect(self._on_show_gcode_changed)
        self._s.show_datum_changed.connect(self._datum_lbl.setVisible)
        self._s.show_tool_changed.connect(self._tool_lbl.setVisible)
        self._s.show_feedrate_changed.connect(self._feed_lbl.setVisible)

    # ── Visibility ────────────────────────────────────────────────────────────

    def _apply_visibility(self, gcode: bool, datum: bool, tool: bool, feedrate: bool) -> None:
        self._gcode_stack.setCurrentIndex(0 if gcode else 1)
        self._datum_lbl.setVisible(datum)
        self._tool_lbl.setVisible(tool)
        self._feed_lbl.setVisible(feedrate)

    def _on_show_gcode_changed(self, visible: bool) -> None:
        self._gcode_stack.setCurrentIndex(0 if visible else 1)

    # ── Data setters ──────────────────────────────────────────────────────────

    def set_gcode(self, raw_text: str) -> None:
        self._gcode_line.set_gcode(raw_text)

    def set_datum(self, wcs_index: int) -> None:
        self._datum_lbl.setText(_WCS_NAMES.get(wcs_index, f"G{wcs_index}"))

    def set_tool(self, tool_number: int) -> None:
        self._tool_lbl.setText(f"T{tool_number}")

    def set_feedrate(self, feed_mm_min: float) -> None:
        if feed_mm_min < 1.0:
            self._feed_lbl.setText("Rapid")
        else:
            self._feed_lbl.setText(f"F{int(feed_mm_min):>5}")

    # ── Button handlers ───────────────────────────────────────────────────────

    def _play_pause_clicked(self) -> None:
        if self._state == 0:
            self.btn_play_pause.setIcon(QIcon(str(_ICONS_DIR / "player-pause.svg")))
            self._state = 1
            self.play_clicked.emit()
        else:
            self.btn_play_pause.setIcon(QIcon(str(_ICONS_DIR / "player-play.svg")))
            self._state = 0
            self.pause_clicked.emit()

    def _stop_clicked(self) -> None:
        self.btn_play_pause.setIcon(QIcon(str(_ICONS_DIR / "player-play.svg")))
        self._state = 0
        self.stop_clicked.emit()

    def reset_play_state(self) -> None:
        """Reset the play/pause button to the 'stopped' icon."""
        self.btn_play_pause.setIcon(QIcon(str(_ICONS_DIR / "player-play.svg")))
        self._state = 0

    # ── Mode ──────────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        """Show/hide sim controls based on SIM vs. MACHINE mode.

        In MACHINE mode playback buttons are hidden — the real controller drives
        the tool position, so simulation controls would be meaningless.
        """
        assert mode in ("SIM", "MACHINE")
        show = (mode == "SIM")
        for w in (self.btn_skip_backward, self.btn_play_pause,
                  self.btn_skip_forward, self.btn_stop, self.slider_speed):
            w.setVisible(show)
            w.setEnabled(show)
