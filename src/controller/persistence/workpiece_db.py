"""
persistence/workpiece_db.py — WorkpieceDatabase: sqlite3-backed workpiece table.

Structure mirrors persistence/tool_db.py deliberately (singleton via
instance(), sqlite3 + row_factory, INSERT ... ON CONFLICT DO UPDATE, a
row<->dataclass mapping pair) — see that module for the established
pattern this one follows.

Scope: this is explicitly a PREPARATION for a future Workpieces page, not
that page itself (see domain.models.Workpiece's docstring on its two new
fields). Today the app has no "create a workpiece" flow and loads exactly
one G-code file at a time — get_or_create_by_path() is the only entry
point that matters right now, keyed on the loaded file's path, which is
the sole stable identifier available (see MachinePage._loaded_path /
DatumSimWidget.set_file()). Operations (Workpiece.operations) are NOT
persisted here — out of scope for this preparation; rows always
round-trip with an empty operations list.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from controller.domain.models import Workpiece

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workpieces (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    name                        TEXT    NOT NULL,
    material                    TEXT    NOT NULL DEFAULT '',
    description                 TEXT    NOT NULL DEFAULT '',
    drawing_number              TEXT    NOT NULL DEFAULT '',
    created_at                  TEXT    NOT NULL,
    gcode_path                  TEXT    UNIQUE,
    collision_detection_enabled INTEGER
);
"""


class WorkpieceDatabase:
    """Singleton sqlite3-backed workpiece table. See module docstring."""

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
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @staticmethod
    def _resolve_default_path() -> str:
        # Same resolution strategy as tool_db.py's ToolDatabase — see its
        # docstring for why these imports are local rather than top-level.
        from controller.sim.core.settings import AppSettings

        configured = AppSettings.instance().workpiece_db_path
        if configured:
            return configured

        from controller.persistence.paths import db_dir

        return str(db_dir() / "workpieces.db")

    # ── Queries ──────────────────────────────────────────────────────────────

    def get_by_path(self, gcode_path: str) -> Workpiece | None:
        row = self._conn.execute(
            "SELECT * FROM workpieces WHERE gcode_path = ?", (gcode_path,)
        ).fetchone()
        return _row_to_workpiece(row) if row is not None else None

    def get_or_create_by_path(
        self, gcode_path: str, default_name: str | None = None,
    ) -> Workpiece:
        """Return the Workpiece already associated with *gcode_path*, or
        create+persist a fresh one (name defaults to the file's stem) if
        none exists yet. This is the entry point DatumSimWidget.set_file()
        uses — every loaded program ends up with a Workpiece row, even
        before any real "create workpiece" UI exists."""
        existing = self.get_by_path(gcode_path)
        if existing is not None:
            return existing
        name = default_name or Path(gcode_path).stem
        workpiece = Workpiece(name=name, gcode_path=gcode_path)
        return self.upsert(workpiece)

    def all_workpieces(self) -> list[Workpiece]:
        rows = self._conn.execute(
            "SELECT * FROM workpieces ORDER BY id"
        ).fetchall()
        return [_row_to_workpiece(r) for r in rows]

    # ── Writes ───────────────────────────────────────────────────────────────

    def upsert(self, workpiece: Workpiece) -> Workpiece:
        """Insert or update *workpiece*. Returns the persisted row as a
        Workpiece (with `.id` populated for a fresh insert)."""
        params = {
            "id": workpiece.id,
            "name": workpiece.name,
            "material": workpiece.material,
            "description": workpiece.description,
            "drawing_number": workpiece.drawing_number,
            "created_at": workpiece.created_at.isoformat(),
            "gcode_path": workpiece.gcode_path,
            "collision_detection_enabled": (
                None if workpiece.collision_detection_enabled is None
                else int(workpiece.collision_detection_enabled)
            ),
        }
        if workpiece.id is not None:
            self._conn.execute(
                """
                INSERT INTO workpieces (
                    id, name, material, description, drawing_number,
                    created_at, gcode_path, collision_detection_enabled
                ) VALUES (
                    :id, :name, :material, :description, :drawing_number,
                    :created_at, :gcode_path, :collision_detection_enabled
                )
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, material=excluded.material,
                    description=excluded.description,
                    drawing_number=excluded.drawing_number,
                    gcode_path=excluded.gcode_path,
                    collision_detection_enabled=excluded.collision_detection_enabled
                """,
                params,
            )
            self._conn.commit()
            return workpiece

        cur = self._conn.execute(
            """
            INSERT INTO workpieces (
                name, material, description, drawing_number,
                created_at, gcode_path, collision_detection_enabled
            ) VALUES (
                :name, :material, :description, :drawing_number,
                :created_at, :gcode_path, :collision_detection_enabled
            )
            ON CONFLICT(gcode_path) DO UPDATE SET
                name=excluded.name, material=excluded.material,
                description=excluded.description,
                drawing_number=excluded.drawing_number,
                collision_detection_enabled=excluded.collision_detection_enabled
            """,
            params,
        )
        self._conn.commit()
        new_id = cur.lastrowid
        if new_id:
            return _replace_id(workpiece, new_id)
        # ON CONFLICT DO UPDATE path (gcode_path already existed) — re-read
        # to get the existing row's real id.
        return self.get_by_path(workpiece.gcode_path)

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


def _row_to_workpiece(row: sqlite3.Row) -> Workpiece:
    cde = row["collision_detection_enabled"]
    return Workpiece(
        id=row["id"],
        name=row["name"],
        material=row["material"],
        description=row["description"],
        drawing_number=row["drawing_number"],
        created_at=datetime.fromisoformat(row["created_at"]),
        gcode_path=row["gcode_path"],
        collision_detection_enabled=None if cde is None else bool(cde),
    )


def _replace_id(workpiece: Workpiece, new_id: int) -> Workpiece:
    """dataclasses.replace() would also copy `operations`/`created_at` by
    reference, which is fine here — just avoids mutating the caller's
    original Workpiece in place."""
    from dataclasses import replace
    return replace(workpiece, id=new_id)
