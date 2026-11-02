"""Background jobs: heartbeat watch, digest schedule, retention purge.

`tick` does one round of all three and is what the scheduler loop and the tests call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import timedelta

from app.clock import iso, parse_iso
from app.services import alerts, digest
from app.services.context import Context
from app.store.events_store import SEVERITY_PROTECTION

log = logging.getLogger(__name__)


def heartbeat_watch(ctx: Context) -> int:
    """Raise one `tamper` event per device that has gone silent past the configured gap."""
    settings = ctx.settings.load()
    now = ctx.clock.now()
    cutoff = now - timedelta(minutes=settings.heartbeat_gap_minutes)
    raised = 0
    for device in ctx.devices.list_active():
        if not device["last_heartbeat_at"] or device["silent_reported_at"]:
            continue
        if parse_iso(device["last_heartbeat_at"]) > cutoff:
            continue
        state = (json.loads(device["last_state"]) or {}).get("state") or {}
        if state.get("protection") is False:
            continue  # already reported as off; silence adds nothing
        ev = {
            "id": str(uuid.uuid4()), "type": "tamper", "occurred_at": iso(now), "action": "none",
            "detail": {"reason": "silent", "last_heartbeat_at": device["last_heartbeat_at"],
                       "gap_minutes": settings.heartbeat_gap_minutes},
        }
        with ctx.conn:
            ctx.events.insert(ev, device["id"], device["person_id"], SEVERITY_PROTECTION, "none")
            ctx.devices.mark_silent_reported(device["id"])
        alerts.protection_alert(ctx, device, ev)
        raised += 1
    return raised


def digests_due(ctx: Context) -> int:
    now = ctx.clock.now()
    sent = 0
    for person in ctx.people.list_people():
        if digest.is_due(ctx, person["id"], now):
            if digest.send_digest(ctx, person["id"], now):
                sent += 1
    return sent


def retention(ctx: Context) -> tuple[int, int]:
    settings = ctx.settings.load()
    now = ctx.clock.now()
    images = ctx.events.images_before(now - timedelta(days=settings.image_retention_days))
    for event_id in images:
        ctx.blobs.delete(event_id)
        ctx.events.remove_image(event_id)
    cutoff = now - timedelta(days=settings.event_retention_days)
    purged = ctx.events.purge_before(cutoff)
    ctx.devices.purge_heartbeats_before(cutoff)
    ctx.devices.purge_metrics_before(cutoff.strftime("%Y-%m-%d"))
    return len(images), purged


def tick(ctx: Context) -> dict:
    out = {"silent": 0, "digests": 0, "images_removed": 0, "events_purged": 0}
    for name, fn in (("heartbeat", heartbeat_watch), ("digests", digests_due), ("retention", retention)):
        try:
            result = fn(ctx)
        except Exception as exc:
            log.exception("job %s failed: %s", name, exc)
            continue
        if name == "heartbeat":
            out["silent"] = result
        elif name == "digests":
            out["digests"] = result
        else:
            out["images_removed"], out["events_purged"] = result
    return out


async def run_forever(ctx: Context, interval_s: int = 60) -> None:
    while True:
        await asyncio.to_thread(tick, ctx)
        await asyncio.sleep(interval_s)
