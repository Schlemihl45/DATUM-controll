"""
sim/simulation/tool_database.py — thin get_tool()/all_tools() wrapper.

Historically a hardcoded 6-tool Python dict ("MOCK_TOOL_TABLE"); the real
data now lives in a proper sqlite3-backed store,
controller.persistence.tool_db.ToolDatabase (which seeds itself with those
same 6 tools on first run — see its _SEED_TOOLS). This module is kept only
so the 3 existing call sites (gcode/compiler.py, sim/ui/main_widget.py,
sim/ui/viewport.py) don't need to change: get_tool(int) -> ToolDefinition |
None and all_tools() -> list[ToolDefinition] keep the exact same signatures.
"""
from __future__ import annotations

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.persistence.tool_db import ToolDatabase


def get_tool(tool_number: int) -> ToolDefinition | None:
    return ToolDatabase.instance().get_tool(tool_number)


def all_tools() -> list[ToolDefinition]:
    return ToolDatabase.instance().all_tools()
