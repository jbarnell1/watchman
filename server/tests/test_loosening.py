"""ADR-050: loosening is loud and gated. Setup runs once, the reporting settings lock, a partner
added beside others needs their code and waits three days, codes cannot be guessed, devices that
stay off are re-announced, and every loosening alerts at once."""

from __future__ import annotations

from urllib.parse import unquote

from app.services import jobs
from tests.conftest import ADMIN, PARTNER, as_login
from tests.test_api import heartbeat

GENERAL = {"household_name": "Home", "timezone": "UTC", "digest_day": "sunday", "digest_time": "19:00",
           "image_retention_days": "7", "event_retention_days": "365", "ntfy_url": "", "adguard_url": ""}


def _settings_code(ctx, client, household) -> str:
    r = client.post("/codes", data={"person": household["person_id"], "purpose": "settings"},
                    headers=as_login(PARTNER), follow_redirects=False)
    assert r.status_code == 303, r.text
    return unquote(r.headers["location"]).split("code=")[1].split("&")[0]


def test_setup_runs_once(client, household):
    assert client.get("/setup", headers=as_login(ADMIN)).status_code == 403
    r = client.post("/setup", data={"person_name": "X", "partner_name": "Me", "partner_email": "me@x"},
                    headers=as_login(ADMIN), follow_redirects=False)
    assert r.status_code == 403


def test_reporting_settings_lock_without_a_paired_device(ctx, client, household, mailer):
    with ctx.conn:
        ctx.settings.set("locks_on", True, "owner")
    assert not ctx.devices.list_active()
    r = client.post("/settings/general", data=dict(GENERAL, digest_day="monday"), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "the digest day" in unquote(r.headers["location"]) and ctx.settings.load().digest_day == "sunday"
    r = client.post("/settings/general", data=dict(GENERAL, event_retention_days="400"), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "saved=1" in r.headers["location"]                     # keeping the record longer is free
    code = _settings_code(ctx, client, household)
    r = client.post("/settings/general", data=dict(GENERAL, digest_day="monday", partner_code=code,
                                                   event_retention_days="400"),
                    headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"] and ctx.settings.load().digest_day == "monday"
    assert any("loosened the digest day" in m.text for m in mailer.sent)


def test_retention_and_time_zone_have_floors(ctx, client, household):
    r = client.post("/settings/general", data=dict(GENERAL, event_retention_days="0"), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "error=" in r.headers["location"] and ctx.settings.load().event_retention_days == 365
    r = client.post("/settings/general", data=dict(GENERAL, timezone="Mars/Olympus"), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "error=" in r.headers["location"]
    # A value stored before the rule existed falls back to its default instead of stopping the server.
    ctx.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('timezone', '\"Mars/Olympus\"')")
    assert ctx.settings.load().timezone == "UTC"


def test_first_mail_setup_is_free_but_changing_it_alerts_first(ctx, client, household, mailer):
    with ctx.conn:
        ctx.settings.set("smtp", {"host": ""}, "owner")
        ctx.settings.set("locks_on", True, "owner")
    form = {"smtp.host": "mail.test", "smtp.port": "587", "smtp.from_address": "w@test", "smtp.starttls": "on"}
    r = client.post("/settings/email", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    r = client.post("/settings/email", data=dict(form, **{"smtp.host": "blackhole.test"}), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "the mail server" in unquote(r.headers["location"]) and ctx.settings.load().smtp.host == "mail.test"


def test_loosening_alerts_at_once_even_with_locks_off(ctx, client, household, mailer):
    from tests.test_portal import LOCK_IDS, PROTECTION_FORM
    form = {**PROTECTION_FORM, **{f"lock.{g}": "on" for g in LOCK_IDS}, "thresholds.cover": "0.90"}
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"], unquote(r.headers["location"])
    alert = [m for m in mailer.sent if m.subject == "Watchman: a setting was loosened"]
    assert alert and "thresholds" in alert[0].text and "sam@example.com" in alert[0].to
    mailer.sent.clear()
    r = client.post("/settings/protection", data=dict(form, **{"thresholds.cover": "0.80"}), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert not [m for m in mailer.sent if "loosened" in m.subject]   # tightening is quiet


def test_a_second_partner_needs_the_first_and_waits(ctx, client, household, clock, mailer):
    with ctx.conn:
        ctx.settings.set("locks_on", True, "owner")
    add = {"person": household["person_id"], "name": "Sock", "email": "sock@example.com", "link_type": "linked",
           "login": "sock@example.com"}
    r = client.post("/settings/partners/add", data=add, headers=as_login(ADMIN), follow_redirects=False)
    assert "settings code" in unquote(r.headers["location"])
    assert len(ctx.people.partners_for(household["person_id"])) == 2
    code = _settings_code(ctx, client, household)
    r = client.post("/settings/partners/add", data=dict(add, partner_code=code), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    assert any(m.subject.startswith("Watchman: a partner was added") and "sam@example.com" in m.to
               for m in mailer.sent)
    # The new partner's codes do not count for three days.
    r = client.post("/codes", data={"person": household["person_id"], "purpose": "settings"},
                    headers=as_login("sock@example.com"), follow_redirects=False)
    assert r.status_code == 403
    clock.advance(days=3, minutes=1)
    r = client.post("/codes", data={"person": household["person_id"], "purpose": "settings"},
                    headers=as_login("sock@example.com"), follow_redirects=False)
    assert r.status_code == 303


def test_pause_code_guessing_is_slowed_and_reported(ctx, client, device, clock, mailer):
    _, headers = device
    for _ in range(5):
        assert client.post("/api/v1/unlock", headers=headers, json={"code": "000-000"}).status_code == 400
    assert client.post("/api/v1/unlock", headers=headers, json={"code": "000-000"}).status_code == 429
    clock.advance(minutes=16)
    for _ in range(5):
        client.post("/api/v1/unlock", headers=headers, json={"code": "000-000"})
    tamper = [e for e in ctx.events.timeline(device[0]["person_id"]) if e["type"] == "tamper"]
    assert len(tamper) == 1 and "code_guessing" in tamper[0]["detail"]
    assert any("ten wrong partner codes" in m.text for m in mailer.sent)


def test_new_device_pairing_alerts(ctx, client, household, mailer):
    with ctx.conn:
        code = ctx.devices.create_pairing_code(household["person_id"])
    client.post("/api/v1/pair", json={"code": code, "device": {"name": "Spare", "platform": "android", "kind": "app"}})
    assert any(m.subject == "Watchman: a device was paired for Alex" for m in mailer.sent)


def test_a_field_that_stops_being_reported_counts_as_off(ctx, client, device, clock, mailer):
    _, headers = device
    heartbeat(client, headers, clock)
    heartbeat(client, headers, clock, accessibility=None)
    ev = [e for e in ctx.events.timeline(device[0]["person_id"]) if e["severity"] == "protection"]
    assert ev and "not reported" in ev[0]["detail"]


def test_devices_that_stay_off_are_announced_again(ctx, client, device, clock, mailer):
    _, headers = device
    heartbeat(client, headers, clock)
    heartbeat(client, headers, clock, accessibility=False)
    mailer.sent.clear()
    clock.advance(hours=5)
    heartbeat(client, headers, clock, accessibility=False)
    assert jobs.reminders(ctx) == 0
    clock.advance(hours=1, minutes=1)
    assert jobs.reminders(ctx) == 1
    assert any("still unprotected" in m.subject for m in mailer.sent)
    assert jobs.reminders(ctx) == 0                                  # once per period
    heartbeat(client, headers, clock)                                # whole again: the reminders stop
    clock.advance(hours=7)
    assert jobs.reminders(ctx) == 0
