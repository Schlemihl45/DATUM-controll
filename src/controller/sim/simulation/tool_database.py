"""sim/simulation/tool_database.py — Mock tool table with 6 sample tools (T1–T6)."""
from controller.sim.simulation.tool_definition import ToolDefinition, ToolType

MOCK_TOOL_TABLE: dict[int, ToolDefinition] = {

    1: ToolDefinition(
        tool_number=1, pocket=1,
        diameter=10.0, z_offset=0.0,
        remark="10mm Schaftfräser 4-Schneider",
        tool_type=ToolType.ENDMILL,
        flute_length=22.0,
        cutting_length=22.0,
        shank_diameter=10.0,
        total_length=72.0,
        manufacturer="Sandvik", material="VHM",
        service_life_min=120.0,
    ),

    2: ToolDefinition(
        tool_number=2, pocket=2,
        diameter=6.0, z_offset=0.0,
        remark="6mm Kugelfräser",
        tool_type=ToolType.BALL_ENDMILL,
        flute_length=15.0,
        cutting_length=15.0,
        shank_diameter=6.0,
        total_length=60.0,
        manufacturer="Sandvik", material="VHM",
        service_life_min=90.0,
    ),

    3: ToolDefinition(
        tool_number=3, pocket=3,
        diameter=8.0, z_offset=0.0,
        remark="8mm Torusfräser r=1mm",
        tool_type=ToolType.BULL_ENDMILL,
        flute_length=20.0,
        cutting_length=20.0,
        shank_diameter=8.0,
        total_length=65.0,
        corner_radius=1.0,
        manufacturer="Kennametal", material="VHM",
        service_life_min=150.0,
    ),

    4: ToolDefinition(
        tool_number=4, pocket=4,
        diameter=12.0, z_offset=0.0,
        remark="90° Gravurfräser",
        tool_type=ToolType.CHAMFER,
        flute_length=10.0,
        cutting_length=10.0,
        shank_diameter=8.0,
        total_length=50.0,
        tip_angle=90.0,
        manufacturer="Datron", material="VHM",
        service_life_min=200.0,
    ),

    5: ToolDefinition(
        tool_number=5, pocket=5,
        diameter=8.0, z_offset=0.0,
        remark="8mm Spiralbohrer HSS",
        tool_type=ToolType.DRILL,
        flute_length=75.0,
        cutting_length=75.0,
        shank_diameter=8.0,
        total_length=115.0,
        tip_angle=118.0,
        manufacturer="Gühring", material="HSS-E",
        service_life_min=60.0,
    ),

    6: ToolDefinition(
        tool_number=6, pocket=6,
        diameter=10.0, z_offset=0.0,
        remark="10mm Konusfräser 5°",
        tool_type=ToolType.TAPER,
        flute_length=18.0,
        cutting_length=18.0,
        shank_diameter=10.0,
        total_length=65.0,
        taper_angle=5.0,
        manufacturer="Datron", material="VHM",
        service_life_min=100.0,
    ),
}


def get_tool(tool_number: int) -> ToolDefinition | None:
    return MOCK_TOOL_TABLE.get(tool_number)

def all_tools() -> list[ToolDefinition]:
    return list(MOCK_TOOL_TABLE.values())
