"""The record (ADR-052): an append-only hash chain of everything that matters for accountability.

Each entry is (seq, at, kind, person_id, ref, body) and its hash is
sha256(prev_hash + "\\n" + canonical JSON of [seq, at, kind, person_id, ref, body]). Changing,
removing or inserting any entry changes every hash after it, so a copy of a later hash held
anywhere else (a partner's own server, a partner's signed checkpoint, an email) shows it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from typing import Any

from app.clock import Clock, iso

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(prev: str, seq: int, at: str, kind: str, person_id: str | None, ref: str | None, body: Any) -> str:
    text = prev + "\n" + canonical([seq, at, kind, person_id, ref, body])
    return hashlib.sha256(text.encode()).hexdigest()


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


class LedgerStore:
    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self.conn = conn
        self.clock = clock
        # One process writes; the lock keeps two requests from taking the same sequence number.
        self._lock = threading.Lock()

    def head(self) -> tuple[int, str]:
        row = self.conn.execute("SELECT seq, hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return (row["seq"], row["hash"]) if row else (0, GENESIS)

    def append(self, kind: str, body: Any, person_id: str | None = None, ref: str | None = None) -> dict:
        with self._lock:
            seq, prev = self.head()
            seq += 1
            at = iso(self.clock.now())
            h = entry_hash(prev, seq, at, kind, person_id, ref, body)
            self.conn.execute(
                "INSERT INTO ledger(seq, at, kind, person_id, ref, body, prev, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, at, kind, person_id, ref, canonical(body), prev, h),
            )
        return {"seq": seq, "at": at, "kind": kind, "person_id": person_id, "ref": ref, "body": body,
                "prev": prev, "hash": h}

    def entries(self, after: int = 0, limit: int = 500) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM ledger WHERE seq > ? ORDER BY seq LIMIT ?", (after, limit)).fetchall()
        return [as_dict(r) for r in rows]

    def entry(self, seq: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM ledger WHERE seq = ?", (seq,)).fetchone()
        return as_dict(row) if row else None

    def all(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM ledger ORDER BY seq").fetchall()

    def of_kind(self, *kinds: str) -> list[dict]:
        marks = ",".join("?" * len(kinds))
        return [as_dict(r) for r in self.conn.execute(
            f"SELECT * FROM ledger WHERE kind IN ({marks}) ORDER BY seq", kinds)]


def as_dict(row: sqlite3.Row) -> dict:
    return {"seq": row["seq"], "at": row["at"], "kind": row["kind"], "person_id": row["person_id"],
            "ref": row["ref"], "body": json.loads(row["body"]), "prev": row["prev"], "hash": row["hash"]}


def check_chain(entries: list[dict], prev: str = GENESIS, start_seq: int = 1) -> str | None:
    """None when the entries link and hash correctly from [prev]; otherwise what is wrong."""
    expected = start_seq
    for e in entries:
        if e["seq"] != expected:
            return f"entry {expected} is missing"
        if e["prev"] != prev:
            return f"entry {e['seq']} does not follow entry {e['seq'] - 1}"
        if entry_hash(prev, e["seq"], e["at"], e["kind"], e["person_id"], e["ref"], e["body"]) != e["hash"]:
            return f"entry {e['seq']} was changed"
        prev = e["hash"]
        expected += 1
    return None


def event_fields(row: sqlite3.Row) -> dict:
    """What a device sent, as stored. Server judgements (severity, second opinion, its own labels,
    review state) change later and are their own entries."""
    try:
        scores = json.loads(row["scores"]) if row["scores"] else None
    except ValueError:
        scores = row["scores"]
    if isinstance(scores, dict):
        scores = {k: v for k, v in scores.items() if k != "server_labels"} or None
    return {k: row[k] for k in ("id", "device_id", "person_id", "type", "occurred_at", "app", "url", "title",
                                "action", "region", "hash", "has_image", "detail", "sealed")} | {"scores": scores}


class Recorded:
    """Mixed into the stores whose changes go on the record. Context.build sets `ledger`."""

    ledger: LedgerStore | None = None

    def record(self, kind: str, body: Any, person_id: str | None = None, ref: str | None = None) -> None:
        if self.ledger is not None:
            self.ledger.append(kind, body, person_id, ref)
