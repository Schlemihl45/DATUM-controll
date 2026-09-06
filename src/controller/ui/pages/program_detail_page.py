"""
ui/pages/program_detail_page.py — ProgramDetailPage: one Operation
version — sim playback of its G-code, notes, used tools (checked live
against the magazine), a collapsed G-code section and a collapsed
version history.

Layout principle: the PAGE ITSELF is NOT scrollable (no outer QScrollArea
wrapping everything) — sim view, G-code section, tool list and history
sit in fixed positions in one QVBoxLayout. Variable content amount is
absorbed by SECTIONS scrolling internally (ui/widgets/collapsible_section.py's
CollapsibleSection caps its expanded body at a max height and scrolls past
that; the always-visible tool list gets its own small fixed-height
internally-scrollable area), never by the page growing. A side effect of
this design: with no page-level QScrollArea competing for mouse-drag/wheel
input, the sim widget's own camera controls (mouse-drag rotate/pan,
wheel-zoom — see sim/ui/viewport.py) have nothing left to conflict with.

Colloquially "Programm" (see domain.models.Operation's docstring) — kept
as Operation in code to avoid colliding with ProgramState/ProgramInfoCard,
which describe the program currently loaded/running on the MACHINE, an
unrelated concept. The sim widget below is loaded with THIS OPERATION
VERSION's own gcode_path, never with whatever the real machine currently
has loaded.

Reached from WorkpieceDetailPage (clicking an operation card) or from
another ProgramDetailPage's own history list (clicking an old version) —
pushed onto the shared `nav` PageStack (see ui/widgets/page_stack.py)
either way, so the app-wide Return button pops back to whichever page
opened this one instead of jumping to Home.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
    QScroller, QSizePolicy, QVBoxLayout, QWidget,
)

from controller.persistence.workpiece_db import WorkpieceDatabase, WorkpieceDatabaseSignals
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card import Card
from controller.ui.widgets.collapsible_section import CollapsibleSection
from controller.ui.widgets.elided_label import ElidedLabel
from controller.ui.widgets.gcode_highlighter import GCodeHighlighter
from controller.ui.widgets.gcode_viewer import GCodeViewer
from controller.ui.widgets.preview_thumbnail import PreviewThumbnail
from controller.ui.widgets.tool_usage_card import ToolUsageCard

# Same fallback MachinePage uses: the real 3D sim widget if moderngl/numpy
# are available, a text placeholder otherwise (see sim_placeholder.py).
try:
    from controller.sim.ui.main_widget import DatumSimWidget as _SimWidget  # noqa: F401
except ImportError:
    from controller.ui.widgets.sim_placeholder import SimPlaceholder as _SimWidget  # type: ignore[assignment]

_DATE_FMT = "%d.%m.%Y %H:%M"

# Section body caps — see module docstring on why sections scroll
# internally instead of the page growing. Kept modest since this page has
# no scroll fallback of its own: expanding G-code AND history at once
# could still push total content past the window's available height (a
# known, accepted trade-off of "never scroll the page itself").
_GCODE_SECTION_HEIGHT = 240
_HISTORY_SECTION_HEIGHT = 220
_TOOLS_SECTION_HEIGHT = 160


class _AutoSaveTextEdit(QPlainTextEdit):
    """Same pattern as tool_card_widget.py's _AutoSaveTextEdit (auto-save
    on focus-out) — duplicated locally since that class is private to its
    own module, not meant to be imported."""

    focus_out = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_out.emit()


class _HistoryRow(Card):
    """One superseded version in the collapsed history list — same card
    styling idiom as the tool/workpiece cards, click opens it (via `nav`)
    as another ProgramDetailPage with the "Veraltet" banner."""

    clicked = Signal()

    def __init__(self, operation, parent: QWidget | None = None) -> None:
        super().__init__(title=None, parent=parent)
        self._operation_id = operation.id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(PreviewThumbnail(size=40))

        info = QVBoxLayout()
        info.setSpacing(2)
        name_lbl = ElidedLabel()
        name_lbl.setObjectName("WorkpieceCardName")
        name_lbl.set_full_text(f"{operation.name} — v{operation.version}")
        sub_lbl = ElidedLabel()
        sub_lbl.setObjectName("WorkpieceCardInfo")
        sub_lbl.set_full_text(
            f"Erstellt: {operation.created_at.strftime(_DATE_FMT)} · "
            f"Geändert: {operation.modified_at.strftime(_DATE_FMT)}"
        )
        info.addWidget(name_lbl)
        info.addWidget(sub_lbl)
        info_widget = QWidget()
        info_widget.setLayout(info)
        row.addWidget(info_widget, stretch=1)

        self.content_layout.addLayout(row)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ProgramDetailPage(QWidget):
    """See module docstring. `nav` is anything exposing push(widget, title)
    AND request_load_in_machine(gcode_path) (see
    ui.pages.workpiece_browser_page.WorkpiecesSection) — threaded through
    so this page can push a new ProgramDetailPage for an old version onto
    the very same navigation stack the caller used to reach this one, and
    so the "In Maschine laden" button can reach all the way up to
    main_window.py without this page knowing anything about MainWindow
    itself."""

    def __init__(self, operation_id: int, nav, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nav = nav
        self._db = WorkpieceDatabase.instance()
        self._operation_id = operation_id
        self._operation = self._db.get_operation(operation_id)
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Deprecated banner (old version only) ────────────────────────────
        self._banner = QLabel("⚠ Veraltete Version — nicht die aktuelle Version dieses Programms")
        self._banner.setObjectName("DeprecatedBanner")
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._banner)

        # ── Header: name, dates, notes (fixed height) ────────────────────────
        header = Card(title=None)
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header_row.addWidget(PreviewThumbnail(size=56))

        header_form = QVBoxLayout()
        self._name_lbl = ElidedLabel()
        self._name_lbl.setObjectName("WorkpieceCardName")
        header_form.addWidget(self._name_lbl)
        self._dates_lbl = QLabel()
        self._dates_lbl.setObjectName("WorkpieceCardInfo")
        header_form.addWidget(self._dates_lbl)
        header_row.addLayout(header_form, stretch=1)
        header.content_layout.addLayout(header_row)

        header.content_layout.addWidget(QLabel("Notizen"))
        self._notes_edit = _AutoSaveTextEdit()
        self._notes_edit.setFixedHeight(60)
        self._notes_edit.focus_out.connect(self._auto_save_notes)
        header.content_layout.addWidget(self._notes_edit)
        root.addWidget(header)

        # ── Sim playback: biggest/flexible share of the page ─────────────────
        self._sim = _SimWidget()
        self._sim.setMinimumHeight(200)
        try:
            self._sim.set_mode("SIM")
        except AttributeError:
            pass
        root.addWidget(self._sim, stretch=1)

        # ── G-code section: collapsed by default, capped + internally
        # scrollable when expanded (see CollapsibleSection) ──────────────────
        self._gcode_section = CollapsibleSection("G-Code")
        self._gcode_section.set_max_body_height(_GCODE_SECTION_HEIGHT)
        self._gcode_view = GCodeViewer()
        # Sized to fit the section's own cap exactly: GCodeViewer's own
        # QPlainTextEdit already scrolls internally once its text overflows
        # this height, so CollapsibleSection's wrapping QScrollArea never
        # actually needs to scroll itself — avoids stacking two scroll
        # mechanisms for the same content.
        self._gcode_view.setFixedHeight(_GCODE_SECTION_HEIGHT - 20)
        self._gcode_highlighter = GCodeHighlighter(self._gcode_view.text_edit.document())
        self._gcode_section.body_layout.addWidget(self._gcode_view)
        root.addWidget(self._gcode_section)

        # ── Used tools: ALWAYS visible, fixed section height, internally
        # scrollable (never collapsible — see module docstring) ─────────────
        root.addWidget(QLabel("Verwendete Werkzeuge"))
        self._tools_scroll = QScrollArea()
        self._tools_scroll.setWidgetResizable(True)
        self._tools_scroll.setFixedHeight(_TOOLS_SECTION_HEIGHT)
        self._tools_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tools_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tools_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        QScroller.grabGesture(
            self._tools_scroll.viewport(), QScroller.ScrollerGestureType.TouchGesture
        )
        tools_content = QWidget()
        self._tools_layout = QVBoxLayout(tools_content)
        self._tools_layout.setContentsMargins(0, 0, 0, 0)
        self._tools_layout.setSpacing(6)
        self._tools_scroll.setWidget(tools_content)
        root.addWidget(self._tools_scroll)
        self._tool_cards: dict[int, ToolUsageCard] = {}

        # ── Collapsed version history ────────────────────────────────────────
        self._history_section = CollapsibleSection("Verlauf")
        self._history_section.set_max_body_height(_HISTORY_SECTION_HEIGHT)
        self._history_section.body_layout.setSpacing(6)
        root.addWidget(self._history_section)

        # ── Load into MachinePage (bottom-right, fixed) ──────────────────────
        self._load_btn = QPushButton(" In Maschine laden")
        self._load_btn.setIcon(get_icon("machine", tint=True))
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.setMinimumHeight(40)
        self._load_btn.clicked.connect(self._on_load_in_machine_clicked)
        load_row = QHBoxLayout()
        load_row.addStretch(1)
        load_row.addWidget(self._load_btn)
        root.addLayout(load_row)

        WorkpieceDatabaseSignals.instance().operation_changed.connect(self._on_operation_changed)

        self._reload()

    # ── Data flow ────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        self._operation = self._db.get_operation(self._operation_id)
        if self._operation is None:
            return
        op = self._operation

        self._banner.setVisible(not op.is_current)
        self._name_lbl.set_full_text(f"{op.name} (v{op.version})")
        self._dates_lbl.setText(
            f"Erstellt: {op.created_at.strftime(_DATE_FMT)} · "
            f"Geändert: {op.modified_at.strftime(_DATE_FMT)}"
        )

        self._loading = True
        if not self._notes_edit.hasFocus():
            self._notes_edit.setPlainText(op.notes)
        self._loading = False

        self._load_btn.setEnabled(not op.file_missing)
        self._load_btn.setToolTip(
            "G-Code-Datei fehlt auf der Festplatte." if op.file_missing else ""
        )

        if op.gcode_path:
            try:
                text = _read_gcode_text(op.gcode_path)
            except OSError:
                text = "(Datei nicht gefunden)"
            self._gcode_view.setPlainText(text)
            try:
                self._sim.set_file(op.gcode_path)
            except Exception:
                pass  # sim widget best-effort — a broken/missing file must not crash this page

        self._rebuild_tool_cards(op.tools)
        self._rebuild_history(op.lineage_id)

    def _rebuild_tool_cards(self, pockets: list[int]) -> None:
        """*pockets* are the raw numbers parsed from this operation's
        T-addresses — pocket numbers, not tool_number identities (see
        ui.widgets.tool_usage_card.ToolUsageCard's docstring)."""
        wanted = set(pockets)
        for pocket in list(self._tool_cards.keys()):
            if pocket not in wanted:
                card = self._tool_cards.pop(pocket)
                self._tools_layout.removeWidget(card)
                card.deleteLater()
        for position, pocket in enumerate(pockets):
            card = self._tool_cards.get(pocket)
            if card is None:
                card = ToolUsageCard(pocket)
                self._tool_cards[pocket] = card
            if self._tools_layout.indexOf(card) != position:
                self._tools_layout.removeWidget(card)
                self._tools_layout.insertWidget(position, card)
            else:
                card.refresh()

    def _rebuild_history(self, lineage_id: int) -> None:
        layout = self._history_section.body_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        history = self._db.operation_history(lineage_id)
        if not history:
            empty = QLabel("Keine älteren Versionen.")
            empty.setObjectName("WorkpieceCardInfo")
            layout.addWidget(empty)
            return
        for old_operation in history:
            row = _HistoryRow(old_operation)
            row.clicked.connect(
                lambda oid=old_operation.id: self._nav.push(
                    ProgramDetailPage(oid, self._nav),
                    f"{old_operation.name} (v{old_operation.version})",
                )
            )
            layout.addWidget(row)

    # ── Signals ──────────────────────────────────────────────────────────────

    def _on_operation_changed(self, operation_id: int) -> None:
        if self._operation is not None and operation_id == self._operation.id:
            self._reload()

    def _auto_save_notes(self) -> None:
        if self._loading or self._operation is None:
            return
        self._operation.notes = self._notes_edit.toPlainText()
        self._db.upsert_operation(self._operation)

    def _on_load_in_machine_clicked(self) -> None:
        """Load this operation version's G-code into MachinePage and
        switch the app over to it — see WorkpiecesSection.request_load_in_machine()
        (ui.pages.workpiece_browser_page), which `nav` (the same object
        threaded through this whole page hierarchy) forwards up to
        main_window.py."""
        if self._operation is None or self._operation.file_missing:
            return
        self._nav.request_load_in_machine(self._operation.gcode_path)


def _read_gcode_text(path: str) -> str:
    from pathlib import Path

    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")
