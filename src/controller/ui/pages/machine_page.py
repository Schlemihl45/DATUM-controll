"""
ui/pages/machine_page.py — Machine page: 3D view + G-code + Start/Stop.

Two top-level view states (QStackedWidget in the viewport area):

  _VIEW_NO_FILE — No program loaded yet.
      Shows a centred "Datei laden" card-button.
      Clicking it loads the example G-code file immediately.
      (Later: navigates to the Workpieces page to pick a file.)

  _VIEW_SIM — A G-code file is loaded.
      Shows the DatumSimWidget (3D path + ControlHub) or SimPlaceholder.
      The sim is in SIM mode and can be played back via the ControlHub
      overlay buttons — no machine state is required for this.

Machine-state rules:
  • Loading a file and viewing it in the 3D sim → always allowed.
  • controller.run_program() (the "Start" button in the control column)
    → still requires the machine to be ON, homed, and idle so the real
    LinuxCNC backend doesn't receive a program command prematurely.

program_state_changed is the single source of truth for the MACHINE vs SIM
mode transition and for the control button states.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtCore import QSize

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

# View-stack indices
_VIEW_NO_FILE = 0
_VIEW_SIM     = 1

# Path to the example G-code file used as the default until a Workpieces page exists
_REPO_ROOT          = Path(__file__).resolve().parents[4]
_DEFAULT_GCODE_PATH = _REPO_ROOT / "workpieces" / "Gcode.cnc"


# ── No-file placeholder widget ────────────────────────────────────────────────

class _NoFileWidget(Card):
    """Shown in the viewport area when no G-code file is loaded.

    Contains a single centred call-to-action button. For now that button
    loads the example file; later it will navigate to the Workpieces page.
    """

    open_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)

        inner = QVBoxLayout()
        inner.setContentsMargins(24, 24, 24, 24)
        inner.setSpacing(16)
        inner.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_icon("workpieces", size=QSize(96, 96)).pixmap(96, 96))
        icon_lbl.setAlignment(Qt.AlignCenter)

        hint = QLabel("Kein Programm geladen")
        hint.setObjectName("CardTitle")
        hint.setAlignment(Qt.AlignCenter)

        open_btn = CardButton("Datei laden", icon=get_icon("workpieces"), icon_size=48)
        open_btn.setFixedSize(160, 60)
        open_btn.clicked.connect(self.open_clicked)

        inner.addStretch(1)
        inner.addWidget(icon_lbl)
        inner.addWidget(hint)
        inner.addSpacing(8)
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

        # ── Viewport area: no-file card  ↔  sim widget ────────────────────────
        self._view_stack = QStackedWidget(self)

        self._no_file_widget = _NoFileWidget(self)
        self._no_file_widget.open_clicked.connect(self._load_example_file)
        self._view_stack.addWidget(self._no_file_widget)   # _VIEW_NO_FILE

        self._sim = _SimWidget(self)
        self._sim.set_mode("SIM")
        self._view_stack.addWidget(self._sim)              # _VIEW_SIM

        self._view_stack.setCurrentIndex(_VIEW_NO_FILE)

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

        # ── G-code viewer ─────────────────────────────────────────────────────
        self._gcode_view = GCodeViewer(self)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self._gcode_view.text_edit.setFont(font)
        self._gcode_view.setContentsMargins(0, 0, 0, 0)
        self._highlighter = GCodeHighlighter(self._gcode_view.text_edit.document())

        # ── Machine control buttons (right column) ────────────────────────────
        self._start_btn = CardButton("Start", icon=get_icon("start", tint=True), icon_size=64)
        self._stop_btn  = CardButton("Feed hold", icon=get_icon("stop",  tint=True), icon_size=64)
        self._reset_btn = CardButton("Reset", icon=get_icon("reset", tint=True), icon_size=64)
        self._single_block_btn = CardButton(
            "Single Block", icon=get_icon("single_block", tint=True), icon_size=48
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

        # ── Bottom row: gcode viewer + control buttons ─────────────────────────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)
        bottom_row.addWidget(self._gcode_view, stretch=1)
        bottom_row.addLayout(controls_col)

        # ── Root layout ────────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addWidget(self._view_stack, stretch=2)
        root.addLayout(info_row)
        root.addWidget(self._override_panel)
        root.addLayout(bottom_row, stretch=1)

        controller.position_changed.connect(self._on_position)
        controller.line_changed.connect(self._on_line)
        controller.program_state_changed.connect(self._sync_ui_state)

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

        Switches the viewport area to the sim widget and populates the
        G-code viewer. Does NOT interact with the machine backend.
        """
        self._loaded_path = path
        self._gcode_view.setPlainText(self._read_gcode_file(path))
        self._sim.set_file(path)
        self._view_stack.setCurrentIndex(_VIEW_SIM)
        # Re-evaluate button states now that a file is available
        self._sync_ui_state(self._controller.program_state)

    # ── UI state sync (single source of truth) ────────────────────────────────

    def _sync_ui_state(self, state: ProgramState) -> None:
        """Sync sim mode + button enabled states to the current program state.

        Called on every program_state_changed signal and after file load.
        """
        now_running = state == ProgramState.RUNNING

        # Switch sim between live-follow mode and playback mode
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
