"""Events, images and known-bad hashes."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from app.clock import Clock, iso

SEVERITY_INFO = "info"
SEVERITY_REVIEW = "review"
SEVERITY_EXPLICIT = "explicit"
SEVERITY_PROTECTION = "protection"


class EventsStore:
    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self.conn = conn
        self.clock = clock

    def exists(self, event_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is not None

    def insert(self, event: dict, device_id: str, person_id: str, severity: str,
               image_status: str) -> None:
        self.conn.execute(
            "INSERT INTO events(id, device_id, person_id, type, occurred_at, received_at, app, url, "
            "title, action, scores, region, hash, has_image, detail, image_status, severity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["id"], device_id, person_id, event["type"], event["occurred_at"],
                iso(self.clock.now()), event.get("app"), event.get("url"), event.get("title"),
                event.get("action", "none"),
                json.dumps(event.get("scores")) if event.get("scores") is not None else None,
                json.dumps(event.get("region")) if event.get("region") is not None else None,
                event.get("hash"), 1 if event.get("has_image") else 0,
                json.dumps(event.get("detail") or {}), image_status, severity,
            ),
        )

    def get(self, event_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    def set_second_opinion(self, event_id: str, score: float, severity: str, image_status: str) -> None:
        self.conn.execute(
            "UPDATE events SET second_opinion = ?, severity = ?, image_status = ? WHERE id = ?",
            (score, severity, image_status, event_id),
        )

    def set_image_status(self, event_id: str, status: str) -> None:
        self.conn.execute("UPDATE events SET image_status = ? WHERE id = ?", (status, event_id))

    def mark_alerted(self, event_id: str) -> None:
        self.conn.execute("UPDATE events SET alerted_at = ? WHERE id = ?", (iso(self.clock.now()), event_id))

    def review(self, event_id: str, state: str, login: str) -> None:
        if state not in ("open", "talked", "false_alarm"):
            raise ValueError(state)
        self.conn.execute(
            "UPDATE events SET review_state = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (state, login, iso(self.clock.now()), event_id),
        )

    def timeline(self, person_id: str, limit: int = 100, before: str | None = None) -> list[sqlite3.Row]:
        if before:
            return self.conn.execute(
                "SELECT * FROM events WHERE person_id = ? AND occurred_at < ? "
                "ORDER BY occurred_at DESC LIMIT ?",
                (person_id, before, limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM events WHERE person_id = ? ORDER BY occurred_at DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()

    def needing_review(self, person_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events WHERE person_id = ? AND severity IN ('review', 'explicit') "
            "AND review_state = 'open' ORDER BY occurred_at DESC",
            (person_id,),
        ).fetchall()

    def between(self, person_id: str, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events WHERE person_id = ? AND occurred_at > ? AND occurred_at <= ? "
            "ORDER BY occurred_at",
            (person_id, iso(start), iso(end)),
        ).fetchall()

    def count_since(self, person_id: str, since: datetime, severity: str | None = None) -> int:
        if severity:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE person_id = ? AND occurred_at >= ? AND severity = ?",
                (person_id, iso(since), severity),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE person_id = ? AND occurred_at >= ?",
                (person_id, iso(since)),
            ).fetchone()
        return int(row["n"])

    def hash_has_image(self, person_id: str, hash_: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM events WHERE person_id = ? AND hash = ? AND image_status = 'stored' LIMIT 1",
            (person_id, hash_),
        ).fetchone()
        return row is not None

    def purge_before(self, cutoff: datetime) -> int:
        cur = self.conn.execute("DELETE FROM events WHERE occurred_at < ?", (iso(cutoff),))
        return cur.rowcount

    # Images

    def add_image(self, event_id: str, width: int, height: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO images(event_id, created_at, width, height) VALUES (?, ?, ?, ?)",
            (event_id, iso(self.clock.now()), width, height),
        )

    def image(self, event_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM images WHERE event_id = ?", (event_id,)).fetchone()

    def images_before(self, cutoff: datetime) -> list[str]:
        return [r["event_id"] for r in self.conn.execute(
            "SELECT event_id FROM images WHERE created_at < ?", (iso(cutoff),)
        )]

    def remove_image(self, event_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM images WHERE event_id = ?", (event_id,))
            self.conn.execute(
                "UPDATE events SET image_status = 'expired' WHERE id = ? AND image_status = 'stored'",
                (event_id,),
            )

    # Known-bad hashes

    def add_known_bad(self, hash_: str, source: str, person_id: str | None = None) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO known_bad_hashes(hash, person_id, added_at, source) VALUES (?, ?, ?, ?)",
            (hash_, person_id, iso(self.clock.now()), source),
        )
        return cur.rowcount == 1

    def remove_known_bad_near(self, hash_: str, tolerance: int) -> int:
        from app.services.imagehash import hamming
        rows = self.conn.execute("SELECT hash FROM known_bad_hashes").fetchall()
        near = [r["hash"] for r in rows if hamming(r["hash"], hash_) <= tolerance]
        for h in near:
            self.conn.execute("DELETE FROM known_bad_hashes WHERE hash = ?", (h,))
        return len(near)

    def recent_image_hashes(self, device_id: str, since: datetime) -> list[str]:
        return [r["hash"] for r in self.conn.execute(
            "SELECT hash FROM events WHERE device_id = ? AND received_at >= ? AND hash IS NOT NULL",
            (device_id, iso(since)),
        )]

    def alerted_hashes(self, person_id: str, since: datetime) -> list[str]:
        return [r["hash"] for r in self.conn.execute(
            "SELECT hash FROM events WHERE person_id = ? AND alerted_at >= ? AND hash IS NOT NULL",
            (person_id, iso(since)),
        )]

    def known_bad(self, person_id: str) -> list[str]:
        return [r["hash"] for r in self.conn.execute(
            "SELECT hash FROM known_bad_hashes WHERE person_id IS NULL OR person_id = ? ORDER BY added_at",
            (person_id,),
        )]
