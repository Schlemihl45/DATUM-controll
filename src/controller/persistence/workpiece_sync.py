"""
persistence/workpiece_sync.py — explicit folder <-> WorkpieceDatabase sync.

No background filesystem watcher exists (or is planned for this pass) —
this module is triggered explicitly: once when WorkpiecesPage is opened,
and again whenever its Sync button is pressed (see ui/pages/workpieces_page.py).

Sync flow (sync_workpieces_root()):
    1. Read AppSettings.workpieces_root_path. Empty -> no-op, same
       convention as linuxcnc_tool_table_path.
    2. Every direct subfolder of the root = one Workpiece (folder_path is
       the sync anchor — see WorkpieceDatabase.get_or_create_by_folder()).
    3. Inside each workpiece folder, every G-code file (GCODE_EXTENSIONS)
       is hashed (SHA-256):
         - unknown gcode_path            -> new Operation, version 1
         - known, hash unchanged         -> nothing to do
         - known, hash changed           -> create_new_version()
         - known operation, file missing -> left alone; Operation.file_missing
           (runtime-only, never persisted) is what the UI checks to show this
    4. Tool numbers are parsed out of each file's raw text (T-addresses,
       comments excluded — see workpiece_db.parse_tools_from_gcode()) into
       tools_auto. tools_manual is never touched by a sync.

Sync errors (unreadable folder, permission denied, ...) are collected into
the returned SyncResult instead of raised — a sync failure must never crash
the Workpieces page, only surface as a visible, non-blocking message.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from controller.domain.models import Operation
from controller.persistence.workpiece_db import (
    WorkpieceDatabase,
    _sha256_file,
    parse_tools_from_gcode,
)

logger = logging.getLogger(__name__)

# .cnc: this app's own existing example file (workpieces/Gcode.cnc, see
# machine_page.py) and .nc are what sim/gcode/compiler.py already loads;
# .ngc is included as the conventional LinuxCNC G-code extension.
GCODE_EXTENSIONS = (".ngc", ".nc", ".cnc")


@dataclass
class SyncResult:
    workpieces_synced: int = 0
    operations_created: int = 0
    operations_versioned: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def sync_workpieces_root(db: WorkpieceDatabase | None = None) -> SyncResult:
    """Run one explicit sync pass over AppSettings.workpieces_root_path.
    See module docstring for the full flow. Safe to call repeatedly
    (idempotent for unchanged files) and never raises."""
    from controller.sim.core.settings import AppSettings

    db = db or WorkpieceDatabase.instance()
    result = SyncResult()

    root = AppSettings.instance().workpieces_root_path
    if not root:
        return result

    root_path = Path(root)
    if not root_path.is_dir():
        result.errors.append(f"Wurzelordner nicht gefunden: {root}")
        return result

    try:
        subfolders = sorted(p for p in root_path.iterdir() if p.is_dir())
    except OSError as exc:
        result.errors.append(f"Wurzelordner konnte nicht gelesen werden: {exc}")
        return result

    for folder in subfolders:
        try:
            _sync_one_workpiece_folder(db, folder, result)
            result.workpieces_synced += 1
        except OSError as exc:
            logger.warning("Sync failed for %s", folder, exc_info=True)
            result.errors.append(f"{folder.name}: {exc}")

    return result


def sync_single_workpiece(workpiece_id: int, db: WorkpieceDatabase | None = None) -> SyncResult:
    """Sync exactly one workpiece's own folder, regardless of whether it
    sits under AppSettings.workpieces_root_path or not.

    Used after a file is copied into a workpiece's folder via
    WorkpieceDetailPage's "Load Programm" card (ui/pages/
    workpiece_detail_page.py) — re-running the FULL sync_workpieces_root()
    for that would not just be wasteful, it would actively miss a
    workpiece created without a root configured (folder_path outside the
    configured root entirely, see ui.pages.workpieces_page's
    _unlinked_workpieces_dir()), since the root-wide sync only ever walks
    that one configured root's direct subfolders."""
    db = db or WorkpieceDatabase.instance()
    result = SyncResult()

    workpiece = db.get_workpiece(workpiece_id)
    if workpiece is None:
        result.errors.append(f"Werkstück {workpiece_id} nicht gefunden.")
        return result

    folder = Path(workpiece.folder_path)
    if not folder.is_dir():
        result.errors.append(f"Werkstück-Ordner nicht gefunden: {folder}")
        return result

    try:
        _sync_one_workpiece_folder(db, folder, result)
        result.workpieces_synced = 1
    except OSError as exc:
        logger.warning("Sync failed for %s", folder, exc_info=True)
        result.errors.append(f"{folder.name}: {exc}")

    return result


def _sync_one_workpiece_folder(db: WorkpieceDatabase, folder: Path, result: SyncResult) -> None:
    workpiece = db.get_or_create_by_folder(str(folder))
    existing_by_path = {
        op.gcode_path: op for op in db.operations_for_workpiece(workpiece.id)
    }

    for file in sorted(folder.iterdir()):
        if not file.is_file() or file.suffix.lower() not in GCODE_EXTENSIONS:
            continue
        try:
            _sync_one_file(db, workpiece.id, file, existing_by_path.get(str(file)), result)
        except OSError as exc:
            logger.warning("Could not sync %s", file, exc_info=True)
            result.errors.append(f"{file.name}: {exc}")

    # Operations whose file is no longer in the folder are intentionally
    # left untouched — never deleted here. Operation.file_missing (computed
    # at runtime from gcode_path) is what surfaces that state to the UI.


def _sync_one_file(
    db: WorkpieceDatabase, workpiece_id: int, file: Path,
    existing: Operation | None, result: SyncResult,
) -> None:
    new_hash = _sha256_file(str(file))

    if existing is None:
        tools_auto = parse_tools_from_gcode(_read_text(file))
        db.create_first_version(workpiece_id, file.stem, str(file), new_hash, tools_auto)
        result.operations_created += 1
        return

    if existing.file_hash == new_hash:
        return  # unchanged — nothing to do

    new_operation = db.create_new_version(existing, str(file), new_hash)
    new_operation.tools_auto = parse_tools_from_gcode(_read_text(file))
    db.upsert_operation(new_operation)
    result.operations_versioned += 1


def _read_text(path: Path) -> str:
    """Same decode-fallback MachinePage._read_gcode_file() uses — G-code
    files in the wild aren't reliably UTF-8."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")
