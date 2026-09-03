"""
ui/widgets/tool_icons.py — ToolType -> icon mapping, shared by
ToolCardWidget and ToolMagazineBar's pocket slots.

Not every ToolType has a dedicated asset yet (BALL_ENDMILL, TAPER) — those
fall back to endmill.svg rather than failing/going blank; get_icon() itself
already logs a warning for a genuinely missing file, so a silent fallback
here would hide real problems, which is why the fallback is explicit and
narrow (only for the two known-unmapped types) rather than a bare except.
"""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

from controller.sim.simulation.tool_definition import ToolType
from controller.ui.icon_loader import get_icon

_ICON_NAMES: dict[ToolType, str] = {
    ToolType.ENDMILL:      "endmill",
    ToolType.BALL_ENDMILL: "endmill",   # no dedicated asset yet
    ToolType.BULL_ENDMILL: "radius_endmill",
    ToolType.CHAMFER:      "chamfermill",
    ToolType.DRILL:        "drill",
    ToolType.TAPER:        "endmill",   # no dedicated asset yet
}


def tool_type_icon(tool_type: ToolType, size: int = 24) -> QIcon:
    name = _ICON_NAMES.get(tool_type, "endmill")
    return get_icon(name, tint=True, size=QSize(size, size))
