"""
persistence/workpiece_db.py — WorkpieceDatabase: sqlite3-backed workpiece +
operation tables.

Structure mirrors persistence/tool_db.py deliberately (singleton via
instance(), sqlite3 + row_factory, INSERT ... ON CONFLICT DO UPDATE, a
row<->dataclass mapping pair per table) — see that module for the
established pattern this one follows.

Two tables:
    workpieces  — one row per physical part. folder_path (a direct
                  subfolder of AppSettings.workpieces_root_path, see
                  persistence/workpiece_sync.py) is the sync anchor.
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

This replaces an earlier version of this module that bound one Workpiece
1:1 to a single gcode_path with no operations table and no versioning at
all — that schema/data was intentionally discarded (not migrated) when
this module was rewritten, per explicit confirmation; there is no
migration path from the old workpieces.db.
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
        """Discard a pre-rewrite `workpieces` table (the old schema: one
        row 1:1-bound to a single gcode_path, no folder_path/notes/
        modified_at columns, no operations table at all).

        `CREATE TABLE IF NOT EXISTS` above is a no-op against an existing
        table, so an old workpieces.db left over from before this module
        was rewritten would otherwise keep its old columns forever and
        crash on the first read (missing e.g. `folder_path`/`notes`).
        There is deliberately no column-by-column migration here (unlike
        tool_db.py's _migrate_schema()) — this schema change was
        confirmed, before it was made, to discard rather than convert old
        data (no operations, no versioning existed yet to convert to), so
        detecting the old shape and starting fresh is the correct fix,
        not a gap to fill in later.
        """
        existing_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(workpieces)")
        }
        if existing_columns and "folder_path" not in existing_columns:
            logger.warning(
                "%s has the pre-rewrite workpieces schema (no folder_path "
                "column) - dropping and recreating; see module docstring, "
                "this data was never migrated on purpose.",
                self._db_path,
            )
            self._conn.execute("DROP TABLE IF EXISTS operations")
            self._conn.execute("DROP TABLE IF EXISTS workpieces")
            self._conn.executescript(_SCHEMA)
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

    def get_by_folder(self, folder_path: str) -> Workpiece | None:
        row = self._conn.execute(
            "SELECT * FROM workpieces WHERE folder_path = ?", (folder_path,)
        ).fetchone()
        return _row_to_workpiece(row) if row is not None else None

    def all_workpieces(self) -> list[Workpiece]:
        """Lightweight list for WorkpiecesPage — operations are NOT
        populated here (would mean one extra query per row for a list
        that only ever shows name/created/modified); fetch a single
        Workpiece via get_workpiece() when its operations are needed."""
        rows = self._conn.execute(
            "SELECT * FROM workpieces ORDER BY id"
        ).fetchall()
        return [_row_to_workpiece(r) for r in rows]

    def get_or_create_by_folder(
        self, folder_path: str, default_name: str | None = None,
    ) -> Workpiece:
        """Return the Workpiece already anchored to *folder_path*, or
        create+persist a fresh one (name defaults to the folder's own
        name) if none exists yet. Entry point for the folder sync (see
        persistence.workpiece_sync) — one direct subfolder = one Workpiece."""
        existing = self.get_by_folder(folder_path)
        if existing is not None:
            return existing
        name = default_name or Path(folder_path).name
        return self.upsert_workpiece(Workpiece(name=name, folder_path=folder_path))

    def get_or_create_by_path(
        self, gcode_path: str, default_name: str | None = None,
    ) -> Workpiece:
        """Resolve the Workpiece a given G-code FILE belongs to, creating
        one (with a fresh first-version Operation) if the file is
        completely unknown yet.

        This is DatumSimWidget.set_file()'s entry point — kept for that
        caller, which loads an arbitrary file (not necessarily one that
        lives under AppSettings.workpieces_root_path, e.g. the bundled
        example G-code) and just needs "some Workpiece row" to hang its
        per-workpiece collision-detection override off of. It is NOT the
        Workpieces UI's own path — that flow always goes through the
        folder sync (get_or_create_by_folder + create_new_version).

        Resolution order: (1) an existing Operation row already points at
        this exact gcode_path -> return its Workpiece. (2) otherwise, the
        file's parent folder is used as a folder-sync anchor
        (get_or_create_by_folder) and a new first-version Operation is
        created under it.
        """
        row = self._conn.execute(
            "SELECT workpiece_id FROM operations WHERE gcode_path = ? LIMIT 1",
            (gcode_path,),
        ).fetchone()
        if row is not None:
            workpiece = self.get_workpiece(row["workpiece_id"])
            if workpiece is not None:
                return workpiece

        folder_path = str(Path(gcode_path).resolve().parent)
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
        new_id = cur.lastrowid or self.get_by_folder(workpiece.folder_path).id
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

        TODO: once a Job persistence/repository exists, this MUST first
        check whether any Job references workpiece_id and refuse (or ask
        for confirmation) if so — a workpiece that's part of production
        history should not simply disappear. No such repository exists
        yet (see domain.models.Job — in-memory only so far), so deletion
        is unconditional for now; this is a known gap, not an oversight.
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
        is flipped to is_current=0, and a new row is inserted pointing
        back at it (previous_version_id), sharing its lineage_id, with
        version = old.version + 1.

        tools_manual carries forward (it's user intent, not derived from
        file content); tools_auto starts empty and is repopulated by the
        next sync pass over the new file's actual content. estimated_time/
        clamping_description/zero_point_notes/notes carry forward too —
        best-effort defaults until edited for the new version.
        """
        self._conn.execute(
            "UPDATE operations SET is_current = 0, modified_at = ? WHERE id = ?",
            (datetime.now().isoformat(), old_operation.id),
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
