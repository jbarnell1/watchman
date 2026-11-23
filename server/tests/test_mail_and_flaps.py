"""ADR-058: email that does not go out is said on every page, digests count only when sent, a
flapping device sends one email per quiet window, and a silent device says what it did when it
comes back."""

from __future__ import annotations

from app.services import alerts, digest, jobs
from tests.conftest import ADMIN, PARTNER, as_login
from tests.test_liveness import _beat


def _protection_mails(mailer):
    return [m for m in mailer.sent if "protection changed" in m.subject]


def test_a_flapping_device_sends_one_email_then_a_count(ctx, client, device, clock, mailer):
    _, headers = device
    _beat(client, headers, clock)
    for _ in range(5):
        clock.advance(seconds=10)
        _beat(client, headers, clock, protection=False, accessibility=False)
        clock.advance(seconds=1)
        _beat(client, headers, clock)
    first = _protection_mails(mailer)
    assert len(first) == 1
    assert "accessibility service turned off" in first[0].text and "protection turned off" in first[0].text
    # Every change is still in the record.
    offs = ctx.conn.execute("SELECT COUNT(*) FROM events WHERE type = 'protection_changed' AND severity = 'protection'"
                            ).fetchone()[0]
    assert offs == 10
    assert alerts.held_summaries(ctx) == 0          # still inside the quiet window
    clock.advance(minutes=11)
    assert jobs.tick(ctx)["held"] == 1
    summary = _protection_mails(mailer)[-1]
    assert "turned off 4 more times" in summary.text and "Protection is on now." in summary.text
    assert alerts.held_summaries(ctx) == 0


def test_after_the_window_the_next_change_alerts_at_once(ctx, client, device, clock, mailer):
    _, headers = device
    _beat(client, headers, clock)
    _beat(client, headers, clock, protection=False)
    clock.advance(minutes=11)
    _beat(client, headers, clock)
    _beat(client, headers, clock, protection=False)
    assert len(_protection_mails(mailer)) == 2


def test_a_device_back_from_silence_says_what_it_did(ctx, client, device, clock, mailer):
    _, headers = device
    _beat(client, headers, clock)
    clock.advance(minutes=31)
    assert jobs.heartbeat_watch(ctx) == 1
    clock.advance(minutes=58)
    mailer.sent.clear()
    _beat(client, headers, clock, judge={"window_s": 5340, "screen_on_s": 5340, "unwatched_s": 300, "judged": 412})
    back = [m for m in mailer.sent if "checking in again" in m.subject]
    assert len(back) == 1
    text = back[0].text
    assert "after 89 minutes" in text and "screen was on for 89 minutes" in text and "watched 84" in text
    assert "412 checks" in text and "For 5 minutes it was not watching" in text and "Protection is on now." in text
    row = ctx.conn.execute("SELECT severity, detail FROM events WHERE type = 'silence_ended'").fetchone()
    assert row["severity"] == "info" and '"screen_on_s": 5340' in row["detail"]
    # The next heartbeat is ordinary.
    mailer.sent.clear()
    clock.advance(minutes=5)
    _beat(client, headers, clock)
    assert not [m for m in mailer.sent if "checking in again" in m.subject]


def test_a_device_back_with_its_screen_off_says_so(ctx, client, device, clock, mailer):
    _, headers = device
    _beat(client, headers, clock)
    clock.advance(minutes=40)
    jobs.heartbeat_watch(ctx)
    _beat(client, headers, clock, judge={"window_s": 2400, "screen_on_s": 0, "unwatched_s": 0, "judged": 0})
    back = [m for m in mailer.sent if "checking in again" in m.subject]
    assert len(back) == 1 and "screen was off" in back[0].text


def test_no_email_set_up_is_said_on_every_page_and_digests_wait(ctx, client, device, clock, mailer, household):
    _, headers = device
    mailer.ready = False
    _beat(client, headers, clock)
    _beat(client, headers, clock, protection=False)
    assert not mailer.sent
    assert ctx.settings.mail_status()["missed"] == 1
    for login in (ADMIN, PARTNER):
        page = client.get(f"/?person={household['person_id']}", headers=as_login(login)).text
        assert "Email is not set up" in page and "1 message did not go out" in page
    assert not digest.send_digest(ctx, household["person_id"])
    assert ctx.codes.last_digest(household["person_id"]) is None
    mailer.ready = True
    assert digest.send_digest(ctx, household["person_id"])
    assert ctx.codes.last_digest(household["person_id"]) is not None
    assert ctx.settings.mail_status()["missed"] == 0
    assert "Email is" not in client.get("/", headers=as_login(ADMIN)).text


def test_failing_email_is_said_and_digests_retry_hourly(ctx, client, device, clock, mailer, household, monkeypatch):
    tries = []

    def broken(mail):
        tries.append(mail)
        raise OSError("connection refused")

    monkeypatch.setattr(mailer, "send", broken)
    assert not digest.send_digest(ctx, household["person_id"])
    assert len(tries) == 1
    page = client.get("/", headers=as_login(ADMIN)).text
    assert "Email is failing" in page and "connection refused" in page
    assert "connection refused" not in client.get(f"/?person={household['person_id']}", headers=as_login(PARTNER)).text
    clock.advance(minutes=30)
    assert not digest.send_digest(ctx, household["person_id"])
    assert len(tries) == 1
    clock.advance(minutes=31)
    assert not digest.send_digest(ctx, household["person_id"])
    assert len(tries) == 2
    assert ctx.codes.last_digest(household["person_id"]) is None
