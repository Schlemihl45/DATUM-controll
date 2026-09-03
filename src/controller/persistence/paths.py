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
