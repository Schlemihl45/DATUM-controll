"""
ui/pages/tool_page.py — ToolPage: the app's tool-magazine management page
(pinned magazine bar + search/filter + main list of inline-expanding tool
cards — DATRON Next style, no separate detail page).

Registered as a top-level page in main_window.py's QStackedWidget,
alongside MachinePage/SettingsPage — reached from the home screen's
"Tools" nav button and left the same way every other page is, via the
app-wide return_btn in the quick bar (main_window.py's Return button no
longer needs any ToolPage-specific handling — there's no separate detail
sub-page to close first anymore; expand/collapse lives entirely inside
the list itself).
"""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from controller.persistence.tool_db import ToolDatabase, ToolDatabaseSignals
from controller.sim.core.settings import AppSettings
from controller.sim.simulation.tool_definition import ToolDefinition, UNASSIGNED_POCKET
from controller.ui.widgets.tool_filter_bar import ToolFilterBar
from controller.ui.widgets.tool_list_card import ToolListView, CreateToolDialog
from controller.ui.widgets.tool_magazine_bar import ToolMagazineBar


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
        # Assigned pockets first, ascending (1, 2, 3, ...); unassigned
        # (-1) tools sorted after all of them, not before.
        return (tool.pocket == UNASSIGNED_POCKET, tool.pocket)
    return (tool.name or tool.remark or "").lower()


class ToolPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = ToolDatabase.instance()
        self._s = AppSettings.instance()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._magazine_bar = ToolMagazineBar(self)
        root.addWidget(self._magazine_bar)

        self._filter_bar = ToolFilterBar(self)
        root.addWidget(self._filter_bar)

        self._list = ToolListView(self)
        root.addWidget(self._list, stretch=1)

        # ── Wiring ───────────────────────────────────────────────────────────
        self._list.create_tool_requested.connect(self._on_create_tool)
        self._list.tool_dropped_for_removal.connect(self._on_tool_removed_from_magazine)
        self._list.pocket_change_requested.connect(self._on_pocket_reassigned)
        self._magazine_bar.tool_dropped.connect(self._on_pocket_reassigned)
        self._magazine_bar.tool_clicked.connect(self._list.expand_and_scroll_to)

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
        tools.sort(key=lambda t: _sort_value(t, self._filter_bar.sort_key()))
        self._list.set_tools(tools)

    def _on_tool_changed(self, _tool_number: int) -> None:
        self._reload()

    def _on_pocket_count_changed(self, n: int) -> None:
        self._magazine_bar.set_pocket_count(n)
        self._magazine_bar.set_tools(getattr(self, "_all_tools", []))

    def _on_create_tool(self) -> None:
        """"Create new tool" card clicked: insert a fresh ToolDefinition
        and open it for editing in a popup (CreateToolDialog) — the same
        ToolCardWidget design, but NOT inserted as a row into the list
        while being edited (see that class's docstring). Once the popup
        closes, the now-populated tool has already auto-saved its way
        into the list like any other row; just scroll to it."""
        tool = self._db.create_new_tool()
        dialog = CreateToolDialog(tool, self)
        dialog.exec()
        self._list.scroll_to(tool.tool_number)

    def _on_tool_removed_from_magazine(self, tool_number: int) -> None:
        """A tool was dropped onto the vertical list — the one and only
        gesture that empties a magazine pocket (see tool_magazine_bar.py's
        module docstring)."""
        self._on_pocket_reassigned(tool_number, UNASSIGNED_POCKET)

    def _on_pocket_reassigned(self, tool_number: int, target_pocket: int) -> None:
        """A tool was dropped onto a magazine pocket (from another pocket,
        from the list, or emptied via _on_tool_removed_from_magazine). If
        that pocket is already occupied by a DIFFERENT tool, the occupant
        is kicked back to "unassigned" — see tool_magazine_bar.py's
        module docstring on the pocket convention.
        """
        moved = self._db.get_tool(tool_number)
        if moved is None:
            return
        if target_pocket == UNASSIGNED_POCKET:
            # No occupant search needed: several tools may legitimately
            # share pocket == UNASSIGNED_POCKET at once.
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
        # _reload() runs automatically via ToolDatabaseSignals.tool_changed
        # (both upsert_tool() calls above emit it).
