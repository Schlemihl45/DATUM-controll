"""
ui/pages/workpieces_page.py — WorkpiecesSection (the page registered in
main_window.py) + WorkpiecesPage (the scrollable list of workpieces
inside it).

WorkpiecesSection wraps a PageStack (ui/widgets/page_stack.py) whose base
page is WorkpiecesPage — clicking a workpiece card pushes a
WorkpieceDetailPage on top, which can itself push a ProgramDetailPage, and
so on. main_window.py only ever talks to WorkpiecesSection's push/pop/
can_pop/reset (delegated straight to the inner PageStack) — it never needs
to know how deep the hierarchy underneath actually is.
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QScrollArea,
    QScroller, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from controller.domain.models import Workpiece
from controller.persistence.workpiece_db import WorkpieceDatabase, WorkpieceDatabaseSignals
from controller.persistence.workpiece_sync import sync_workpieces_root
from controller.sim.core.settings import AppSettings
from controller.ui.icon_loader import get_icon
from controller.ui.pages.workpiece_detail_page import WorkpieceDetailPage
from controller.ui.widgets.card import Card
from controller.ui.widgets.card_button import CardButton
from controller.ui.widgets.elided_label import ElidedLabel
from controller.ui.widgets.page_stack import PageStack
from controller.ui.widgets.preview_thumbnail import PreviewThumbnail
from controller.ui.widgets.workpiece_filter_bar import WorkpieceFilterBar

_DATE_FMT = "%d.%m.%Y %H:%M"

# Filesystem characters invalid on at least one of Windows/Linux/macOS —
# a typed workpiece name is sanitized into a real folder name with this
# rather than rejecting the input outright.
_INVALID_FOLDER_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class _CreateWorkpieceCard(CardButton):
    """Pinned first row: "+ New Workpiece" — same idiom as ToolPage's
    _CreateToolCard (tool_list_card.py)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Neues Werkstück", icon=get_icon("addtool", tint=True), icon_size=32,
        )
        self.setProperty("variant", "create_tool")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(64)


class _WorkpieceCard(Card):
    """One workpiece row: icon, two-line info (name / created+modified),
    settings menu (Delete). Kept narrow enough for the app's ~600px window
    width (see main_window.py's resize(600, 900)) — ElidedLabel on the
    name so a long name never pushes the settings button off-frame."""

    clicked = Signal()
    delete_requested = Signal()

    def __init__(self, workpiece: Workpiece, parent: QWidget | None = None) -> None:
        super().__init__(title=None, parent=parent)
        self._id = workpiece.id
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

        self._menu_btn = QToolButton()
        self._menu_btn.setIcon(get_icon("settings", tint=True))
        self._menu_btn.setIconSize(QSize(18, 18))
        self._menu_btn.setFixedSize(32, 32)
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.setToolTip("Optionen")
        self._menu_btn.clicked.connect(self._show_menu)
        row.addWidget(self._menu_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.content_layout.addLayout(row)
        self.set_workpiece(workpiece)

    def set_workpiece(self, workpiece: Workpiece) -> None:
        self._id = workpiece.id
        self._name_lbl.set_full_text(workpiece.name)
        self._sub_lbl.set_full_text(
            f"Erstellt: {workpiece.created_at.strftime(_DATE_FMT)} · "
            f"Geändert: {workpiece.modified_at.strftime(_DATE_FMT)}"
        )
        self._thumb.set_preview_source("", None)
        self._thumb.set_material_hint(workpiece.material)

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
        box.setWindowTitle("Werkstück löschen")
        box.setText(
            "Werkstück wirklich löschen?\n"
            "Alle zugehörigen Programme werden mitgelöscht.\n"
            "Diese Aktion kann nicht rückgängig gemacht werden."
        )
        delete_btn = box.addButton("Löschen", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(delete_btn)
        box.exec()
        if box.clickedButton() is delete_btn:
            self.delete_requested.emit()


class _WorkpieceListView(QScrollArea):
    """Vertically scrollable list: pinned _CreateWorkpieceCard first, then
    one _WorkpieceCard per workpiece. Update-in-place (matched by id),
    same reasoning as ToolListView.set_tools() — see tool_list_card.py."""

    create_requested = Signal()
    workpiece_clicked = Signal(int)
    delete_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        QScroller.grabGesture(self.viewport(), QScroller.ScrollerGestureType.TouchGesture)

        container = QWidget()
        self._col = QVBoxLayout(container)
        self._col.setContentsMargins(8, 8, 8, 8)
        self._col.setSpacing(6)
        self._col.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(container)

        self._create_card = _CreateWorkpieceCard()
        self._create_card.clicked.connect(self.create_requested)
        self._col.addWidget(self._create_card)

        self._cards: dict[int, _WorkpieceCard] = {}
        self._empty_lbl: QLabel | None = None

    def set_workpieces(self, workpieces: list[Workpiece]) -> None:
        wanted = {w.id: w for w in workpieces}
        for wid in list(self._cards.keys()):
            if wid not in wanted:
                card = self._cards.pop(wid)
                self._col.removeWidget(card)
                card.deleteLater()

        if self._empty_lbl is not None:
            self._col.removeWidget(self._empty_lbl)
            self._empty_lbl.deleteLater()
            self._empty_lbl = None

        if not workpieces:
            self._empty_lbl = QLabel("Keine Werkstücke gefunden.")
            self._empty_lbl.setObjectName("CardTitle")
            self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._col.addWidget(self._empty_lbl)
            return

        for position, workpiece in enumerate(workpieces):
            card = self._cards.get(workpiece.id)
            if card is None:
                card = _WorkpieceCard(workpiece)
                card.clicked.connect(lambda wid=workpiece.id: self.workpiece_clicked.emit(wid))
                card.delete_requested.connect(lambda wid=workpiece.id: self.delete_requested.emit(wid))
                self._cards[workpiece.id] = card
            else:
                card.set_workpiece(workpiece)
            target_index = position + 1   # index 0 is the create-card
            if self._col.indexOf(card) != target_index:
                self._col.removeWidget(card)
                self._col.insertWidget(target_index, card)


class WorkpiecesPage(QWidget):
    """The base page of the Workpieces section: the scrollable list. See
    module docstring — `nav` is threaded down to whatever gets pushed
    (WorkpieceDetailPage) so it can keep pushing further (ProgramDetailPage)
    onto the same stack."""

    def __init__(self, nav, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nav = nav
        self._db = WorkpieceDatabase.instance()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(12, 8, 12, 4)
        title = QLabel("Werkstücke")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch(1)

        self._sync_btn = QToolButton()
        self._sync_btn.setIcon(get_icon("sync", tint=True))
        self._sync_btn.setIconSize(QSize(20, 20))
        self._sync_btn.setToolTip("Ordner synchronisieren")
        self._sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sync_btn.clicked.connect(self._run_sync)
        header.addWidget(self._sync_btn)
        root.addLayout(header)

        self._filter_bar = WorkpieceFilterBar(self)
        root.addWidget(self._filter_bar)

        self._list = _WorkpieceListView(self)
        root.addWidget(self._list, stretch=1)

        self._list.create_requested.connect(self._on_create_workpiece)
        self._list.workpiece_clicked.connect(self._on_workpiece_clicked)
        self._list.delete_requested.connect(self._on_delete_requested)

        self._filter_bar.search_changed.connect(self._refresh_list)
        self._filter_bar.sort_changed.connect(self._refresh_list)

        WorkpieceDatabaseSignals.instance().workpiece_changed.connect(self._reload)

        self._synced_once = False
        self._all_workpieces: list[Workpiece] = []
        self._reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Explicit sync on open (see persistence/workpiece_sync.py's module
        # docstring — no background watcher). Only once per page instance;
        # the Sync button covers every re-sync after that.
        if not self._synced_once:
            self._synced_once = True
            self._run_sync()

    # ── Data flow ────────────────────────────────────────────────────────────

    def _run_sync(self) -> None:
        result = sync_workpieces_root(self._db)
        if not result.ok:
            QMessageBox.warning(
                self, "Sync-Fehler",
                "Beim Synchronisieren der Werkstück-Ordner sind Fehler aufgetreten:\n\n"
                + "\n".join(result.errors),
            )
        self._reload()

    def _reload(self, *_args) -> None:
        self._all_workpieces = self._db.all_workpieces()
        self._refresh_list()

    def _refresh_list(self, *_args) -> None:
        workpieces = list(self._all_workpieces)
        search = self._filter_bar.search_text()
        if search:
            workpieces = [w for w in workpieces if search in w.name.lower()]
        key = self._filter_bar.sort_key()
        if key == "created_at":
            workpieces.sort(key=lambda w: w.created_at, reverse=True)
        elif key == "modified_at":
            workpieces.sort(key=lambda w: w.modified_at, reverse=True)
        else:
            workpieces.sort(key=lambda w: w.name.lower())
        self._list.set_workpieces(workpieces)

    def _on_create_workpiece(self) -> None:
        """"New Workpiece" — per spec, this creates a real folder AND a DB
        entry, not a DB-only stand-in. If AppSettings.workpieces_root_path
        is configured, the folder is created there, so the next sync pass
        picks it up like any other workpiece folder. If no root is
        configured yet (empty by default — see AppSettings.workpieces_root_path),
        the folder is still created, just under this app's own local data
        directory (see persistence/paths.py's db_dir(), same "developer/
        user can actually find it" reasoning), and the user is told so —
        it silently participates in sync once a root is set."""
        name, ok = QInputDialog.getText(self, "Neues Werkstück", "Name:")
        name = name.strip() if ok else ""
        if not name:
            return

        root = AppSettings.instance().workpieces_root_path
        base_dir = Path(root) if root else _unlinked_workpieces_dir()
        folder = _unique_workpiece_folder(base_dir, name)
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            QMessageBox.warning(
                self, "Werkstück anlegen fehlgeschlagen",
                f"Der Ordner konnte nicht angelegt werden:\n{folder}\n\n{exc}",
            )
            return

        if not root:
            QMessageBox.information(
                self, "Kein Wurzelordner konfiguriert",
                "Es ist noch kein Werkstück-Wurzelordner konfiguriert.\n\n"
                f"Das Werkstück wurde lokal unter\n{folder}\nangelegt und "
                "nimmt automatisch am Ordner-Sync teil, sobald ein "
                "Wurzelordner eingestellt ist.",
            )

        workpiece = self._db.get_or_create_by_folder(str(folder), default_name=name)
        self._on_workpiece_clicked(workpiece.id)

    def _on_workpiece_clicked(self, workpiece_id: int) -> None:
        workpiece = self._db.get_workpiece(workpiece_id)
        title = workpiece.name if workpiece else "Werkstück"
        self._nav.push(WorkpieceDetailPage(workpiece_id, self._nav), title)

    def _on_delete_requested(self, workpiece_id: int) -> None:
        self._db.delete_workpiece(workpiece_id)


class WorkpiecesSection(QWidget):
    """The page registered in main_window.py's top-level QStackedWidget —
    hosts the whole Workpieces navigation hierarchy behind one PageStack.
    See module docstring.

    Also doubles as the `nav` object every page in the hierarchy holds a
    reference to (WorkpiecesPage(nav=self), then passed on unchanged to
    WorkpieceDetailPage/ProgramDetailPage) — so besides push/pop/can_pop/
    reset, it carries load_in_machine_requested: ProgramDetailPage's "In
    Maschine laden" button calls request_load_in_machine() on it without
    knowing anything about MainWindow; main_window.py connects to the
    signal once and does the actual MachinePage.load_file() + page switch.
    """

    load_in_machine_requested = Signal(str)   # gcode_path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        list_page = WorkpiecesPage(nav=self)
        self._nav = PageStack(list_page, "Werkstücke", self)
        layout.addWidget(self._nav)

    def push(self, widget: QWidget, title: str) -> None:
        self._nav.push(widget, title)

    def request_load_in_machine(self, gcode_path: str) -> None:
        self.load_in_machine_requested.emit(gcode_path)

    def pop(self):
        return self._nav.pop()

    def can_pop(self) -> bool:
        return self._nav.can_pop()

    def reset(self) -> None:
        self._nav.reset()

    @property
    def current_title(self) -> str:
        return self._nav.current_title


def _unlinked_workpieces_dir() -> Path:
    """Fallback location for a manually-created workpiece folder when no
    AppSettings.workpieces_root_path is configured yet — a sibling of
    persistence/paths.py's db_dir(), for the same "a developer/user can
    actually find it without knowing OS conventions" reasoning that
    module's own docstring gives for data/db/."""
    from controller.persistence.paths import db_dir

    return db_dir().parent / "workpieces_unlinked"


def _sanitize_folder_name(name: str) -> str:
    cleaned = _INVALID_FOLDER_CHARS_RE.sub("_", name).strip().strip(".")
    return cleaned or "Werkstueck"


def _unique_workpiece_folder(base_dir: Path, name: str) -> Path:
    """base_dir/<sanitized name>, or base_dir/<sanitized name>_2, _3, ...
    the first one that doesn't already exist — so two workpieces named
    the same never collide on disk."""
    slug = _sanitize_folder_name(name)
    candidate = base_dir / slug
    counter = 2
    while candidate.exists():
        candidate = base_dir / f"{slug}_{counter}"
        counter += 1
    return candidate
