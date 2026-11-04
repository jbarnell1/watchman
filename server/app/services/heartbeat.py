"""Heartbeats, and the protection events the server derives from them.

Every state field that turns off between two heartbeats becomes a `protection_changed` event with
severity `protection`, which alerts. A safe-mode boot becomes a `tamper` event. Devices that stop
checking in are caught by the heartbeat watch job (jobs.py).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from app.clock import iso
from app.services import alerts, metrics
from app.services.context import Context
from app.services.policy import paused_until
from app.store.events_store import SEVERITY_INFO, SEVERITY_PROTECTION

ON_VALUES = {True, "granted", "not_applicable"}


def _is_off(value) -> bool:
    return value is not None and value not in ON_VALUES


def off_fields(state: dict) -> list[str]:
    return [k for k, v in (state or {}).items() if _is_off(v)]


def household_day(ctx: Context, when=None) -> str:
    try:
        tz = ZoneInfo(ctx.settings.load().timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    return (when or ctx.clock.now()).astimezone(tz).strftime("%Y-%m-%d")


def record(ctx: Context, device: sqlite3.Row, body: dict) -> dict:
    previous = json.loads(device["last_state"]) if device["last_state"] else None
    now = ctx.clock.now()
    work = metrics.clean(body.pop("metrics", None))
    with ctx.conn:
        ctx.devices.record_heartbeat(device["id"], body)
        if work and (work["counters"] or work["timings"]):
            day = household_day(ctx, now)
            have = ctx.devices.metrics_for(device["id"], day) or metrics.empty()
            ctx.devices.put_metrics(device["id"], day, metrics.merge(have, work))
        new_state = body.get("state") or {}
        old_state = (previous or {}).get("state") or {}
        derived: list[dict] = []
        for field, value in new_state.items():
            old = old_state.get(field)
            if previous is not None and old != value and (_is_off(value) or _is_off(old)):
                derived.append({
                    "id": str(uuid.uuid4()), "type": "protection_changed", "occurred_at": iso(now),
                    "action": "none", "detail": {"field": field, "from": old, "to": value},
                    "severity": SEVERITY_PROTECTION if _is_off(value) else SEVERITY_INFO,
                })
        # A field the device used to report and now leaves out or sends as null counts as off: a
        # stand-in client that sends less would otherwise look healthy (ADR-050).
        for field, old in old_state.items():
            if old is not None and new_state.get(field) is None:
                derived.append({
                    "id": str(uuid.uuid4()), "type": "protection_changed", "occurred_at": iso(now),
                    "action": "none", "detail": {"field": field, "from": old, "to": "not reported"},
                    "severity": SEVERITY_PROTECTION,
                })
        if body.get("safe_mode") and not (previous or {}).get("safe_mode"):
            derived.append({
                "id": str(uuid.uuid4()), "type": "tamper", "occurred_at": iso(now), "action": "none",
                "detail": {"reason": "safe_mode"}, "severity": SEVERITY_PROTECTION,
            })
        for ev in derived:
            severity = ev.pop("severity")
            ctx.events.insert(ev, device["id"], device["person_id"], severity, "none")
            if severity == SEVERITY_PROTECTION:
                alerts.protection_alert(ctx, device, ev)
        # The reminder clock (jobs.reminders) starts when a device goes off and stops when it is whole.
        if off_fields(new_state):
            if not device["reminded_at"] or not off_fields(old_state):
                ctx.devices.set_reminded(device["id"], iso(now))
        else:
            ctx.devices.set_reminded(device["id"], None)
    settings = ctx.settings.load()
    return {
        "policy_version": settings.policy_version,
        "paused_until": paused_until(ctx, device["person_id"]),
        "server_time": iso(now),
    }
