"""
persistence/tool_db.py — ToolDatabase: sqlite3-backed tool table.

The app's first "real" persistence layer (everything else is QSettings via
AppSettings) — see README's roadmap ("persistence layer first, then one
page at a time") and tool_info_card.py's docstring ("no ToolRepository
exists yet"). Deliberately built so a future Toolpage can do full CRUD
against it directly.

Two tables:
    tools          — one row per tool number. LinuxCNC-native fields
                     (tool_number/pocket/diameter/offsets/remark) plus the
                     app's geometry extension (tool_type, cutting_length,
                     ...) already defined on ToolDefinition, plus
                     holder_preset (FK into tool_holders, nullable).
    tool_holders   — named HolderProfile presets (see tool_holder.py),
                     seeded with the standard ER/SK/BT presets on first run.

tool.tbl sync
-------------
This app's tools.db is the SOURCE OF TRUTH. Every upsert_tool()/
delete_tool() call re-writes the ENTIRE configured LinuxCNC tool.tbl file
(AppSettings.linuxcnc_tool_table_path) afterward, automatically — there is
no manual "export" action. An empty path (the default) just skips the
write, so nothing happens until a path is actually configured.
import_linuxcnc_tbl() is the only path in the OTHER direction, and is never
called automatically — it exists purely as a one-time bootstrapping utility
for adopting an existing tool.tbl into the DB.

Thread safety
-------------
sqlite3 connections are opened with check_same_thread=False so reads from a
background thread (e.g. a presim/collision-check pass warming up tool data)
don't raise — SQLite's own file-level locking serialises the rare concurrent
write. This mirrors the level of thread-safety the rest of the sim engine
already assumes for infrequent, small operations (see VoxelSimController's
docstring) rather than adding new locking machinery for what is, today,
an occasional read-mostly table.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from controller.sim.simulation.tool_definition import ToolDefinition, ToolType
from controller.sim.simulation.tool_holder import HolderProfile, STANDARD_HOLDERS

logger = logging.getLogger(__name__)


class ToolDatabaseSignals(QObject):
    """Qt signal bridge for ToolDatabase writes.

    Kept deliberately separate from ToolDatabase itself, which stays a
    plain, Qt-free class (see its module docstring on thread safety —
    background threads and non-GUI tests construct/use it directly). UI
    code that needs to react to a tool being edited or deleted (ToolPage's
    list reload, the collision pre-pass's invalidation when the ACTIVE
    tool's geometry changes — see DatumSimWidget) connects to this
    singleton instead of coupling the persistence layer to Qt."""

    tool_changed = Signal(int)   # tool_number — emitted by upsert/delete

    _instance: "ToolDatabaseSignals | None" = None

    @classmethod
    def instance(cls) -> "ToolDatabaseSignals":
        if cls._instance is None:
            cls._instance = ToolDatabaseSignals()
        return cls._instance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
    tool_number      INTEGER PRIMARY KEY,
    pocket           INTEGER NOT NULL,
    diameter         REAL    NOT NULL,
    z_offset         REAL    NOT NULL DEFAULT 0.0,
    x_offset         REAL    NOT NULL DEFAULT 0.0,
    y_offset         REAL    NOT NULL DEFAULT 0.0,
    remark           TEXT    NOT NULL DEFAULT '',
    tool_type        TEXT    NOT NULL DEFAULT 'ENDMILL',
    flute_length     REAL    NOT NULL DEFAULT 0.0,
    cutting_length   REAL    NOT NULL DEFAULT 0.0,
    shank_diameter   REAL    NOT NULL DEFAULT 0.0,
    total_length     REAL    NOT NULL DEFAULT 0.0,
    corner_radius    REAL    NOT NULL DEFAULT 0.0,
    tip_angle        REAL    NOT NULL DEFAULT 0.0,
    taper_angle      REAL    NOT NULL DEFAULT 0.0,
    manufacturer     TEXT    NOT NULL DEFAULT '',
    material         TEXT    NOT NULL DEFAULT '',
    service_life_min REAL    NOT NULL DEFAULT 0.0,
    used_min         REAL    NOT NULL DEFAULT 0.0,
    holder_preset    TEXT    REFERENCES tool_holders(name)
    -- name/flute_count/clearance_angle/cutting_speed/feed_rate (ToolPage
    -- fields) are NOT listed here — they were added after this table
    -- already existed in the wild, so they're applied via _migrate_schema()
    -- (ALTER TABLE ADD COLUMN) instead, which also covers a fresh DB (the
    -- CREATE TABLE above runs first either way, then the migration adds
    -- whatever's missing — a no-op on a fresh DB that already lacks them).
);

CREATE TABLE IF NOT EXISTS tool_holders (
    name          TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    gauge_length  REAL NOT NULL,
    profile_json  TEXT NOT NULL
);
"""

# First-run seed data — the app's original 6 sample tools (formerly
# MOCK_TOOL_TABLE in tool_database.py; that module is now a thin wrapper
# around this one, so the seed data lives here where it's actually used).
_SEED_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        tool_number=1, pocket=1, diameter=6.0, z_offset=0.0,
        remark="6mm Schaftfräser 4-Schneider", tool_type=ToolType.ENDMILL,
        flute_length=1.0, cutting_length=1.0, shank_diameter=10.0,
        total_length=25.0, manufacturer="Sandvik", material="VHM",
        service_life_min=120.0, holder_preset="ER32",
        name="10mm Schaftfräser", flute_count=4,
    ),
    ToolDefinition(
        tool_number=2, pocket=2, diameter=6.0, z_offset=0.0,
        remark="6mm Kugelfräser", tool_type=ToolType.BALL_ENDMILL,
        flute_length=15.0, cutting_length=15.0, shank_diameter=6.0,
        total_length=60.0, manufacturer="Sandvik", material="VHM",
        service_life_min=90.0, holder_preset="ER20",
        name="6mm Kugelfräser", flute_count=2,
    ),
    ToolDefinition(
        tool_number=3, pocket=3, diameter=8.0, z_offset=0.0,
        remark="8mm Torusfräser r=1mm", tool_type=ToolType.BULL_ENDMILL,
        flute_length=20.0, cutting_length=20.0, shank_diameter=8.0,
        total_length=65.0, corner_radius=1.0, manufacturer="Kennametal",
        material="VHM", service_life_min=150.0, holder_preset="ER25",
        name="8mm Torusfräser r=1mm", flute_count=4,
    ),
    ToolDefinition(
        tool_number=4, pocket=4, diameter=12.0, z_offset=0.0,
        remark="90° Gravurfräser", tool_type=ToolType.CHAMFER,
        flute_length=10.0, cutting_length=10.0, shank_diameter=8.0,
        total_length=50.0, tip_angle=90.0, manufacturer="Datron",
        material="VHM", service_life_min=200.0, holder_preset="ER25",
        name="90° Gravurfräser", flute_count=2,
    ),
    ToolDefinition(
        tool_number=5, pocket=5, diameter=8.0, z_offset=0.0,
        remark="8mm Spiralbohrer HSS", tool_type=ToolType.DRILL,
        flute_length=75.0, cutting_length=75.0, shank_diameter=8.0,
        total_length=115.0, tip_angle=118.0, manufacturer="Gühring",
        material="HSS-E", service_life_min=60.0, holder_preset="ER25",
        name="8mm Spiralbohrer", flute_count=2,
    ),
    ToolDefinition(
        tool_number=6, pocket=6, diameter=10.0, z_offset=0.0,
        remark="10mm Konusfräser 5°", tool_type=ToolType.TAPER,
        flute_length=18.0, cutting_length=18.0, shank_diameter=10.0,
        total_length=65.0, taper_angle=5.0, manufacturer="Datron",
        material="VHM", service_life_min=100.0, holder_preset="ER32",
        name="10mm Konusfräser 5°", flute_count=2,
    ),
]

# Columns added to `tools` after the table already existed in deployed
# DBs — applied via _migrate_schema()'s idempotent ALTER TABLE ADD COLUMN,
# rather than folded into _SCHEMA's CREATE TABLE, so an existing tools.db
# (and its data) keeps working without a manual migration step. See
# ToolDefinition's docstring for what each field means.
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("name",            "TEXT NOT NULL DEFAULT ''"),
    ("flute_count",     "INTEGER NOT NULL DEFAULT 0"),
    ("clearance_angle", "REAL NOT NULL DEFAULT 0.0"),
    ("cutting_speed",   "REAL NOT NULL DEFAULT 0.0"),
    ("feed_rate",       "REAL NOT NULL DEFAULT 0.0"),
]

_TOOL_TBL_LINE_RE = re.compile(
    r"T(?P<T>\d+)\s+P(?P<P>\d+)"
    r"(?:\s+X(?P<X>[-+\d.]+))?(?:\s+Y(?P<Y>[-+\d.]+))?(?:\s+Z(?P<Z>[-+\d.]+))?"
    r"(?:\s+D(?P<D>[-+\d.]+))?"
    r"(?:\s*;\s*(?P<comment>.*))?$"
)


class ToolDatabase:
    """Singleton sqlite3-backed tool table. See module docstring."""

    _instance: "ToolDatabase | None" = None

    @classmethod
    def instance(cls) -> "ToolDatabase":
        if cls._instance is None:
            cls._instance = ToolDatabase()
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
        self._migrate_schema()
        self._seed_if_empty()

    def _migrate_schema(self) -> None:
        """Add any of _NEW_COLUMNS not already present on `tools`.

        Idempotent (checks PRAGMA table_info first) — a no-op on a DB
        that's already current, so this is safe to run on every startup.
        Existing rows get the new columns' DEFAULT retroactively (SQLite's
        standard ALTER TABLE ADD COLUMN behavior); no data is lost and no
        manual migration step is needed."""
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(tools)")
        }
        for col, ddl in _NEW_COLUMNS:
            if col not in existing:
                self._conn.execute(f"ALTER TABLE tools ADD COLUMN {col} {ddl}")
        self._conn.commit()

    @staticmethod
    def _resolve_default_path() -> str:
        # Imported lazily: AppSettings.tool_db_path lives in QSettings and
        # doesn't need a QApplication instance to read, but keeping the
        # import local avoids persistence/ needing sim/core at module
        # import time for callers that only ever pass an explicit db_path
        # (e.g. tests).
        from controller.sim.core.settings import AppSettings

        configured = AppSettings.instance().tool_db_path
        if configured:
            return configured

        from controller.persistence.paths import db_dir

        return str(db_dir() / "tools.db")

    # ── Seeding ──────────────────────────────────────────────────────────────

    def _seed_if_empty(self) -> None:
        if self._conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0] == 0:
            for holder in STANDARD_HOLDERS.values():
                self.upsert_holder(holder, _export=False)
            for tool in _SEED_TOOLS:
                self.upsert_tool(tool, _export=False)
            # One export covers the whole seed set instead of one per tool.
            self._export_tool_tbl()

    # ── Tools ────────────────────────────────────────────────────────────────

    def get_tool(self, tool_number: int) -> ToolDefinition | None:
        row = self._conn.execute(
            "SELECT * FROM tools WHERE tool_number = ?", (tool_number,)
        ).fetchone()
        return _row_to_tool(row) if row is not None else None

    def all_tools(self) -> list[ToolDefinition]:
        rows = self._conn.execute(
            "SELECT * FROM tools ORDER BY tool_number"
        ).fetchall()
        return [_row_to_tool(r) for r in rows]

    def upsert_tool(self, tool: ToolDefinition, _export: bool = True) -> None:
        params = _tool_to_params(tool)
        self._conn.execute(
            """
            INSERT INTO tools (
                tool_number, pocket, diameter, z_offset, x_offset, y_offset,
                remark, tool_type, flute_length, cutting_length,
                shank_diameter, total_length, corner_radius, tip_angle,
                taper_angle, manufacturer, material, service_life_min,
                used_min, holder_preset, name, flute_count, clearance_angle,
                cutting_speed, feed_rate
            ) VALUES (
                :tool_number, :pocket, :diameter, :z_offset, :x_offset, :y_offset,
                :remark, :tool_type, :flute_length, :cutting_length,
                :shank_diameter, :total_length, :corner_radius, :tip_angle,
                :taper_angle, :manufacturer, :material, :service_life_min,
                :used_min, :holder_preset, :name, :flute_count, :clearance_angle,
                :cutting_speed, :feed_rate
            )
            ON CONFLICT(tool_number) DO UPDATE SET
                pocket=excluded.pocket, diameter=excluded.diameter,
                z_offset=excluded.z_offset, x_offset=excluded.x_offset,
                y_offset=excluded.y_offset, remark=excluded.remark,
                tool_type=excluded.tool_type, flute_length=excluded.flute_length,
                cutting_length=excluded.cutting_length,
                shank_diameter=excluded.shank_diameter,
                total_length=excluded.total_length,
                corner_radius=excluded.corner_radius, tip_angle=excluded.tip_angle,
                taper_angle=excluded.taper_angle, manufacturer=excluded.manufacturer,
                material=excluded.material, service_life_min=excluded.service_life_min,
                used_min=excluded.used_min, holder_preset=excluded.holder_preset,
                name=excluded.name, flute_count=excluded.flute_count,
                clearance_angle=excluded.clearance_angle,
                cutting_speed=excluded.cutting_speed, feed_rate=excluded.feed_rate
            """,
            params,
        )
        self._conn.commit()
        if _export:
            self._export_tool_tbl()
        ToolDatabaseSignals.instance().tool_changed.emit(tool.tool_number)

    def delete_tool(self, tool_number: int) -> None:
        self._conn.execute("DELETE FROM tools WHERE tool_number = ?", (tool_number,))
        self._conn.commit()
        self._export_tool_tbl()
        ToolDatabaseSignals.instance().tool_changed.emit(tool_number)

    # ── Holders ──────────────────────────────────────────────────────────────

    def get_holder(self, name: str | None) -> HolderProfile | None:
        if not name:
            return None
        row = self._conn.execute(
            "SELECT * FROM tool_holders WHERE name = ?", (name,)
        ).fetchone()
        return _row_to_holder(row) if row is not None else None

    def all_holders(self) -> list[HolderProfile]:
        rows = self._conn.execute(
            "SELECT * FROM tool_holders ORDER BY name"
        ).fetchall()
        return [_row_to_holder(r) for r in rows]

    def upsert_holder(self, holder: HolderProfile, _export: bool = True) -> None:
        self._conn.execute(
            """
            INSERT INTO tool_holders (name, kind, gauge_length, profile_json)
            VALUES (:name, :kind, :gauge_length, :profile_json)
            ON CONFLICT(name) DO UPDATE SET
                kind=excluded.kind, gauge_length=excluded.gauge_length,
                profile_json=excluded.profile_json
            """,
            {
                "name": holder.name,
                "kind": holder.kind,
                "gauge_length": holder.gauge_length,
                "profile_json": json.dumps(holder.profile),
            },
        )
        self._conn.commit()
        # Holders themselves have no LinuxCNC tool.tbl representation —
        # nothing to export on their own change (_export param kept only
        # for call-site symmetry with upsert_tool during seeding).

    # ── LinuxCNC tool.tbl ────────────────────────────────────────────────────

    def _export_tool_tbl(self) -> None:
        """Re-write the configured tool.tbl from the DB (source of truth).

        No-op if no path is configured. Failures (unwritable path, etc.)
        are logged, not raised — a stale/missing tool.tbl export must never
        take down the tool database itself.
        """
        from controller.sim.core.settings import AppSettings

        path = AppSettings.instance().linuxcnc_tool_table_path
        if not path:
            return
        try:
            lines = [
                f"T{t.tool_number} P{t.pocket} "
                f"X{t.x_offset:.6f} Y{t.y_offset:.6f} Z{t.z_offset:.6f} "
                f"D{t.diameter:.6f} ;{t.remark}"
                for t in self.all_tools()
            ]
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            logger.warning("Could not write tool.tbl to %s", path, exc_info=True)

    def import_linuxcnc_tbl(self, path: str) -> int:
        """One-time bootstrap: read a real LinuxCNC tool.tbl and upsert its
        native fields (T/P/X/Y/Z/D/comment) into the DB. NEVER called
        automatically — the DB is the source of truth going forward; this
        exists only to adopt an existing tool.tbl once. Geometry-extension
        fields (cutting_length, ...) are left at their defaults/whatever
        already existed for that tool_number. Returns the number of tools
        imported."""
        count = 0
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            m = _TOOL_TBL_LINE_RE.match(line)
            if not m:
                continue
            existing = self.get_tool(int(m["T"]))
            tool = existing or ToolDefinition(
                tool_number=int(m["T"]), pocket=int(m["P"]), diameter=0.0,
            )
            tool.pocket   = int(m["P"])
            tool.x_offset = float(m["X"]) if m["X"] else 0.0
            tool.y_offset = float(m["Y"]) if m["Y"] else 0.0
            tool.z_offset = float(m["Z"]) if m["Z"] else 0.0
            if m["D"]:
                tool.diameter = float(m["D"])
            if m["comment"]:
                tool.remark = m["comment"]
            self.upsert_tool(tool)
            count += 1
        return count


# ── Row <-> dataclass mapping ──────────────────────────────────────────────────

def _row_to_tool(row: sqlite3.Row) -> ToolDefinition:
    keys = row.keys()
    return ToolDefinition(
        tool_number=row["tool_number"], pocket=row["pocket"],
        diameter=row["diameter"], z_offset=row["z_offset"],
        x_offset=row["x_offset"], y_offset=row["y_offset"],
        remark=row["remark"], tool_type=ToolType[row["tool_type"]],
        flute_length=row["flute_length"], cutting_length=row["cutting_length"],
        shank_diameter=row["shank_diameter"], total_length=row["total_length"],
        corner_radius=row["corner_radius"], tip_angle=row["tip_angle"],
        taper_angle=row["taper_angle"], manufacturer=row["manufacturer"],
        material=row["material"], service_life_min=row["service_life_min"],
        used_min=row["used_min"], holder_preset=row["holder_preset"],
        # These columns are added by _migrate_schema() rather than _SCHEMA
        # — guard with "in keys" so a row read mid-migration (shouldn't
        # normally happen, __init__ always migrates before any query, but
        # cheap insurance) degrades to the field's own default rather than
        # raising.
        name=row["name"] if "name" in keys else "",
        flute_count=row["flute_count"] if "flute_count" in keys else 0,
        clearance_angle=row["clearance_angle"] if "clearance_angle" in keys else 0.0,
        cutting_speed=row["cutting_speed"] if "cutting_speed" in keys else 0.0,
        feed_rate=row["feed_rate"] if "feed_rate" in keys else 0.0,
    )


def _tool_to_params(tool: ToolDefinition) -> dict:
    return {
        "tool_number": tool.tool_number, "pocket": tool.pocket,
        "diameter": tool.diameter, "z_offset": tool.z_offset,
        "x_offset": tool.x_offset, "y_offset": tool.y_offset,
        "remark": tool.remark, "tool_type": tool.tool_type.name,
        "flute_length": tool.flute_length, "cutting_length": tool.cutting_length,
        "shank_diameter": tool.shank_diameter, "total_length": tool.total_length,
        "corner_radius": tool.corner_radius, "tip_angle": tool.tip_angle,
        "taper_angle": tool.taper_angle, "manufacturer": tool.manufacturer,
        "material": tool.material, "service_life_min": tool.service_life_min,
        "used_min": tool.used_min, "holder_preset": tool.holder_preset,
        "name": tool.name, "flute_count": tool.flute_count,
        "clearance_angle": tool.clearance_angle,
        "cutting_speed": tool.cutting_speed, "feed_rate": tool.feed_rate,
    }


def _row_to_holder(row: sqlite3.Row) -> HolderProfile:
    profile = [tuple(p) for p in json.loads(row["profile_json"])]
    return HolderProfile(
        name=row["name"], kind=row["kind"],
        gauge_length=row["gauge_length"], profile=profile,
    )
