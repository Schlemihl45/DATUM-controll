"""
persistence/paths.py — Shared filesystem location for this app's local
databases (tools.db, workpieces.db, ...).

Kept as a repo-relative folder (data/db/), not an OS app-data directory —
the app is still in early development (see README's "Phase 1"), so a
location a developer can actually find without knowing platform-specific
QStandardPaths conventions wins over "correct for a real installed app".
Revisit this once the app is actually packaged/distributed.
"""
from __future__ import annotations

from pathlib import Path

# persistence/paths.py -> persistence -> controller -> src -> repo root.
# Same pattern machine_page.py's _REPO_ROOT already uses (one less parents[]
# level here since this file sits one directory shallower).
_REPO_ROOT = Path(__file__).resolve().parents[3]


def db_dir() -> Path:
    """<repo_root>/data/db — the shared folder every local sqlite database
    lives in. Callers are responsible for creating it (they already do,
    via Path(db_path).parent.mkdir(parents=True, exist_ok=True))."""
    return _REPO_ROOT / "data" / "db"


# Default workpieces-sync root: <repo_root>/workpieces — the folder that
# already ships the bundled example G-code (workpieces/Gcode.cnc, see
# machine_page.py's old _DEFAULT_GCODE_PATH). Used by
# AppSettings.workpieces_root_path as its default value so folder sync
# (persistence/workpiece_sync.py) works out of the box on a fresh
# checkout, without requiring manual configuration first. A relative,
# repo-anchored path (not an OS-specific absolute one) so it resolves the
# same way on every machine/OS this repo is cloned onto.
DEFAULT_WORKPIECES_ROOT = _REPO_ROOT / "workpieces"
