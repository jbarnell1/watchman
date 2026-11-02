"""Timed medium-blur peeks for linked partners (ADR-004)."""

from __future__ import annotations

from datetime import timedelta

from app.clock import iso, parse_iso
from app.services.context import Context
from app.services.errors import NotFound, RateLimited


def start_peek(ctx: Context, partner_id: str, event_id: str) -> dict:
    event = ctx.events.get(event_id)
    partner = ctx.people.get_partner(partner_id)
    if event is None or partner is None or event["person_id"] != partner["person_id"]:
        raise NotFound("no such item")
    if event["image_status"] != "stored":
        raise NotFound("no image for this item")
    settings = ctx.settings.load()
    last = ctx.codes.last_peek(partner_id)
    now = ctx.clock.now()
    if last:
        ready_at = parse_iso(last["at"]) + timedelta(hours=settings.peek_cooldown_hours)
        if ready_at > now:
            raise RateLimited(f"next peek at {iso(ready_at)}")
    token = ctx.codes.add_peek(event_id, partner_id, token_seconds=5)
    return {
        "url": f"/images/{event_id}/medium?t={token}",
        "seconds": settings.peek_seconds,
        "next_peek_at": iso(now + timedelta(hours=settings.peek_cooldown_hours)),
    }


def next_peek_at(ctx: Context, partner_id: str) -> str | None:
    last = ctx.codes.last_peek(partner_id)
    if not last:
        return None
    settings = ctx.settings.load()
    ready = parse_iso(last["at"]) + timedelta(hours=settings.peek_cooldown_hours)
    return iso(ready) if ready > ctx.clock.now() else None
