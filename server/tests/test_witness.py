"""ADR-054: a partner's own server watches this household's record."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.clients.identity import FakeIdentity, Identity
from app.clients.mailer import FakeMailer
from app.clients.second_opinion import FakeSecondOpinion
from app.clients.witness_client import FakeWitnessClient, test_client_handler
from app.config import Config
from app.main import build_context, create_app
from app.services import jobs, witness
from tests.conftest import ADMIN, PARTNER, as_login


def _token(client, household) -> str:
    r = client.post("/record/witness", data={"partner_id": household["sam"]}, headers=as_login(PARTNER),
                    follow_redirects=False)
    assert r.status_code == 200 and "token=" not in str(r.url)
    return re.search(r"wmw_[A-Za-z0-9_-]+", r.text).group(0)


def _watcher(tmp_path, clock, watched_client):
    """Sam's own household server, which watches Alex's."""
    mail = FakeMailer()
    ctx = build_context(Config(data_dir=tmp_path / "sam", models_dir=tmp_path / "m", fake_second_opinion=True),
                        clock=clock, identity=FakeIdentity(Identity(login="sam@example.com", name="Sam")),
                        mailer=mail, second_opinion=FakeSecondOpinion(0.0), db_path=":memory:")

    ctx.witness = FakeWitnessClient(test_client_handler(watched_client))
    return ctx, mail


def test_the_witness_api_needs_a_token(client, household):
    assert client.get("/api/v1/witness/status").status_code == 401
    token = _token(client, household)
    r = client.get("/api/v1/witness/ledger", params={"after": 0, "limit": 5},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["entries"][0]["kind"] == "genesis"
    assert client.get("/api/v1/witness/status", headers={"Authorization": f"Bearer {token}"}).json()["problems"] == []


def test_a_watcher_hears_about_loosening_new_devices_and_rewrites(ctx, client, household, clock, tmp_path):
    token = _token(client, household)
    w, mail = _watcher(tmp_path, clock, client)
    witness.add_watch(w, "Alex's household", "https://alex.example", token, "sam@example.com")
    assert witness.poll_all(w, w.witness) == 1 and not mail.sent           # the first read is the baseline
    # Loosening, a device, a partner: all reach Sam through Sam's own server.
    with ctx.conn:
        ctx.settings.set("max_pause_minutes", 240, ADMIN)
        code = ctx.devices.create_pairing_code(household["person_id"])
    client.post("/api/v1/pair", json={"code": code, "device": {"name": "Spare", "platform": "android", "kind": "app"}})
    clock.advance(minutes=6)
    assert witness.poll_all(w, w.witness) == 1
    text = mail.sent[-1].text
    assert "loosened the longest pause" in text and "a device was paired: Spare" in text
    # Rewriting history is caught even when the rewritten record is consistent with itself.
    mail.sent.clear()
    from app.store.ledger_store import entry_hash
    rows = ctx.conn.execute("SELECT * FROM ledger ORDER BY seq").fetchall()
    prev = "0" * 64
    for r in rows:
        body = r["body"].replace("240", "60")
        h = entry_hash(prev, r["seq"], r["at"], r["kind"], r["person_id"], r["ref"], __import__("json").loads(body))
        ctx.conn.execute("UPDATE ledger SET body = ?, prev = ?, hash = ? WHERE seq = ?", (body, prev, h, r["seq"]))
        prev = h
    clock.advance(minutes=6)
    witness.poll_all(w, w.witness)
    assert any("was rewritten" in m.text for m in mail.sent)


def test_a_watched_server_that_goes_quiet_is_reported(ctx, client, household, clock, tmp_path):
    token = _token(client, household)
    w, mail = _watcher(tmp_path, clock, client)
    witness.add_watch(w, "Alex's household", "https://alex.example", token, "sam@example.com")
    witness.poll_all(w, w.witness)
    w.witness.down = True
    for _ in range(5):
        clock.advance(minutes=6)
        jobs.watch_others(w)
    assert len([m for m in mail.sent if "cannot be reached" in m.subject]) == 1
    clock.advance(hours=6)
    jobs.watch_others(w)
    assert len([m for m in mail.sent if "cannot be reached" in m.subject]) == 2
    w.witness.down = False
    clock.advance(minutes=6)
    jobs.watch_others(w)
    assert any("can be reached again" in m.subject for m in mail.sent)


def test_watching_page_adds_and_lists(ctx, client, household, tmp_path, clock):
    token = _token(client, household)
    ctx.witness = FakeWitnessClient(test_client_handler(TestClient(create_app(ctx=ctx, run_jobs=False))))
    r = client.post("/watching", data={"name": "Self", "url": "https://self.example", "token": token,
                                       "email": "owner@example.com"}, headers=as_login(ADMIN), follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/watching", headers=as_login(ADMIN)).text
    assert "Self" in page and "checked" in page
