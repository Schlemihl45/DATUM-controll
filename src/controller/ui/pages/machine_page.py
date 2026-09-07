"""
ui/pages/machine_page.py — Machine page: 3D view + G-code + Start/Stop.

Viewport area:
  Always shows the DatumSimWidget (or SimPlaceholder). Even when no file is
  loaded the 3D widget fills the viewport so the window layout stays stable.

G-code editor area:
  A QStackedWidget toggles between:
    _GCODE_NO_FILE  — centred placeholder with workpieces icon + "Datei laden" button,
                      which no longer loads a fixed file itself: it emits
                      open_workpieces_requested, and main_window.py switches the
                      app over to the Workpieces page so the user picks a real
                      program. load_file() (public) is what actually loads a
                      chosen file back in here afterward — called from
                      main_window.py when a ProgramDetailPage's "In Maschine
                      laden" button (ui/pages/program_detail_page.py) fires.
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

from PySide6.QtCore import Qt, QSize, QTimer, Signal
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
from controller.sim.core.settings import AppSettings
from controller.sim.gcode.compiler import validate_tools
from controller.sim.simulation.tool_database import get_tool_by_pocket
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
        icon_lbl.setPixmap(get_icon("workpieces", tint=True, size=QSize(64, 64)).pixmap(64, 64))
        icon_lbl.setAlignment(Qt.AlignCenter)

        hint = QLabel("Kein Programm geladen")
        hint.setObjectName("CardTitle")
        hint.setAlignment(Qt.AlignCenter)

        open_btn = CardButton("Datei laden", icon=get_icon("workpieces", tint=True), icon_size=36)
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

    # Emitted by the "Datei laden" placeholder button — main_window.py
    # switches the app to the Workpieces page in response (see module
    # docstring). MachinePage itself no longer picks/loads any file on
    # its own initiative.
    open_workpieces_requested = Signal()

    def __init__(
        self,
        controller: MachineController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller  = controller
        self._loaded_path: str | None = None
        # True once DatumSimWidget.file_ready fires for _loaded_path — set_file()
        # is now fire-and-forget (see load_file()/_on_sim_file_ready()), so
        # this gates Start etc. until the background compile has actually
        # landed, not just been requested.
        self._file_ready: bool = False

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
        self._gcode_no_file.open_clicked.connect(self.open_workpieces_requested)
        self._gcode_stack.addWidget(self._gcode_no_file)   # _GCODE_NO_FILE

        self._gcode_view = GCodeViewer(self)
        font = QFont("Helvetica")
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
        # Five controls, stacked vertically — Start / Feed-Hold / Stop /
        # Reset / Single-Step. This replaces the earlier Start/"Pause"
        # (pause_program(), spindle kept running)/Reset/Single-Block
        # layout, which had two problems: "Pause" was a name for
        # pause_program() that read as a synonym for the app-wide quick
        # bar's actual Feed Hold button (set_feed_hold()) despite being a
        # different, more drastic action; and pause_program() itself (axes
        # stop, spindle keeps spinning) is no longer wired to any UI
        # control at all — hold_axes_and_spindle() (axes AND spindle stop,
        # still resumable via Start) is the one safe stop this page now
        # offers, so it has no ambiguous "is the spindle still running?"
        # question left for the operator.
        self._start_btn = CardButton("Start", icon=get_icon("start", tint=True), icon_size=36)

        # Feed-Hold is now also directly on this page (in addition to the
        # app-wide quick bar's own button, main_window.py's feed_hold_btn).
        # ONE-WAY trigger, not a toggle: pressing it only ever engages
        # feed-hold (set_feed_hold(True)) — the button itself goes grey
        # the instant it's engaged (see _sync_ui_state) and clicking it
        # again is not how you release it; that's the green Start button's
        # job now (see _on_start_clicked). Not checkable for exactly that
        # reason — there is no "un-toggle via the same button" state.
        self._feed_hold_btn = CardButton(
            "Feed-Hold", icon=get_icon("player-pause", tint=True), icon_size=36,
        )

        # "Stop" = hold_axes_and_spindle(): axes AND spindle stop, program
        # position preserved, resumable via Start — NOT
        # MachineController.stop_program() (a destructive abort, position
        # lost, per that method's own docstring). Deliberate choice: a
        # button labeled plain "Stop" that quietly discarded program
        # position would be a data-loss trap for an operator expecting to
        # resume afterward.
        self._stop_btn  = CardButton("Stop", icon=get_icon("stop", tint=True), icon_size=36)
        self._reset_btn = CardButton("Reset", icon=get_icon("reset", tint=True), icon_size=36)
        self._single_block_btn = CardButton(
            "Single-Step", icon=get_icon("single_block", tint=True), icon_size=32
        )
        self._single_block_btn.setCheckable(True)

        self._start_btn.setProperty("variant", "start")
        self._feed_hold_btn.setProperty("variant", "feed_hold")
        self._stop_btn.setProperty( "variant", "stop")
        self._reset_btn.setProperty("variant", "reset")
        self._single_block_btn.setProperty("variant", "single_block")

        self._start_btn.clicked.connect(self._on_start_clicked)
        self._feed_hold_btn.clicked.connect(self._on_feed_hold_clicked)
        self._stop_btn.clicked.connect( self._on_stop_clicked)
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        self._single_block_btn.toggled.connect(controller.set_single_block)
        controller.single_block_changed.connect(self._on_single_block_changed)

        controls_col = QVBoxLayout()
        controls_col.setSpacing(8)
        for btn in (self._start_btn, self._feed_hold_btn, self._stop_btn,
                    self._single_block_btn, self._reset_btn):
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
        controller.program_state_changed.connect(self._on_program_state_changed)
        controller.feed_hold_changed.connect(self._on_feed_hold_changed)
        self._sim.file_ready.connect(self._on_sim_file_ready)
        self._sim.load_failed.connect(self._on_sim_load_failed)

        # Feed/rapid overrides feed the sim widget's estimated part-time
        # calculation — feed_changed carries a FeedData with feed_override;
        # rapid_override_changed carries the fraction directly. The sim
        # widget owns the computation (it has the PathBuffer); the result is
        # displayed in ProgramInfoCard's "Approximated" field, not in the
        # sim widget itself.
        controller.feed_changed.connect(
            lambda fd: self._sim.set_feed_override(fd.feed_override)
        )
        controller.rapid_override_changed.connect(self._sim.set_rapid_override)
        self._sim.part_time_changed.connect(self._program_info.set_part_time)

        # Tool called out by the running program — reflects T-commands
        # crossed during SIM playback AND during a real MACHINE-mode run
        # started via the Start button (see DatumSimWidget._apply_tool()).
        self._sim.tool_changed.connect(self._tool_info.set_tool)
        self._sim.collision_detected.connect(self._on_live_collision)

        self._sync_ui_state(controller.program_state)

    # ── File loading (no machine-state requirement) ───────────────────────────

    def load_file(self, path: str) -> None:
        """Load a G-code file into the sim viewer and G-code preview.

        Switches the G-code area to the viewer, populates it, and tells the
        3D sim to compile and display the program. Does NOT interact with the
        machine backend — no machine state is required.

        Public: called by main_window.py in response to a
        ProgramDetailPage's "Ausführen" button (see
        ui.pages.workpieces_page.WorkpiecesSection.load_in_machine_requested)
        — the only way a file reaches this page now that the old fixed
        example-file button is gone (see open_workpieces_requested).

        self._sim.set_file(path) is fire-and-forget (the actual G-code
        compile now runs in a background thread — see DatumSimWidget.set_file()'s
        docstring) — this method returns immediately, and _sync_ui_state()
        runs once right here (with _file_ready still False, same as "no
        file loaded" — Start stays grey) and again from _on_sim_file_ready()
        once the compile actually lands, so Start never briefly shows
        startable before the file is really usable.
        """
        if not Path(path).is_file():
            logger.warning("G-Code-Datei nicht gefunden: %s", path)
            self._controller.error_occurred.emit(
                MachineError(
                    f"G-Code-Datei nicht gefunden: {path}",
                    ErrorSeverity.WARNING, source="MachinePage",
                )
            )
            return
        self._loaded_path = path
        self._file_ready = False

        # Detach the highlighter before setPlainText() and reattach it a
        # tick later (A4): QSyntaxHighlighter re-highlights the WHOLE
        # document synchronously on attach, which for a large file is a
        # real, separate synchronous cost on top of setPlainText() itself.
        # Deliberately not gated on the background compile (file_ready) —
        # highlighting only needs the raw text already in the document, so
        # tying it to the (slower, unrelated) compile would just delay
        # colorization for no reason.
        self._highlighter.setDocument(None)
        self._gcode_view.setPlainText(self._read_gcode_file(path))
        self._gcode_stack.setCurrentIndex(_GCODE_VIEWER)
        # `self` as the context object: if this page is destroyed before
        # the deferred call fires (e.g. torn down between test cases),
        # Qt drops the callback instead of running it against an
        # already-deleted C++ widget.
        QTimer.singleShot(
            0, self, lambda: self._highlighter.setDocument(self._gcode_view.text_edit.document())
        )

        self._sim.set_file(path)   # fire-and-forget — see docstring above

        # Update the program info card immediately (before the machine starts)
        self._program_info.set_file(path)

        # Re-evaluate button states now that a load has been requested —
        # _file_ready is still False at this point, so this keeps Start
        # grey exactly as if no file were loaded, until _on_sim_file_ready().
        self._sync_ui_state(self._controller.program_state)

    def _on_sim_file_ready(self, path: str) -> None:
        """DatumSimWidget finished compiling a file in the background —
        see load_file()'s docstring."""
        if path != self._loaded_path:
            return   # a newer load already superseded this one
        self._file_ready = True
        self._sync_ui_state(self._controller.program_state)

    def _on_sim_load_failed(self, exc: Exception) -> None:
        self._loaded_path = None
        self._file_ready = False
        self._controller.error_occurred.emit(
            MachineError(
                f"G-Code konnte nicht kompiliert werden: {exc}",
                ErrorSeverity.WARNING, source="MachinePage",
            )
        )
        self._sync_ui_state(self._controller.program_state)

    # ── UI state sync (single source of truth) ────────────────────────────────

    def _on_program_state_changed(self, state: ProgramState) -> None:
        self._sync_ui_state(state, self._controller.feed_hold)

    def _on_feed_hold_changed(self, held: bool) -> None:
        # feed_hold is orthogonal to ProgramState (the machine stays
        # RUNNING throughout a feed-hold — see controller.py) — nothing
        # else re-syncs button state when only this flag changes, so this
        # connection is required, not redundant with the one above.
        self._sync_ui_state(self._controller.program_state, held)

    def _sync_ui_state(self, state: ProgramState, feed_hold: bool | None = None) -> None:
        """Sync sim mode + button enabled states to (state, feed_hold)
        together — the single place that decides every button's
        enabled/color at once. feed_hold defaults to reading it live from
        the controller so existing single-arg call sites keep working.

        Feed-Hold is a ONE-WAY trigger, not a toggle: once engaged, this
        button (and main_window.py's quick-bar twin) go grey/hidden — the
        only way to release it is the green Start button (clears the
        freeze, program never stopped) or the red Stop button
        (hold_axes_and_spindle(), a full stop that also clears feed-hold —
        see MachineController.hold_axes_and_spindle()).

            State      feed_hold   Start                  Feed-Hold  Stop        Reset
            IDLE, no file   False  grey/off               grey/off   grey/off    on
            IDLE, ready     False  GREEN/on               grey/off   grey/off    on
            RUNNING         False  grey/off               on         RED/on      grey/off
            RUNNING         True   GREEN/on (clears hold)  grey/off   RED/on      grey/off
            PAUSED          False  GREEN/on (resumes)      grey/off   grey/off    on
            ERROR           False  grey/off                grey/off   grey/off    on (only way out)

        PAUSED/ERROR + feed_hold=True are not reachable: SimulatedBackend
        only ever sets feed_hold while RUNNING, and every transition away
        from RUNNING (stop_program(), rewind_program(), and
        hold_axes_and_spindle() per its own fix) clears it.
        """
        if feed_hold is None:
            feed_hold = self._controller.feed_hold
        now_running = state == ProgramState.RUNNING

        self._sim.set_mode("MACHINE" if now_running else "SIM")
        if now_running:
            pos = self._controller.position
            self._sim.set_position(pos.x, pos.y, pos.z)
            self._sim.set_line(self._controller.current_line)
        self._sim.set_state(state.name)

        file_loaded = self._loaded_path is not None and self._file_ready
        startable = state in (ProgramState.IDLE, ProgramState.PAUSED)
        self._start_btn.setEnabled((file_loaded and startable) or (now_running and feed_hold))
        self._feed_hold_btn.setEnabled(now_running and not feed_hold)
        self._stop_btn.setEnabled(now_running)
        # Reset is the only way out of ERROR, and stays available while
        # paused/idle — just never while a program is actually RUNNING
        # (rewind_program() itself already refuses that; this just makes
        # it visibly unavailable rather than silently rejected on click).
        self._reset_btn.setEnabled(not now_running)

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

        Before a fresh start (not a resume-from-pause): first, every
        T-address the program calls out must resolve to a tool CURRENTLY
        SITTING IN that magazine pocket — a T-address selects a pocket
        directly, not a ToolDefinition.tool_number identity (see
        gcode/compiler.py's ToolChange.pocket_number docstring and
        validate_tools()) — empty pockets block Start outright, behind an
        error dialog listing them (no "start anyway" — there's nothing to
        insert to work around it). Only once that passes — and only if
        AppSettings.collision_check_before_start_enabled is on (Settings ->
        General -> Sicherheit; off by default) — does the whole-program
        collision pre-check run in the background: see
        DatumSimWidget.presim_check_collisions(). This is a separate switch
        from AppSettings.collision_detection_enabled (Settings -> Simulation
        -> Simulation), which only gates the always-informational, never-
        blocking collision feedback shown while a program is actually
        running/simulating (see _on_live_collision()) — enabling that one
        does not by itself make Start pre-flight the program too. With the
        pre-start check off, Start launches the program immediately, same
        as before this setting existed. With it on: a clean program starts
        with no perceptible delay; a detected collision blocks Start behind
        a confirmation dialog instead.
        """
        if self._controller.feed_hold:
            # Program never stopped — Start's job here is just to release
            # the freeze, not run_program()/resume_program() again. See
            # _sync_ui_state()'s state matrix and _on_feed_hold_clicked().
            self._controller.set_feed_hold(False)
            return

        if self._controller.program_state == ProgramState.PAUSED:
            self._controller.resume_program()
            return

        if self._loaded_path is None:
            # Shouldn't happen (button is disabled), but guard anyway —
            # send the user to pick a real program instead of guessing one.
            self.open_workpieces_requested.emit()
            return

        if not self._file_ready:
            # The background compile (DatumSimWidget.set_file() — see its
            # docstring) hasn't landed yet. This must be a hard, silent
            # no-op, not a fallthrough: self._sim.tool_changes/_last_program
            # are still stale/empty at this point, which would make
            # validate_tools() and presim_check_collisions() below both
            # trivially "pass" against a program that was never actually
            # checked — a real safety gap, not just a cosmetic race. The
            # button itself is already disabled while this is true (see
            # _sync_ui_state()); this is the defensive backstop for any
            # caller that bypasses the button (tests included).
            return

        tool_validation = validate_tools(self._sim.tool_changes, get_tool_by_pocket)
        if not tool_validation.ok:
            self._show_missing_tools_dialog(tool_validation)
            return

        if not AppSettings.instance().collision_check_before_start_enabled:
            self._controller.run_program(self._loaded_path)
            return

        self._sim.presim_check_collisions(self._on_presim_collision_checked)

    def _show_missing_tools_dialog(self, result) -> None:
        from PySide6.QtWidgets import QMessageBox

        pockets = ", ".join(f"T{t}" for t in result.missing)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Werkzeuge fehlen")
        box.setText(
            "Das Programm kann nicht gestartet werden — folgende "
            "Magazinplätze sind leer bzw. keinem Werkzeug zugewiesen:\n\n"
            + pockets
        )
        box.exec()

    def _on_presim_collision_checked(self, hit) -> None:
        if hit is None:
            self._controller.run_program(self._loaded_path)
            return

        from PySide6.QtWidgets import QMessageBox

        line = hit.line_number if hit.line_number >= 0 else "?"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Kollision erkannt")
        box.setText(
            f"Kollision in Zeile {line} erkannt ({hit.kind}).\n\n"
            "Der Werkzeugweg wurde vor dem Start geprüft — an dieser Stelle "
            "würde das Werkzeug (bzw. Schaft/Aufnahme) vorhandenes Material "
            "berühren, wo es das nicht sollte."
        )
        start_anyway = box.addButton("Trotzdem starten", QMessageBox.ButtonRole.AcceptRole)
        cancel       = box.addButton("Abbrechen",        QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()

        if box.clickedButton() is start_anyway:
            self._controller.run_program(self._loaded_path)
        else:
            # Jump the sim view to the collision point for inspection.
            self._sim.seek_to_point(hit.point)
            self._sim.viewport.set_collision(True, hit.point)

    def _on_live_collision(self, hit) -> None:
        """A collision detected DURING a real MACHINE-mode run.

        Purely informative — same as the sim widget's own pill/tool-tint
        feedback (see DatumSimWidget._handle_collision_state()'s
        docstring): this is a SIMULATED prediction (voxel model vs.
        toolpath), not a physical sensor reading, so it must never by
        itself stop or pause the machine. That holds unconditionally, not
        only after the operator dismissed the pre-start check with
        "Trotzdem starten" — there is no code path left here that calls
        stop_program()/set_feed_hold() off the back of a collision hit.
        Acting on a detected collision (Feed-Hold/Stop) is always the
        operator's own call, never automatic.

        collision_detected also fires while just scrubbing/playing back a
        program in SIM mode (no real machine involved) — this handler only
        surfaces the notice through the status bar while a machine program
        is actually RUNNING; a SIM-mode-only hit is left to the sim
        widget's own warning-tinted tool + collision pill.
        """
        if self._controller.program_state != ProgramState.RUNNING:
            return

        line = hit.line_number if hit.line_number >= 0 else "?"
        self._controller.error_occurred.emit(
            MachineError(
                f"Kollision erkannt bei Zeile {line} ({hit.kind}) — "
                "rein informative Meldung, die Maschine läuft weiter.",
                ErrorSeverity.WARNING, source="MachinePage",
            )
        )

    def _on_feed_hold_clicked(self) -> None:
        """One-way trigger: engages feed-hold. Releasing it happens via
        the Start button instead (_on_start_clicked) — this button is
        disabled the instant feed-hold engages (see _sync_ui_state)."""
        self._controller.set_feed_hold(True)

    def _on_stop_clicked(self) -> None:
        """Stops axes AND spindle, program position preserved — resume via
        Start. See hold_axes_and_spindle()'s own docstring; deliberately
        NOT stop_program() (that aborts and loses position — see the
        control-column comment above this button's construction). Also
        clears feed-hold if one was active (hold_axes_and_spindle() itself
        does this) — a full stop subsumes a mere motion-freeze."""
        self._controller.hold_axes_and_spindle()

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
