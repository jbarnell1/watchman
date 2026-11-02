"""Pause codes (ADR-007). Only a partner can issue one; a device redeems it; everyone hears about it."""

from __future__ import annotations

import sqlite3
import uuid

from app.clients.mailer import Mail
from app.clock import iso
from app.services.alerts import _send
from app.services.context import Context
from app.services.errors import ApiError
from app.store.events_store import SEVERITY_INFO


def issue_code(ctx: Context, person_id: str, partner_id: str, minutes: int) -> str:
    settings = ctx.settings.load()
    if minutes < 1 or minutes > settings.max_pause_minutes:
        raise ApiError(f"a code may grant 1 to {settings.max_pause_minutes} minutes")
    _, code = ctx.codes.create_code(person_id, partner_id, minutes)
    return code


def redeem(ctx: Context, device: sqlite3.Row, code: str) -> dict:
    with ctx.conn:
        row = ctx.codes.consume_code(device["person_id"], code)
        if row is None:
            raise ApiError("unlock code is invalid, used or expired", code="unlock_code_invalid")
        ends = ctx.codes.start_pause(device["person_id"], row["id"], row["minutes"])
        partner = ctx.people.get_partner(row["partner_id"])
        ctx.events.insert({
            "id": str(uuid.uuid4()), "type": "pause_started", "occurred_at": iso(ctx.clock.now()),
            "action": "none",
            "detail": {"minutes": row["minutes"], "ends_at": iso(ends),
                       "partner": partner["name"] if partner else None},
        }, device["id"], device["person_id"], SEVERITY_INFO, "none")
        ctx.settings.bump_policy()
    return {"paused_until": iso(ends)}


def request_code(ctx: Context, device: sqlite3.Row, minutes: int, reason: str | None) -> None:
    settings = ctx.settings.load()
    if minutes < 1 or minutes > settings.max_pause_minutes:
        raise ApiError(f"a pause may be 1 to {settings.max_pause_minutes} minutes")
    person = ctx.people.get_person(device["person_id"])
    name = person["name"] if person else "The protected person"
    with ctx.conn:
        ctx.codes.add_request(device["person_id"], device["id"], minutes, reason)
        for partner in ctx.people.partners_for(device["person_id"]):
            if partner["link_type"] == "email":
                _, code = ctx.codes.create_code(device["person_id"], partner["id"], minutes)
                text = (f"{name} is asking for a {minutes} minute pause of Watchman on {device['name']}.\n"
                        f"Reason given: {reason or 'none'}.\n\n"
                        f"If you agree, give them this one-time code: {code}\n"
                        f"It pauses protection for {minutes} minutes and expires in 24 hours. "
                        f"If you do not agree, do nothing.")
            else:
                text = (f"{name} is asking for a {minutes} minute pause of Watchman on {device['name']}.\n"
                        f"Reason given: {reason or 'none'}.\n\n"
                        f"Open the portal and issue a code from the Codes page if you agree.")
            _send(ctx, Mail(to=[partner["email"]], subject=f"Watchman: {name} asks for a pause", text=text))
