"""SQLite connection and schema. All SQL lives under app/store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database, creating the schema when it is new.

    ":memory:" gives tests a private database. check_same_thread is off because FastAPI
    serves sync route functions from a thread pool; writes are serialised by SQLite itself.
    """
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS leaves an existing table
# as it was, so each new column is added here when it is missing.
COLUMNS = (
    ("partners", "trusted_at", "TEXT"),          # ADR-050: a partner added later acts from here
    ("devices", "reminded_at", "TEXT"),          # ADR-050: last alert about this device being off or silent
    ("devices", "not_judging_at", "TEXT"),       # ADR-051: when "on but judging nothing" was raised
    ("heartbeats", "screen_on_s", "INTEGER"),    # ADR-051: the heartbeat's judge block
    ("heartbeats", "unwatched_s", "INTEGER"),
    ("heartbeats", "judged", "INTEGER"),
    ("events", "sealed", "TEXT"),                # ADR-053
    ("images", "sealed", "INTEGER NOT NULL DEFAULT 0"),
    ("unlock_codes", "signed", "INTEGER NOT NULL DEFAULT 0"),
    ("watching", "my_key", "TEXT"),              # ADR-055: the partner key this server holds for them
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in COLUMNS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
