"""ADR-051: devices prove they judge, report gaps after the fact, and name apps that hide their
screen; nothing here covers those apps."""

from __future__ import annotations

import uuid
from datetime import timedelta

from app.clock import iso
from app.services import digest, jobs, liveness
from tests.test_api import heartbeat


def _beat(client, headers, clock, judge=None, device=None, secure=None, **state):
    base = {"protection": True, "accessibility": True, "capture": "granted", "overlay": True,
            "device_admin": True, "extension": None, "vpn": True}
    base.update(state)
    body = {"sent_at": iso(clock.now()), "app_version": "0.1.0", "booted_at": iso(clock.now()),
            "safe_mode": False, "state": base}
    if judge is not None:
        body["judge"] = judge
    if device is not None:
        body["device"] = device
    if secure is not None:
        body["secure_apps"] = secure
    return client.post("/api/v1/heartbeat", headers=headers, json=body)


def test_on_and_watched_but_judging_nothing_alerts_once(ctx, client, device, clock, mailer):
    _, headers = device
    for _ in range(4):
        clock.advance(minutes=5)
        _beat(client, headers, clock, judge={"window_s": 300, "screen_on_s": 300, "unwatched_s": 0, "judged": 0})
    assert liveness.not_judging(ctx) == 1
    assert liveness.not_judging(ctx) == 0
    assert any("judged nothing" in m.text for m in mailer.sent)
    clock.advance(minutes=5)
    _beat(client, headers, clock, judge={"window_s": 300, "screen_on_s": 300, "unwatched_s": 0, "judged": 3})
    assert liveness.not_judging(ctx) == 0
    assert ctx.devices.get(device[0]["id"])["not_judging_at"] is None


def test_unwatched_time_and_old_clients_do_not_alert(ctx, client, device, clock):
    _, headers = device
    for _ in range(6):
        clock.advance(minutes=5)
        # An app that is not watched was in front: nothing should have been judged.
        _beat(client, headers, clock, judge={"window_s": 300, "screen_on_s": 300, "unwatched_s": 280, "judged": 0})
    assert liveness.not_judging(ctx) == 0
    for _ in range(12):
        clock.advance(minutes=5)
        heartbeat(client, headers, clock)          # a client that sends no judge block
    assert liveness.not_judging(ctx) == 0


def test_usb_debugging_turning_on_alerts(ctx, client, device, clock, mailer):
    _, headers = device
    _beat(client, headers, clock, device={"developer_options": True, "usb_debugging": False})
    mailer.sent.clear()
    _beat(client, headers, clock, device={"developer_options": True, "usb_debugging": True})
    assert any("USB debugging turned on" in m.text for m in mailer.sent)
    mailer.sent.clear()
    _beat(client, headers, clock, device={"developer_options": True, "usb_debugging": True})
    assert not mailer.sent


def test_apps_that_hide_their_screen_alert_once_and_reach_the_digest(ctx, client, device, clock, mailer, household):
    _, headers = device
    _beat(client, headers, clock, secure={"com.bank.app": 200, "com.example.hidebrowser": 120})
    assert not [m for m in mailer.sent if "hid its screen" in m.subject]
    _beat(client, headers, clock, secure={"com.bank.app": 200, "bad name!": 999})
    hid = [m for m in mailer.sent if "hid its screen" in m.subject]
    assert len(hid) == 1 and "com.bank.app" in hid[0].text
    _beat(client, headers, clock, secure={"com.bank.app": 600})
    assert len([m for m in mailer.sent if "hid its screen" in m.subject]) == 1
    summary = digest.summarise(ctx, household["person_id"], clock.now() - timedelta(days=1), clock.now())
    assert ("com.bank.app", 17) in summary["hidden"] and ("com.example.hidebrowser", 2) in summary["hidden"]
    text, _, _ = digest.render(ctx, "Alex", summary, clock.now() - timedelta(days=1), clock.now(),
                               __import__("zoneinfo").ZoneInfo("UTC"))
    assert "Screens Watchman could not see" in text and "com.bank.app: 17 min" in text


def test_a_gap_reported_after_the_fact_alerts_when_long(ctx, client, device, clock, mailer):
    _, headers = device
    now = clock.now()

    def gap(minutes, reason="killed"):
        return {"id": str(uuid.uuid4()), "type": "protection_gap", "occurred_at": iso(now), "action": "none",
                "detail": {"from": iso(now - timedelta(minutes=minutes)), "to": iso(now), "reason": reason}}

    r = client.post("/api/v1/events", headers=headers, json={"events": [gap(2), gap(20), gap(5, "reboot")]})
    assert len(r.json()["accepted"]) == 3
    sev = sorted(e["severity"] for e in ctx.events.timeline(device[0]["person_id"]) if e["type"] == "protection_gap")
    assert sev == ["info", "info", "protection"]
    assert any("was unprotected from" in m.text for m in mailer.sent)


def test_tick_runs_the_liveness_job(ctx):
    assert "not_judging" in jobs.tick(ctx)
