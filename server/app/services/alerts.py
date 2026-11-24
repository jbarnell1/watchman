"""Immediate alerts: confirmed explicit content and protection turned off (ADR-005).

Sent to the protected person and every partner. Email is the channel; ntfy, when configured,
is a short extra ping for the household admin. Failures to send never fail the request that
triggered them; they are counted, and every portal page says so until mail goes out again (ADR-058).

Protection changes on one device are emailed at most once per QUIET window: a device whose
accessibility service flaps sends the first change at once, then a count with the state it ended
in when the window has passed (ADR-058). Every change is still its own event in the record.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
from datetime import timedelta

from app.clients.mailer import Attachment, Mail
from app.clock import iso, parse_iso
from app.services.context import Context

log = logging.getLogger(__name__)

QUIET = timedelta(minutes=10)
NOT_SET_UP = "Email is not set up"

FIELD_WORDS = {
    "protection": "protection", "accessibility": "the accessibility service", "capture": "screen capture",
    "overlay": "the overlay", "device_admin": "device admin", "extension": "the browser extension",
    "vpn": "the VPN", "usb_debugging": "USB debugging", "device": "the device",
}


def recipients(ctx: Context, person_id: str, kind: str | None = None) -> list[str]:
    """The protected person and every partner. With `kind` (`image` or `protection`), only those
    who chose to hear about it right away; the rest read it in the weekly digest (ADR-062)."""
    column = {"image": "image_alerts", "protection": "protection_alerts"}.get(kind or "")

    def now(row: sqlite3.Row) -> bool:
        return column is None or row[column] == "now"

    person = ctx.people.get_person(person_id)
    emails = [p["email"] for p in ctx.people.partners_for(person_id) if p["email"] and now(p)]
    if person and person["email"] and now(person):
        emails.insert(0, person["email"])
    return list(dict.fromkeys(e.lower() for e in emails))


def send(ctx: Context, mail: Mail, what: str = "alert") -> bool:
    """Send, and count what did not go out so the portal can say so (ADR-058)."""
    if not ctx.mailer.configured():
        log.warning("%s email not sent: email is not set up", what)
        ctx.settings.mail_failed(NOT_SET_UP)
        return False
    try:
        ctx.mailer.send(mail)
    except Exception as exc:  # the alert must not break intake
        log.warning("%s email failed: %s", what, exc)
        ctx.settings.mail_failed(f"{type(exc).__name__}: {exc}")
        return False
    ctx.settings.mail_sent()
    return True


def _send(ctx: Context, mail: Mail) -> bool:
    # Every email carries the record's latest hash: a copy outside the server (ADR-052).
    from app.services.ledger import head_line
    mail.text = mail.text + "\n\n" + head_line(ctx)
    return send(ctx, mail)


def _ntfy(ctx: Context, title: str, body: str, kind: str | None = None) -> None:
    settings = ctx.settings.load()
    url = settings.ntfy_url
    if not url or (kind == "image" and not settings.push_images) or (
            kind == "protection" and not settings.push_protection):
        return
    try:
        req = urllib.request.Request(url, data=body.encode(), headers={"Title": title, "Priority": "high"})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as exc:
        log.warning("ntfy failed: %s", exc)


def protection_alert(ctx: Context, device: sqlite3.Row, event: dict) -> None:
    protection_alerts(ctx, device, [event])


def protection_alerts(ctx: Context, device: sqlite3.Row, events: list[dict]) -> None:
    """One email for everything one heartbeat or report turned off. Changes inside the quiet
    window after the last email are held and counted for `held_summaries`."""
    if not events:
        return
    fresh = ctx.devices.get(device["id"]) or device
    now = ctx.clock.now()
    changes = [e for e in events if e["type"] == "protection_changed"]
    if (len(changes) == len(events) and fresh["alerted_at"]
            and now - parse_iso(fresh["alerted_at"]) < QUIET):
        ctx.devices.hold_alerts(device["id"], 1, iso(now))
        return
    person = ctx.people.get_person(device["person_id"])
    name = person["name"] if person else "the protected person"
    lines = [_line(device, e, name) for e in events]
    subject = f"Watchman: protection changed on {name}'s {device['name']}"
    if len(events) == 1 and events[0]["type"] == "block_passed":
        subject = f"Watchman: {name} continued past a block on {device['name']}"
    held = fresh["alerts_held"] or 0
    extra = (f"\n\nBefore this, something turned off {held} more time{'s' if held != 1 else ''} since "
             f"{fresh['held_since']}; every change is on the portal's timeline.") if held else ""
    text = ("\n".join(lines) + extra + f"\n\nTime: {events[0]['occurred_at']}\n\n"
            f"This alert goes to {name} and every partner. Nothing pauses protection without a partner code.")
    to = recipients(ctx, device["person_id"], "protection")
    if to:
        _send(ctx, Mail(to=to, subject=subject, text=text))
    _ntfy(ctx, "Watchman: protection changed", lines[0], "protection")
    if changes:
        ctx.devices.set_alerted(device["id"], iso(now))


def held_summaries(ctx: Context) -> int:
    """After the quiet window, one email per device with how many changes were held and the
    state the device is in now."""
    from app.services.heartbeat import off_fields

    now = ctx.clock.now()
    sent = 0
    for device in ctx.devices.list_active():
        held = device["alerts_held"] or 0
        if not held or (device["alerted_at"] and now - parse_iso(device["alerted_at"]) < QUIET):
            continue
        person = ctx.people.get_person(device["person_id"])
        name = person["name"] if person else "the protected person"
        state = (json.loads(device["last_state"] or "null") or {}).get("state") or {}
        off = off_fields(state)
        now_line = ("Protection is on now." if not off else
                    "Off now: " + ", ".join(FIELD_WORDS.get(f, f) for f in off) + ".")
        line = (f"{device['name']}: something turned off {held} more time{'s' if held != 1 else ''} "
                f"since {device['held_since']}. {now_line}")
        to = recipients(ctx, device["person_id"], "protection")
        if to:
            _send(ctx, Mail(to=to, subject=f"Watchman: protection changed on {name}'s {device['name']}",
                            text=line + "\n\nEvery change is on the portal's timeline. This alert goes to "
                                        f"{name} and every partner."))
        _ntfy(ctx, "Watchman: protection changed", line, "protection")
        ctx.devices.set_alerted(device["id"], iso(now))
        sent += 1
    return sent


def back_alert(ctx: Context, device: sqlite3.Row, event: dict) -> None:
    """A device that was reported silent is checking in again: say what it did meanwhile, from
    what its first heartbeat back reports (ADR-058)."""
    person = ctx.people.get_person(device["person_id"])
    name = person["name"] if person else "the protected person"
    d = event.get("detail") or {}
    lines = [f"{device['name']} is checking in again after {d.get('minutes')} minutes without a word "
             f"(from {d.get('from')})."]
    if d.get("restarted_at"):
        lines.append(f"It restarted at {d['restarted_at']}, so what follows covers the time since then.")
    screen = d.get("screen_on_s")
    if screen is not None:
        unwatched = d.get("unwatched_s") or 0
        if screen < 60:
            lines.append("Its screen was off for that time.")
        else:
            lines.append(f"Its screen was on for {screen // 60} minutes and Watchman watched "
                         f"{max(0, screen - unwatched) // 60} of them, with {d.get('judged') or 0} checks. "
                         "It was working but could not reach this server, which usually means the device "
                         "had no network or Tailscale was off.")
            if unwatched >= 60:
                lines.append(f"For {unwatched // 60} minutes it was not watching: paused, an app it does "
                             "not watch, or a screen an app keeps private.")
    lines.append("Protection is on now." if d.get("protection") is True else
                 "Protection is not on now; a separate alert says what is off.")
    to = recipients(ctx, device["person_id"], "protection")
    if to:
        _send(ctx, Mail(to=to, subject=f"Watchman: {name}'s {device['name']} is checking in again",
                        text="\n".join(lines)))
    _ntfy(ctx, "Watchman: device back", lines[0], "protection")


def _line(device: sqlite3.Row, event: dict, name: str) -> str:
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
    return line


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
    to = recipients(ctx, event["person_id"], "image")
    if to:
        _send(ctx, Mail(to=to, subject=subject, text=text, html=html, inline=inline))
    ctx.events.mark_alerted(event["id"])
    _ntfy(ctx, "Watchman: explicit image covered", f"{where}, {event['occurred_at']}", "image")


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
