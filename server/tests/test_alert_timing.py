"""ADR-062: each person and partner hears about explicit images and protection turning off either
by email right away or only in the weekly digest. Moving to the digest is loud and locked."""

from __future__ import annotations

from urllib.parse import unquote

from app.services import alerts, digest
from tests.conftest import ADMIN, as_login
from tests.test_api import heartbeat
from tests.test_loosening import _settings_code


def test_each_choice_decides_who_is_emailed_at_once(ctx, client, household, device, clock, mailer):
    pid = household["person_id"]
    with ctx.conn:
        ctx.people.set_alert_timing("partner", household["pat"], "digest", "now")
        ctx.people.set_alert_timing("person", pid, "now", "digest")
    assert alerts.recipients(ctx, pid, "image") == ["alex@example.com", "sam@example.com"]
    assert alerts.recipients(ctx, pid, "protection") == ["sam@example.com", "pat@example.com"]
    assert len(alerts.recipients(ctx, pid)) == 3              # the digest and change alerts reach everyone

    _, headers = device
    heartbeat(client, headers, clock)
    heartbeat(client, headers, clock, accessibility=False)
    sent = [m for m in mailer.sent if "protection changed" in m.subject]
    assert sent and sent[-1].to == ["sam@example.com", "pat@example.com"]


def test_moving_to_the_digest_needs_a_code_and_everyone_hears(ctx, client, household, mailer):
    with ctx.conn:
        ctx.settings.set("locks_on", True, "owner")
    form = {"who": "partner", "id": household["pat"], "image_alerts": "digest", "protection_alerts": "now"}
    r = client.post("/settings/people/alerts", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "settings code" in unquote(r.headers["location"])
    assert ctx.people.get_partner(household["pat"])["image_alerts"] == "now"

    code = _settings_code(ctx, client, household)
    mailer.sent.clear()
    r = client.post("/settings/people/alerts", data=dict(form, partner_code=code), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    assert ctx.people.get_partner(household["pat"])["image_alerts"] == "digest"
    loud = [m for m in mailer.sent if "only in the digest" in m.subject]
    assert loud and "pat@example.com" in loud[0].to and "explicit images" in loud[0].text
    assert any(e["kind"] == "alert_timing" for e in ctx.ledger.entries())

    # Back to right away is tightening: no code, no alert.
    mailer.sent.clear()
    r = client.post("/settings/people/alerts", data=dict(form, image_alerts="now"), headers=as_login(ADMIN),
                    follow_redirects=False)
    assert "saved=1" in r.headers["location"] and mailer.sent == []
    assert ctx.people.get_partner(household["pat"])["image_alerts"] == "now"


def test_the_people_page_shows_the_choices(ctx, client, household):
    page = client.get("/settings", params={"area": "people"}, headers=as_login(ADMIN))
    assert page.status_code == 200
    assert page.text.count('action="/settings/people/alerts"') == 3   # Alex, Sam and Pat
    assert "Weekly digest only" in page.text


def test_the_digest_lists_each_protection_change(ctx, client, household, device, clock):
    _, headers = device
    heartbeat(client, headers, clock)
    heartbeat(client, headers, clock, accessibility=False)
    start, end = digest.period_for(ctx, household["person_id"], clock.now())
    summary = digest.summarise(ctx, household["person_id"], start, end)
    text, html, _ = digest.render(ctx, "Alex", summary, start, end, digest.ZoneInfo("UTC"))
    assert "Protection changes:" in text
    assert "Pixel: the accessibility service turned off" in text


def test_push_follows_its_own_choices(ctx, client, household, device, clock, monkeypatch):
    pushed = []

    class Done:
        def close(self):
            pass

    monkeypatch.setattr(alerts.urllib.request, "urlopen", lambda req, timeout: pushed.append(req) or Done())
    with ctx.conn:
        ctx.settings.set("ntfy_url", "https://ntfy.test/watchman")
        ctx.settings.set("push_protection", False)
    _, headers = device
    heartbeat(client, headers, clock)
    heartbeat(client, headers, clock, accessibility=False)
    assert pushed == []
    with ctx.conn:
        ctx.settings.set("push_protection", True)
    clock.advance(minutes=11)
    heartbeat(client, headers, clock, accessibility=True)
    heartbeat(client, headers, clock, accessibility=False)
    assert len(pushed) == 1
