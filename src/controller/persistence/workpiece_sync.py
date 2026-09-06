"""
persistence/workpiece_sync.py — explicit, recursive folder <-> WorkpieceDatabase
sync, with a group-vs-workpiece classification rule applied at every
folder level.

No background filesystem watcher exists (or is planned) — this module is
triggered explicitly: once when a WorkpieceBrowserPage level is opened,
and again via its Sync button (see ui/pages/workpiece_browser_page.py).

Classification rule (applied recursively, starting at each folder passed
to sync_folder_tree(), never at the configured root itself — see
_sync_recursive()'s docstring for why the root is always treated as a
group):

    1. The folder directly (non-recursively) contains at least one
       GCODE_EXTENSIONS file, OR a Workpiece DB row already exists for
       its relative path -> it's a WORKPIECE.
         - Existing DB row for this folder_path -> reused.
         - No DB row yet -> created automatically (name = folder name).
         - A workpiece folder is a LEAF: its own subfolders (if any) are
           never recursed into for classification. If it has any anyway,
           that's the Schritt-4 ambiguous case — left alone entirely
           (not touched, not deleted), surfaced as a non-blocking warning
           on WorkpieceDetailPage via has_unexpected_subfolders().
         - Once classified a workpiece (DB row exists), it STAYS one even
           if every G-code file is later removed from it — no automatic
           demotion back to a group. Only explicit deletion via the UI
           removes the workpiece status.
    2. No G-code file directly inside AND no DB row yet -> the folder is
       a GROUP (pure organizational folder, no DB row of its own — see
       domain.models' module-level note on why groups aren't persisted).
       Recurse into its subfolders and apply this same rule to each.
    3. Symlinks and hidden folders (leading ".") are skipped during the
       recursive scan (avoids symlink cycles and noise).

Known, accepted limitation (documented, not solved): renaming/moving a
workpiece folder OUTSIDE the app (in the OS file manager) makes the next
sync see it as a brand-new workpiece folder at the new path; the old DB
row is left behind pointing at a folder_path that no longer exists
(surfaced at runtime as `folder_missing`, analogous to Operation's own
file_missing — computed on demand, never persisted). Folder renames/moves
are meant to happen through the app's own UI (see
ui/pages/workpiece_browser_page.py) — sufficient for the personal-use
scope this app targets.

Per-workpiece file sync (once a folder is classified/reused as a
workpiece): every GCODE_EXTENSIONS file inside is SHA-256-hashed —
    - unknown gcode_path             -> new Operation, version 1
    - known, hash unchanged          -> nothing to do
    - known, hash changed            -> create_new_version()
    - known Operation, file missing  -> left alone; Operation.file_missing
      (runtime-only) is what the UI checks to show this
    - tools_auto is re-parsed from the file's T-addresses on every create/
      version (see workpiece_db.parse_tools_from_gcode()); tools_manual is
      never touched by a sync.

Sync errors (unreadable folder, permission denied, ...) are collected
into the returned SyncResult instead of raised — a sync failure must
never crash the page, only surface as a visible, non-blocking message.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from controller.domain.models import Operation, Workpiece
from controller.persistence.gcode_files import GCODE_EXTENSIONS, is_gcode_file
from controller.persistence.workpiece_db import (
    WorkpieceDatabase,
    _sha256_file,
    parse_tools_from_gcode,
)

logger = logging.getLogger(__name__)

# Re-exported for existing callers that import GCODE_EXTENSIONS from here
# rather than from persistence.gcode_files directly.
__all__ = [
    "GCODE_EXTENSIONS", "SyncResult", "FolderContents",
    "sync_folder_tree", "sync_single_workpiece", "list_folder_contents",
    "absolute_folder_for", "has_unexpected_subfolders",
    "create_group_folder", "create_workpiece_folder",
    "sanitize_folder_name", "unique_child_relative_path",
]

_INVALID_FOLDER_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class SyncResult:
    workpieces_synced: int = 0
    operations_created: int = 0
    operations_versioned: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class FolderContents:
    """One folder level's direct children, already classified — see
    list_folder_contents()."""
    groups: list[str] = field(default_factory=list)          # relative paths
    workpieces: list[Workpiece] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ── Root resolution ──────────────────────────────────────────────────────────

def _root_path() -> Path | None:
    from controller.sim.core.settings import AppSettings

    root = AppSettings.instance().workpieces_root_path
    return Path(root) if root else None


def absolute_folder_for(workpiece: Workpiece) -> Path | None:
    """Resolve a Workpiece's folder_path to a real filesystem path.

    folder_path is normally relative to AppSettings.workpieces_root_path
    (see workpiece_db.py's module docstring), but get_or_create_by_path()
    stores an ABSOLUTE path as a fallback for a workpiece outside the
    configured root — Path.is_absolute() tells the two apart, so an
    already-absolute value is returned as-is instead of being (wrongly)
    joined onto the root.

    Returns None only when folder_path is relative AND no root is
    configured, i.e. there is nothing to resolve it against.
    """
    p = Path(workpiece.folder_path)
    if p.is_absolute():
        return p
    root = _root_path()
    return (root / workpiece.folder_path) if root else None


def has_unexpected_subfolders(workpiece: Workpiece) -> bool:
    """True if a workpiece's own folder ALSO contains subfolders — the
    Schritt-4 ambiguous case (see module docstring, rule 1). Never
    auto-resolved; WorkpieceDetailPage surfaces this as a non-blocking
    warning banner."""
    folder = absolute_folder_for(workpiece)
    if folder is None or not folder.is_dir():
        return False
    try:
        return any(_is_real_dir(e) for e in os.scandir(folder))
    except OSError:
        return False


# ── Recursive sync ───────────────────────────────────────────────────────────

def sync_folder_tree(relative_path: str = "", db: WorkpieceDatabase | None = None) -> SyncResult:
    """Recursively sync the folder tree starting at *relative_path*
    ("" = the configured root itself) downward, applying the group-vs-
    workpiece classification rule at every level under it. See module
    docstring. Safe to call repeatedly (idempotent for unchanged files)
    and never raises."""
    db = db or WorkpieceDatabase.instance()
    result = SyncResult()

    root = _root_path()
    if root is None:
        return result
    if not root.is_dir():
        result.errors.append(f"Wurzelordner nicht gefunden: {root}")
        return result

    start = (root / relative_path) if relative_path else root
    if not start.is_dir():
        result.errors.append(f"Ordner nicht gefunden: {start}")
        return result

    _sync_recursive(db, start, relative_path, result)
    return result


def sync_single_workpiece(workpiece_id: int, db: WorkpieceDatabase | None = None) -> SyncResult:
    """Sync exactly one workpiece's own folder (not its DB-less GROUP
    ancestors) — used after a file is copied into it via
    WorkpieceDetailPage's "Load Programm" card. Works regardless of
    whether the workpiece's folder sits under the configured root or was
    created via the get_or_create_by_path() absolute-path fallback (see
    absolute_folder_for())."""
    db = db or WorkpieceDatabase.instance()
    result = SyncResult()

    workpiece = db.get_workpiece(workpiece_id)
    if workpiece is None:
        result.errors.append(f"Werkstück {workpiece_id} nicht gefunden.")
        return result

    folder = absolute_folder_for(workpiece)
    if folder is None or not folder.is_dir():
        result.errors.append(f"Werkstück-Ordner nicht gefunden: {folder}")
        return result

    try:
        _sync_workpiece_files(db, workpiece, folder, result)
        result.workpieces_synced = 1
    except OSError as exc:
        logger.warning("Sync failed for %s", folder, exc_info=True)
        result.errors.append(f"{folder.name}: {exc}")

    return result


def _sync_recursive(db: WorkpieceDatabase, folder: Path, rel: str, result: SyncResult) -> None:
    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        result.errors.append(f"{rel or '.'}: {exc}")
        return

    # The configured ROOT itself is always a group/container, never a
    # workpiece, regardless of what's directly inside it — a loose
    # G-code file sitting right in the root (no enclosing folder) has no
    # "workpiece folder" to become, so it's simply left unmanaged.
    existing = db.get_workpiece_by_folder(rel) if rel else None
    has_gcode_direct = rel and any(
        e.is_file(follow_symlinks=False) and is_gcode_file(e.name) for e in entries
    )

    if rel and (existing is not None or has_gcode_direct):
        workpiece = existing or db.get_or_create_by_folder(rel, default_name=folder.name)
        try:
            _sync_workpiece_files(db, workpiece, folder, result)
            result.workpieces_synced += 1
        except OSError as exc:
            logger.warning("Could not sync workpiece folder %s", folder, exc_info=True)
            result.errors.append(f"{folder.name}: {exc}")
        return  # workpieces are leaves — never recursed into

    for entry in entries:
        if not _is_real_dir(entry):
            continue
        child_rel = f"{rel}/{entry.name}" if rel else entry.name
        _sync_recursive(db, Path(entry.path), child_rel, result)


def _sync_workpiece_files(
    db: WorkpieceDatabase, workpiece: Workpiece, folder: Path, result: SyncResult,
) -> None:
    existing_by_path = {
        op.gcode_path: op for op in db.operations_for_workpiece(workpiece.id)
    }
    for file in sorted(folder.iterdir()):
        if not file.is_file() or not is_gcode_file(file):
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


def _is_real_dir(entry: "os.DirEntry") -> bool:
    """Skip symlinks (avoid infinite loops from a cycle) and hidden
    folders (leading dot) — see module docstring, rule 3."""
    return (
        not entry.is_symlink()
        and entry.is_dir(follow_symlinks=False)
        and not entry.name.startswith(".")
    )


def _read_text(path: Path) -> str:
    """Same decode-fallback MachinePage._read_gcode_file() uses — G-code
    files in the wild aren't reliably UTF-8."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")


# ── Browser page support: listing + folder creation ─────────────────────────

def list_folder_contents(relative_path: str, db: WorkpieceDatabase | None = None) -> FolderContents:
    """Direct children of *relative_path*, already classified into
    groups/workpieces — call sync_folder_tree(relative_path) first so
    this reflects the current filesystem state (this function itself
    only reads the DB + does one non-recursive directory listing, it
    does not sync). Groups sorted alphabetically by name; workpieces
    sorted alphabetically by their own `.name`."""
    db = db or WorkpieceDatabase.instance()
    contents = FolderContents()

    root = _root_path()
    if root is None:
        return contents

    folder = (root / relative_path) if relative_path else root
    if not folder.is_dir():
        contents.errors.append(f"Ordner nicht gefunden: {folder}")
        return contents

    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        contents.errors.append(f"{folder}: {exc}")
        return contents

    for entry in entries:
        if not _is_real_dir(entry):
            continue
        child_rel = f"{relative_path}/{entry.name}" if relative_path else entry.name
        workpiece = db.get_workpiece_by_folder(child_rel)
        if workpiece is not None:
            contents.workpieces.append(workpiece)
        else:
            contents.groups.append(child_rel)

    contents.groups.sort(key=lambda rel: rel.rsplit("/", 1)[-1].lower())
    contents.workpieces.sort(key=lambda w: w.name.lower())
    return contents


def sanitize_folder_name(name: str) -> str:
    """A typed folder/workpiece name, made safe as a real filesystem
    folder name (invalid characters on Windows/Linux/macOS replaced)."""
    cleaned = _INVALID_FOLDER_CHARS_RE.sub("_", name).strip().strip(".")
    return cleaned or "Ordner"


def unique_child_relative_path(parent_relative: str, name: str) -> str:
    """*parent_relative* + a sanitized, collision-free slug of *name* —
    the first candidate whose folder doesn't already exist on disk under
    the configured root, so two folders created with the same typed name
    never collide."""
    root = _root_path()
    slug = sanitize_folder_name(name)
    candidate = f"{parent_relative}/{slug}" if parent_relative else slug
    if root is None:
        return candidate
    counter = 2
    while (root / candidate).exists():
        numbered = f"{slug}_{counter}"
        candidate = f"{parent_relative}/{numbered}" if parent_relative else numbered
        counter += 1
    return candidate


def create_group_folder(relative_path: str) -> Path:
    """Create an empty subfolder (a GROUP — no DB entry, see module
    docstring rule 2) at *relative_path* under the configured root.
    Raises ValueError if no root is configured, OSError on a filesystem
    failure (caller surfaces either as a non-blocking message)."""
    root = _root_path()
    if root is None:
        raise ValueError("Kein Werkstück-Wurzelordner konfiguriert.")
    folder = root / relative_path
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def create_workpiece_folder(
    relative_path: str, name: str | None = None, db: WorkpieceDatabase | None = None,
) -> Workpiece:
    """Create a folder at *relative_path* AND immediately register it as
    a Workpiece — used by "New Workpiece" (unlike create_group_folder(),
    which leaves the folder unregistered/a GROUP)."""
    db = db or WorkpieceDatabase.instance()
    folder = create_group_folder(relative_path)
    return db.get_or_create_by_folder(relative_path, default_name=name or folder.name)
