"""
sim/gcode/parser.py — Converts raw token lists into structured GCodeCommand objects.

Each command carries the G/M codes it activates plus all axis/parameter values
for that block. The parser is intentionally line-stateless — modal persistence
is the motion planner's job.
"""
from controller.sim.gcode.lexer import Token
from dataclasses import dataclass, field


@dataclass
class GCodeCommand:
    """One parsed G-code block (one non-empty line)."""
    line_index: int
    g_codes:    list[int]           = field(default_factory=list)
    m_codes:    list[int]           = field(default_factory=list)
    parameters: dict[str, float]    = field(default_factory=dict)


def parse(tokens_per_line: list[list[Token]]) -> list[GCodeCommand]:
    """Convert tokenized lines into a list of GCodeCommands.

    Args:
        tokens_per_line: One list of Tokens per source line (from tokenize()).
                         Empty lists (blank/comment lines) are skipped.

    Returns:
        List of GCodeCommand, preserving original line indices.
    """
    commands = []

    for index, raw in enumerate(tokens_per_line):
        if not raw:
            continue

        cmd = GCodeCommand(line_index=index)

        for t in raw:
            if t.letter == 'G':
                cmd.g_codes.append(int(t.value))
            elif t.letter == 'M':
                cmd.m_codes.append(int(t.value))
            else:
                # Axis letters (X,Y,Z), feeds (F), spindle (S), tool (T), offsets (I,J,K), etc.
                cmd.parameters[t.letter] = t.value

        commands.append(cmd)

    return commands
