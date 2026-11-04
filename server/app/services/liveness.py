"""Is the device judging, not just alive (ADR-051)?

A heartbeat says protection is on; a stand-in client can say that too. These checks look at what
the device did:
- `judge`: screen-on seconds, unwatched seconds (paused, an app not watched, a screen an app
  protects) and how many checks ran a model. A device whose screen was on and watched for 15
  minutes in an hour without judging anything raises `tamper` (`not_judging`).
- `device`: developer options and USB debugging. Debugging turning on is a protection change.
- `secure_apps`: seconds per app that a screen Watchman cannot see (banking apps, DRM, or a
  browser built to hide itself) was in front. Kept per day, listed in the digest, and an app's
  first five minutes in a day alerts once, ever.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import timedelta

from app.clock import iso, parse_iso
from app.services import alerts
from app.services.context import Context
from app.services.policy import paused_until
from app.store.events_store import SEVERITY_PROTECTION

WINDOW = timedelta(hours=1)
NOT_JUDGING_S = 15 * 60
SECURE_ALERT_S = 5 * 60
MAX_APPS = 64
APP_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,200}$")


def _seconds(value, cap: int = 86400) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(min(value, cap))


def record(ctx: Context, device: sqlite3.Row, body: dict, previous: dict | None, day: str) -> list[dict]:
    """Store what the heartbeat says about judging and hidden screens. Returns derived events
    (debugging turned on) for the caller to insert and alert. Runs inside the heartbeat's transaction."""
    derived: list[dict] = []
    now = ctx.clock.now()
    judge = body.get("judge")
    if isinstance(judge, dict):
        ctx.conn.execute(
            "UPDATE heartbeats SET screen_on_s = ?, unwatched_s = ?, judged = ? WHERE device_id = ? AND at = ?",
            (_seconds(judge.get("screen_on_s")), _seconds(judge.get("unwatched_s")) or 0,
             _seconds(judge.get("judged"), 10**7), device["id"], iso(now)),
        )
    info = body.get("device")
    old_info = (previous or {}).get("device") or {}
    if isinstance(info, dict) and info.get("usb_debugging") is True and old_info.get("usb_debugging") is not True:
        derived.append({"id": str(uuid.uuid4()), "type": "protection_changed", "occurred_at": iso(now),
                        "action": "none", "detail": {"field": "usb_debugging", "from": old_info.get("usb_debugging"),
                                                     "to": True}})
    secure = body.get("secure_apps")
    if isinstance(secure, dict):
        for app, value in list(secure.items())[:MAX_APPS]:
            secs = _seconds(value)
            if not secs or not isinstance(app, str) or not APP_NAME.match(app):
                continue
            ctx.conn.execute(
                "INSERT INTO secure_apps(device_id, person_id, day, app, seconds) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(device_id, day, app) DO UPDATE SET seconds = seconds + excluded.seconds",
                (device["id"], device["person_id"], day, app, secs),
            )
    return derived


def secure_alerts(ctx: Context, device: sqlite3.Row, day: str) -> int:
    """Alert once, ever, for each app that hides its screen for five minutes in a day. A bank's app
    is announced once and then only counted; a browser someone built to hide is announced too."""
    rows = ctx.conn.execute(
        "SELECT app, SUM(seconds) AS s FROM secure_apps WHERE person_id = ? AND day = ? GROUP BY app "
        "HAVING s >= ? AND app NOT IN (SELECT app FROM secure_apps_seen WHERE person_id = ?)",
        (device["person_id"], day, SECURE_ALERT_S, device["person_id"]),
    ).fetchall()
    for r in rows:
        with ctx.conn:
            ctx.conn.execute("INSERT OR IGNORE INTO secure_apps_seen(person_id, app, first_at) VALUES (?, ?, ?)",
                             (device["person_id"], r["app"], iso(ctx.clock.now())))
        person = ctx.people.get_person(device["person_id"])
        name = person["name"] if person else "the protected person"
        alerts.change_alert(ctx, f"an app hid its screen from Watchman on {name}'s {device['name']}", [
            f"{r['app']} kept Watchman from seeing its screen for {r['s'] // 60} minutes today on {device['name']}.",
            "Banking, payment and video apps do this, and Watchman leaves them alone. This alert comes once per app; "
            "after it, the app's minutes are listed in each digest. If you do not recognise the app, ask about it.",
        ], device["person_id"])
    return len(rows)


def secure_minutes(ctx: Context, person_id: str, start_day: str, end_day: str) -> list[tuple[str, int]]:
    rows = ctx.conn.execute(
        "SELECT app, SUM(seconds) AS s FROM secure_apps WHERE person_id = ? AND day >= ? AND day <= ? "
        "GROUP BY app HAVING s >= 60 ORDER BY s DESC",
        (person_id, start_day, end_day),
    ).fetchall()
    return [(r["app"], round(r["s"] / 60)) for r in rows]


def not_judging(ctx: Context) -> int:
    """One `tamper` per episode for a device that was on and watched but judged nothing."""
    now = ctx.clock.now()
    since = iso(now - WINDOW)
    raised = 0
    for device in ctx.devices.list_active():
        rows = ctx.conn.execute(
            "SELECT screen_on_s, unwatched_s, judged FROM heartbeats WHERE device_id = ? AND at > ? "
            "AND screen_on_s IS NOT NULL",
            (device["id"], since),
        ).fetchall()
        if not rows:
            continue
        watched = sum(max(0, (r["screen_on_s"] or 0) - (r["unwatched_s"] or 0)) for r in rows)
        judged = sum(r["judged"] or 0 for r in rows)
        if judged > 0:
            if device["not_judging_at"]:
                with ctx.conn:
                    ctx.conn.execute("UPDATE devices SET not_judging_at = NULL WHERE id = ?", (device["id"],))
            continue
        if watched < NOT_JUDGING_S or device["not_judging_at"]:
            continue
        state = (json.loads(device["last_state"] or "null") or {}).get("state") or {}
        pause = paused_until(ctx, device["person_id"])
        if state.get("protection") is not True or (pause and parse_iso(pause) > now):
            continue
        ev = {"id": str(uuid.uuid4()), "type": "tamper", "occurred_at": iso(now), "action": "none",
              "detail": {"reason": "not_judging", "watched_minutes": watched // 60}}
        with ctx.conn:
            ctx.events.insert(ev, device["id"], device["person_id"], SEVERITY_PROTECTION, "none")
            ctx.conn.execute("UPDATE devices SET not_judging_at = ? WHERE id = ?", (iso(now), device["id"]))
        alerts.protection_alert(ctx, device, ev)
        raised += 1
    return raised


def purge_before(ctx: Context, day: str) -> None:
    ctx.conn.execute("DELETE FROM secure_apps WHERE day < ?", (day,))
