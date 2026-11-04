"""The digest (ADR-005): a calm summary first, detail below, heavy-blur thumbnails only.

Sent on the configured day and time in the household time zone to the protected person and all
partners. The period runs from the previous digest (or 7 days back) to now.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from app.clients.mailer import Attachment, Mail
from app.clock import iso, parse_iso
from app.services import liveness
from app.services.alerts import recipients
from app.services.context import Context

log = logging.getLogger(__name__)

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MAX_THUMBS = 12


def period_for(ctx: Context, person_id: str, now: datetime) -> tuple[datetime, datetime]:
    last = ctx.codes.last_digest(person_id)
    start = parse_iso(last["period_end"]) if last else now - timedelta(days=7)
    return start, now


def protected_hours(ctx: Context, person_id: str, start: datetime, end: datetime) -> float:
    """Hours with at least one of this person's devices reporting protection on."""
    return round(ctx.devices.protected_minutes(person_id, start, end) / 60, 1)


def summarise(ctx: Context, person_id: str, start: datetime, end: datetime) -> dict:
    rows = ctx.events.between(person_id, start, end)
    counts = {"explicit": 0, "review": 0, "info_images": 0, "urls": 0, "searches": 0, "apps": 0,
              "protection": 0, "tamper": 0}
    items = []
    for r in rows:
        t = r["type"]
        if r["severity"] == "explicit":
            counts["explicit"] += 1
            items.append(r)
        elif r["severity"] == "review":
            counts["review"] += 1
            items.append(r)
        elif t == "image_flagged":
            counts["info_images"] += 1
        elif t == "url_blocked":
            counts["urls"] += 1
        elif t == "search_flagged":
            counts["searches"] += 1
        elif t == "app_blocked":
            counts["apps"] += 1
        if r["severity"] == "protection":
            counts["protection"] += 1
        if t == "tamper":
            counts["tamper"] += 1
    pauses = ctx.codes.pauses_between(person_id, start, end)
    peeks = ctx.codes.peeks_between(person_id, start, end)
    changes = ctx.settings.unreported_changes()
    days = max(1, round((end - start).total_seconds() / 86400))
    tz = ZoneInfo(ctx.settings.load().timezone)
    hidden = liveness.secure_minutes(ctx, person_id, start.astimezone(tz).strftime("%Y-%m-%d"),
                                     end.astimezone(tz).strftime("%Y-%m-%d"))
    return {"hidden": hidden,
        "counts": counts, "items": items, "pauses": pauses, "peeks": peeks, "changes": changes,
        "days": days, "hours": protected_hours(ctx, person_id, start, end),
    }


def headline(summary: dict) -> str:
    c = summary["counts"]
    covered = c["explicit"] + c["review"]
    if c["explicit"] == 0 and c["protection"] == 0 and c["tamper"] == 0:
        hours = summary['hours']
        return (f"All clear: {summary['days']} days, {hours:g} hour{'s' if hours != 1 else ''} protected, "
                f"{covered} image{'s' if covered != 1 else ''} covered.")
    parts = []
    if c["explicit"]:
        parts.append(f"{c['explicit']} explicit image{'s' if c['explicit'] != 1 else ''} confirmed")
    if c["protection"]:
        parts.append(f"{c['protection']} protection gap{'s' if c['protection'] != 1 else ''}")
    hours = summary['hours']
    parts.append(f"{hours:g} hour{'s' if hours != 1 else ''} protected")
    return f"{summary['days']} days: " + ", ".join(parts) + "."


def _when(ts: str, tz: ZoneInfo) -> str:
    return parse_iso(ts).astimezone(tz).strftime("%a %d %b %H:%M")


def render(ctx: Context, person_name: str, summary: dict, start: datetime, end: datetime,
           tz: ZoneInfo) -> tuple[str, str, list[Attachment]]:
    c = summary["counts"]
    head = headline(summary)
    period = f"{start.astimezone(tz).strftime('%d %b')} to {end.astimezone(tz).strftime('%d %b %Y')}"
    lines = [head, "", f"{person_name}, {period}", "",
             f"Images covered: {c['explicit'] + c['review']} ({c['explicit']} confirmed explicit, "
             f"{c['review']} for review)",
             f"Images noted: {c['info_images']}",
             f"Sites blocked: {c['urls']}", f"Searches flagged: {c['searches']}",
             f"Apps blocked: {c['apps']}", f"Protection gaps: {c['protection']}",
             f"Pauses: {len(summary['pauses'])}", f"Peeks by partners: {len(summary['peeks'])}", ""]
    html = [f"<h2 style='font-weight:500'>{escape(head)}</h2>",
            f"<p style='color:#5f6b70'>{escape(person_name)}, {escape(period)}</p>",
            "<table cellpadding='4'>"]
    for label, value in [("Images covered", c["explicit"] + c["review"]),
                         ("Confirmed explicit", c["explicit"]), ("For review", c["review"]),
                         ("Images noted", c["info_images"]), ("Sites blocked", c["urls"]),
                         ("Searches flagged", c["searches"]), ("Apps blocked", c["apps"]),
                         ("Protection gaps", c["protection"]), ("Pauses", len(summary["pauses"])),
                         ("Peeks by partners", len(summary["peeks"]))]:
        html.append(f"<tr><td>{label}</td><td align='right'>{value}</td></tr>")
    html.append("</table>")

    attachments: list[Attachment] = []
    if summary["items"]:
        lines.append("Items:")
        html.append("<h3 style='font-weight:500'>Items</h3>")
        for r in summary["items"]:
            where = r["app"] or "device"
            if r["url"]:
                where += f" {r['url']}"
            sev = "explicit" if r["severity"] == "explicit" else "review"
            lines.append(f"  {_when(r['occurred_at'], tz)}  {sev:8}  {where}")
            thumb = ""
            if len(attachments) < MAX_THUMBS and r["image_status"] == "stored":
                data = ctx.blobs.get(r["id"], "heavy")
                if data:
                    cid = f"img{len(attachments)}"
                    attachments.append(Attachment(cid=cid, data=data, filename=f"{cid}.webp"))
                    thumb = (f"<img src='cid:{cid}' width='64' height='64' alt='heavily blurred' "
                             "style='vertical-align:middle;margin-right:8px'>")
            html.append(f"<p>{thumb}{escape(_when(r['occurred_at'], tz))} · {sev} · {escape(where)}</p>")
        lines.append("")
    if summary["pauses"]:
        lines.append("Pauses:")
        html.append("<h3 style='font-weight:500'>Pauses</h3>")
        for p in summary["pauses"]:
            s = f"{_when(p['started_at'], tz)}, {p['minutes']} min, code from {p['partner_name']}"
            lines.append("  " + s)
            html.append(f"<p>{escape(s)}</p>")
        lines.append("")
    if summary["peeks"]:
        lines.append("Peeks:")
        html.append("<h3 style='font-weight:500'>Peeks</h3>")
        for p in summary["peeks"]:
            s = f"{_when(p['at'], tz)} by {p['partner_name']}"
            lines.append("  " + s)
            html.append(f"<p>{escape(s)}</p>")
        lines.append("")
    if summary.get("hidden"):
        lines.append("Screens Watchman could not see (the app hides its screen; banking apps do):")
        html.append("<h3 style='font-weight:500'>Screens Watchman could not see</h3>"
                    "<p style='color:#5f6b70'>These apps hide their screen from every other app. Banking and "
                    "video apps do this. Ask about any you do not recognise.</p>")
        for app, minutes in summary["hidden"]:
            s = f"{app}: {minutes} min"
            lines.append("  " + s)
            html.append(f"<p>{escape(s)}</p>")
        lines.append("")
    if summary["changes"]:
        lines.append("Settings changed:")
        html.append("<h3 style='font-weight:500'>Settings changed</h3>")
        for ch in summary["changes"]:
            s = f"{_when(ch['at'], tz)} {ch['login']} set {ch['key']} to {ch['new_value']}"
            lines.append("  " + s)
            html.append(f"<p>{escape(s)}</p>")
        lines.append("")
    lines.append("Partners never see originals. Originals are deleted after the second opinion.")
    html.append("<p style='color:#5f6b70'>Partners never see originals. "
                "Originals are deleted after the second opinion.</p>")
    return "\n".join(lines), "\n".join(html), attachments


def send_digest(ctx: Context, person_id: str, now: datetime | None = None) -> bool:
    now = now or ctx.clock.now()
    person = ctx.people.get_person(person_id)
    if person is None:
        return False
    settings = ctx.settings.load()
    tz = ZoneInfo(settings.timezone)
    start, end = period_for(ctx, person_id, now)
    summary = summarise(ctx, person_id, start, end)
    text, html, attachments = render(ctx, person["name"], summary, start, end, tz)
    to = recipients(ctx, person_id)
    subject = f"Watchman digest: {headline(summary)}"
    with ctx.conn:
        ctx.codes.record_digest(person_id, start, end)
        ctx.settings.mark_changes_reported([ch["id"] for ch in summary["changes"]])
    if not to:
        log.info("digest for %s has no recipients", person["name"])
        return False
    try:
        ctx.mailer.send(Mail(to=to, subject=subject, text=text, html=html, inline=attachments))
    except Exception as exc:
        log.warning("digest email failed: %s", exc)
        return False
    return True


def is_due(ctx: Context, person_id: str, now: datetime) -> bool:
    """True when the configured day and time has passed since the last digest."""
    settings = ctx.settings.load()
    tz = ZoneInfo(settings.timezone)
    local = now.astimezone(tz)
    try:
        hour, minute = (int(x) for x in settings.digest_time.split(":"))
    except ValueError:
        hour, minute = 19, 0
    day = DAYS.index(settings.digest_day.lower()) if settings.digest_day.lower() in DAYS else 6
    # Most recent scheduled moment at or before now.
    days_back = (local.weekday() - day) % 7
    scheduled = (local - timedelta(days=days_back)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if scheduled > local:
        scheduled -= timedelta(days=7)
    last = ctx.codes.last_digest(person_id)
    if last is not None:
        marker = parse_iso(last["sent_at"])
    else:
        # Never sent: the first digest goes at the first scheduled moment after the person was added.
        person = ctx.people.get_person(person_id)
        marker = parse_iso(person["created_at"]) if person else now
    return marker < scheduled.astimezone(ZoneInfo("UTC"))


__all__ = ["send_digest", "is_due", "summarise", "headline", "iso"]
