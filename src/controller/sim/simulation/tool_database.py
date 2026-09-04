"""
sim/simulation/tool_database.py — thin get_tool()/get_tool_by_pocket()/
all_tools() wrapper.

Historically a hardcoded 6-tool Python dict ("MOCK_TOOL_TABLE"); the real
data now lives in a proper sqlite3-backed store,
controller.persistence.tool_db.ToolDatabase (which seeds itself with those
same 6 tools on first run — see its _SEED_TOOLS). This module is kept only
so the existing call sites (gcode/compiler.py, sim/ui/main_widget.py,
sim/ui/viewport.py, sim/voxel/prepass.py) don't need to import
ToolDatabase directly. get_tool(tool_number) resolves a tool's persistent
identity; get_tool_by_pocket(pocket) resolves whatever's CURRENTLY
sitting in a magazine slot — the one G-code T-addresses actually use (see
get_tool_by_pocket()'s docstring below) — these are NOT interchangeable.
"""
from __future__ import annotations

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.persistence.tool_db import ToolDatabase


def get_tool(tool_number: int) -> ToolDefinition | None:
    return ToolDatabase.instance().get_tool(tool_number)


def get_tool_by_pocket(pocket: int) -> ToolDefinition | None:
    """Resolve a magazine pocket number to the tool currently sitting
    there — this is what a G-code T-address actually selects during
    simulation/machine execution; see ToolDatabase.get_tool_by_pocket()'s
    docstring and gcode/compiler.py's ToolChange.pocket_number."""
    return ToolDatabase.instance().get_tool_by_pocket(pocket)


def all_tools() -> list[ToolDefinition]:
    return ToolDatabase.instance().all_tools()
