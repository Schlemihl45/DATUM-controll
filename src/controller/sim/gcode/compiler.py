"""sim/gcode/compiler.py — GCodeCompiler: load a .nc/.cnc file → GCodeProgram."""

from pathlib import Path
from dataclasses import dataclass
from controller.sim.gcode.lexer          import tokenize
from controller.sim.gcode.parser import parse, GCodeCommand
from controller.sim.gcode.motion_planner import plan, MotionSegment
from controller.sim.gcode.path_buffer    import PathBuffer
from controller.sim.simulation.tool_database import get_tool

@dataclass
class ToolChange:
    line_index: int
    tool_number: int

@dataclass
class ToolValidationResult:
    missing: list[int]
    found: list[int]
    ok: bool = False

    def __str__(self) -> str:
        if self.ok:
            return "OK"
        lines = [f"Warning: "]
        for t in self.missing:
            lines.append(f"T{t} - not found")
        return "\n".join(lines)

def validate_tools(tool_changes: list[ToolChange], get_tool) -> ToolValidationResult:
    missing, found, = [], []
    seen = set()

    for tc in tool_changes:
        if tc.tool_number in seen:
            continue
        seen.add(tc.tool_number)

        if get_tool(tc.tool_number) is None:
            missing.append(tc.tool_number)
        else:
            found.append(tc.tool_number)

    return ToolValidationResult(missing=missing, found=found, ok=not missing)


@dataclass
class GCodeProgram:
    raw_lines: list[str]
    clean_lines: list[str]
    segments:  list[MotionSegment]
    path:      PathBuffer
    tool_changes: list[ToolChange]

def _extract_tool_changes(commands: list[GCodeCommand]) -> list[ToolChange]:
    changes = []
    pending_tool = None

    for cmd in commands:
        if "T" in cmd.parameters:
            pending_tool = int(cmd.parameters["T"])
        if 6 in cmd.m_codes and pending_tool is not None:
            changes.append(ToolChange(
                line_index=cmd.line_index,
                tool_number=pending_tool,
            ))
    return changes

class GCodeCompiler:
    def __init__(self):
        pass

    # Entry Point
    def load_file(self, path: str) -> GCodeProgram:
        raw_lines = Path(path).read_text().splitlines()

        # Tokenize ALL lines — no filtering here
        tokens_per_line = [tokenize(line) for line in raw_lines]

        # clean_lines has exactly the same index as raw_lines
        clean_lines = []
        for tokens in tokens_per_line:
            if tokens:
                clean_lines.append(" ".join(f"{t.letter}{t.value:g}" for t in tokens))
            else:
                clean_lines.append("")

        commands = parse(tokens_per_line)  # parser skips empty lines internally
        segments = plan(commands)
        buf = PathBuffer(segments)
        tool_changes = _extract_tool_changes(commands)
        tool_validation = validate_tools(tool_changes, get_tool)

        if not tool_validation.ok:
            print(f"[Compiler] ⚠  {path}")
            print(tool_validation)
        else:
            if tool_validation.found:
                print(f"[Compiler] ✓  Tools OK: {tool_validation.found}")

        return GCodeProgram(
            raw_lines=raw_lines,
            clean_lines=clean_lines,
            segments=segments,
            path=buf,
            tool_changes=tool_changes,
        )
