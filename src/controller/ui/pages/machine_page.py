"""
ui/pages/machine_page.py — Machine page: 3D view + G-code + Start/Stop.

Viewport area:
  Always shows the DatumSimWidget (or SimPlaceholder). Even when no file is
  loaded the 3D widget fills the viewport so the window layout stays stable.

G-code editor area:
  A QStackedWidget toggles between:
    _GCODE_NO_FILE  — centred placeholder with workpieces icon + "Datei laden" button
    _GCODE_VIEWER   — GCodeViewer with the loaded G-code text

Machine-state rules:
  • Loading a file and viewing it in the 3D sim → always allowed.
  • controller.run_program() (the "Start" button) → still requires the machine
    to be ON, homed, and idle so the real LinuxCNC backend doesn't receive a
    program command prematurely.

program_state_changed is the single source of truth for MACHINE vs SIM mode
and for control-button enabled states.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from controller.core.machine.controller import ErrorSeverity, MachineController, MachineError
from controller.domain.models import Position, ProgramState
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card import Card
from controller.ui.widgets.card_button import CardButton
from controller.ui.widgets.gcode_highlighter import GCodeHighlighter
from controller.ui.widgets.gcode_viewer import GCodeViewer
from controller.ui.widgets.override_panel import OverridePanel
from controller.ui.widgets.program_info_card import ProgramInfoCard
from controller.ui.widgets.tool_info_card import ToolInfoCard

# Try to import the real 3D sim widget; fall back to the text placeholder if
# moderngl or numpy are not installed in this environment.
# NOTE: only ONE import in the try-block so a failure never silently overwrites
# an already-successful _SimWidget assignment.
try:
    from controller.sim.ui.main_widget import DatumSimWidget as _SimWidget  # noqa: F401
    _SIM_AVAILABLE = True
except ImportError:
    from controller.ui.widgets.sim_placeholder import SimPlaceholder as _SimWidget  # type: ignore[assignment]
    _SIM_AVAILABLE = False

logger = logging.getLogger(__name__)

_LOG_LEVEL = {
    ErrorSeverity.INFO:     logging.INFO,
    ErrorSeverity.WARNING:  logging.WARNING,
    ErrorSeverity.ERROR:    logging.ERROR,
    ErrorSeverity.CRITICAL: logging.CRITICAL,
}

# G-code editor stack indices
_GCODE_NO_FILE = 0
_GCODE_VIEWER  = 1

# Path to the example G-code file used as the default until a Workpieces page exists
_REPO_ROOT          = Path(__file__).resolve().parents[4]
_DEFAULT_GCODE_PATH = _REPO_ROOT / "workpieces" / "Gcode.cnc"


# ── No-file placeholder for the G-code editor area ────────────────────────────

class _GCodeNoFileWidget(Card):
    """Shown in the G-code editor area when no program is loaded.

    Mirrors the workpieces icon aesthetic the user liked; the "Datei laden"
    button is the primary call-to-action. The 3D viewport is always visible
    above this regardless of load state.
    """

    open_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)

        inner = QVBoxLayout()
        inner.setContentsMargins(16, 16, 16, 16)
        inner.setSpacing(10)
        inner.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_icon("workpieces", size=QSize(64, 64)).pixmap(64, 64))
        icon_lbl.setAlignment(Qt.AlignCenter)

        hint = QLabel("Kein Programm geladen")
        hint.setObjectName("CardTitle")
        hint.setAlignment(Qt.AlignCenter)

        open_btn = CardButton("Datei laden", icon=get_icon("workpieces"), icon_size=36)
        open_btn.setFixedSize(140, 52)
        open_btn.clicked.connect(self.open_clicked)

        inner.addStretch(1)
        inner.addWidget(icon_lbl)
        inner.addWidget(hint)
        inner.addSpacing(6)
        inner.addWidget(open_btn, alignment=Qt.AlignCenter)
        inner.addStretch(1)

        self.content_layout.addLayout(inner)


# ── Machine Page ──────────────────────────────────────────────────────────────

class MachinePage(QWidget):
    """Machine page combining the 3D viewer, G-code preview and run controls."""

    def __init__(
        self,
        controller: MachineController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller  = controller
        self._loaded_path: str | None = None

        controller.error_occurred.connect(self._on_error)

        # ── 3D Viewport: always visible ───────────────────────────────────────
        self._sim = _SimWidget(self)
        self._sim.set_mode("SIM")

        # ── Info row ──────────────────────────────────────────────────────────
        self._program_info = ProgramInfoCard(controller, self)
        self._tool_info    = ToolInfoCard(self)
        self._tool_info.set_tool(None)

        info_row = QHBoxLayout()
        info_row.setSpacing(12)
        info_row.addWidget(self._program_info, stretch=1)
        info_row.addWidget(self._tool_info)

        # ── Override sliders ──────────────────────────────────────────────────
        self._override_panel = OverridePanel(controller, self)

        # ── G-code editor area: no-file placeholder ↔ GCodeViewer ────────────
        self._gcode_stack = QStackedWidget(self)

        self._gcode_no_file = _GCodeNoFileWidget(self)
        self._gcode_no_file.open_clicked.connect(self._load_example_file)
        self._gcode_stack.addWidget(self._gcode_no_file)   # _GCODE_NO_FILE

        self._gcode_view = GCodeViewer(self)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self._gcode_view.text_edit.setFont(font)
        self._gcode_view.setContentsMargins(0, 0, 0, 0)
        self._highlighter = GCodeHighlighter(self._gcode_view.text_edit.document())
        self._gcode_stack.addWidget(self._gcode_view)      # _GCODE_VIEWER

        self._gcode_stack.setCurrentIndex(_GCODE_NO_FILE)

        # ── Machine control buttons (right column) ────────────────────────────
        # icon_size=36 keeps a 100×100 Card (16px margins → 68px content)
        # balanced: 36px icon + 4px gap + ~16px label = 56px, neatly centred.
        self._start_btn = CardButton("Start",     icon=get_icon("start",        tint=True), icon_size=36)
        self._stop_btn  = CardButton("Feed hold", icon=get_icon("stop",         tint=True), icon_size=36)
        self._reset_btn = CardButton("Reset",     icon=get_icon("reset",        tint=True), icon_size=36)
        self._single_block_btn = CardButton(
            "Single Block", icon=get_icon("single_block", tint=True), icon_size=32
        )
        self._single_block_btn.setCheckable(True)

        self._start_btn.setProperty("variant", "start")
        self._stop_btn.setProperty( "variant", "stop")
        self._reset_btn.setProperty("variant", "reset")
        self._single_block_btn.setProperty("variant", "single_block")

        self._start_btn.clicked.connect(self._on_start_clicked)
        self._stop_btn.clicked.connect( self._on_stop_clicked)
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        self._single_block_btn.toggled.connect(controller.set_single_block)
        controller.single_block_changed.connect(self._on_single_block_changed)

        controls_col = QVBoxLayout()
        controls_col.setSpacing(8)
        for btn in (self._start_btn, self._stop_btn,
                    self._reset_btn, self._single_block_btn):
            btn.setFixedSize(100, 100)
            controls_col.addWidget(btn)
        controls_col.addStretch(1)

        # ── Bottom row: gcode stack + control buttons ─────────────────────────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)
        bottom_row.addWidget(self._gcode_stack, stretch=1)
        bottom_row.addLayout(controls_col)

        # ── Root layout ────────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addWidget(self._sim, stretch=2)
        root.addLayout(info_row)
        root.addWidget(self._override_panel)
        root.addLayout(bottom_row, stretch=1)

        controller.position_changed.connect(self._on_position)
        controller.line_changed.connect(self._on_line)
        controller.program_state_changed.connect(self._sync_ui_state)

        # Feed/rapid overrides feed the sim widget's estimated part-time
        # display — feed_changed carries a FeedData with feed_override;
        # rapid_override_changed carries the fraction directly.
        controller.feed_changed.connect(
            lambda fd: self._sim.set_feed_override(fd.feed_override)
        )
        controller.rapid_override_changed.connect(self._sim.set_rapid_override)

        self._sync_ui_state(controller.program_state)

    # ── File loading (no machine-state requirement) ───────────────────────────

    def _load_example_file(self) -> None:
        """Load the default example G-code file into the sim viewer.

        No machine state is required for this — it only drives the visual
        simulation. The machine Start button is separate and still requires
        the machine to be ON, homed, and idle.

        TODO: replace with Workpieces-page navigation once that page exists.
        """
        path = str(_DEFAULT_GCODE_PATH)
        if not Path(path).exists():
            logger.warning("Beispieldatei nicht gefunden: %s", path)
            self._controller.error_occurred.emit(
                MachineError(
                    f"Beispieldatei nicht gefunden: {path}",
                    ErrorSeverity.WARNING, source="MachinePage",
                )
            )
            return
        self._do_load_file(path)

    def _do_load_file(self, path: str) -> None:
        """Load a G-code file into the sim viewer and G-code preview.

        Switches the G-code area to the viewer, populates it, and tells the
        3D sim to compile and display the program. Does NOT interact with the
        machine backend — no machine state is required.
        """
        self._loaded_path = path
        self._gcode_view.setPlainText(self._read_gcode_file(path))
        self._gcode_stack.setCurrentIndex(_GCODE_VIEWER)

        self._sim.set_file(path)

        # Update the program info card immediately (before the machine starts)
        self._program_info.set_file(path)

        # Re-evaluate button states now that a file is available
        self._sync_ui_state(self._controller.program_state)

    # ── UI state sync (single source of truth) ────────────────────────────────

    def _sync_ui_state(self, state: ProgramState) -> None:
        """Sync sim mode + button enabled states to the current program state."""
        now_running = state == ProgramState.RUNNING

        self._sim.set_mode("MACHINE" if now_running else "SIM")
        if now_running:
            pos = self._controller.position
            self._sim.set_position(pos.x, pos.y, pos.z)
            self._sim.set_line(self._controller.current_line)
        self._sim.set_state(state.name)

        file_loaded = self._loaded_path is not None
        self._start_btn.setEnabled(file_loaded and not now_running)
        self._stop_btn.setEnabled(now_running)

    # ── Controller signal handlers ────────────────────────────────────────────

    def _on_position(self, pos: Position) -> None:
        self._sim.set_position(pos.x, pos.y, pos.z)

    def _on_line(self, line: int) -> None:
        self._sim.set_line(line)
        if self._controller.program_state != ProgramState.IDLE:
            self._highlight_line(line)

    # ── Machine control buttons ───────────────────────────────────────────────

    def _on_start_clicked(self) -> None:
        """Start or resume the machine program.

        Requires the machine to be ON, homed, and idle (checked by
        MachineController.run_program). The 3D simulation viewer is
        independent of this and already running once a file is loaded.
        """
        if self._controller.program_state == ProgramState.PAUSED:
            self._controller.resume_program()
            return

        if self._loaded_path is None:
            # Shouldn't happen (button is disabled), but guard anyway
            self._load_example_file()
            return

        self._controller.run_program(self._loaded_path)

    def _on_stop_clicked(self) -> None:
        if self._controller.program_state == ProgramState.RUNNING:
            self._controller.pause_program()

    def _on_reset_clicked(self) -> None:
        if self._controller.program_state == ProgramState.RUNNING:
            self._controller.error_occurred.emit(
                MachineError(
                    "Reset nicht möglich: Programm läuft noch.",
                    ErrorSeverity.WARNING, source="MachinePage",
                )
            )
            return
        self._controller.rewind_program()
        text_edit = self._gcode_view.text_edit
        text_edit.setExtraSelections([])
        text_edit.moveCursor(QTextCursor.MoveOperation.Start)

    def _on_single_block_changed(self, enabled: bool) -> None:
        if enabled:
            self._start_btn.set_icon(get_icon("start_single_block", tint=True))
            self._start_btn.set_text("Step")
        else:
            self._start_btn.set_icon(get_icon("start", tint=True))
            self._start_btn.set_text("Start")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _highlight_line(self, line: int) -> None:
        text_edit = self._gcode_view.text_edit
        block = text_edit.document().findBlockByNumber(line)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)

        fmt = QTextCharFormat()
        fmt.setBackground(QColor(59, 130, 196, 60))

        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = fmt
        text_edit.setExtraSelections([selection])
        text_edit.setTextCursor(cursor)
        text_edit.centerCursor()

    @staticmethod
    def _read_gcode_file(path: str) -> str:
        data = Path(path).read_bytes()
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("cp1252")

    def _on_error(self, error: MachineError) -> None:
        logger.log(_LOG_LEVEL[error.severity], "%s", error.message)
