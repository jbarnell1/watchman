"""Unlock codes, pauses, unlock requests, peeks and digests."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta

from app.clock import Clock, iso
from app.store.ledger_store import Recorded


def hash_code(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").encode()).hexdigest()


def new_unlock_code() -> str:
    digits = f"{secrets.randbelow(1_000_000):06d}"
    return f"{digits[:3]}-{digits[3:]}"


class CodesStore(Recorded):
    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self.conn = conn
        self.clock = clock

    # Unlock codes

    def create_code(self, person_id: str, partner_id: str, minutes: int, purpose: str = "pause",
                    valid_hours: int = 24, approval: dict | None = None) -> tuple[str, str]:
        now = self.clock.now()
        code = new_unlock_code()
        cid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO unlock_codes(id, code_hash, person_id, partner_id, minutes, purpose, created_at, "
            "expires_at, signed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, hash_code(code), person_id, partner_id, minutes, purpose, iso(now),
             iso(now + timedelta(hours=valid_hours)), 1 if approval else 0),
        )
        body = {"partner_id": partner_id, "purpose": purpose, "minutes": minutes}
        if approval:
            body["approval"] = approval
        self.record("code_issued", body, person_id, cid)
        return cid, code

    def consume_code(self, person_id: str, code: str, purpose: str = "pause") -> sqlite3.Row | None:
        now = iso(self.clock.now())
        # A code counts only from a partner past their waiting period (ADR-050).
        row = self.conn.execute(
            "SELECT c.* FROM unlock_codes c JOIN partners p ON p.id = c.partner_id "
            "WHERE c.code_hash = ? AND c.person_id = ? AND c.purpose = ? "
            "AND c.used_at IS NULL AND c.expires_at > ? AND (p.trusted_at IS NULL OR p.trusted_at <= ?)",
            (hash_code(code), person_id, purpose, now, now),
        ).fetchone()
        if row is None:
            return None
        self.conn.execute("UPDATE unlock_codes SET used_at = ? WHERE id = ?", (now, row["id"]))
        self.record("code_used", {"purpose": purpose}, person_id, row["id"])
        return row

    def open_codes(self, person_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT c.*, p.name AS partner_name FROM unlock_codes c JOIN partners p ON p.id = c.partner_id "
            "WHERE c.person_id = ? AND c.used_at IS NULL AND c.expires_at > ? ORDER BY c.created_at DESC",
            (person_id, iso(self.clock.now())),
        ).fetchall()

    # Pauses

    def start_pause(self, person_id: str, code_id: str, minutes: int) -> datetime:
        now = self.clock.now()
        ends = now + timedelta(minutes=minutes)
        self.conn.execute(
            "INSERT INTO pauses(id, person_id, code_id, started_at, ends_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), person_id, code_id, iso(now), iso(ends)),
        )
        self.record("pause", {"code_id": code_id, "minutes": minutes, "ends_at": iso(ends)}, person_id)
        return ends

    def active_pause(self, person_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM pauses WHERE person_id = ? AND ends_at > ? ORDER BY ends_at DESC LIMIT 1",
            (person_id, iso(self.clock.now())),
        ).fetchone()

    def pauses_between(self, person_id: str, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT pa.*, p.name AS partner_name, c.minutes FROM pauses pa "
            "JOIN unlock_codes c ON c.id = pa.code_id JOIN partners p ON p.id = c.partner_id "
            "WHERE pa.person_id = ? AND pa.started_at > ? AND pa.started_at <= ? ORDER BY pa.started_at",
            (person_id, iso(start), iso(end)),
        ).fetchall()

    # Unlock requests from the protected person

    def add_request(self, person_id: str, device_id: str, minutes: int, reason: str | None) -> str:
        rid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO unlock_requests(id, person_id, device_id, minutes, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rid, person_id, device_id, minutes, reason, iso(self.clock.now())),
        )
        return rid

    def recent_requests(self, person_id: str, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM unlock_requests WHERE person_id = ? ORDER BY created_at DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()

    # Peeks. The protected person's own peeks (ADR-048) live in self_peeks, only for the cooldown:
    # they are not shown to partners.

    def last_peek(self, partner_id: str | None = None, person_id: str | None = None) -> sqlite3.Row | None:
        """A partner's last peek, or with [person_id] the protected person's own last peek (ADR-048)."""
        table, col, who = _peek_table(partner_id, person_id)
        return self.conn.execute(
            f"SELECT * FROM {table} WHERE {col} = ? ORDER BY at DESC LIMIT 1", (who,)
        ).fetchone()

    def add_peek(self, event_id: str, partner_id: str | None = None, token_seconds: int = 5,
                 person_id: str | None = None) -> str:
        now = self.clock.now()
        token = secrets.token_urlsafe(24)
        table, col, who = _peek_table(partner_id, person_id)
        self.conn.execute(
            f"INSERT INTO {table}(id, event_id, {col}, at, token_hash, token_expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, who, iso(now), hash_code(token),
             iso(now + timedelta(seconds=token_seconds))),
        )
        return token

    def consume_peek_token(self, event_id: str, token: str) -> bool:
        now = iso(self.clock.now())
        for table in ("peeks", "self_peeks"):
            row = self.conn.execute(
                f"SELECT id FROM {table} WHERE event_id = ? AND token_hash = ? AND token_used_at IS NULL "
                "AND token_expires_at > ?",
                (event_id, hash_code(token), now),
            ).fetchone()
            if row is not None:
                self.conn.execute(f"UPDATE {table} SET token_used_at = ? WHERE id = ?", (now, row["id"]))
                return True
        return False

    def peeks_for_person(self, person_id: str, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT pk.*, p.name AS partner_name, e.app, e.occurred_at AS event_at FROM peeks pk "
            "JOIN partners p ON p.id = pk.partner_id JOIN events e ON e.id = pk.event_id "
            "WHERE e.person_id = ? ORDER BY pk.at DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()

    def peeks_between(self, person_id: str, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT pk.*, p.name AS partner_name FROM peeks pk "
            "JOIN partners p ON p.id = pk.partner_id JOIN events e ON e.id = pk.event_id "
            "WHERE e.person_id = ? AND pk.at > ? AND pk.at <= ? ORDER BY pk.at",
            (person_id, iso(start), iso(end)),
        ).fetchall()

    # Digests

    def last_digest(self, person_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM digests WHERE person_id = ? ORDER BY sent_at DESC LIMIT 1", (person_id,)
        ).fetchone()

    def record_digest(self, person_id: str, start: datetime, end: datetime) -> None:
        self.conn.execute(
            "INSERT INTO digests(id, person_id, period_start, period_end, sent_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), person_id, iso(start), iso(end), iso(self.clock.now())),
        )
        self.record("digest_sent", {"period_start": iso(start), "period_end": iso(end)}, person_id)


def _peek_table(partner_id: str | None, person_id: str | None) -> tuple[str, str, str | None]:
    return ("self_peeks", "person_id", person_id) if person_id else ("peeks", "partner_id", partner_id)
