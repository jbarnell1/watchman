"""Timed medium-blur peeks for linked partners (ADR-004), and for the protected person at their own
items when the household turned self_peek on (ADR-048)."""

from __future__ import annotations

from datetime import timedelta

from app.clock import iso, parse_iso
from app.services.context import Context
from app.services.errors import NotFound, RateLimited


def start_peek(ctx: Context, partner_id: str | None, event_id: str, person_id: str | None = None) -> dict:
    """A partner's peek, or with [person_id] (and no partner) the protected person's own."""
    event = ctx.events.get(event_id)
    if person_id:
        if event is None or event["person_id"] != person_id:
            raise NotFound("no such item")
    else:
        partner = ctx.people.get_partner(partner_id)
        if event is None or partner is None or event["person_id"] != partner["person_id"]:
            raise NotFound("no such item")
    if event["image_status"] != "stored":
        raise NotFound("no image for this item")
    settings = ctx.settings.load()
    last = ctx.codes.last_peek(partner_id, person_id)
    now = ctx.clock.now()
    cooldown = timedelta(seconds=settings.peek_cooldown_seconds)
    if last:
        ready_at = parse_iso(last["at"]) + cooldown
        if ready_at > now:
            raise RateLimited(f"next peek at {iso(ready_at)}")
    token = ctx.codes.add_peek(event_id, partner_id, token_seconds=5, person_id=person_id)
    return {
        "url": f"/images/{event_id}/medium?t={token}",
        "seconds": settings.peek_seconds,
        "next_peek_at": iso(now + cooldown),
    }


def next_peek_at(ctx: Context, partner_id: str | None, person_id: str | None = None) -> str | None:
    last = ctx.codes.last_peek(partner_id, person_id)
    if not last:
        return None
    settings = ctx.settings.load()
    ready = parse_iso(last["at"]) + timedelta(seconds=settings.peek_cooldown_seconds)
    return iso(ready) if ready > ctx.clock.now() else None
