from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.clock import iso
from app.services import digest, jobs
from tests.conftest import jpeg_bytes
from tests.test_api import heartbeat


def test_silent_device_is_reported_once(ctx, client, device, clock, mailer):
    _, headers = device
    heartbeat(client, headers, clock)
    clock.advance(minutes=20)
    assert jobs.heartbeat_watch(ctx) == 0
    clock.advance(minutes=15)
    assert jobs.heartbeat_watch(ctx) == 1
    assert jobs.heartbeat_watch(ctx) == 0
    assert len(mailer.sent) == 1 and "stopped checking in" in mailer.sent[0].text
    # A new heartbeat clears the gap so a later one is reported again.
    heartbeat(client, headers, clock)
    clock.advance(minutes=40)
    assert jobs.heartbeat_watch(ctx) == 1


def test_retention_removes_images_then_events(ctx, client, device, clock, opinion):
    _, headers = device
    ev_id = str(uuid.uuid4())
    client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "image_covered", "occurred_at": iso(clock.now()), "action": "covered",
        "has_image": True}]})
    client.put(f"/api/v1/events/{ev_id}/image", headers=headers, content=jpeg_bytes())
    assert ctx.blobs.exists(ev_id)
    clock.advance(days=8)
    images, events = jobs.retention(ctx)
    assert images == 1 and events == 0
    assert not ctx.blobs.exists(ev_id)
    assert ctx.events.get(ev_id)["image_status"] == "expired"
    clock.advance(days=360)
    assert jobs.retention(ctx)[1] == 1
    assert ctx.events.get(ev_id) is None


def test_digest_is_due_at_the_configured_moment(ctx, client, device, clock):
    _, headers = device
    heartbeat(client, headers, clock)
    with ctx.conn:
        ctx.settings.set("timezone", "America/New_York")
        ctx.settings.set("digest_day", "wednesday")
        ctx.settings.set("digest_time", "07:00")
    person = device[0]["person_id"]
    clock.set(datetime(2026, 11, 4, 11, 59, tzinfo=UTC))   # Wed 06:59 New York
    assert not digest.is_due(ctx, person, clock.now())
    clock.set(datetime(2026, 11, 4, 12, 1, tzinfo=UTC))    # Wed 07:01
    assert digest.is_due(ctx, person, clock.now())
    assert jobs.digests_due(ctx) == 1
    assert not digest.is_due(ctx, person, clock.now())
    clock.set(datetime(2026, 11, 11, 12, 1, tzinfo=UTC))   # next Wednesday
    assert digest.is_due(ctx, person, clock.now())


def test_digest_counts_hours_items_pauses_and_changes(ctx, client, device, household, clock, opinion, mailer):
    dev, headers = device
    for _ in range(12):            # one hour of heartbeats
        heartbeat(client, headers, clock)
        clock.advance(minutes=5)
    ev_id = str(uuid.uuid4())
    client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "image_covered", "occurred_at": iso(clock.now()), "app": "org.mozilla.firefox",
        "action": "covered", "scores": {"classifier": 0.9}, "has_image": True}]})
    opinion.fixed = 0.95
    client.put(f"/api/v1/events/{ev_id}/image", headers=headers, content=jpeg_bytes())
    with ctx.conn:
        _, code = ctx.codes.create_code(household["person_id"], household["sam"], 15)
        ctx.settings.set("image_retention_days", 10, "owner@example.com")
    client.post("/api/v1/unlock", headers=headers, json={"code": code})
    mailer.sent.clear()
    assert digest.send_digest(ctx, household["person_id"])
    mail = mailer.sent[-1]
    assert set(mail.to) == {"alex@example.com", "sam@example.com", "pat@example.com"}
    assert "1 explicit image confirmed" in mail.subject
    assert "1 hour protected" in mail.subject
    assert "Pauses: 1" in mail.text and "15 min" in mail.text
    assert "image_retention_days" in mail.text
    assert mail.inline and mail.inline[0].mime == "image/webp"
    # Settings changes are reported once.
    assert ctx.settings.unreported_changes() == []
    assert "image_retention_days" not in digest.render(
        ctx, "Alex", digest.summarise(ctx, household["person_id"], clock.now(), clock.now()), clock.now(),
        clock.now(), __import__("zoneinfo").ZoneInfo("UTC"))[0]


def test_all_clear_headline(ctx, household, clock):
    summary = digest.summarise(ctx, household["person_id"], clock.now(), clock.now())
    assert digest.headline(summary).startswith("All clear")
