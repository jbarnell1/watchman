"""Devices and pairing codes."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta

from app.clock import Clock, iso, parse_iso

PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O/1/I/L


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_pairing_code() -> str:
    parts = ["".join(secrets.choice(PAIRING_ALPHABET) for _ in range(3)) for _ in range(3)]
    return "-".join(parts)


class DevicesStore:
    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self.conn = conn
        self.clock = clock

    # Pairing codes

    def create_pairing_code(self, person_id: str, valid_minutes: int = 15) -> str:
        now = self.clock.now()
        code = new_pairing_code()
        self.conn.execute(
            "INSERT INTO pairing_codes(code, person_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (code, person_id, iso(now), iso(now + timedelta(minutes=valid_minutes))),
        )
        return code

    def consume_pairing_code(self, code: str) -> str | None:
        """Return the person_id for a valid unused code and mark it used; None otherwise."""
        now = iso(self.clock.now())
        # Typed any way (lower case, no dashes, spaces): compare on the nine characters alone.
        chars = "".join(c for c in (code or "").upper() if c in PAIRING_ALPHABET)
        if len(chars) != 9:
            return None
        code = f"{chars[:3]}-{chars[3:6]}-{chars[6:]}"
        row = self.conn.execute(
            "SELECT person_id FROM pairing_codes WHERE code = ? AND used_at IS NULL AND expires_at > ?",
            (code, now),
        ).fetchone()
        if row is None:
            return None
        self.conn.execute("UPDATE pairing_codes SET used_at = ? WHERE code = ?", (now, code))
        return row["person_id"]

    def open_pairing_codes(self, person_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM pairing_codes WHERE person_id = ? AND used_at IS NULL AND expires_at > ? "
            "ORDER BY created_at DESC",
            (person_id, iso(self.clock.now())),
        ).fetchall()

    # Devices

    def add_device(self, person_id: str, name: str, platform: str, kind: str,
                   app_version: str | None) -> tuple[str, str]:
        """Create a device and return (device_id, plaintext token). The token is stored hashed."""
        device_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        self.conn.execute(
            "INSERT INTO devices(id, person_id, name, platform, kind, app_version, token_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (device_id, person_id, name, platform, kind, app_version, hash_token(token),
             iso(self.clock.now())),
        )
        return device_id, token

    def by_token(self, token: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM devices WHERE token_hash = ? AND revoked_at IS NULL", (hash_token(token),)
        ).fetchone()

    def get(self, device_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()

    def list_for(self, person_id: str, include_revoked: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM devices WHERE person_id = ?"
        if not include_revoked:
            sql += " AND revoked_at IS NULL"
        return self.conn.execute(sql + " ORDER BY created_at", (person_id,)).fetchall()

    def list_active(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM devices WHERE revoked_at IS NULL").fetchall()

    def revoke(self, device_id: str) -> None:
        self.conn.execute("UPDATE devices SET revoked_at = ? WHERE id = ?", (iso(self.clock.now()), device_id))

    def record_heartbeat(self, device_id: str, body: dict) -> None:
        now = iso(self.clock.now())
        protection = 1 if (body.get("state") or {}).get("protection") else 0
        self.conn.execute(
            "UPDATE devices SET last_heartbeat_at = ?, last_state = ?, app_version = ?, "
            "silent_reported_at = NULL WHERE id = ?",
            (now, json.dumps(body), body.get("app_version"), device_id),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO heartbeats(device_id, at, protection) VALUES (?, ?, ?)",
            (device_id, now, protection),
        )

    def metrics_for(self, device_id: str, day: str) -> dict | None:
        row = self.conn.execute("SELECT data FROM device_metrics WHERE device_id = ? AND day = ?",
                                (device_id, day)).fetchone()
        return json.loads(row["data"]) if row else None

    def put_metrics(self, device_id: str, day: str, data: dict) -> None:
        self.conn.execute(
            "INSERT INTO device_metrics(device_id, day, data, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(device_id, day) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (device_id, day, json.dumps(data), iso(self.clock.now())),
        )

    def purge_metrics_before(self, day: str) -> int:
        return self.conn.execute("DELETE FROM device_metrics WHERE day < ?", (day,)).rowcount

    def heartbeats_between(self, person_id: str, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT h.device_id, h.at, h.protection FROM heartbeats h JOIN devices d ON d.id = h.device_id "
            "WHERE d.person_id = ? AND h.at >= ? AND h.at < ? ORDER BY h.at",
            (person_id, iso(start), iso(end)),
        ).fetchall()

    def protected_minutes(self, person_id: str, start: datetime, end: datetime, slot_minutes: int = 5) -> int:
        """Minutes with at least one device reporting protection on, counted in 5 minute slots."""
        slots: set[int] = set()
        base = int(start.timestamp())
        for h in self.heartbeats_between(person_id, start, end):
            if h["protection"]:
                slots.add((int(parse_iso(h["at"]).timestamp()) - base) // (slot_minutes * 60))
        return len(slots) * slot_minutes

    def purge_heartbeats_before(self, cutoff: datetime) -> int:
        return self.conn.execute("DELETE FROM heartbeats WHERE at < ?", (iso(cutoff),)).rowcount

    def set_reminded(self, device_id: str, when: str | None) -> None:
        self.conn.execute("UPDATE devices SET reminded_at = ? WHERE id = ?", (when, device_id))

    def mark_silent_reported(self, device_id: str) -> None:
        now = iso(self.clock.now())
        self.conn.execute(
            "UPDATE devices SET silent_reported_at = ?, reminded_at = ? WHERE id = ?", (now, now, device_id)
        )

    def protection_on(self, person_id: str | None = None) -> bool:
        """Protection counts as on once any device is paired. Locked settings apply from then."""
        if person_id:
            row = self.conn.execute(
                "SELECT 1 FROM devices WHERE person_id = ? AND revoked_at IS NULL LIMIT 1", (person_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT 1 FROM devices WHERE revoked_at IS NULL LIMIT 1").fetchone()
        return row is not None
