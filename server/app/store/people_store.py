"""People (the protected), partners, and portal logins."""

from __future__ import annotations

import sqlite3
import uuid

from app.clock import Clock, iso
from app.store.ledger_store import Recorded


class PeopleStore(Recorded):
    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self.conn = conn
        self.clock = clock

    # People

    def add_person(self, name: str, email: str | None = None, login: str | None = None) -> str:
        pid = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                "INSERT INTO people(id, name, email, created_at) VALUES (?, ?, ?, ?)",
                (pid, name, email, iso(self.clock.now())),
            )
            self.record("person_added", {"name": name, "email": email}, pid, pid)
            if login:
                self.add_login(login, "protected", pid)
        return pid

    def get_person(self, person_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()

    def list_people(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM people ORDER BY created_at").fetchall()

    def update_person(self, person_id: str, name: str, email: str | None) -> None:
        self.conn.execute("UPDATE people SET name = ?, email = ? WHERE id = ?", (name, email, person_id))
        self.record("person_changed", {"name": name, "email": email}, person_id, person_id)

    # Logins

    def add_login(self, login: str, role: str, person_id: str | None = None,
                  partner_id: str | None = None) -> None:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO logins(login, role, person_id, partner_id) VALUES (?, ?, ?, ?)",
            (login.lower(), role, person_id, partner_id),
        )
        if cur.rowcount:
            self.record("login_added", {"login": login.lower(), "role": role, "partner_id": partner_id}, person_id)

    def roles_for(self, login: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM logins WHERE login = ?", (login.lower(),)
        ).fetchall()

    def admins(self) -> list[str]:
        return [r["login"] for r in self.conn.execute("SELECT login FROM logins WHERE role = 'admin'")]

    def has_admin(self) -> bool:
        return self.conn.execute("SELECT 1 FROM logins WHERE role = 'admin' LIMIT 1").fetchone() is not None

    def remove_login(self, login: str, role: str, person_id: str | None) -> None:
        cur = self.conn.execute(
            "DELETE FROM logins WHERE login = ? AND role = ? AND person_id IS ?",
            (login.lower(), role, person_id),
        )
        if cur.rowcount:
            self.record("login_removed", {"login": login.lower(), "role": role}, person_id)

    # Partners

    def add_partner(self, person_id: str, name: str, email: str, link_type: str,
                    login: str | None = None, trusted_at: str | None = None, approval: dict | None = None) -> str:
        """[trusted_at]: when this partner's codes start to count; None is at once (ADR-050)."""
        if link_type not in ("linked", "email"):
            raise ValueError("link_type must be linked or email")
        if link_type == "linked" and not login:
            raise ValueError("a linked partner needs a Tailscale login")
        pid = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                "INSERT INTO partners(id, person_id, name, email, link_type, login, created_at, trusted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, person_id, name, email, link_type, login.lower() if login else None,
                 iso(self.clock.now()), trusted_at),
            )
            body = {"name": name, "email": email, "link_type": link_type,
                    "login": login.lower() if login else None, "trusted_at": trusted_at}
            if approval:
                body["approval"] = approval
            self.record("partner_added", body, person_id, pid)
            if login:
                self.add_login(login, "partner", person_id, pid)
        return pid

    def is_trusted(self, partner: sqlite3.Row) -> bool:
        return not partner["trusted_at"] or partner["trusted_at"] <= iso(self.clock.now())

    def get_partner(self, partner_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()

    def partners_for(self, person_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM partners WHERE person_id = ? ORDER BY created_at", (person_id,)
        ).fetchall()

    def partner_for_login(self, login: str, person_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM partners WHERE login = ? AND person_id = ?", (login.lower(), person_id)
        ).fetchone()

    def remove_partner(self, partner_id: str, approval: dict | None = None) -> None:
        row = self.get_partner(partner_id)
        if row is None:
            return
        with self.conn:
            if row["login"]:
                self.conn.execute(
                    "DELETE FROM logins WHERE partner_id = ?", (partner_id,)
                )
            self.conn.execute("DELETE FROM partners WHERE id = ?", (partner_id,))
            body = {"name": row["name"], "email": row["email"]}
            if approval:
                body["approval"] = approval
            self.record("partner_removed", body, row["person_id"], partner_id)
            self.conn.execute("DELETE FROM partner_keys WHERE partner_id = ?", (partner_id,))
