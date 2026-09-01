"""
ui/pages/machine_page.py — Machine page: 3D view + G-code + Start/Stop.

DatumSimWidget defaults to SIM mode (local preview, no controller
involvement). Start switches it to MACHINE mode and calls
controller.run_program() — the ONLY place that transition happens.
program_state_changed is the single source of truth for both the
sim widget's mode and the button states; nothing else sets them.
"""

from __future__ import annotations

from cgitb import enable
from pathlib import Path

from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor, QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
)

from datum_sim.ui.main_widget import DatumSimWidget

from src.controller.core.machine.controller import MachineController, MachineError, ErrorSeverity
from src.controller.domain.models import Position, ProgramState
from src.controller.ui.widgets.card_button import CardButton
from src.controller.ui.icon_loader import get_icon
from src.controller.ui.widgets.gcode_highlighter import GCodeHighlighter
from src.controller.ui.widgets.gcode_viewer import GCodeViewer
from src.controller.ui.widgets.override_panel import OverridePanel
from src.controller.ui.widgets.program_info_card import ProgramInfoCard
from src.controller.ui.widgets.tool_info_card import ToolInfoCard

# TODO: Platzhalter, bis die Workpieces-Seite einen echten Pfad liefert.
_TEST_GCODE_PATH = r"C:\Users\felix\PycharmProjects\DATUM-controll\workpieces\Gcode.cnc"


class MachinePage(QWidget):

    def __init__(
        self,
        controller: MachineController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._loaded_path: str | None = None

        controller.error_occurred.connect(self._on_error)

        self._sim = DatumSimWidget(self)
        self._sim.set_mode("SIM")

        # Info Row
        self._program_info = ProgramInfoCard(controller, self)
        self._tool_info = ToolInfoCard(self)
        self._tool_info.set_tool(None)

        info_row = QHBoxLayout()
        info_row.setSpacing(12)
        info_row.addWidget(self._program_info, stretch=1)
        info_row.addWidget(self._tool_info)

        # Override
        self._override_panel = OverridePanel(controller, self)

        # Gcode Editor
        self._gcode_view = GCodeViewer(self)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self._gcode_view.text_edit.setFont(font)
        self._gcode_view.setContentsMargins(0, 0, 0, 0)
        self._highlighter = GCodeHighlighter(self._gcode_view.text_edit.document())

        # Buttons
        self._start_btn = CardButton("Start", icon=get_icon("start", tint=True), icon_size=64)
        self._stop_btn = CardButton("Feed hold",icon=get_icon("stop", tint=True), icon_size=64)
        self._reset_btn = CardButton("Reset",icon=get_icon("reset", tint=True), icon_size=64)
        self._single_block_btn = CardButton("Single Block", icon=get_icon("single_block", tint=True), icon_size=48)
        self._single_block_btn.setCheckable(True)
        self._start_btn.setProperty("variant", "start")
        self._stop_btn.setProperty("variant", "stop")
        self._reset_btn.setProperty("variant", "reset")
        self._single_block_btn.setProperty("variant", "single_block")
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        self._single_block_btn.toggled.connect(controller.set_single_block)
        controller.single_block_changed.connect(self._on_single_block_changed)

        controls_col = QVBoxLayout()
        controls_col.setSpacing(8)
        for btn in (self._start_btn, self._stop_btn, self._reset_btn, self._single_block_btn):
            btn.setFixedSize(100, 100)
            controls_col.addWidget(btn)
        controls_col.addStretch(1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)
        bottom_row.addWidget(self._gcode_view, stretch=1)
        bottom_row.addLayout(controls_col)

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

        self._sync_ui_state(controller.program_state)

    # ------------------------------------------------------------------
    # Einzige Stelle, die Sim-Modus + Button-Zustand entscheidet
    # ------------------------------------------------------------------

    def _sync_ui_state(self, state: ProgramState) -> None:
        now_running = state == ProgramState.RUNNING

        self._sim.set_mode("MACHINE" if now_running else "SIM")
        if now_running:
            pos = self._controller.position
            self._sim.set_position(pos.x, pos.y, pos.z)
            self._sim.set_line(self._controller.current_line)

        self._sim.set_state(state.name)
        self._start_btn.setEnabled(not now_running)
        self._stop_btn.setEnabled(now_running)

    # ------------------------------------------------------------------
    # Controller-Signale -> Anzeige
    # ------------------------------------------------------------------

    def _on_position(self, pos: Position) -> None:
        self._sim.set_position(pos.x, pos.y, pos.z)

    def _on_line(self, line: int) -> None:
        self._sim.set_line(line)
        if self._controller.program_state != ProgramState.IDLE:
            self._highlight_line(line)

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def _on_start_clicked(self) -> None:
        if self._controller.program_state == ProgramState.PAUSED:
            self._controller.resume_program()
            return

        path = _TEST_GCODE_PATH
        if not Path(path).exists():
            print(f"[MachinePage] G-Code-Datei nicht gefunden: {path}")
            return

        self._loaded_path = path
        self._gcode_view.setPlainText(self._read_gcode_file(path))
        self._sim.set_file(path)
        self._controller.run_program(path)

    def _on_stop_clicked(self) -> None:
        if self._controller.program_state == ProgramState.RUNNING:
            self._controller.pause_program()

    def _on_reset_clicked(self) -> None:
        if self._controller.program_state == ProgramState.RUNNING:
            self._controller.error_occurred.emit(
                MachineError("Reset nicht möglich: Programm läuft noch.", ErrorSeverity.WARNING, source="MachinePage")
            )
            return
        self._controller.rewind_program()
        text_edit = self._gcode_view.text_edit
        text_edit.setExtraSelections([])
        text_edit.moveCursor(QTextCursor.MoveOperation.Start)

    def _on_single_block_changed(self, enabled: bool)->None:
        if enabled:
            self._start_btn.set_icon(get_icon("start_single_block", tint=True))
            self._start_btn.set_text("Step")
        else:
            self._start_btn.set_icon(get_icon("start", tint=True))
            self._start_btn.set_text("Start")
    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------

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

    def _on_error(self, error) -> None:
        print(f"[MachinePage] {error.severity.name}: {error.message}")