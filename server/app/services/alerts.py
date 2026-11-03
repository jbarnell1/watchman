"""Immediate alerts: confirmed explicit content and protection turned off (ADR-005).

Sent to the protected person and every partner. Email is the channel; ntfy, when configured,
is a short extra ping for the household admin. Failures to send never fail the request that
triggered them; they are logged and the event stays visible on the portal.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request

from app.clients.mailer import Attachment, Mail
from app.services.context import Context

log = logging.getLogger(__name__)

FIELD_WORDS = {
    "protection": "protection", "accessibility": "the accessibility service", "capture": "screen capture",
    "overlay": "the overlay", "device_admin": "device admin", "extension": "the browser extension",
    "vpn": "the VPN",
}


def recipients(ctx: Context, person_id: str) -> list[str]:
    person = ctx.people.get_person(person_id)
    emails = [p["email"] for p in ctx.people.partners_for(person_id) if p["email"]]
    if person and person["email"]:
        emails.insert(0, person["email"])
    return list(dict.fromkeys(e.lower() for e in emails))


def _send(ctx: Context, mail: Mail) -> bool:
    try:
        ctx.mailer.send(mail)
        return True
    except Exception as exc:  # the alert must not break intake
        log.warning("alert email failed: %s", exc)
        return False


def _ntfy(ctx: Context, title: str, body: str) -> None:
    url = ctx.settings.load().ntfy_url
    if not url:
        return
    try:
        req = urllib.request.Request(url, data=body.encode(), headers={"Title": title, "Priority": "high"})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as exc:
        log.warning("ntfy failed: %s", exc)


def protection_alert(ctx: Context, device: sqlite3.Row, event: dict) -> None:
    person = ctx.people.get_person(device["person_id"])
    name = person["name"] if person else "the protected person"
    detail = event.get("detail") or {}
    if event["type"] == "tamper":
        what = {"safe_mode": "booted into safe mode", "silent": "has stopped checking in"}.get(
            detail.get("reason"), f"reported tampering ({detail.get('reason')})")
        line = f"{device['name']} {what}."
    elif event["type"] == "block_passed":
        line = (f"{device['name']}: {name} chose Continue anyway on a screen that was mostly covered"
                f" in {event.get('app') or 'an app'}. The pictures stayed blurred.")
    else:
        word = FIELD_WORDS.get(detail.get("field"), detail.get("field"))
        line = f"{device['name']}: {word} turned off ({detail.get('from')} to {detail.get('to')})."
    subject = f"Watchman: protection changed on {name}'s {device['name']}"
    if event["type"] == "block_passed":
        subject = f"Watchman: {name} continued past a block on {device['name']}"
    text = (f"{line}\n\nTime: {event['occurred_at']}\n\n"
            f"This alert goes to {name} and every partner. Nothing pauses protection without a partner code.")
    to = recipients(ctx, device["person_id"])
    if to:
        _send(ctx, Mail(to=to, subject=subject, text=text))
    _ntfy(ctx, "Watchman: protection changed", line)


def explicit_alert(ctx: Context, event: sqlite3.Row, heavy_webp: bytes | None) -> None:
    person = ctx.people.get_person(event["person_id"])
    name = person["name"] if person else "the protected person"
    device = ctx.devices.get(event["device_id"])
    scores = json.loads(event["scores"]) if event["scores"] else {}
    where = event["app"] or (device["name"] if device else "a device")
    if event["url"]:
        where += f" ({event['url']})"
    subject = f"Watchman: explicit image covered on {name}'s {device['name'] if device else 'device'}"
    text = (f"An explicit image was covered at {event['occurred_at']} in {where}.\n"
            f"Device score {scores.get('classifier', 0):.2f}, second opinion {event['second_opinion']:.2f}.\n\n"
            f"The image was blurred before anyone saw it and the original has been deleted. "
            f"Partners can review it on the portal.")
    inline = []
    html = None
    if heavy_webp:
        inline.append(Attachment(cid="heavy", data=heavy_webp))
        html = (f"<p>An explicit image was covered at {event['occurred_at']} in {where}.</p>"
                f"<p><img src=\"cid:heavy\" width=\"160\" alt=\"blurred image\"></p>"
                f"<p>Device score {scores.get('classifier', 0):.2f}, second opinion "
                f"{event['second_opinion']:.2f}. The original has been deleted.</p>")
    to = recipients(ctx, event["person_id"])
    if to:
        _send(ctx, Mail(to=to, subject=subject, text=text, html=html, inline=inline))
    ctx.events.mark_alerted(event["id"])
    _ntfy(ctx, "Watchman: explicit image covered", f"{where}, {event['occurred_at']}")
