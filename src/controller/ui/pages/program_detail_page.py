"""
ui/pages/program_detail_page.py — ProgramDetailPage: one Operation
version — sim playback of its G-code, notes, used tools (checked live
against the magazine), an always-expanded G-code preview and a collapsed
version history.

Reached from WorkpieceDetailPage (clicking an operation card) or from
another ProgramDetailPage's own history list (clicking an old version) —
pushed onto the shared `nav` PageStack (see ui/widgets/page_stack.py)
either way, so the app-wide Return button pops back to whichever page
opened this one instead of jumping to Home.

Colloquially "Programm" (see domain.models.Operation's docstring) — kept
as Operation in code to avoid colliding with ProgramState/ProgramInfoCard,
which describe the program currently loaded/running on the MACHINE, an
unrelated concept. The sim widget below is loaded with THIS OPERATION
VERSION's own gcode_path, never with whatever the real machine currently
has loaded.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QScrollArea, QScroller, QSizePolicy, QVBoxLayout, QWidget,
)

from controller.persistence.tool_db import ToolDatabase, ToolDatabaseSignals
from controller.persistence.workpiece_db import WorkpieceDatabase, WorkpieceDatabaseSignals
from controller.sim.simulation.tool_definition import ToolType, UNASSIGNED_POCKET
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card import Card
from controller.ui.widgets.collapsible_section import CollapsibleSection
from controller.ui.widgets.elided_label import ElidedLabel
from controller.ui.widgets.gcode_highlighter import GCodeHighlighter
from controller.ui.widgets.gcode_viewer import GCodeViewer
from controller.ui.widgets.preview_thumbnail import PreviewThumbnail
from controller.ui.widgets.tool_icons import tool_type_icon

# Same fallback MachinePage uses: the real 3D sim widget if moderngl/numpy
# are available, a text placeholder otherwise (see sim_placeholder.py).
try:
    from controller.sim.ui.main_widget import DatumSimWidget as _SimWidget  # noqa: F401
except ImportError:
    from controller.ui.widgets.sim_placeholder import SimPlaceholder as _SimWidget  # type: ignore[assignment]

_DATE_FMT = "%d.%m.%Y %H:%M"


class _AutoSaveTextEdit(QPlainTextEdit):
    """Same pattern as tool_card_widget.py's _AutoSaveTextEdit (auto-save
    on focus-out) — duplicated locally since that class is private to its
    own module, not meant to be imported."""

    focus_out = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_out.emit()


class _ToolRow(Card):
    """One used tool, styled like ToolCardWidget's collapsed header (type
    icon + pocket badge + name — see tool_card_widget.py) but flat: no
    expansion, and a live "im Magazin" checkbox instead of the settings
    button (pocket assignment itself still only happens on ToolPage)."""

    def __init__(self, tool_number: int, parent: QWidget | None = None) -> None:
        super().__init__(title=None, parent=parent)
        self._tool_number = tool_number
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout()
        row.setSpacing(10)

        self._type_icon_lbl = QLabel()
        self._type_icon_lbl.setFixedSize(40, 40)
        row.addWidget(self._type_icon_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._pocket_badge = QLabel()
        self._pocket_badge.setObjectName("PocketBadge")
        self._pocket_badge.setFixedSize(30, 22)
        self._pocket_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._pocket_badge, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._name_lbl = ElidedLabel()
        self._name_lbl.setObjectName("ToolCardButtonLabel")
        row.addWidget(self._name_lbl, stretch=1)

        self._magazine_chk = QCheckBox("im Magazin")
        self._magazine_chk.setEnabled(False)
        row.addWidget(self._magazine_chk, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.content_layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        tool = ToolDatabase.instance().get_tool(self._tool_number)
        in_magazine = tool is not None and tool.pocket != UNASSIGNED_POCKET

        tool_type = tool.tool_type if tool is not None else ToolType.ENDMILL
        icon_size = QSize(40, 40)
        self._type_icon_lbl.setPixmap(tool_type_icon(tool_type, size=40).pixmap(icon_size))
        self._pocket_badge.setText(str(tool.pocket) if in_magazine else "-")

        if tool is not None:
            self._name_lbl.set_full_text(tool.name or tool.remark or f"T{self._tool_number}")
        else:
            self._name_lbl.set_full_text(f"T{self._tool_number} (unbekannt)")

        self._magazine_chk.setChecked(in_magazine)
        self.setProperty("magazine", in_magazine)
        self.style().unpolish(self)
        self.style().polish(self)


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
    ui.pages.workpieces_page.WorkpiecesSection) — threaded through so this
    page can push a new ProgramDetailPage for an old version onto the very
    same navigation stack the caller used to reach this one, and so the
    "In Maschine laden" button can reach all the way up to main_window.py
    without this page knowing anything about MainWindow itself."""

    def __init__(self, operation_id: int, nav, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nav = nav
        self._db = WorkpieceDatabase.instance()
        self._operation_id = operation_id
        self._operation = self._db.get_operation(operation_id)
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        QScroller.grabGesture(scroll.viewport(), QScroller.ScrollerGestureType.TouchGesture)
        outer.addWidget(scroll)

        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(12, 12, 12, 12)
        col.setSpacing(12)
        scroll.setWidget(content)

        # ── Deprecated banner (old version only) ────────────────────────────
        self._banner = QLabel("⚠ Veraltete Version — nicht die aktuelle Version dieses Programms")
        self._banner.setObjectName("DeprecatedBanner")
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._banner)

        # ── Header: name, dates, notes ───────────────────────────────────────
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
        self._notes_edit.setFixedHeight(70)
        self._notes_edit.focus_out.connect(self._auto_save_notes)
        header.content_layout.addWidget(self._notes_edit)
        col.addWidget(header)

        # ── Sim playback ─────────────────────────────────────────────────────
        self._sim = _SimWidget()
        self._sim.setMinimumHeight(320)
        try:
            self._sim.set_mode("SIM")
        except AttributeError:
            pass
        col.addWidget(self._sim)

        # ── G-code preview (always expanded — no collapse option) ────────────
        col.addWidget(QLabel("G-Code"))
        self._gcode_view = GCodeViewer()
        self._gcode_view.setMinimumHeight(240)
        self._gcode_highlighter = GCodeHighlighter(self._gcode_view.text_edit.document())
        col.addWidget(self._gcode_view)

        # ── Used tools (flat list, magazine checkbox) ───────────────────────
        col.addWidget(QLabel("Verwendete Werkzeuge"))
        self._tools_col = QVBoxLayout()
        self._tools_col.setSpacing(6)
        self._tool_rows: dict[int, _ToolRow] = {}
        col.addLayout(self._tools_col)

        # ── Collapsed version history ────────────────────────────────────────
        self._history_section = CollapsibleSection("Verlauf")
        history_layout = QVBoxLayout(self._history_section.body)
        history_layout.setContentsMargins(0, 4, 0, 0)
        history_layout.setSpacing(6)
        self._history_layout = history_layout
        col.addWidget(self._history_section)

        col.addStretch(1)

        # ── Load into MachinePage (bottom-right) ─────────────────────────────
        self._load_btn = QPushButton(" In Maschine laden")
        self._load_btn.setIcon(get_icon("machine", tint=True))
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.setMinimumHeight(40)
        self._load_btn.clicked.connect(self._on_load_in_machine_clicked)
        load_row = QHBoxLayout()
        load_row.addStretch(1)
        load_row.addWidget(self._load_btn)
        col.addLayout(load_row)

        ToolDatabaseSignals.instance().tool_changed.connect(self._on_tool_changed)
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

        self._rebuild_tool_rows(op.tools)
        self._rebuild_history(op.lineage_id)

    def _rebuild_tool_rows(self, tool_numbers: list[int]) -> None:
        wanted = set(tool_numbers)
        for number in list(self._tool_rows.keys()):
            if number not in wanted:
                row = self._tool_rows.pop(number)
                self._tools_col.removeWidget(row)
                row.deleteLater()
        for number in tool_numbers:
            if number not in self._tool_rows:
                row = _ToolRow(number)
                self._tool_rows[number] = row
                self._tools_col.addWidget(row)
            else:
                self._tool_rows[number].refresh()

    def _rebuild_history(self, lineage_id: int) -> None:
        while self._history_layout.count():
            item = self._history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        history = self._db.operation_history(lineage_id)
        if not history:
            empty = QLabel("Keine älteren Versionen.")
            empty.setObjectName("ToolCardButtonLabel")
            self._history_layout.addWidget(empty)
            return
        for old_operation in history:
            row = _HistoryRow(old_operation)
            row.clicked.connect(
                lambda oid=old_operation.id: self._nav.push(
                    ProgramDetailPage(oid, self._nav),
                    f"{old_operation.name} (v{old_operation.version})",
                )
            )
            self._history_layout.addWidget(row)

    # ── Signals ──────────────────────────────────────────────────────────────

    def _on_tool_changed(self, _tool_number: int) -> None:
        for row in self._tool_rows.values():
            row.refresh()

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
        (ui.pages.workpieces_page), which `nav` (the same object threaded
        through this whole page hierarchy) forwards up to main_window.py."""
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
