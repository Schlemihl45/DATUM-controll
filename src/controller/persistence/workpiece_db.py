"""
persistence/workpiece_db.py — WorkpieceDatabase: sqlite3-backed workpiece +
operation tables.

Structure mirrors persistence/tool_db.py deliberately (singleton via
instance(), sqlite3 + row_factory, INSERT ... ON CONFLICT DO UPDATE, a
row<->dataclass mapping pair per table) — see that module for the
established pattern this one follows.

Two tables:
    workpieces  — one row per physical part. folder_path is a path
                  RELATIVE to AppSettings.workpieces_root_path, using
                  forward slashes regardless of OS (e.g.
                  "ProjektA/FlanschTeil") — see persistence/workpiece_sync.py
                  for the folder <-> DB sync this anchors, and
                  ui/pages/workpiece_browser_page.py for why relative:
                  portable if the root folder is ever moved, and the
                  breadcrumb there is just folder_path.split("/").
    operations  — one row per G-code file *version* (colloquially a
                  "Programm" — see domain.models.Operation's docstring on
                  why the class keeps that name). A file that gets
                  re-posted from CAM produces a NEW row (create_new_version()),
                  never an overwrite of the old one — lineage_id/version/
                  previous_version_id/is_current thread the history
                  together. Table is named `operations`, not `programs`,
                  to stay consistent with the domain class name and avoid
                  colliding with ProgramState/ProgramInfoCard, which
                  describe the program currently loaded/running on the
                  MACHINE — an unrelated concept.

This replaces two earlier versions of this module, both intentionally
discarded (not migrated) rather than converted, per explicit confirmation
each time — see _SCHEMA_VERSION below for how that discard is detected
and applied:
    v0 -> v1: bound one Workpiece 1:1 to a single gcode_path, no
              operations table, no versioning at all.
    v1 -> v2: folder_path changed from an ABSOLUTE path (one flat level
              under the root) to a RELATIVE path supporting an arbitrary-
              depth folder hierarchy (groups vs. workpieces — see
              persistence/workpiece_sync.py's module docstring).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from controller.domain.models import Operation, Workpiece

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workpieces (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    name                        TEXT    NOT NULL,
    material                    TEXT    NOT NULL DEFAULT '',
    description                 TEXT    NOT NULL DEFAULT '',
    drawing_number              TEXT    NOT NULL DEFAULT '',
    notes                       TEXT    NOT NULL DEFAULT '',
    folder_path                 TEXT    NOT NULL UNIQUE,
    created_at                  TEXT    NOT NULL,
    modified_at                 TEXT    NOT NULL,
    collision_detection_enabled INTEGER
);

CREATE TABLE IF NOT EXISTS operations (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    workpiece_id          INTEGER NOT NULL REFERENCES workpieces(id) ON DELETE CASCADE,
    lineage_id            INTEGER NOT NULL,
    version               INTEGER NOT NULL DEFAULT 1,
    previous_version_id   INTEGER REFERENCES operations(id),
    is_current            INTEGER NOT NULL DEFAULT 1,
    name                  TEXT    NOT NULL,
    gcode_path            TEXT    NOT NULL,
    file_hash             TEXT    NOT NULL DEFAULT '',
    clamping_description  TEXT    NOT NULL DEFAULT '',
    zero_point_notes      TEXT    NOT NULL DEFAULT '',
    notes                 TEXT    NOT NULL DEFAULT '',
    estimated_time        REAL    NOT NULL DEFAULT 0.0,
    tools_auto            TEXT    NOT NULL DEFAULT '[]',
    tools_manual          TEXT    NOT NULL DEFAULT '[]',
    preview_source        TEXT    NOT NULL DEFAULT '',
    created_at            TEXT    NOT NULL,
    modified_at           TEXT    NOT NULL
);
"""

# T-address matcher for auto tool extraction (see workpiece_sync.py's
# module docstring for the full sync flow this feeds into). The negative
# lookbehind guards against matching "T12" inside a longer token (e.g. a
# word ending in a letter right before the T); callers are responsible
# for stripping comments first (see strip_gcode_comments()) so a
# T-address mentioned only in a comment is never counted.
#
# Deliberately NO trailing \b after the digits: real G-code overwhelmingly
# writes a tool change as "T2M6"/"T02M06" with no separator between the
# T-address and the M6 that executes it, and a trailing \b would refuse
# to match there at all (no word-boundary between a digit and the "M"
# right after it) — an earlier version of this regex had exactly that \b
# and silently produced zero matches on that whole class of files.
# \d+ already delimits the number on its own (it stops at the first
# non-digit), so no boundary assertion is needed after it.
_T_ADDRESS_RE = re.compile(r"(?<![A-Za-z0-9])T(\d+)")

# Bumped whenever a change to this module's tables would silently produce
# wrong results if old data were kept as-is (see module docstring's
# "v0 -> v1"/"v1 -> v2" history) — checked against the DB file's own
# PRAGMA user_version in _migrate_schema(). A plain CREATE TABLE IF NOT
# EXISTS is a no-op against an already-existing table, so without this,
# an old DB would keep its old column set (v0->v1) or its old absolute-
# path folder_path values (v1->v2) forever.
_SCHEMA_VERSION = 2


class WorkpieceDatabaseSignals(QObject):
    """Qt signal bridge for WorkpieceDatabase writes — kept separate from
    WorkpieceDatabase itself for the same reason ToolDatabaseSignals is
    kept separate from ToolDatabase (see that module's docstring): the
    persistence class stays plain/Qt-free, UI code reacts via this
    singleton instead."""

    workpiece_changed = Signal(int)   # workpiece id — emitted by upsert/delete
    operation_changed = Signal(int)   # operation id — emitted by upsert/delete

    _instance: "WorkpieceDatabaseSignals | None" = None

    @classmethod
    def instance(cls) -> "WorkpieceDatabaseSignals":
        if cls._instance is None:
            cls._instance = WorkpieceDatabaseSignals()
        return cls._instance


class WorkpieceDatabase:
    """Singleton sqlite3-backed workpiece+operation tables. See module docstring."""

    _instance: "WorkpieceDatabase | None" = None

    @classmethod
    def instance(cls) -> "WorkpieceDatabase":
        if cls._instance is None:
            cls._instance = WorkpieceDatabase()
        return cls._instance

    def __init__(self, db_path: str | None = None) -> None:
        """db_path overrides the configured/default path — mainly for
        tests, which pass a tempfile path to avoid touching the real DB."""
        if db_path is None:
            db_path = self._resolve_default_path()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Needed for "ON DELETE CASCADE" (operations -> workpieces) to
        # actually take effect — sqlite3 defaults foreign_keys to OFF per
        # connection.
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Discard (never convert) data from a schema version older than
        _SCHEMA_VERSION — see that constant and the module docstring for
        why each bump was a "confirmed discard", not a gap to fill in
        later with a real column-by-column migration (unlike
        tool_db.py's _migrate_schema(), which does convert old data).

        PRAGMA user_version is SQLite's own built-in integer version slot
        for exactly this purpose — a plain CREATE TABLE IF NOT EXISTS
        can't detect "the columns match, but their CONTENT'S meaning
        changed" (v1 -> v2's folder_path: absolute -> relative), so
        column introspection alone isn't enough here.
        """
        current_version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version < _SCHEMA_VERSION:
            if current_version > 0:
                logger.warning(
                    "%s is at schema version %d, expected %d - dropping and "
                    "recreating; see module docstring, this data was never "
                    "migrated on purpose.",
                    self._db_path, current_version, _SCHEMA_VERSION,
                )
            self._conn.execute("DROP TABLE IF EXISTS operations")
            self._conn.execute("DROP TABLE IF EXISTS workpieces")
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()

    @staticmethod
    def _resolve_default_path() -> str:
        # Local import — see tool_db.py's _resolve_default_path() for why
        # (AppSettings pulls in sim/core at import time; keeping this lazy
        # lets tests/callers that pass an explicit db_path skip that).
        from controller.sim.core.settings import AppSettings

        configured = AppSettings.instance().workpiece_db_path
        if configured:
            return configured

        from controller.persistence.paths import db_dir

        return str(db_dir() / "workpieces.db")

    # ── Workpieces: queries ─────────────────────────────────────────────────

    def get_workpiece(self, workpiece_id: int) -> Workpiece | None:
        """Return the Workpiece, hydrated with its CURRENT operations
        (operations_for_workpiece(..., include_history=False)) — the
        history itself is fetched separately (operation_history()) since
        it's only shown on ProgramDetailPage, not the workpiece overview."""
        row = self._conn.execute(
            "SELECT * FROM workpieces WHERE id = ?", (workpiece_id,)
        ).fetchone()
        if row is None:
            return None
        workpiece = _row_to_workpiece(row)
        workpiece.operations = self.operations_for_workpiece(workpiece_id)
        return workpiece

    def get_workpiece_by_folder(self, folder_path: str) -> Workpiece | None:
        row = self._conn.execute(
            "SELECT * FROM workpieces WHERE folder_path = ?", (folder_path,)
        ).fetchone()
        return _row_to_workpiece(row) if row is not None else None

    def workpieces_under(self, folder_prefix: str) -> list[Workpiece]:
        """All workpieces whose folder_path is *folder_prefix* itself or
        nested under it (folder_prefix + "/" + anything) — used to check
        whether a GROUP folder (see workpiece_sync.py's classification
        rule) is empty of workpieces before allowing it to be deleted
        (ui/pages/workpiece_browser_page.py)."""
        prefix = folder_prefix.rstrip("/")
        if not prefix:
            return self.all_workpieces()
        rows = self._conn.execute(
            "SELECT * FROM workpieces WHERE folder_path = :prefix "
            "OR folder_path LIKE :nested ESCAPE '\\' ORDER BY folder_path",
            {
                "prefix": prefix,
                "nested": _escape_like(prefix) + "/%",
            },
        ).fetchall()
        return [_row_to_workpiece(r) for r in rows]

    def all_workpieces(self) -> list[Workpiece]:
        """Lightweight list for the workpiece browser — operations are
        NOT populated here (would mean one extra query per row); fetch a
        single Workpiece via get_workpiece() when its operations are
        needed."""
        rows = self._conn.execute(
            "SELECT * FROM workpieces ORDER BY id"
        ).fetchall()
        return [_row_to_workpiece(r) for r in rows]

    def get_or_create_by_folder(
        self, folder_path: str, default_name: str | None = None,
    ) -> Workpiece:
        """Return the Workpiece already anchored to *folder_path* (a path
        RELATIVE to AppSettings.workpieces_root_path — see module
        docstring), or create+persist a fresh one (name defaults to the
        folder's own last path segment) if none exists yet. Entry point
        for the folder sync (see persistence.workpiece_sync)."""
        existing = self.get_workpiece_by_folder(folder_path)
        if existing is not None:
            return existing
        name = default_name or folder_path.rsplit("/", 1)[-1]
        return self.upsert_workpiece(Workpiece(name=name, folder_path=folder_path))

    def get_or_create_by_path(
        self, gcode_path: str, default_name: str | None = None,
    ) -> Workpiece:
        """Resolve the Workpiece a given G-code FILE belongs to, creating
        one (with a fresh first-version Operation) if the file is
        completely unknown yet.

        This is DatumSimWidget.set_file()'s entry point — kept for that
        caller, which loads an arbitrary file and just needs "some
        Workpiece row" to hang its per-workpiece collision-detection
        override off of. It is NOT the Workpieces UI's own path — that
        flow always goes through the folder sync (get_or_create_by_folder
        + create_new_version).

        Resolution order: (1) an existing Operation row already points at
        this exact gcode_path -> return its Workpiece. (2) otherwise, the
        file's parent folder is used as a folder-sync anchor. If that
        folder lies under AppSettings.workpieces_root_path, folder_path
        is stored properly relative to it (POSIX slashes) so this
        workpiece shows up in the folder browser like any other; if the
        file lives entirely outside the configured root (e.g. no root
        configured, or a path picked from anywhere on disk), folder_path
        falls back to the absolute path as a last resort — such a
        workpiece simply won't appear in the browser (it has nothing to
        be "relative to"), which is fine: this fallback exists only for
        DatumSimWidget's per-workpiece override lookup, not for the
        browser's own bookkeeping.
        """
        row = self._conn.execute(
            "SELECT workpiece_id FROM operations WHERE gcode_path = ? LIMIT 1",
            (gcode_path,),
        ).fetchone()
        if row is not None:
            workpiece = self.get_workpiece(row["workpiece_id"])
            if workpiece is not None:
                return workpiece

        folder_path = _relative_or_absolute_folder(Path(gcode_path).resolve().parent)
        workpiece = self.get_or_create_by_folder(folder_path, default_name)
        self.create_first_version(
            workpiece.id, Path(gcode_path).stem, gcode_path, _sha256_file(gcode_path),
        )
        return self.get_workpiece(workpiece.id)

    # ── Workpieces: writes ───────────────────────────────────────────────────

    def upsert_workpiece(self, workpiece: Workpiece) -> Workpiece:
        """Insert or update *workpiece* (operations are NOT written here —
        see upsert_operation()/create_new_version()). Returns the persisted
        row as a Workpiece (with `.id` populated for a fresh insert)."""
        now = datetime.now()
        params = {
            "id": workpiece.id,
            "name": workpiece.name,
            "material": workpiece.material,
            "description": workpiece.description,
            "drawing_number": workpiece.drawing_number,
            "notes": workpiece.notes,
            "folder_path": workpiece.folder_path,
            "created_at": workpiece.created_at.isoformat(),
            "modified_at": now.isoformat(),
            "collision_detection_enabled": (
                None if workpiece.collision_detection_enabled is None
                else int(workpiece.collision_detection_enabled)
            ),
        }
        if workpiece.id is not None:
            self._conn.execute(
                """
                INSERT INTO workpieces (
                    id, name, material, description, drawing_number, notes,
                    folder_path, created_at, modified_at, collision_detection_enabled
                ) VALUES (
                    :id, :name, :material, :description, :drawing_number, :notes,
                    :folder_path, :created_at, :modified_at, :collision_detection_enabled
                )
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, material=excluded.material,
                    description=excluded.description,
                    drawing_number=excluded.drawing_number, notes=excluded.notes,
                    folder_path=excluded.folder_path, modified_at=excluded.modified_at,
                    collision_detection_enabled=excluded.collision_detection_enabled
                """,
                params,
            )
            self._conn.commit()
            WorkpieceDatabaseSignals.instance().workpiece_changed.emit(workpiece.id)
            return self.get_workpiece(workpiece.id)

        cur = self._conn.execute(
            """
            INSERT INTO workpieces (
                name, material, description, drawing_number, notes,
                folder_path, created_at, modified_at, collision_detection_enabled
            ) VALUES (
                :name, :material, :description, :drawing_number, :notes,
                :folder_path, :created_at, :modified_at, :collision_detection_enabled
            )
            ON CONFLICT(folder_path) DO UPDATE SET
                name=excluded.name, material=excluded.material,
                description=excluded.description,
                drawing_number=excluded.drawing_number, notes=excluded.notes,
                modified_at=excluded.modified_at,
                collision_detection_enabled=excluded.collision_detection_enabled
            """,
            params,
        )
        self._conn.commit()
        new_id = cur.lastrowid or self.get_workpiece_by_folder(workpiece.folder_path).id
        WorkpieceDatabaseSignals.instance().workpiece_changed.emit(new_id)
        return self.get_workpiece(new_id)

    def set_collision_detection_enabled(
        self, workpiece_id: int, enabled: bool | None,
    ) -> None:
        """Set (or clear, with None) the per-workpiece collision-detection
        override. None means "inherit AppSettings.collision_detection_enabled"."""
        value = None if enabled is None else int(enabled)
        self._conn.execute(
            "UPDATE workpieces SET collision_detection_enabled = ? WHERE id = ?",
            (value, workpiece_id),
        )
        self._conn.commit()
        WorkpieceDatabaseSignals.instance().workpiece_changed.emit(workpiece_id)

    def delete_workpiece(self, workpiece_id: int) -> None:
        """Delete a workpiece and (via ON DELETE CASCADE) all its operations.

        No Job cross-check here, deliberately — domain.models.Job stays a
        plain in-memory dataclass with no persistence, queue, or UI (see
        claude_code_prompt_workpieces_page.md's Abschnitt A); running a
        program is now a direct action (ProgramDetailPage's "Ausführen"
        button loads the operation's gcode_path straight into MachinePage,
        see WorkpiecesSection.request_load_in_machine()), not something
        routed through a Job. There is nothing left for this method to
        check against.
        """
        self._conn.execute("DELETE FROM workpieces WHERE id = ?", (workpiece_id,))
        self._conn.commit()
        WorkpieceDatabaseSignals.instance().workpiece_changed.emit(workpiece_id)

    # ── Operations: queries ──────────────────────────────────────────────────

    def get_operation(self, operation_id: int) -> Operation | None:
        row = self._conn.execute(
            "SELECT * FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        return _row_to_operation(row) if row is not None else None

    def operations_for_workpiece(
        self, workpiece_id: int, include_history: bool = False,
    ) -> list[Operation]:
        """Current operations of a workpiece (is_current=1) — pass
        include_history=True to also include their superseded versions
        (is_current=0). Ordered by id for stable display order."""
        if include_history:
            rows = self._conn.execute(
                "SELECT * FROM operations WHERE workpiece_id = ? ORDER BY id",
                (workpiece_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM operations WHERE workpiece_id = ? AND is_current = 1 "
                "ORDER BY id",
                (workpiece_id,),
            ).fetchall()
        return [_row_to_operation(r) for r in rows]

    def operation_history(self, lineage_id: int) -> list[Operation]:
        """All superseded (is_current=0) versions of one operation family,
        newest version first. The current version is fetched separately
        (it belongs to operations_for_workpiece(), not here)."""
        rows = self._conn.execute(
            "SELECT * FROM operations WHERE lineage_id = ? AND is_current = 0 "
            "ORDER BY version DESC",
            (lineage_id,),
        ).fetchall()
        return [_row_to_operation(r) for r in rows]

    # ── Operations: writes ───────────────────────────────────────────────────

    def upsert_operation(self, operation: Operation) -> Operation:
        """Insert or update *operation* as-is — does NOT perform
        versioning (see create_new_version() for that). Returns the
        persisted row (with `.id` populated for a fresh insert)."""
        now = datetime.now()
        params = _operation_to_params(operation, now)
        if operation.id:
            self._conn.execute(
                """
                INSERT INTO operations (
                    id, workpiece_id, lineage_id, version, previous_version_id,
                    is_current, name, gcode_path, file_hash, clamping_description,
                    zero_point_notes, notes, estimated_time, tools_auto,
                    tools_manual, preview_source, created_at, modified_at
                ) VALUES (
                    :id, :workpiece_id, :lineage_id, :version, :previous_version_id,
                    :is_current, :name, :gcode_path, :file_hash, :clamping_description,
                    :zero_point_notes, :notes, :estimated_time, :tools_auto,
                    :tools_manual, :preview_source, :created_at, :modified_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    lineage_id=excluded.lineage_id, version=excluded.version,
                    previous_version_id=excluded.previous_version_id,
                    is_current=excluded.is_current, name=excluded.name,
                    gcode_path=excluded.gcode_path, file_hash=excluded.file_hash,
                    clamping_description=excluded.clamping_description,
                    zero_point_notes=excluded.zero_point_notes, notes=excluded.notes,
                    estimated_time=excluded.estimated_time, tools_auto=excluded.tools_auto,
                    tools_manual=excluded.tools_manual, preview_source=excluded.preview_source,
                    modified_at=excluded.modified_at
                """,
                params,
            )
            self._conn.commit()
            WorkpieceDatabaseSignals.instance().operation_changed.emit(operation.id)
            return self.get_operation(operation.id)

        cur = self._conn.execute(
            """
            INSERT INTO operations (
                workpiece_id, lineage_id, version, previous_version_id,
                is_current, name, gcode_path, file_hash, clamping_description,
                zero_point_notes, notes, estimated_time, tools_auto,
                tools_manual, preview_source, created_at, modified_at
            ) VALUES (
                :workpiece_id, :lineage_id, :version, :previous_version_id,
                :is_current, :name, :gcode_path, :file_hash, :clamping_description,
                :zero_point_notes, :notes, :estimated_time, :tools_auto,
                :tools_manual, :preview_source, :created_at, :modified_at
            )
            """,
            params,
        )
        self._conn.commit()
        new_id = cur.lastrowid
        WorkpieceDatabaseSignals.instance().operation_changed.emit(new_id)
        return self.get_operation(new_id)

    def create_first_version(
        self, workpiece_id: int, name: str, gcode_path: str,
        file_hash: str = "", tools_auto: list[int] | None = None,
    ) -> Operation:
        """Insert a brand-new Operation as the first version of its own
        lineage (lineage_id == its own id, version=1). Used both by
        get_or_create_by_path() and by the folder sync (workpiece_sync.py)
        whenever a G-code file is seen for the first time."""
        saved = self.upsert_operation(Operation(
            id=0, workpiece_id=workpiece_id, name=name, lineage_id=0,
            gcode_path=gcode_path, file_hash=file_hash,
            tools_auto=list(tools_auto or []),
        ))
        self._conn.execute(
            "UPDATE operations SET lineage_id = ? WHERE id = ?", (saved.id, saved.id)
        )
        self._conn.commit()
        return self.get_operation(saved.id)

    def delete_operation(self, operation_id: int) -> None:
        """Delete *operation_id* AND every other version in its lineage
        (same lineage_id).

        Deliberately a whole-lineage delete, not a single-row one: an old
        version is only ever reachable through its lineage's CURRENT
        version's ProgramDetailPage (see operation_history()) — deleting
        just the current row would leave its history permanently
        orphaned (unreachable, but still in the DB) rather than actually
        removed. It also sidesteps operations.previous_version_id's
        foreign key: deleting an older version on its own, while a newer
        one still points at it via previous_version_id, would raise a
        FOREIGN KEY constraint failure (PRAGMA foreign_keys is ON) —
        deleting the whole lineage in one statement removes both sides
        together, which SQLite allows.
        """
        row = self._conn.execute(
            "SELECT lineage_id FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            return
        self._conn.execute(
            "DELETE FROM operations WHERE lineage_id = ?", (row["lineage_id"],)
        )
        self._conn.commit()
        WorkpieceDatabaseSignals.instance().operation_changed.emit(operation_id)

    def create_new_version(
        self, old_operation: Operation, new_gcode_path: str, new_hash: str,
    ) -> Operation:
        """Produce a new version of *old_operation*'s family: the old row
        is flipped to is_current=0 AND renamed to "{old.name} (Version
        {old.version})", and a new row is inserted pointing back at it
        (previous_version_id), sharing its lineage_id, with
        version = old.version + 1 and the unchanged, file-derived name.

        The rename happens to the operation being SUPERSEDED, not the new
        one — the new operation keeps the plain name derived from its
        filename, while the one dropping out of "current" gets a name
        that's only ever seen inside the history list. This is naturally
        idempotent across repeated versioning: each row is renamed
        exactly once, at the moment IT is superseded, using ITS OWN
        version number (old_operation.version) — no separate bookkeeping,
        and no risk of a version being renamed twice or getting a
        double suffix.

        tools_manual carries forward (it's user intent, not derived from
        file content); tools_auto starts empty and is repopulated by the
        next sync pass over the new file's actual content. estimated_time/
        clamping_description/zero_point_notes/notes carry forward too —
        best-effort defaults until edited for the new version.
        """
        self._conn.execute(
            "UPDATE operations SET is_current = 0, name = ?, modified_at = ? WHERE id = ?",
            (
                f"{old_operation.name} (Version {old_operation.version})",
                datetime.now().isoformat(),
                old_operation.id,
            ),
        )
        self._conn.commit()
        WorkpieceDatabaseSignals.instance().operation_changed.emit(old_operation.id)

        new_operation = Operation(
            id=0,
            workpiece_id=old_operation.workpiece_id,
            name=old_operation.name,
            lineage_id=old_operation.lineage_id,
            gcode_path=new_gcode_path,
            clamping_description=old_operation.clamping_description,
            zero_point_notes=old_operation.zero_point_notes,
            notes=old_operation.notes,
            estimated_time=old_operation.estimated_time,
            version=old_operation.version + 1,
            previous_version_id=old_operation.id,
            is_current=True,
            file_hash=new_hash,
            tools_manual=list(old_operation.tools_manual),
        )
        return self.upsert_operation(new_operation)


# ── Shared helpers (also used by persistence/workpiece_sync.py) ────────────

def _escape_like(text: str) -> str:
    """Escape SQL LIKE wildcards (% and _) in *text* using backslash, for
    use with an explicit ESCAPE '\\' clause — folder names containing a
    literal % or _ must not be treated as LIKE wildcards in
    workpieces_under()."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _relative_or_absolute_folder(absolute_folder: Path) -> str:
    """*absolute_folder* expressed relative to AppSettings.workpieces_root_path
    (POSIX slashes, so it matches folder_path's convention — see module
    docstring) if it lies under that root, else the absolute path
    unchanged as a last-resort fallback (see get_or_create_by_path())."""
    from controller.sim.core.settings import AppSettings

    root = AppSettings.instance().workpieces_root_path
    if root:
        try:
            return absolute_folder.relative_to(Path(root)).as_posix()
        except ValueError:
            pass
    return str(absolute_folder)


def _sha256_file(path: str) -> str:
    """SHA-256 hex digest of a file's content, or "" if it can't be read
    (missing/permission error) — sync failures must never raise, only
    surface as a visible-but-non-blocking error (see workpiece_sync.py)."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        logger.warning("Could not hash file %s", path, exc_info=True)
        return ""


def strip_gcode_comments(text: str) -> str:
    """Remove G-code comments (parenthesized `(...)` and `;`-to-end-of-line)
    before scanning for T-addresses, so a tool number only ever mentioned
    in a comment is never counted as actually used."""
    without_parens = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r";.*", "", without_parens)


def parse_tools_from_gcode(text: str) -> list[int]:
    """Extract T<number> tool addresses from raw G-code text, in first-
    occurrence order, comments excluded (see strip_gcode_comments()) and
    de-duplicated. M6 itself carries no tool number and needs no special
    handling — it never matches _T_ADDRESS_RE on its own."""
    code_only = strip_gcode_comments(text)
    seen: list[int] = []
    for match in _T_ADDRESS_RE.finditer(code_only):
        number = int(match.group(1))
        if number not in seen:
            seen.append(number)
    return seen


# ── Row <-> dataclass mapping ────────────────────────────────────────────────

def _row_to_workpiece(row: sqlite3.Row) -> Workpiece:
    cde = row["collision_detection_enabled"]
    return Workpiece(
        id=row["id"],
        name=row["name"],
        material=row["material"],
        description=row["description"],
        drawing_number=row["drawing_number"],
        notes=row["notes"],
        folder_path=row["folder_path"],
        created_at=datetime.fromisoformat(row["created_at"]),
        modified_at=datetime.fromisoformat(row["modified_at"]),
        collision_detection_enabled=None if cde is None else bool(cde),
    )


def _row_to_operation(row: sqlite3.Row) -> Operation:
    return Operation(
        id=row["id"],
        workpiece_id=row["workpiece_id"],
        name=row["name"],
        lineage_id=row["lineage_id"],
        gcode_path=row["gcode_path"],
        file_hash=row["file_hash"],
        clamping_description=row["clamping_description"],
        zero_point_notes=row["zero_point_notes"],
        notes=row["notes"],
        estimated_time=row["estimated_time"],
        created_at=datetime.fromisoformat(row["created_at"]),
        modified_at=datetime.fromisoformat(row["modified_at"]),
        version=row["version"],
        previous_version_id=row["previous_version_id"],
        is_current=bool(row["is_current"]),
        tools_auto=json.loads(row["tools_auto"]),
        tools_manual=json.loads(row["tools_manual"]),
        preview_source=row["preview_source"],
    )


def _operation_to_params(operation: Operation, now: datetime) -> dict:
    return {
        "id": operation.id or None,
        "workpiece_id": operation.workpiece_id,
        "lineage_id": operation.lineage_id,
        "version": operation.version,
        "previous_version_id": operation.previous_version_id,
        "is_current": int(operation.is_current),
        "name": operation.name,
        "gcode_path": operation.gcode_path,
        "file_hash": operation.file_hash,
        "clamping_description": operation.clamping_description,
        "zero_point_notes": operation.zero_point_notes,
        "notes": operation.notes,
        "estimated_time": operation.estimated_time,
        "tools_auto": json.dumps(operation.tools_auto),
        "tools_manual": json.dumps(operation.tools_manual),
        "preview_source": operation.preview_source,
        "created_at": operation.created_at.isoformat(),
        "modified_at": now.isoformat(),
    }
