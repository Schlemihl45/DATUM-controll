"""
sim/gcode/lexer.py — G-code tokenizer.

Strips comments, block-delete lines (/), and program markers (%).
Returns a list of Token(letter, value) per line for the parser.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

# Matches a single letter code followed by an optional sign and numeric value.
# Handles scientific notation (e.g. G0 X1.5e-2) and floats.
_TOKEN_RE = re.compile(
    r"([A-Za-z])\s*(-?\d*\.?\d+(?:e[+-]?\d+)?)",
    re.IGNORECASE
)

@dataclass(frozen=True, slots=True)
class Token:
    """One parsed word: letter + numeric value (e.g. G1, X10.5, F500)."""
    letter: str
    value: float

    def __repr__(self):
        return f"({self.letter}, {self.value})"


def tokenize(line: str) -> list[Token]:
    """Tokenize a single G-code line into a list of Tokens.

    Skips:
    - Empty lines
    - Lines starting with % (program begin/end marker)
    - Lines starting with / (optional block-delete)
    - Inline comments in parentheses (...)
    - End-of-line comments after ;
    """
    line = line.strip()

    if not line:
        return []
    if line.startswith("%"):
        return []
    if line.startswith("/"):
        return []

    # Strip parenthetical inline comments: (this is a comment)
    line = re.sub(r"\(.*?\)", "", line)

    # Strip semicolon end-of-line comments
    line = re.sub(r"\;.*", "", line)

    tokens = []
    for letter, number in _TOKEN_RE.findall(line):
        tokens.append(Token(letter, float(number)))

    return tokens
