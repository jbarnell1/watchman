from __future__ import annotations

from app.services.context import Context
from app.services.errors import ApiError

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
    settings = ctx.settings.load()
    return {
        "device_id": device_id,
        "token": token,
        "person_id": person_id,
        "server_name": settings.household_name,
        "api_version": 1,
    }
