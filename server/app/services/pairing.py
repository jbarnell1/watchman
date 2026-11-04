from __future__ import annotations

import uuid

from app.clock import iso
from app.services import alerts
from app.services.context import Context
from app.services.errors import ApiError
from app.store.events_store import SEVERITY_INFO

PLATFORMS = {"android", "windows", "ios", "chrome", "edge", "firefox", "firefox-android", "safari"}
KINDS = {"app", "service", "extension"}


def pair(ctx: Context, code: str, device: dict) -> dict:
    if device.get("platform") not in PLATFORMS or device.get("kind") not in KINDS:
        raise ApiError("unknown platform or kind")
    name = (device.get("name") or "").strip() or f"{device['platform']} {device['kind']}"
    with ctx.conn:
        person_id = ctx.devices.consume_pairing_code(code)
        if person_id is None:
            raise ApiError("pairing code is invalid or expired", code="pairing_code_invalid", status=400)
        device_id, token = ctx.devices.add_device(
            person_id, name, device["platform"], device["kind"], device.get("app_version")
        )
        ctx.events.insert({"id": str(uuid.uuid4()), "type": "protection_changed", "occurred_at": iso(ctx.clock.now()),
                           "action": "none", "detail": {"field": "device", "from": None, "to": "paired",
                                                        "name": name, "platform": device["platform"]}},
                          device_id, person_id, SEVERITY_INFO, "none")
    # A new device is how a stand-in client starts (ADR-050): everyone hears about it at once.
    person = ctx.people.get_person(person_id)
    alerts.change_alert(ctx, f"a device was paired for {person['name'] if person else 'someone'}", [
        f"{name} ({device['platform']} {device['kind']}) was paired for {person['name'] if person else 'someone'}.",
        "If nobody you know set up a new device just now, ask about it.",
    ], person_id)
    settings = ctx.settings.load()
    return {
        "device_id": device_id,
        "token": token,
        "person_id": person_id,
        "server_name": settings.household_name,
        "api_version": 1,
    }
