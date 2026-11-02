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
    return conn
