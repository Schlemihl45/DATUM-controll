"""
ui/pages/tool_page.py — ToolPage: the app's tool-magazine management page
(pinned magazine bar on top + stacked view for List+Filter vs Detail).
"""
from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from controller.persistence.tool_db import ToolDatabase, ToolDatabaseSignals
from controller.sim.core.settings import AppSettings
from controller.sim.simulation.tool_definition import ToolDefinition
from controller.ui.pages.tool_detail_page import ToolDetailPage
from controller.ui.widgets.tool_filter_bar import ToolFilterBar
from controller.ui.widgets.tool_list_card import ToolListView
from controller.ui.widgets.tool_magazine_bar import ToolMagazineBar, UNASSIGNED_POCKET

_LIST_INDEX   = 0
_DETAIL_INDEX = 1


def _sort_value(tool: ToolDefinition, key: str):
    if key == "diameter":
        return tool.diameter
    if key == "flute_count":
        return tool.flute_count
    if key == "tool_type":
        return tool.tool_type.name
    if key == "tool_number":
        return tool.tool_number
    if key == "pocket":
        has_pocket = tool.pocket if tool.pocket != UNASSIGNED_POCKET and tool.pocket >= 1 else float("inf")
        return (has_pocket, tool.tool_number)
    return (tool.name or tool.remark or "").lower()


class ToolPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = ToolDatabase.instance()
        self._s = AppSettings.instance()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Magazin-Leiste bleibt ganz oben fixiert (immer sichtbar)
        self._magazine_bar = ToolMagazineBar(self)
        root.addWidget(self._magazine_bar)

        # 2. QStackedWidget für den Wechsel zwischen Listen-Ansicht und Detail-Ansicht
        self._detail_stack = QStackedWidget(self)

        # --- SEITE 0: Listen-Ansicht (Filterleiste + ToolListView) ---
        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)

        self._filter_bar = ToolFilterBar(self)
        list_layout.addWidget(self._filter_bar)

        self._list = ToolListView(self)
        list_layout.addWidget(self._list, stretch=1)

        self._detail_stack.addWidget(list_container)              # _LIST_INDEX

        # --- SEITE 1: Detail-Ansicht ---
        self._detail = ToolDetailPage(self)
        self._detail_stack.addWidget(self._detail)                # _DETAIL_INDEX

        root.addWidget(self._detail_stack, stretch=1)

        # ── Wiring ───────────────────────────────────────────────────────────
        self._list.tool_details_requested.connect(self._open_detail)
        if hasattr(self._list, "tool_clicked"):
            self._list.tool_clicked.connect(self._open_detail)

        self._detail.back_requested.connect(self._close_detail)
        self._magazine_bar.tool_dropped.connect(self._on_pocket_reassigned)
        self._magazine_bar.tool_clicked.connect(self._open_detail)

        self._filter_bar.search_changed.connect(self._refresh_list)
        self._filter_bar.sort_changed.connect(self._refresh_list)
        self._filter_bar.magazine_only_toggled.connect(self._refresh_list)

        self._s.tool_pocket_count_changed.connect(self._on_pocket_count_changed)
        ToolDatabaseSignals.instance().tool_changed.connect(self._on_tool_changed)

        self._magazine_bar.set_pocket_count(self._s.tool_pocket_count)
        self._reload()

    # ── Data flow ────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        tools = self._db.all_tools()
        self._magazine_bar.set_tools(tools)
        self._all_tools = tools
        self._refresh_list()

    def _refresh_list(self, *_args) -> None:
        tools = list(getattr(self, "_all_tools", []))
        search = self._filter_bar._search.text().strip().lower()
        if search:
            tools = [
                t for t in tools
                if search in (t.name or "").lower() or search in (t.remark or "").lower()
            ]
        if self._filter_bar._magazine_only_btn.isChecked():
            tools = [t for t in tools if t.pocket >= 1]

        sort_key = self._filter_bar.sort_key() if hasattr(self._filter_bar, "sort_key") else "pocket"
        tools.sort(key=lambda t: _sort_value(t, sort_key))
        self._list.set_tools(tools)

    def _on_tool_changed(self, _tool_number: int) -> None:
        self._reload()

    def _on_pocket_count_changed(self, n: int) -> None:
        self._magazine_bar.set_pocket_count(n)
        self._magazine_bar.set_tools(getattr(self, "_all_tools", []))

    def _on_pocket_reassigned(self, tool_number: int, target_pocket: int) -> None:
        moved = self._db.get_tool(tool_number)
        if moved is None:
            return
        if target_pocket == UNASSIGNED_POCKET:
            moved.pocket = UNASSIGNED_POCKET
            self._db.upsert_tool(moved)
            return
        occupant = next(
            (t for t in self._db.all_tools()
             if t.pocket == target_pocket and t.tool_number != tool_number),
            None,
        )
        if occupant is not None:
            occupant.pocket = UNASSIGNED_POCKET
            self._db.upsert_tool(occupant)
        moved.pocket = target_pocket
        self._db.upsert_tool(moved)

    # ── List <-> Detail navigation ──────────────────────────────────────────

    def is_showing_detail(self) -> bool:
        return self._detail_stack.currentIndex() == _DETAIL_INDEX

    def close_detail(self) -> None:
        self._close_detail()

    def _open_detail(self, tool_number: int) -> None:
        tool = self._db.get_tool(tool_number)
        if tool is None:
            return
        self._detail.set_tool(tool)
        self._detail_stack.setCurrentIndex(_DETAIL_INDEX)

    def _close_detail(self) -> None:
        self._detail_stack.setCurrentIndex(_LIST_INDEX)