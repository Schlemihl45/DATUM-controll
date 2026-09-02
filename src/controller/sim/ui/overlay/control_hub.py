"""
sim/ui/overlay/control_hub.py — Floating playback-control bar.

Overlaid at the bottom-centre of DatumSimWidget.  No container background —
the widget is fully transparent; each info-label chip carries its own dark
rounded pill, and the play/stop buttons are icon-only with a subtle hover glow.

Layout
------
Controls-only (all info labels hidden):
    [8 px top] [buttons ─ stretch ─ slider ─ stretch] [8 px bottom]

Controls + any info label active:
    [8 px] [buttons ─ stretch ─ slider ─ stretch]
    [8 px] [datum  gcode-line…  tool  feedrate]
    [8 px]

Equal 8 px gaps above, between, and below give an evenly-spaced look.
The widget height adjusts automatically whenever info-label visibility changes;
``layout_changed`` is emitted so the parent can reposition.

Speed slider — bidirectional:
  • Centre (value = 0)   → paused / frozen at current position
  • +100 (default snap)  → +1× real-time forward
  • Right of centre      → faster forward (up to +20×)
  • Left of centre       → faster reverse (down to −10×)
  • Snaps: 0 (±30) and 100 (±20)
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QSize, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from controller.sim.core.settings import AppSettings

# Icons live in the shared resources folder (parents[3] = src/controller/)
_ICONS_DIR = Path(__file__).resolve().parents[3] / "ui" / "resources" / "icons"

# WCS index → G-word label
_WCS_NAMES = {
    1: "G54", 2: "G55", 3: "G56", 4: "G57", 5: "G58",
    6: "G59", 7: "G59.1", 8: "G59.2", 9: "G59.3",
}

# Speed slider range and snap constants
_SPEED_MIN      = -1000   # −10× reverse
_SPEED_MAX      = +2000   # +20× forward
_SPEED_DEFAULT  =  +100   # +1× real-time
_SNAP_ZERO_R    =   30    # snap to 0 within ±30
_SNAP_ONE_LOW   =   80    # snap to 100 if value in [80, 150]
_SNAP_ONE_HIGH  =  150

# ── Per-widget inline styles (dark overlay aesthetic) ─────────────────────────
# Self-contained so the overlay looks the same regardless of the app QSS theme.

_LABEL_STYLE = """
QLabel {
    background: rgba(24, 24, 26, 180);
    border: 1px solid rgba(255, 255, 255, 10%);
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
    background: rgba(226, 232, 240, 120);
    border-radius: 2px;
}
QSlider::add-page:horizontal {
    background: rgba(255, 255, 255, 15%);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #E2E8F0;
    width: 14px; height: 14px;
    margin-top: -5px; margin-bottom: -5px;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: #FFFFFF; }
"""


# ── Helper: format a duration for the part-time pill ──────────────────────────

def _format_duration(seconds: float) -> str:
    """Format seconds as "M:SS", or "H:MM:SS" once an hour is reached."""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── Helper: load SVG icon ─────────────────────────────────────────────────────

def _svg_icon(name: str, size: int = 24) -> QIcon:
    """Load an SVG from the shared resources/icons/ directory into a QIcon."""
    from PySide6.QtGui import QPixmap
    from PySide6.QtSvg import QSvgRenderer

    path = _ICONS_DIR / name
    renderer = QSvgRenderer(str(path))
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


# ── GCode syntax-highlighted label ────────────────────────────────────────────

class GCodeLine(QLabel):
    """Expandable label that syntax-highlights a G-code fragment."""

    _COLORS = {
        "G": "#E06C75", "M": "#E06C75",
        "F": "#E5C07B", "S": "#C678DD",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        font = QFont("Consolas", 12)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

    def set_gcode(self, raw_text: str) -> None:
        def _replace(m: re.Match) -> str:
            letter = m.group(1).upper()
            color  = self._COLORS.get(letter, "#E0E0E0")
            return f'<span style="color:{color};">{letter}{m.group(2)}</span>'

        text = re.sub(r'([A-Za-z])([-+]?\d*\.?\d+)', _replace, raw_text)
        text = re.sub(r'(\(.*?\))', r'<span style="color:#7F848E;">\1</span>', text)
        self.setText(text)


# ── Bidirectional speed slider ────────────────────────────────────────────────

class SpeedSlider(QSlider):
    """Horizontal speed slider with centre = 0, popup label while dragging.

    Range: _SPEED_MIN to _SPEED_MAX (symmetric around 0 in appearance).
    Centre position (value 0) = frozen / paused position.
    Positive = forward playback, negative = reverse.
    Snaps to 0 (±30 ticks) and to +100 / 1× (±20 ticks of 100).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)

        self._popup = QLabel(self, Qt.WindowType.ToolTip)
        self._popup.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._popup.setStyleSheet("""
            QLabel {
                background: rgba(30, 30, 30, 230);
                color: #E2E8F0;
                border: 1px solid rgba(255,255,255,20%);
                border-radius: 4px;
                padding: 4px 8px;
                font-family: Consolas;
                font-size: 12px;
            }
        """)
        self._popup.hide()

        self.sliderPressed.connect(self._on_pressed)
        self.sliderReleased.connect(self._popup.hide)
        self.valueChanged.connect(self._on_value_changed)

    def _on_pressed(self) -> None:
        self._update_popup()
        self._popup.show()

    def _on_value_changed(self, val: int) -> None:
        # Snap to 0
        if -_SNAP_ZERO_R <= val <= _SNAP_ZERO_R and val != 0:
            self.setValue(0)
            return
        # Snap to +100 (1× real-time)
        if _SNAP_ONE_LOW <= val <= _SNAP_ONE_HIGH and val != 100:
            self.setValue(100)
            return

        if self._popup.isVisible():
            self._update_popup()

    def _update_popup(self) -> None:
        val   = self.value()
        speed = val / 100.0
        if val == 0:
            label = "0× — pausiert"
        elif val > 0:
            label = f"+{speed:.1f}×"
        else:
            label = f"{speed:.1f}× ↩"
        self._popup.setText(label)
        self._popup.adjustSize()

        span  = self.maximum() - self.minimum()
        ratio = (val - self.minimum()) / span if span else 0.5
        hx    = int(6 + ratio * (self.width() - 12))

        gp    = self.mapToGlobal(QPoint(hx, 0))
        self._popup.move(
            gp.x() - self._popup.width() // 2,
            gp.y() - self._popup.height() - 6,
        )


# ── ControlHub ────────────────────────────────────────────────────────────────

class ControlHub(QWidget):
    """Bottom-centre floating playback and info bar for DatumSimWidget.

    Fully transparent — no container background painted.  Width is fixed at
    520 px; height adjusts automatically when info-label visibility changes.
    ``layout_changed`` is emitted after each resize so the parent can
    reposition the widget.
    """

    play_clicked          = Signal()
    pause_clicked         = Signal()
    stop_clicked          = Signal()
    skip_forward_clicked  = Signal()
    skip_backward_clicked = Signal()
    speed_changed         = Signal(float)   # −10.0 … +20.0
    layout_changed        = Signal()        # emitted when height changes

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(520)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._state = 0    # 0 = stopped/paused icon, 1 = playing icon
        self._s     = AppSettings.instance()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 8)
        root.setSpacing(8)

        # ── Playback control row ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.btn_skip_backward = QPushButton(self)
        self.btn_play_pause    = QPushButton(self)
        self.btn_skip_forward  = QPushButton(self)
        self.btn_stop          = QPushButton(self)

        self.btn_play_pause.setIcon(   _svg_icon("player-play.svg"))
        self.btn_stop.setIcon(         _svg_icon("player-stop.svg"))
        self.btn_skip_backward.setIcon(_svg_icon("player-skip-back.svg"))
        self.btn_skip_forward.setIcon( _svg_icon("player-skip-forward.svg"))

        for btn in (self.btn_skip_backward, self.btn_play_pause,
                    self.btn_skip_forward, self.btn_stop):
            btn.setFixedSize(40, 40)
            btn.setIconSize(QSize(24, 24))
            btn.setStyleSheet(_BTN_STYLE)
            btn_row.addWidget(btn)

        btn_row.addStretch()

        # Speed slider (bidirectional)
        self.slider_speed = SpeedSlider(self)
        self.slider_speed.setRange(_SPEED_MIN, _SPEED_MAX)
        self.slider_speed.setValue(_SPEED_DEFAULT)
        self.slider_speed.setFixedWidth(240)
        self.slider_speed.setStyleSheet(_SPEED_SLIDER_STYLE)
        btn_row.addWidget(self.slider_speed)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Info container (transparent, hidden when all labels are off) ───────
        self._info_container = QWidget(self)
        self._info_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        info_row = QHBoxLayout(self._info_container)
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(6)

        self._datum_lbl = QLabel("G54", self._info_container)
        self._datum_lbl.setFixedWidth(54)
        self._datum_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._datum_lbl.setStyleSheet(_LABEL_STYLE)

        self._gcode_lbl = GCodeLine(self._info_container)
        self._gcode_lbl.setStyleSheet(_LABEL_STYLE)

        self._tool_lbl = QLabel("T1", self._info_container)
        self._tool_lbl.setFixedWidth(40)
        self._tool_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tool_lbl.setStyleSheet(_LABEL_STYLE)

        self._feed_lbl = QLabel("F  0", self._info_container)
        self._feed_lbl.setFixedWidth(90)
        self._feed_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._feed_lbl.setStyleSheet(_LABEL_STYLE)

        self._time_lbl = QLabel("--:--", self._info_container)
        self._time_lbl.setFixedWidth(64)
        self._time_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_lbl.setStyleSheet(_LABEL_STYLE)

        info_row.addWidget(self._datum_lbl)
        info_row.addWidget(self._gcode_lbl)
        info_row.addWidget(self._tool_lbl)
        info_row.addWidget(self._feed_lbl)
        info_row.addWidget(self._time_lbl)

        root.addWidget(self._info_container)

        # Initial visibility from settings
        self._apply_visibility(
            self._s.show_gcode_line, self._s.show_datum,
            self._s.show_tool, self._s.show_feedrate, self._s.show_part_time,
        )

        # Connect signals
        self.btn_play_pause.clicked.connect(self._play_pause_clicked)
        self.btn_stop.clicked.connect(self._stop_clicked)
        self.btn_skip_forward.clicked.connect(self.skip_forward_clicked)
        self.btn_skip_backward.clicked.connect(self.skip_backward_clicked)
        self.slider_speed.valueChanged.connect(
            lambda v: self.speed_changed.emit(v / 100.0)
        )

        self._s.show_gcode_line_changed.connect(self._on_gcode_vis)
        self._s.show_datum_changed.connect(self._on_datum_vis)
        self._s.show_tool_changed.connect(self._on_tool_vis)
        self._s.show_feedrate_changed.connect(self._on_feedrate_vis)
        self._s.show_part_time_changed.connect(self._on_part_time_vis)

    # ── Visibility management ─────────────────────────────────────────────────

    def _apply_visibility(
        self, gcode: bool, datum: bool, tool: bool, feedrate: bool, part_time: bool = True,
    ) -> None:
        self._gcode_lbl.setVisible(gcode)
        self._datum_lbl.setVisible(datum)
        self._tool_lbl.setVisible(tool)
        self._feed_lbl.setVisible(feedrate)
        self._time_lbl.setVisible(part_time)
        self._sync_info()

    def _sync_info(self) -> None:
        """Show info container iff any label is visible; resize and notify parent.

        Uses layout().activate() to force an immediate size recalculation before
        reading sizeHint() — without this, Qt may return a stale value immediately
        after setVisible() and the widget gets the wrong height.
        """
        # isHidden() checks the widget's own visibility flag, independent of
        # parent state.  isVisible() returns False when any ancestor is hidden,
        # so it would permanently block the container from ever appearing.
        any_vis = (
            not self._datum_lbl.isHidden()
            or not self._gcode_lbl.isHidden()
            or not self._tool_lbl.isHidden()
            or not self._feed_lbl.isHidden()
            or not self._time_lbl.isHidden()
        )
        self._info_container.setVisible(any_vis)
        # Force the VBoxLayout to recalculate immediately (not deferred)
        self.layout().activate()
        new_h = self.sizeHint().height()
        if new_h > 0:
            old_h = self.height()
            self.setFixedSize(520, new_h)
            if new_h != old_h:
                self.layout_changed.emit()

    def _on_gcode_vis(self, visible: bool) -> None:
        self._gcode_lbl.setVisible(visible)
        self._sync_info()

    def _on_datum_vis(self, visible: bool) -> None:
        self._datum_lbl.setVisible(visible)
        self._sync_info()

    def _on_tool_vis(self, visible: bool) -> None:
        self._tool_lbl.setVisible(visible)
        self._sync_info()

    def _on_feedrate_vis(self, visible: bool) -> None:
        self._feed_lbl.setVisible(visible)
        self._sync_info()

    def _on_part_time_vis(self, visible: bool) -> None:
        self._time_lbl.setVisible(visible)
        self._sync_info()

    # ── Data setters ──────────────────────────────────────────────────────────

    def set_gcode(self, raw_text: str) -> None:
        self._gcode_lbl.set_gcode(raw_text)

    def set_datum(self, wcs_index: int) -> None:
        self._datum_lbl.setText(_WCS_NAMES.get(wcs_index, f"G{wcs_index}"))

    def set_tool(self, tool_number: int) -> None:
        self._tool_lbl.setText(f"T{tool_number}")

    def set_part_time(self, seconds: float | None) -> None:
        """Show the approximated total part/cycle time (whole program)."""
        self._time_lbl.setText(_format_duration(seconds) if seconds is not None else "--:--")

    def set_feedrate(self, feed_mm_min: float) -> None:
        if feed_mm_min < 1.0:
            self._feed_lbl.setText("Rapid")
        else:
            self._feed_lbl.setText(f"F{int(feed_mm_min):>5}")

    # ── Button handlers ───────────────────────────────────────────────────────

    def _play_pause_clicked(self) -> None:
        if self._state == 0:
            self.btn_play_pause.setIcon(_svg_icon("player-pause.svg"))
            self._state = 1
            self.play_clicked.emit()
        else:
            self.btn_play_pause.setIcon(_svg_icon("player-play.svg"))
            self._state = 0
            self.pause_clicked.emit()

    def _stop_clicked(self) -> None:
        self.btn_play_pause.setIcon(_svg_icon("player-play.svg"))
        self._state = 0
        self.stop_clicked.emit()

    def reset_play_state(self) -> None:
        """Reset play/pause button to the stopped icon."""
        self.btn_play_pause.setIcon(_svg_icon("player-play.svg"))
        self._state = 0

    # ── Mode ──────────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        """Hide/show sim controls in MACHINE mode (controller drives position)."""
        assert mode in ("SIM", "MACHINE")
        show = (mode == "SIM")
        for w in (self.btn_skip_backward, self.btn_play_pause,
                  self.btn_skip_forward, self.btn_stop, self.slider_speed):
            w.setVisible(show)
            w.setEnabled(show)
