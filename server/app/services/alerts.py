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
    "vpn": "the VPN", "usb_debugging": "USB debugging", "device": "the device",
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
        what = {"safe_mode": "booted into safe mode", "silent": "has stopped checking in",
                "code_guessing": "had ten wrong partner codes typed within an hour",
                "not_judging": (f"was on and watched for {detail.get('watched_minutes')} minutes in the last hour "
                                "but judged nothing"),
                "page_unblur": f"had a web page remove Watchman's blurs ({event.get('url') or 'a page'})"}.get(
            detail.get("reason"), f"reported tampering ({detail.get('reason')})")
        line = f"{device['name']} {what}."
    elif event["type"] == "protection_gap":
        line = (f"{device['name']} was unprotected from {detail.get('from')} to {detail.get('to')}"
                f"{' (it restarted)' if detail.get('reason') == 'reboot' else ''}. "
                "It said so when it came back.")
    elif event["type"] == "block_passed":
        line = (f"{device['name']}: {name} chose Continue anyway on a screen that was mostly covered"
                f" in {event.get('app') or 'an app'}. The pictures stayed blurred.")
    else:
        word = FIELD_WORDS.get(detail.get("field"), detail.get("field"))
        if detail.get("field") == "usb_debugging":
            line = (f"{device['name']}: USB debugging turned on. A computer can now install apps and change "
                    "settings on it without the screen.")
        else:
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


def household_recipients(ctx: Context) -> list[str]:
    """Every protected person and every partner on this server: household settings reach all."""
    out: list[str] = []
    for person in ctx.people.list_people():
        out.extend(recipients(ctx, person["id"]))
    return list(dict.fromkeys(out))


def change_alert(ctx: Context, subject: str, lines: list[str], person_id: str | None = None) -> bool:
    """A change that weakens protection or accountability, sent at once instead of waiting for the
    digest (ADR-050): loosened settings, a device paired or removed, a partner added or removed.
    Sent before the change is applied where the change could stop the mail, so it goes out through
    the mail settings that were in place."""
    to = recipients(ctx, person_id) if person_id else household_recipients(ctx)
    text = "\n".join(lines) + ("\n\nThis alert goes to the protected person and every partner. "
                               "Every change is also in the next digest.")
    _ntfy(ctx, subject, lines[0] if lines else subject)
    if not to:
        return False
    return _send(ctx, Mail(to=to, subject=f"Watchman: {subject}", text=text))
