"""
ui/pages/workpiece_browser_page.py — WorkpiecesSection (the page
registered in main_window.py) + WorkpieceBrowserPage (one folder level:
groups above workpieces, arbitrarily deep).

WorkpiecesSection wraps a PageStack (ui/widgets/page_stack.py) whose base
page is WorkpieceBrowserPage(relative_path="") — the configured root.
Clicking a GROUP folder card pushes another WorkpieceBrowserPage for that
child path (breadcrumb title = the folder's own name); clicking a
WORKPIECE card pushes a WorkpieceDetailPage, which can push a
ProgramDetailPage, and so on — PageStack itself has no depth limit, so
this nests as deep as the folder tree on disk does.

Groups are NOT persisted (see domain/models.py's module-level note) —
every WorkpieceBrowserPage instance re-derives its own level's contents
from persistence.workpiece_sync.list_folder_contents() on open/Sync,
which itself re-syncs (persistence.workpiece_sync.sync_folder_tree()) the
filesystem under that level into WorkpieceDatabase first.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QScrollArea,
    QScroller, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from controller.domain.models import Workpiece
from controller.persistence.workpiece_db import WorkpieceDatabase, WorkpieceDatabaseSignals
from controller.persistence.workpiece_sync import (
    absolute_folder_for,
    create_group_folder,
    create_workpiece_folder,
    list_folder_contents,
    sync_folder_tree,
    unique_child_relative_path,
)
from controller.sim.core.settings import AppSettings
from controller.ui.icon_loader import get_icon
from controller.ui.pages.workpiece_detail_page import WorkpieceDetailPage
from controller.ui.widgets.card import Card
from controller.ui.widgets.card_button import CardButton
from controller.ui.widgets.elided_label import ElidedLabel
from controller.ui.widgets.page_stack import PageStack
from controller.ui.widgets.preview_thumbnail import PreviewThumbnail
from controller.ui.widgets.tap_gesture import TapGestureMixin
from controller.ui.widgets.workpiece_filter_bar import WorkpieceFilterBar

_DATE_FMT = "%d.%m.%Y %H:%M"


def _no_root_warning(parent: QWidget) -> None:
    QMessageBox.warning(
        parent, "Kein Wurzelordner konfiguriert",
        "Es ist noch kein Werkstück-Wurzelordner konfiguriert.\n\n"
        "Unter Einstellungen -> Workpieces einen Wurzelordner festlegen, "
        "um Werkstücke und Ordner anlegen zu können.",
    )


class _NewWorkpieceCard(TapGestureMixin, CardButton):
    """Pinned action card: creates a new subfolder AND registers it as a
    Workpiece immediately (see create_workpiece_folder()). TapGestureMixin
    listed first so its press/release tap detection shadows CardButton's
    own click-on-press behaviour (see that mixin's module docstring)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "New Workpiece", icon=get_icon("addtool", tint=True), icon_size=32,
        )
        self.setProperty("variant", "create_tool")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(64)


class _NewFolderCard(TapGestureMixin, CardButton):
    """Pinned action card: creates an empty subfolder — a pure GROUP, no
    DB entry (see create_group_folder()). See _NewWorkpieceCard on why
    TapGestureMixin comes first."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "New Folder", icon=get_icon("folder", tint=True), icon_size=32,
        )
        self.setProperty("variant", "create_tool")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(64)


class _GroupCard(TapGestureMixin, Card):
    """One GROUP subfolder: folder icon + name, no dates (groups carry no
    DB metadata — see domain/models.py's module-level note). Settings
    menu offers Delete, cascading — see WorkpieceBrowserPage._on_group_delete_requested()."""

    clicked = Signal()
    delete_requested = Signal()

    def __init__(self, relative_path: str, parent: QWidget | None = None) -> None:
        super().__init__(title=None, parent=parent)
        self.relative_path = relative_path
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout()
        row.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setPixmap(get_icon("folder", tint=True, size=QSize(28, 28)).pixmap(28, 28))
        row.addWidget(icon_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._name_lbl = ElidedLabel()
        self._name_lbl.setObjectName("WorkpieceCardName")
        self._name_lbl.set_full_text(relative_path.rsplit("/", 1)[-1])
        row.addWidget(self._name_lbl, stretch=1)

        self._menu_btn = QToolButton()
        self._menu_btn.setIcon(get_icon("settings", tint=True))
        self._menu_btn.setIconSize(QSize(18, 18))
        self._menu_btn.setFixedSize(32, 32)
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.clicked.connect(self._show_menu)
        row.addWidget(self._menu_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.content_layout.addLayout(row)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        delete_action = menu.addAction(get_icon("delete", tint=True), "Delete")
        delete_action.triggered.connect(self.delete_requested)
        menu.exec(self._menu_btn.mapToGlobal(self._menu_btn.rect().bottomLeft()))


class _WorkpieceCard(TapGestureMixin, Card):
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
            "Der zugehörige Ordner samt aller G-Code-Dateien wird dabei "
            "UNWIDERRUFLICH von der Festplatte gelöscht.\n"
            "Diese Aktion kann nicht rückgängig gemacht werden."
        )
        delete_btn = box.addButton("Löschen", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(delete_btn)
        box.exec()
        if box.clickedButton() is delete_btn:
            self.delete_requested.emit()


class _BrowserListView(QScrollArea):
    """Vertically scrollable list: two pinned action cards first, then
    group cards (folders), then workpiece cards — see
    WorkpieceBrowserPage._refresh_list() for the sort/filter that decides
    what ends up in *groups*/*workpieces*. Update-in-place (matched by
    relative_path / id), same reasoning as ToolListView.set_tools()."""

    create_workpiece_requested = Signal()
    create_folder_requested = Signal()
    group_clicked = Signal(str)             # relative_path
    group_delete_requested = Signal(str)    # relative_path
    workpiece_clicked = Signal(int)
    workpiece_delete_requested = Signal(int)

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

        # Side by side, half-width each — the vertical list of group/
        # workpiece cards only starts below this one row.
        actions_row = QHBoxLayout()
        actions_row.setSpacing(6)
        self._new_workpiece_card = _NewWorkpieceCard()
        self._new_workpiece_card.clicked.connect(self.create_workpiece_requested)
        actions_row.addWidget(self._new_workpiece_card, stretch=1)

        self._new_folder_card = _NewFolderCard()
        self._new_folder_card.clicked.connect(self.create_folder_requested)
        actions_row.addWidget(self._new_folder_card, stretch=1)

        actions_widget = QWidget()
        actions_widget.setLayout(actions_row)
        self._col.addWidget(actions_widget)

        self._group_cards: dict[str, _GroupCard] = {}
        self._workpiece_cards: dict[int, _WorkpieceCard] = {}
        self._empty_lbl: QLabel | None = None

    def set_contents(self, groups: list[str], workpieces: list[Workpiece]) -> None:
        wanted_groups = set(groups)
        for rel in list(self._group_cards.keys()):
            if rel not in wanted_groups:
                card = self._group_cards.pop(rel)
                self._col.removeWidget(card)
                card.deleteLater()

        wanted_wp = {w.id: w for w in workpieces}
        for wid in list(self._workpiece_cards.keys()):
            if wid not in wanted_wp:
                card = self._workpiece_cards.pop(wid)
                self._col.removeWidget(card)
                card.deleteLater()

        if self._empty_lbl is not None:
            self._col.removeWidget(self._empty_lbl)
            self._empty_lbl.deleteLater()
            self._empty_lbl = None

        if not groups and not workpieces:
            self._empty_lbl = QLabel("Dieser Ordner ist leer.")
            self._empty_lbl.setObjectName("CardTitle")
            self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._col.addWidget(self._empty_lbl)
            return

        # Groups first (navigation before content), then workpieces —
        # each bucket already arrives alphabetically sorted (see
        # WorkpieceBrowserPage._refresh_list()).
        position = 1   # index 0 is the pinned action-cards row
        for rel in groups:
            card = self._group_cards.get(rel)
            if card is None:
                card = _GroupCard(rel)
                card.clicked.connect(lambda r=rel: self.group_clicked.emit(r))
                card.delete_requested.connect(lambda r=rel: self.group_delete_requested.emit(r))
                self._group_cards[rel] = card
            if self._col.indexOf(card) != position:
                self._col.removeWidget(card)
                self._col.insertWidget(position, card)
            position += 1

        for workpiece in workpieces:
            card = self._workpiece_cards.get(workpiece.id)
            if card is None:
                card = _WorkpieceCard(workpiece)
                card.clicked.connect(lambda wid=workpiece.id: self.workpiece_clicked.emit(wid))
                card.delete_requested.connect(
                    lambda wid=workpiece.id: self.workpiece_delete_requested.emit(wid)
                )
                self._workpiece_cards[workpiece.id] = card
            else:
                card.set_workpiece(workpiece)
            if self._col.indexOf(card) != position:
                self._col.removeWidget(card)
                self._col.insertWidget(position, card)
            position += 1


class WorkpieceBrowserPage(QWidget):
    """One folder level. `nav` is anything exposing push(widget, title)
    (see WorkpiecesSection) — threaded down so a pushed child level (or a
    WorkpieceDetailPage) can keep pushing further onto the same stack."""

    def __init__(self, relative_path: str, nav, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._relative_path = relative_path
        self._nav = nav
        self._db = WorkpieceDatabase.instance()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(12, 8, 12, 4)
        title = QLabel(relative_path.rsplit("/", 1)[-1] if relative_path else "Werkstücke")
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

        self._list = _BrowserListView(self)
        root.addWidget(self._list, stretch=1)

        self._list.create_workpiece_requested.connect(self._on_create_workpiece)
        self._list.create_folder_requested.connect(self._on_create_folder)
        self._list.group_clicked.connect(self._on_group_clicked)
        self._list.group_delete_requested.connect(self._on_group_delete_requested)
        self._list.workpiece_clicked.connect(self._on_workpiece_clicked)
        self._list.workpiece_delete_requested.connect(self._on_workpiece_delete_requested)

        self._filter_bar.search_changed.connect(self._refresh_list)

        WorkpieceDatabaseSignals.instance().workpiece_changed.connect(self._reload)

        self._synced_once = False
        self._groups: list[str] = []
        self._workpieces: list[Workpiece] = []
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
        result = sync_folder_tree(self._relative_path, self._db)
        if not result.ok:
            QMessageBox.warning(
                self, "Sync-Fehler",
                "Beim Synchronisieren der Werkstück-Ordner sind Fehler aufgetreten:\n\n"
                + "\n".join(result.errors),
            )
        self._reload()

    def _reload(self, *_args) -> None:
        contents = list_folder_contents(self._relative_path, self._db)
        if not contents.ok:
            QMessageBox.warning(self, "Fehler", "\n".join(contents.errors))
        self._groups = contents.groups
        self._workpieces = contents.workpieces
        self._refresh_list()

    def _refresh_list(self, *_args) -> None:
        search = self._filter_bar.search_text()
        groups = self._groups
        workpieces = self._workpieces
        if search:
            groups = [g for g in groups if search in g.rsplit("/", 1)[-1].lower()]
            workpieces = [w for w in workpieces if search in w.name.lower()]
        self._list.set_contents(groups, workpieces)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_create_workpiece(self) -> None:
        if not AppSettings.instance().workpieces_root_path:
            _no_root_warning(self)
            return
        name, ok = QInputDialog.getText(self, "Neues Werkstück", "Name:")
        name = name.strip() if ok else ""
        if not name:
            return
        child_rel = unique_child_relative_path(self._relative_path, name)
        try:
            workpiece = create_workpiece_folder(child_rel, name=name, db=self._db)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Werkstück anlegen fehlgeschlagen", str(exc))
            return
        self._on_workpiece_clicked(workpiece.id)

    def _on_create_folder(self) -> None:
        if not AppSettings.instance().workpieces_root_path:
            _no_root_warning(self)
            return
        name, ok = QInputDialog.getText(self, "Neuer Ordner", "Name:")
        name = name.strip() if ok else ""
        if not name:
            return
        child_rel = unique_child_relative_path(self._relative_path, name)
        try:
            create_group_folder(child_rel)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Ordner anlegen fehlgeschlagen", str(exc))
            return
        self._reload()   # no navigation — the new folder just appears in the list

    def _on_group_clicked(self, relative_path: str) -> None:
        title = relative_path.rsplit("/", 1)[-1]
        self._nav.push(WorkpieceBrowserPage(relative_path, self._nav), title)

    def _on_group_delete_requested(self, relative_path: str) -> None:
        """Cascading folder delete — replaces the earlier "blocked unless
        empty" rule: deleting a GROUP now recursively removes every
        Workpiece underneath it (workpieces_under(), any depth) AND the
        physical folder tree itself (their G-code files included).

        Order matters for consistency: the folder is removed from disk
        FIRST, and DB rows only deleted once that succeeded. Deleting the
        DB rows first and having the filesystem removal fail afterward
        (permission error, a file open elsewhere) would leave orphaned
        G-code files with no DB reference — which the next sync would
        misread as brand-new workpieces.
        """
        under = self._db.workpieces_under(relative_path)
        folder_name = relative_path.rsplit("/", 1)[-1]

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Ordner löschen")
        if under:
            box.setText(
                f"Ordner \"{folder_name}\" wirklich löschen?\n\n"
                f"Dieser Ordner enthält {len(under)} Werkstück(e). Alle "
                "zugehörigen G-Code-Dateien werden dabei UNWIDERRUFLICH "
                "von der Festplatte gelöscht — nicht nur die "
                "Datenbank-Einträge.\n\n"
                "Diese Aktion kann nicht rückgängig gemacht werden."
            )
        else:
            box.setText(
                f"Ordner \"{folder_name}\" wirklich löschen?\n"
                "Diese Aktion kann nicht rückgängig gemacht werden."
            )
        delete_btn = box.addButton("Ordner löschen", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        # Deliberate departure from tool_card_widget.py's single-tool
        # _confirm_delete() convention (which defaults to the destructive
        # button): a cascading delete of potentially many workpieces AND
        # their files is a much heavier action — Abbrechen gets the
        # default focus/Enter-key binding here, "Ordner löschen" is never
        # triggered by a stray Enter press.
        box.setDefaultButton(cancel_btn)
        box.exec()
        if box.clickedButton() is not delete_btn:
            return

        root = AppSettings.instance().workpieces_root_path
        try:
            shutil.rmtree(Path(root) / relative_path)
        except OSError as exc:
            QMessageBox.warning(self, "Löschen fehlgeschlagen", str(exc))
            return

        for workpiece in under:
            self._db.delete_workpiece(workpiece.id)
        self._reload()

    def _on_workpiece_clicked(self, workpiece_id: int) -> None:
        workpiece = self._db.get_workpiece(workpiece_id)
        title = workpiece.name if workpiece else "Werkstück"
        self._nav.push(WorkpieceDetailPage(workpiece_id, self._nav), title)

    def _on_workpiece_delete_requested(self, workpiece_id: int) -> None:
        """DB row + this one workpiece's own folder contents — not the
        Job cross-check (see Abschnitt A: dropped entirely, never built).
        Same disk-first-then-DB ordering as the cascading group delete
        (_on_group_delete_requested()), for the same orphan-avoidance
        reason."""
        workpiece = self._db.get_workpiece(workpiece_id)
        if workpiece is None:
            return
        folder = absolute_folder_for(workpiece)
        if folder is not None and folder.is_dir():
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                QMessageBox.warning(self, "Löschen fehlgeschlagen", str(exc))
                return
        self._db.delete_workpiece(workpiece_id)


class WorkpiecesSection(QWidget):
    """The page registered in main_window.py's top-level QStackedWidget —
    hosts the whole Workpieces navigation hierarchy behind one PageStack,
    starting at WorkpieceBrowserPage("") (the configured root).

    Also doubles as the `nav` object every page in the hierarchy holds a
    reference to — besides push/pop/can_pop/reset, it carries
    load_in_machine_requested: ProgramDetailPage's "In Maschine laden"
    button calls request_load_in_machine() on it without knowing anything
    about MainWindow; main_window.py connects to the signal once and does
    the actual MachinePage.load_file() + page switch."""

    load_in_machine_requested = Signal(str)   # gcode_path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        root_page = WorkpieceBrowserPage("", nav=self)
        self._nav = PageStack(root_page, "Werkstücke", self)
        layout.addWidget(self._nav)

    def push(self, widget: QWidget, title: str) -> None:
        self._nav.push(widget, title)

    def pop(self):
        return self._nav.pop()

    def can_pop(self) -> bool:
        return self._nav.can_pop()

    def reset(self) -> None:
        self._nav.reset()

    def request_load_in_machine(self, gcode_path: str) -> None:
        self.load_in_machine_requested.emit(gcode_path)

    @property
    def current_title(self) -> str:
        return self._nav.current_title
