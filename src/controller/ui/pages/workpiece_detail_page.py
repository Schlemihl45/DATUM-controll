"""
ui/pages/workpiece_detail_page.py — WorkpieceDetailPage: one Workpiece's
header (name, dates, estimated total time, notes), its current operations
("Programme" in UI text — see domain.models.Operation's docstring) as a
non-scrolling list of cards, and a collapsed list of every tool used
across them, checked against ToolDatabase.

Reached from WorkpiecesPage (clicking a workpiece card) — pushed onto the
shared `nav` PageStack (see ui/widgets/page_stack.py). Clicking an
operation card pushes ProgramDetailPage the same way.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPlainTextEdit,
    QScrollArea, QScroller, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from controller.persistence.tool_db import ToolDatabase, ToolDatabaseSignals
from controller.persistence.workpiece_db import WorkpieceDatabase, WorkpieceDatabaseSignals
from controller.sim.simulation.tool_definition import UNASSIGNED_POCKET
from controller.ui.icon_loader import get_icon
from controller.ui.pages.program_detail_page import ProgramDetailPage
from controller.ui.widgets.card import Card
from controller.ui.widgets.collapsible_section import CollapsibleSection
from controller.ui.widgets.elided_label import ElidedLabel
from controller.ui.widgets.preview_thumbnail import PreviewThumbnail

_DATE_FMT = "%d.%m.%Y %H:%M"


class _AutoSaveTextEdit(QPlainTextEdit):
    """Same pattern as tool_card_widget.py's _AutoSaveTextEdit — duplicated
    locally, see program_detail_page.py's copy for the same reasoning."""

    focus_out = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_out.emit()


class _OperationCard(Card):
    """One current operation ("Programm"): icon, name, dates, a Wizard
    stub button, and a settings menu (Delete). Rendered with a visible
    warning style whenever its G-code file is missing on disk
    (Operation.file_missing) — never silently like a normal card."""

    clicked = Signal()
    wizard_requested = Signal()
    delete_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(title=None, parent=parent)
        self._operation_id: int | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout()
        row.setSpacing(10)

        self._thumb = PreviewThumbnail(size=48)
        row.addWidget(self._thumb, alignment=Qt.AlignmentFlag.AlignVCenter)

        info = QVBoxLayout()
        info.setSpacing(2)
        self._name_lbl = ElidedLabel()
        self._name_lbl.setObjectName("WorkpieceCardName")
        self._sub_lbl = ElidedLabel()
        self._sub_lbl.setObjectName("WorkpieceCardInfo")
        info.addWidget(self._name_lbl)
        info.addWidget(self._sub_lbl)
        info_widget = QWidget()
        info_widget.setLayout(info)
        row.addWidget(info_widget, stretch=1)

        self._wizard_btn = QToolButton()
        self._wizard_btn.setIcon(get_icon("wand", tint=True))
        self._wizard_btn.setIconSize(QSize(18, 18))
        self._wizard_btn.setFixedSize(32, 32)
        self._wizard_btn.setToolTip("Setup-Assistent")
        self._wizard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wizard_btn.clicked.connect(self.wizard_requested)
        row.addWidget(self._wizard_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._menu_btn = QToolButton()
        self._menu_btn.setIcon(get_icon("settings", tint=True))
        self._menu_btn.setIconSize(QSize(18, 18))
        self._menu_btn.setFixedSize(32, 32)
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.clicked.connect(self._show_menu)
        row.addWidget(self._menu_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.content_layout.addLayout(row)

    def set_operation(self, operation) -> None:
        self._operation_id = operation.id
        self._name_lbl.set_full_text(operation.name)
        self._sub_lbl.set_full_text(
            f"Erstellt: {operation.created_at.strftime(_DATE_FMT)} · "
            f"Geändert: {operation.modified_at.strftime(_DATE_FMT)}"
        )
        self._thumb.set_preview_source(operation.preview_source or "", operation.gcode_path)
        self.setProperty("file_missing", operation.file_missing)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        delete_action = menu.addAction(get_icon("delete", tint=True), "Delete")
        delete_action.triggered.connect(self._confirm_delete)
        menu.exec(self._menu_btn.mapToGlobal(self._menu_btn.rect().bottomLeft()))

    def _confirm_delete(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Programm löschen")
        box.setText(
            "Programm wirklich löschen?\n"
            "Alle älteren Versionen dieses Programms werden mitgelöscht.\n"
            "Diese Aktion kann nicht rückgängig gemacht werden."
        )
        delete_btn = box.addButton("Löschen", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(delete_btn)
        box.exec()
        if box.clickedButton() is delete_btn:
            self.delete_requested.emit()


class _UsedToolRow(QWidget):
    """One row in the collapsed "used tools" section — a pocket badge
    (same PocketBadge style ToolPage uses) + name + magazine status,
    resolved against ToolDatabase (no editing here, see ToolPage for
    that)."""

    def __init__(self, tool_number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tool_number = tool_number

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._pocket_badge = QLabel()
        self._pocket_badge.setObjectName("PocketBadge")
        self._pocket_badge.setFixedSize(30, 22)
        self._pocket_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._pocket_badge)

        self._label = QLabel()
        self._label.setObjectName("ToolCardButtonLabel")
        row.addWidget(self._label, stretch=1)
        self.refresh()

    def refresh(self) -> None:
        tool = ToolDatabase.instance().get_tool(self._tool_number)
        if tool is None:
            self._pocket_badge.setText("-")
            self._label.setText(f"T{self._tool_number} — nicht in der Werkzeugdatenbank")
            return
        in_magazine = tool.pocket != UNASSIGNED_POCKET
        self._pocket_badge.setText(str(tool.pocket) if in_magazine else "-")
        name = tool.name or tool.remark or f"T{self._tool_number}"
        status = "im Magazin" if in_magazine else "nicht im Magazin"
        self._label.setText(f"T{self._tool_number} — {name} ({status})")


class WorkpieceDetailPage(QWidget):
    """See module docstring. `nav` is anything exposing push(widget, title)
    (see ui.pages.workpieces_page.WorkpiecesSection)."""

    def __init__(self, workpiece_id: int, nav, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nav = nav
        self._db = WorkpieceDatabase.instance()
        self._workpiece_id = workpiece_id
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

        # ── Header ───────────────────────────────────────────────────────────
        header = Card(title=None)
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        self._thumb = PreviewThumbnail(size=56)
        header_row.addWidget(self._thumb)

        header_col = QVBoxLayout()
        self._name_lbl = ElidedLabel()
        self._name_lbl.setObjectName("WorkpieceCardName")
        header_col.addWidget(self._name_lbl)
        self._dates_lbl = QLabel()
        self._dates_lbl.setObjectName("WorkpieceCardInfo")
        header_col.addWidget(self._dates_lbl)
        self._time_lbl = QLabel()
        self._time_lbl.setObjectName("WorkpieceCardInfo")
        header_col.addWidget(self._time_lbl)
        header_row.addLayout(header_col, stretch=1)
        header.content_layout.addLayout(header_row)

        header.content_layout.addWidget(QLabel("Notizen"))
        self._notes_edit = _AutoSaveTextEdit()
        self._notes_edit.setFixedHeight(70)
        self._notes_edit.focus_out.connect(self._auto_save_notes)
        header.content_layout.addWidget(self._notes_edit)
        col.addWidget(header)

        # ── Operations (non-scrolling; the PAGE scrolls, not this list) ──────
        col.addWidget(QLabel("Programme"))
        self._operations_col = QVBoxLayout()
        self._operations_col.setSpacing(6)
        self._operation_cards: dict[int, _OperationCard] = {}
        col.addLayout(self._operations_col)

        # ── Collapsed used-tools section ─────────────────────────────────────
        self._tools_section = CollapsibleSection("Verwendete Werkzeuge")
        tools_layout = QVBoxLayout(self._tools_section.body)
        tools_layout.setContentsMargins(0, 4, 0, 0)
        tools_layout.setSpacing(4)
        self._tools_layout = tools_layout
        self._tool_rows: dict[int, _UsedToolRow] = {}
        col.addWidget(self._tools_section)

        col.addStretch(1)

        WorkpieceDatabaseSignals.instance().workpiece_changed.connect(self._on_workpiece_changed)
        WorkpieceDatabaseSignals.instance().operation_changed.connect(self._on_operation_changed)
        ToolDatabaseSignals.instance().tool_changed.connect(self._on_tool_changed)

        self._reload()

    # ── Data flow ────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        workpiece = self._db.get_workpiece(self._workpiece_id)
        if workpiece is None:
            return
        self._workpiece = workpiece

        self._name_lbl.set_full_text(workpiece.name)
        self._dates_lbl.setText(
            f"Erstellt: {workpiece.created_at.strftime(_DATE_FMT)} · "
            f"Geändert: {workpiece.modified_at.strftime(_DATE_FMT)}"
        )
        self._time_lbl.setText(
            f"Geschätzte Gesamtzeit: {_format_hms(workpiece.estimated_total_time)}"
        )
        self._thumb.set_material_hint(workpiece.material)

        self._loading = True
        if not self._notes_edit.hasFocus():
            self._notes_edit.setPlainText(workpiece.notes)
        self._loading = False

        self._rebuild_operations(workpiece.operations)

        used_tools: list[int] = []
        for op in workpiece.operations:
            for t in op.tools:
                if t not in used_tools:
                    used_tools.append(t)
        self._rebuild_used_tools(used_tools)

    def _rebuild_operations(self, operations) -> None:
        wanted = {op.id: op for op in operations}
        for op_id in list(self._operation_cards.keys()):
            if op_id not in wanted:
                card = self._operation_cards.pop(op_id)
                self._operations_col.removeWidget(card)
                card.deleteLater()

        if not operations:
            return

        for position, op in enumerate(operations):
            card = self._operation_cards.get(op.id)
            if card is None:
                card = _OperationCard()
                card.set_operation(op)
                card.clicked.connect(lambda oid=op.id: self._on_operation_clicked(oid))
                card.wizard_requested.connect(self._show_wizard_stub)
                card.delete_requested.connect(lambda oid=op.id: self._on_delete_operation(oid))
                self._operation_cards[op.id] = card
            else:
                card.set_operation(op)
            target_index = position
            if self._operations_col.indexOf(card) != target_index:
                self._operations_col.removeWidget(card)
                self._operations_col.insertWidget(target_index, card)

    def _rebuild_used_tools(self, tool_numbers: list[int]) -> None:
        wanted = set(tool_numbers)
        for number in list(self._tool_rows.keys()):
            if number not in wanted:
                row = self._tool_rows.pop(number)
                self._tools_layout.removeWidget(row)
                row.deleteLater()
        for number in tool_numbers:
            if number not in self._tool_rows:
                row = _UsedToolRow(number)
                self._tool_rows[number] = row
                self._tools_layout.addWidget(row)
            else:
                self._tool_rows[number].refresh()

    # ── Interaction ──────────────────────────────────────────────────────────

    def _on_operation_clicked(self, operation_id: int) -> None:
        operation = self._db.get_operation(operation_id)
        title = operation.name if operation else "Programm"
        self._nav.push(ProgramDetailPage(operation_id, self._nav), title)

    def _show_wizard_stub(self) -> None:
        QMessageBox.information(
            self, "Setup-Assistent",
            "Der Setup-Assistent ist noch nicht implementiert.",
        )

    def _on_delete_operation(self, operation_id: int) -> None:
        self._db.delete_operation(operation_id)

    def _auto_save_notes(self) -> None:
        if self._loading:
            return
        workpiece = self._db.get_workpiece(self._workpiece_id)
        if workpiece is None:
            return
        workpiece.notes = self._notes_edit.toPlainText()
        self._db.upsert_workpiece(workpiece)

    # ── Signals ──────────────────────────────────────────────────────────────

    def _on_workpiece_changed(self, workpiece_id: int) -> None:
        if workpiece_id == self._workpiece_id:
            self._reload()

    def _on_operation_changed(self, _operation_id: int) -> None:
        self._reload()

    def _on_tool_changed(self, tool_number: int) -> None:
        row = self._tool_rows.get(tool_number)
        if row is not None:
            row.refresh()


def _format_hms(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
