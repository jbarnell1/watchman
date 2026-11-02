from __future__ import annotations

import uuid
from urllib.parse import unquote

from app.clock import iso
from tests.conftest import ADMIN, PARTNER, PROTECTED, as_login, jpeg_bytes
from tests.test_api import heartbeat


def test_anonymous_gets_a_sign_in_page(ctx, client, identity):
    identity.current = None
    r = client.get("/", headers={"Accept": "text/html"})
    assert r.status_code == 403 and "Tailscale" in r.text


def test_first_login_becomes_admin_and_lands_on_setup(ctx, client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert ADMIN in ctx.people.admins()


def test_setup_creates_person_and_partner(ctx, client):
    r = client.post("/setup", data={
        "household_name": "Test house", "timezone": "America/New_York", "digest_day": "wednesday",
        "digest_time": "07:00", "person_name": "Alex", "person_email": "alex@example.com", "person_is_me": "on",
        "partner_name": "Sam", "partner_email": "sam@example.com", "partner_link_type": "linked",
        "partner_login": "sam@example.com", "smtp.host": "", "smtp.port": "587",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = ctx.settings.load()
    assert s.setup_complete and s.household_name == "Test house" and s.digest_day == "wednesday"
    people = ctx.people.list_people()
    assert len(people) == 1 and people[0]["name"] == "Alex"
    partners = ctx.people.partners_for(people[0]["id"])
    assert partners[0]["link_type"] == "linked" and partners[0]["login"] == "sam@example.com"
    # The admin is also the protected person and sees their own timeline.
    r = client.get("/")
    assert r.status_code == 200 and "Timeline" in r.text


def test_roles_see_only_their_person(ctx, client, household, device, clock):
    _, headers = device
    heartbeat(client, headers, clock)
    # Protected person sees timeline and devices, but not Codes or Settings.
    r = client.get("/", headers=as_login(PROTECTED))
    assert r.status_code == 200 and "Protection on across 1 device" in r.text
    assert "/codes" not in r.text and "/settings" not in r.text
    # Partner sees Codes.
    r = client.get("/", headers=as_login(PARTNER))
    assert r.status_code == 200 and "/codes" in r.text
    # A stranger sees nothing.
    r = client.get("/", headers=as_login("stranger@example.com"))
    assert r.status_code == 200 and "No one is set up" in r.text
    r = client.get(f"/review?person={household['person_id']}", headers=as_login("stranger@example.com"))
    assert r.status_code == 403


def _covered_event(ctx, client, headers, clock, opinion, score: float) -> str:
    ev_id = str(uuid.uuid4())
    client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "image_covered", "occurred_at": iso(clock.now()), "app": "org.mozilla.firefox",
        "action": "covered", "scores": {"classifier": 0.9}, "has_image": True,
        "region": {"w": 48, "h": 48, "screen_w": 1440, "screen_h": 3120}}]})
    opinion.fixed = score
    client.put(f"/api/v1/events/{ev_id}/image", headers=headers, content=jpeg_bytes())
    return ev_id


def test_review_peek_and_marking(ctx, client, household, device, clock, opinion):
    _, headers = device
    ev_id = _covered_event(ctx, client, headers, clock, opinion, 0.3)
    r = client.get("/review", headers=as_login(PARTNER))
    assert r.status_code == 200 and "Needs a second look" in r.text and "Hold to peek" in r.text
    # Heavy blur is visible to everyone who may see the person; medium only through a peek token.
    assert client.get(f"/images/{ev_id}/heavy", headers=as_login(PROTECTED)).status_code == 200
    assert client.get(f"/images/{ev_id}/medium?t=bogus", headers=as_login(PARTNER)).status_code == 404
    # The protected person cannot peek.
    assert client.post(f"/peek/{ev_id}", headers=as_login(PROTECTED)).status_code == 403
    r = client.post(f"/peek/{ev_id}", headers=as_login(PARTNER))
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert r.json()["seconds"] == 3
    r = client.get(url, headers=as_login(PARTNER))
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert client.get(url, headers=as_login(PARTNER)).status_code == 404    # token is single use
    # Cooldown: a second peek within 24 hours is refused, visible to the protected person.
    r = client.post(f"/peek/{ev_id}", headers=as_login(PARTNER))
    assert r.status_code == 429
    r = client.get("/", headers=as_login(PROTECTED))
    assert "Sam" in r.text and "Peeks by partners" in r.text
    clock.advance(hours=25)
    assert client.post(f"/peek/{ev_id}", headers=as_login(PARTNER)).status_code == 200
    # Marking.
    assert client.post(f"/review/{ev_id}", data={"state": "false_alarm"}, headers=as_login(PROTECTED)).status_code == 403
    r = client.post(f"/review/{ev_id}", data={"state": "false_alarm"}, headers=as_login(PARTNER), follow_redirects=False)
    assert r.status_code == 303
    assert ctx.events.get(ev_id)["review_state"] == "false_alarm"
    assert ctx.events.needing_review(household["person_id"]) == []


def test_partner_issues_codes_from_the_portal(ctx, client, household, device):
    r = client.post("/codes", data={"person": household["person_id"], "purpose": "pause", "minutes": "20"},
                    headers=as_login(PARTNER), follow_redirects=False)
    assert r.status_code == 303 and "code=" in r.headers["location"]
    code = r.headers["location"].split("code=")[1].split("&")[0]
    _, headers = device
    assert client.post("/api/v1/unlock", headers=headers, json={"code": code}).status_code == 200
    # Over the cap, and not a partner.
    r = client.post("/codes", data={"person": household["person_id"], "purpose": "pause", "minutes": "500"},
                    headers=as_login(PARTNER))
    assert r.status_code == 400
    r = client.post("/codes", data={"person": household["person_id"], "purpose": "pause", "minutes": "5"},
                    headers=as_login(PROTECTED))
    assert r.status_code == 403


def test_devices_page_pairing_code_and_revoke(ctx, client, household, device, mailer):
    dev, headers = device
    r = client.post("/devices/pairing-code", data={"person": household["person_id"]}, headers=as_login(PROTECTED),
                    follow_redirects=False)
    assert r.status_code == 303 and "code=" in r.headers["location"]
    r = client.get(r.headers["location"], headers=as_login(PROTECTED))
    assert "Pairing code" in r.text
    # Only the admin may revoke, and revoking alerts.
    assert client.post(f"/devices/{dev['id']}/revoke", headers=as_login(PROTECTED)).status_code == 403
    mailer.sent.clear()
    r = client.post(f"/devices/{dev['id']}/revoke", headers=as_login(ADMIN), follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/api/v1/policy", headers=headers).status_code == 401
    assert len(mailer.sent) == 1 and "revoked" in mailer.sent[0].text


def test_locked_settings_need_a_partner_code(ctx, client, household, device):
    with ctx.conn:
        ctx.settings.set("locks_on", True, "owner")
    # Protection is on (a device is paired), so a locked key needs a settings code.
    r = client.post("/settings/general", data={
        "household_name": "Example household", "timezone": "UTC", "digest_day": "sunday", "digest_time": "19:00",
        "image_retention_days": "3", "event_retention_days": "365", "ntfy_url": "", "adguard_url": "",
    }, headers=as_login(ADMIN), follow_redirects=False)
    assert "error=" in r.headers["location"] and "settings code" in unquote(r.headers["location"])
    assert ctx.settings.load().image_retention_days == 7
    # Unlocked keys still save.
    r = client.post("/settings/general", data={
        "household_name": "Example household", "timezone": "UTC", "digest_day": "sunday", "digest_time": "19:00",
        "image_retention_days": "7", "event_retention_days": "365", "ntfy_url": "", "adguard_url": "",
    }, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    assert ctx.settings.load().household_name == "Example household"
    # With a settings code from the partner it goes through, once.
    r = client.post("/codes", data={"person": household["person_id"], "purpose": "settings", "minutes": "0"},
                    headers=as_login(PARTNER), follow_redirects=False)
    code = r.headers["location"].split("code=")[1].split("&")[0]
    r = client.post("/settings/general", data={
        "household_name": "Example household", "timezone": "UTC", "digest_day": "sunday", "digest_time": "19:00",
        "image_retention_days": "3", "event_retention_days": "365", "ntfy_url": "", "adguard_url": "",
        "partner_code": code,
    }, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"], r.headers["location"]
    assert ctx.settings.load().image_retention_days == 3
    changes = ctx.settings.unreported_changes()
    assert any(c["key"] == "image_retention_days" for c in changes)


PROTECTION_FORM = {
    "cover_on_device": "on", "ask_server": "on", "strict_mode": "on", "coverage": "explicit",
    "thresholds.cover": "0.85", "thresholds.flag": "0.85", "thresholds.ask": "0.60", "thresholds.sure": "0.97", "thresholds.detector_cover": "0.55",
    "thresholds.second_opinion": "0.70", "thresholds.known_bad": "0.90", "protected_browser": "org.mozilla.firefox",
    "risky_apps": "com.google.android.youtube\norg.mozilla.firefox\ncom.reddit.frontpage",
    "blocked_apps": "com.android.chrome\ncom.brave.browser\ncom.opera.browser\ncom.microsoft.emmx\n"
                    "com.duckduckgo.mobile.android\ncom.sec.android.app.sbrowser",
    "sample_interval_s": "180", "whole_screen_interval_s": "5", "max_pause_minutes": "60", "heartbeat_gap_minutes": "30",
    "peek_seconds": "3", "peek_cooldown_hours": "24", "second_opinion_device": "cpu",
    "watch_default": "realtime", "checked_interval_s": "60", "checked_apps": "",
    "realtime_apps": "\n".join([
        "com.instagram.android", "com.google.android.youtube", "org.mozilla.firefox", "com.reddit.frontpage",
        "com.zhiliaoapp.musically", "com.twitter.android", "com.facebook.katana", "com.snapchat.android",
        "com.pinterest", "com.tumblr",
    ]),
}


def test_one_person_can_have_their_own_protection_values(ctx, client, household):
    pid = household["person_id"]
    # Report only for this person: blur off, ask off. The household keeps its defaults.
    form = {**PROTECTION_FORM, "for_person": pid}
    form.pop("cover_on_device")
    form.pop("ask_server")
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"] and f"for={pid}" in r.headers["location"]
    s = ctx.settings.load()
    assert s.cover_on_device is True and s.person_overrides == {pid: {"cover_on_device": False, "ask_server": False}}
    page = client.get(f"/settings?area=protection&for={pid}", headers=as_login(ADMIN))
    assert page.status_code == 200 and "differs from the household in ask_server, cover_on_device" in page.text
    assert "Report only" in page.text  # the presets
    # Back to the household's values.
    r = client.post("/settings/protection/reset", data={"for_person": pid}, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    assert ctx.settings.load().person_overrides == {}


def test_settings_pages_render_for_admin_only(client, household, device):
    for area in ("general", "protection", "email", "people"):
        r = client.get(f"/settings?area={area}", headers=as_login(ADMIN))
        assert r.status_code == 200, area
        assert "Locks are off while you set up" in r.text   # locks start off (ADR-028)
    assert client.get("/settings", headers=as_login(PARTNER)).status_code == 403


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True and body["models"] == 0 and body["second_opinion"] == "missing"
    assert body["version"] and set(body) == {"ok", "version", "models", "second_opinion"}


def test_healthz_counts_verified_models(tmp_path):
    import hashlib
    import json

    from app.services.health import verified_models

    (tmp_path / "good.onnx").write_bytes(b"good")
    (tmp_path / "bad.onnx").write_bytes(b"tampered")
    manifest = {"models": [
        {"file": "good.onnx", "sha256": hashlib.sha256(b"good").hexdigest()},
        {"file": "bad.onnx", "sha256": hashlib.sha256(b"bad").hexdigest()},
        {"file": "absent.onnx", "sha256": "0" * 64},
    ]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verified_models(tmp_path) == 1
    assert verified_models(tmp_path / "nowhere") == 0

def test_devices_page_shows_a_pairing_qr(client, household):
    r = client.post("/devices/pairing-code", data={"person": household["person_id"]}, headers=as_login(ADMIN),
                    follow_redirects=True)
    assert r.status_code == 200 and "<svg" in r.text and "Scan code" in r.text

def test_pair_page_hands_over_to_the_app(client):
    r = client.get("/pair", params={"code": "k7q4mzp2d"}, headers={"host": "watchman.example.ts.net"})
    assert r.status_code == 200 and "K7Q-4MZ-P2D" in r.text
    assert "watchman://pair?server=https%3A%2F%2Fwatchman.example.ts.net&amp;code=K7Q4MZP2D" in r.text
    assert "no pairing code" in client.get("/pair", params={"code": "x"}).text

def test_tightening_is_free_and_loosening_needs_a_code(ctx, client, household, device):
    with ctx.conn:
        ctx.settings.set("locks_on", True, "owner")
    # Protection is on (a device is paired). Adding a blocked app and lowering a threshold: free.
    form = {**PROTECTION_FORM, "locks_on": "on", **{f"lock.{g}": "on" for g in ("protection", "apps", "timing", "pauses", "retention", "partners")}}
    form["blocked_apps"] += "\ncom.example.newbrowser"
    form["thresholds.cover"] = "0.80"
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"], unquote(r.headers["location"])
    s = ctx.settings.load()
    assert "com.example.newbrowser" in s.blocked_apps and s.thresholds.cover == 0.80
    # Removing a blocked app is loosening, even alongside an addition.
    swapped = dict(form, blocked_apps=form["blocked_apps"].replace("com.android.chrome\n", "") + "\ncom.example.other")
    r = client.post("/settings/protection", data=swapped, headers=as_login(ADMIN), follow_redirects=False)
    assert "blocked apps" in unquote(r.headers["location"]) and "settings code" in unquote(r.headers["location"])
    assert "com.android.chrome" in ctx.settings.load().blocked_apps
    # Raising a threshold back up is loosening too.
    r = client.post("/settings/protection", data=dict(form, **{"thresholds.cover": "0.90"}), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "thresholds" in unquote(r.headers["location"])


def test_unlocked_groups_loosen_freely_but_unlocking_needs_a_code(ctx, client, household, device):
    with ctx.conn:
        ctx.settings.set("locks_on", True, "owner")
    locked_all = {"locks_on": "on", **{f"lock.{g}": "on" for g in ("protection", "apps", "timing", "pauses", "retention", "partners")}}
    form = {**PROTECTION_FORM, **locked_all}
    form.pop("lock.apps")                                   # unlock the apps group: loosening the locks
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "the locks" in unquote(r.headers["location"])
    with ctx.conn:                                           # as if a partner code had been given
        ctx.settings.set("unlocked_groups", ["apps"], "owner")
    form["blocked_apps"] = form["blocked_apps"].replace("com.android.chrome\n", "")
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"], unquote(r.headers["location"])
    assert "com.android.chrome" not in ctx.settings.load().blocked_apps
    # Locking the group again is tightening: free.
    r = client.post("/settings/protection", data={**form, **locked_all}, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"] and ctx.settings.load().unlocked_groups == []


def test_loosening_rules():
    from app.services import locks
    from app.settings import Settings
    s = Settings()
    assert not locks.loosens("coverage", "explicit", "strict") and locks.loosens("coverage", "strict", "revealing")
    assert locks.loosens("protected_browser", "org.mozilla.firefox", "com.android.chrome")
    assert not locks.loosens("max_pause_minutes", 60, 30) and locks.loosens("max_pause_minutes", 30, 60)
    assert not locks.loosens("strict_mode", False, True) and locks.loosens("ask_server", True, False)
    pid = "p1"
    stricter = {pid: {"thresholds": {**s.thresholds.model_dump(), "cover": 0.7}}}
    assert not locks.loosens("person_overrides", {}, stricter, s)
    assert locks.loosens("person_overrides", stricter, {}, s)   # dropping a stricter override loosens


def test_locks_start_off_turn_on_free_and_off_needs_a_code(ctx, client, household, device):
    assert ctx.settings.load().locks_on is False
    form = dict(PROTECTION_FORM, blocked_apps="")               # removing every blocked app: free while testing
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"] and ctx.settings.load().blocked_apps == []
    on = {**form, "locks_on": "on", **{f"lock.{g}": "on" for g in ("protection", "apps", "timing", "pauses", "retention", "partners")}}
    r = client.post("/settings/protection", data=on, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"] and ctx.settings.load().locks_on is True
    off = {k: v for k, v in on.items() if k != "locks_on"}
    r = client.post("/settings/protection", data=off, headers=as_login(ADMIN), follow_redirects=False)
    assert "turning locks off" in unquote(r.headers["location"]) and ctx.settings.load().locks_on is True

def test_exempt_apps_reach_the_policy_and_adding_one_loosens(ctx, client, household, device):
    from app.services import locks
    assert locks.loosens("exempt_apps", [], ["com.google.android.apps.photos"])
    assert not locks.loosens("exempt_apps", ["com.google.android.apps.photos"], [])
    r = client.post("/settings/protection", data=dict(PROTECTION_FORM, exempt_apps="com.google.android.apps.photos",
                    **{f"lock.{g}": "on" for g in ("protection", "apps", "timing", "pauses", "retention", "partners")}),
                    headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"]                 # locks are off while testing
    _, headers = device
    assert client.get("/api/v1/policy", headers=headers).json()["exempt_apps"] == ["com.google.android.apps.photos"]
    with ctx.conn:
        ctx.settings.set("locks_on", True, "owner")
    form = dict(PROTECTION_FORM, exempt_apps="com.google.android.apps.photos\ncom.example.more", locks_on="on",
                **{f"lock.{g}": "on" for g in ("protection", "apps", "timing", "pauses", "retention", "partners")})
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "apps not watched" in unquote(r.headers["location"])


def test_watch_levels_reach_the_policy_and_lighter_levels_loosen(ctx, client, household, device):
    from app.services import locks
    assert locks.loosens("watch_default", "realtime", "checked")
    assert not locks.loosens("watch_default", "checked", "realtime")
    assert locks.loosens("checked_apps", [], ["com.google.android.gm"], ctx.settings.load())
    assert locks.loosens("realtime_apps", ["com.instagram.android"], [])
    assert locks.loosens("checked_interval_s", 60, 120)
    form = dict(PROTECTION_FORM, checked_apps="com.google.android.gm", checked_interval_s="90")
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    _, headers = device
    policy = client.get("/api/v1/policy", headers=headers).json()
    assert policy["checked_apps"] == ["com.google.android.gm"] and policy["checked_interval_s"] == 90
    assert policy["watch_default"] == "realtime" and "com.instagram.android" in policy["realtime_apps"]
    # Moving an app from Not watched to checked watches it more: no code needed.
    with ctx.conn:
        ctx.settings.set("exempt_apps", ["com.google.android.apps.photos"], "owner")
    assert not locks.loosens("checked_apps", [], ["com.google.android.apps.photos"], ctx.settings.load())


def test_follow_scroll_is_off_by_default_and_can_be_turned_on(ctx, client, household, device):
    from app.services import locks
    assert locks.loosens("follow_scroll", True, False)
    _, headers = device
    assert client.get("/api/v1/policy", headers=headers).json()["follow_scroll"] is False
    r = client.post("/settings/protection", data=dict(PROTECTION_FORM, follow_scroll="on"), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    policy = client.get("/api/v1/policy", headers=headers).json()
    assert policy["follow_scroll"] is True and policy["strict_mode"] is True
