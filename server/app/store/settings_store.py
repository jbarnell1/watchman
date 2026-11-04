from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from pydantic import BaseModel, ValidationError

from app.clock import Clock, iso
from app.settings import POLICY_KEYS, Settings, coerce

log = logging.getLogger(__name__)


class SettingsStore:
    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self.conn = conn
        self.clock = clock

    def load(self) -> Settings:
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        raw = {r["key"]: json.loads(r["value"]) for r in rows}
        try:
            return Settings.model_validate(raw)
        except ValidationError:
            # A stored value from before a rule existed (a retention under the new floor, an unknown
            # zone) falls back to its default rather than stopping every page and job.
            good = {}
            for key, value in raw.items():
                try:
                    Settings.model_validate({key: value})
                    good[key] = value
                except ValidationError:
                    log.warning("setting %s has a value no longer allowed; using the default", key)
            return Settings.model_validate(good)

    def get(self, key: str) -> Any:
        return getattr(self.load(), key)

    def set(self, key: str, value: Any, login: str = "system") -> Any:
        """Store one setting. Records the change for the digest and bumps the policy version
        when devices need to know. Returns the typed value."""
        typed = coerce(key, value)
        current = self.load()
        old = getattr(current, key)
        if old == typed:
            return typed
        new_json = json.dumps(typed.model_dump() if isinstance(typed, BaseModel) else typed)
        old_json = json.dumps(old.model_dump() if isinstance(old, BaseModel) else old)
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, new_json),
            )
            if key != "policy_version":
                self.conn.execute(
                    "INSERT INTO settings_changes(at, login, key, old_value, new_value) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (iso(self.clock.now()), login, key, old_json, new_json),
                )
            if key in POLICY_KEYS:
                self.conn.execute(
                    "INSERT INTO settings(key, value) VALUES ('policy_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (json.dumps(current.policy_version + 1),),
                )
        return typed

    def bump_policy(self) -> int:
        version = self.load().policy_version + 1
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES ('policy_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(version),),
        )
        return version

    def unreported_changes(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM settings_changes WHERE reported = 0 ORDER BY at"
        ).fetchall()

    def mark_changes_reported(self, ids: list[int]) -> None:
        self.conn.executemany("UPDATE settings_changes SET reported = 1 WHERE id = ?", [(i,) for i in ids])
