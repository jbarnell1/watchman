"""ADR-052: the record is a hash chain, and the database must match it."""

from __future__ import annotations

import uuid

from app.clock import iso
from app.services import jobs, ledger
from app.store.ledger_store import check_chain
from tests.conftest import ADMIN, PARTNER, as_login, jpeg_bytes
from tests.test_api import heartbeat


def _covered(ctx, client, headers, clock) -> str:
    ev_id = str(uuid.uuid4())
    r = client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "image_covered", "occurred_at": iso(clock.now()), "app": "org.mozilla.firefox",
        "url": "https://example.com/a", "action": "covered", "has_image": True, "hash": "0f0f0f0f0f0f0f0f",
        "scores": {"classifier": 0.95}}]})
    assert ev_id in r.json()["upload"]
    client.put(f"/api/v1/events/{ev_id}/image", headers=headers, content=jpeg_bytes())
    return ev_id


def test_a_working_household_matches_its_record(ctx, client, device, clock, household):
    _, headers = device
    heartbeat(client, headers, clock)
    ev = _covered(ctx, client, headers, clock)
    client.post(f"/review/{ev}", data={"state": "talked"}, headers=as_login(PARTNER))
    client.post("/codes", data={"person": household["person_id"], "purpose": "pause", "minutes": "10"},
                headers=as_login(PARTNER))
    with ctx.conn:
        ctx.settings.set("strict_mode", False, ADMIN)
    clock.advance(days=400)
    jobs.retention(ctx)
    assert ledger.verify(ctx) == []
    entries = ctx.ledger.entries(0, 10**6)
    assert entries[0]["kind"] == "genesis" and check_chain(entries) is None
    kinds = {e["kind"] for e in entries}
    assert {"event", "judged", "image", "review", "code_issued", "setting", "partner_added", "device_paired",
            "purge", "image_removed"} <= kinds


def test_edits_to_the_database_show(ctx, client, device, clock, household):
    _, headers = device
    ev = _covered(ctx, client, headers, clock)
    c = ctx.conn
    c.execute("UPDATE events SET severity = 'explicit' WHERE id = ?", (ev,))
    c.execute("UPDATE settings SET value = '\"monday\"' WHERE key = 'digest_day'")
    c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('digest_day', '\"monday\"')")
    c.execute("INSERT INTO partners(id, person_id, name, email, link_type, login, created_at) "
              "VALUES ('x', ?, 'Sock', 'sock@x', 'linked', 'sock@x', '2026-01-01')", (household["person_id"],))
    c.execute("INSERT INTO logins(login, role, person_id, partner_id) VALUES ('sock@x', 'partner', ?, 'x')",
              (household["person_id"],))
    c.execute("UPDATE devices SET revoked_at = '2026-01-01' WHERE id = ?", (device[0]["id"],))
    ctx.config.images_dir.joinpath(f"{ev}.medium.bin").write_bytes(b"swapped")
    problems = "\n".join(ledger.verify(ctx))
    for words in (f"event {ev} severity", "setting digest_day", "partner x", "portal login sock@x",
                  f"device {device[0]['id']}", "medium image"):
        assert words in problems, words


def test_deleting_events_or_rewriting_the_record_shows(ctx, client, device, clock):
    _, headers = device
    ev = _covered(ctx, client, headers, clock)
    ctx.conn.execute("DELETE FROM images WHERE event_id = ?", (ev,))
    ctx.conn.execute("DELETE FROM events WHERE id = ?", (ev,))
    assert any("deleted before its retention" in p for p in ledger.verify(ctx))
    ctx.conn.execute("UPDATE ledger SET body = '{}' WHERE seq = 2")
    assert ledger.verify(ctx)[0].startswith("the record itself was altered: entry 2 was changed")


def test_every_email_carries_the_head(ctx, client, device, clock, mailer):
    _, headers = device
    heartbeat(client, headers, clock)
    heartbeat(client, headers, clock, accessibility=False)
    seq, h = ctx.ledger.head()
    assert mailer.sent and "Record: entry" in mailer.sent[-1].text


def test_the_hourly_check_alerts_once_per_new_problem(ctx, client, device, clock, mailer):
    jobs._last_verify.clear()
    assert jobs.verify_record(ctx) == 0
    ctx.conn.execute("UPDATE devices SET revoked_at = '2026-01-01' WHERE id = ?", (device[0]["id"],))
    assert jobs.verify_record(ctx) == 0                      # within the hour
    clock.advance(hours=1, minutes=1)
    assert jobs.verify_record(ctx) == 1
    assert any("no longer matches the record" in m.subject for m in mailer.sent)
    clock.advance(hours=1, minutes=1)
    assert jobs.verify_record(ctx) == 0                      # already reported
