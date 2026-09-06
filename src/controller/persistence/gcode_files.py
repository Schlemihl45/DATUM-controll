"""
persistence/gcode_files.py — GCODE_EXTENSIONS: the one shared definition
of "what counts as a G-code file", used by the workpiece folder sync
(persistence/workpiece_sync.py) to classify a folder as a workpiece
(Schritt 4's classification rule) and to find files to hash/parse inside
one, and by any UI file picker that should only offer G-code files.

Kept as its own tiny module (not folded into workpiece_sync.py or
sim/gcode/__init__.py) so persistence/ code and sim/ code can both import
it without creating a persistence <-> sim import direction that didn't
exist before.
"""
from __future__ import annotations

# .ngc is the conventional LinuxCNC G-code extension; .nc and .cnc are
# what sim/gcode/compiler.py already loads (see its own module docstring)
# and this app's own bundled example file (workpieces/Gcode.cnc) uses —
# dropping .cnc here would stop that file from being recognized as a
# workpiece file at all, so it stays even though it's not in every
# G-code-extension convention list.
GCODE_EXTENSIONS: tuple[str, ...] = (".ngc", ".nc", ".cnc")


def is_gcode_file(path) -> bool:
    """True if *path* (a str or Path) has one of GCODE_EXTENSIONS,
    case-insensitively. Accepts anything with a `.suffix`-like attribute
    or a plain string path."""
    from pathlib import Path

    return Path(path).suffix.lower() in GCODE_EXTENSIONS
