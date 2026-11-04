"""ADR-055: a partner who runs Watchman keeps their key on their own server and acts from there."""

from __future__ import annotations

import base64
import json
import uuid
from urllib.parse import unquote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi.testclient import TestClient

from app.clock import iso
from app.main import create_app
from app.services import keys, ledger, witness
from app.store.ledger_store import canonical
from tests.conftest import ADMIN, PARTNER, as_login, jpeg_bytes
from tests.test_keys import Browser
from tests.test_witness import _token, _watcher

WRAPPED = {"salt": "AAAA", "iterations": 600000, "iv": "AAAA", "data": "AAAA"}


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class OwnServer:
    """Sam's browser on Sam's own server."""

    def __init__(self, wclient, wid: str, partner_id: str) -> None:
        self.c, self.wid, self.partner_id = wclient, wid, partner_id
        self.sign_key = ec.generate_private_key(ec.SECP256R1())
        self.enc_key = ec.generate_private_key(ec.SECP256R1())

    def make_key(self):
        sign_pub = self.sign_key.public_key().public_bytes(serialization.Encoding.DER,
                                                           serialization.PublicFormat.SubjectPublicKeyInfo)
        enc_pub = self.enc_key.public_key().public_bytes(serialization.Encoding.X962,
                                                         serialization.PublicFormat.UncompressedPoint)
        return self.c.post(f"/watching/{self.wid}/key", json={"partner_id": self.partner_id, "sign_pub": _b64(sign_pub),
                                                               "enc_pub": _b64(enc_pub), "wrapped": WRAPPED})

    def form(self, action: str, params: dict) -> dict:
        nonce = self.c.post(f"/watching/{self.wid}/challenge", json={"partner_id": self.partner_id}).json()["nonce"]
        payload = canonical({"v": 1, "action": action, "params": params, "nonce": nonce, "partner_id": self.partner_id})
        r, s = decode_dss_signature(self.sign_key.sign(payload.encode(), ec.ECDSA(hashes.SHA256())))
        return {"approval_payload": payload, "approval_sig": _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big")),
                "approval_partner": self.partner_id}


def _setup(ctx, client, household, clock, tmp_path):
    w, mail = _watcher(tmp_path, clock, client)
    wclient = TestClient(create_app(ctx=w, run_jobs=False))
    token = _token(client, household)
    r = wclient.post("/watching", data={"name": "Alex's household", "url": "https://alex.example", "token": token,
                                        "email": "sam@example.com"}, follow_redirects=False)
    assert r.status_code == 303
    wid = witness.watched(w)[0]["id"]
    sam = OwnServer(wclient, wid, household["sam"])
    assert sam.make_key().status_code == 200
    return w, mail, wclient, wid, sam


def test_the_key_lives_on_the_partners_server(ctx, client, household, clock, tmp_path, mailer):
    w, mail, wclient, wid, sam = _setup(ctx, client, household, clock, tmp_path)
    key = keys.key_for(ctx, household["sam"])
    assert json.loads(key["wrapped"]) == keys.HELD_ELSEWHERE and keys.sealed(ctx)
    assert client.get("/keys/mine", headers=as_login(PARTNER)).json()["keys"] == []      # nothing to unlock there
    assert json.loads(witness.watched(w)[0]["my_key"])["wrapped"] == WRAPPED
    assert any("on their own Watchman server" in m.text for m in mailer.sent)
    page = wclient.get(f"/watching/{wid}").text
    assert key["fingerprint"] in page and "different key" not in page


def test_approve_review_and_peek_from_the_partners_server(ctx, client, household, device, clock, tmp_path):
    w, mail, wclient, wid, sam = _setup(ctx, client, household, clock, tmp_path)
    with ctx.conn:
        ctx.settings.set("locks_on", True, ADMIN)
    from tests.test_portal import LOCK_IDS, PROTECTION_FORM
    form = {**PROTECTION_FORM, **{f"lock.{g}": "on" for g in LOCK_IDS}, "locks_on": "on",
            "max_pause_minutes": "120"}
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=pending" in r.headers["location"]
    page = wclient.get(f"/watching/{wid}").text
    assert "loosen the longest pause" in page
    req = ctx.conn.execute("SELECT * FROM approvals").fetchone()
    signed = {"id": req["id"], "digest": ledger.digest(json.loads(req["params"]))}
    r = wclient.post(f"/watching/{wid}/approve/{req['id']}", data=sam.form("approve", signed), follow_redirects=False)
    assert "done=approved" in r.headers["location"], unquote(r.headers["location"])
    assert ctx.settings.load().max_pause_minutes == 120

    ev = str(uuid.uuid4())
    client.post("/api/v1/events", headers=device[1], json={"events": [{
        "id": ev, "type": "image_covered", "occurred_at": iso(clock.now()), "action": "covered",
        "url": "https://example.com/hidden", "has_image": True, "hash": "3f3f3f3f3f3f3f3f"}]})
    client.put(f"/api/v1/events/{ev}/image", headers=device[1], content=jpeg_bytes())
    with ctx.conn:
        ctx.conn.execute("UPDATE events SET severity = 'review' WHERE id = ?", (ev,))
        ctx.ledger.append("judged", {"second_opinion": 0.9, "severity": "review"}, household["person_id"], ev)
    page = wclient.get(f"/watching/{wid}").text
    assert f'data-aad="{ev}"' in page and "hidden" not in page.split(ev)[1][:4000].replace("data-sealed", "")
    assert wclient.get(f"/watching/{wid}/image/{ev}").content[:4] == b"RIFF"
    r = wclient.post(f"/watching/{wid}/peek/{ev}")
    assert r.headers["x-watchman-sealed"] == "1"
    assert keys.open_sealed(r.content, ev + ":medium", household["sam"], sam.enc_key)[:4] == b"RIFF"
    assert wclient.post(f"/watching/{wid}/peek/{ev}").status_code == 429                    # the cooldown holds
    r = wclient.post(f"/watching/{wid}/review/{ev}", data={"state": "talked", **sam.form("review", {
        "event_id": ev, "state": "talked"})}, follow_redirects=False)
    assert "done=reviewed" in r.headers["location"] and ctx.events.get(ev)["review_state"] == "talked"
    assert ledger.verify(ctx) == []


def test_a_key_set_up_as_the_partner_elsewhere_is_reported(ctx, client, household, clock, tmp_path):
    w, mail, wclient, wid, sam = _setup(ctx, client, household, clock, tmp_path)
    witness.poll(w, w.witness, witness.watched(w)[0])
    mail.sent.clear()
    # Someone with Alex's server sets up a key "as Sam" on its own portal.
    Browser(client, household["sam"]).set_up()
    clock.advance(minutes=6)
    witness.poll_all(w, w.witness)
    assert any("Someone there is acting as you" in m.text for m in mail.sent)
    assert "different key" in wclient.get(f"/watching/{wid}").text


def test_unsigned_actions_through_the_token_are_refused(ctx, client, household, device, clock, tmp_path):
    w, mail, wclient, wid, sam = _setup(ctx, client, household, clock, tmp_path)
    token = json.loads(json.dumps(witness.watched(w)[0]["token"]))
    auth = {"Authorization": f"Bearer {token}"}
    ev = str(uuid.uuid4())
    client.post("/api/v1/events", headers=device[1], json={"events": [{
        "id": ev, "type": "image_covered", "occurred_at": iso(clock.now()), "action": "covered"}]})
    assert client.post(f"/api/v1/witness/review/{ev}", headers=auth, json={"state": "talked"}).status_code == 400
    head = client.get("/api/v1/witness/status", headers=auth).json()["head"]
    nonce = client.post("/api/v1/witness/challenge", headers=auth).json()["nonce"]
    other = ec.generate_private_key(ec.SECP256R1())
    payload = canonical({"v": 1, "action": "checkpoint", "params": head, "nonce": nonce, "partner_id": household["sam"]})
    r_, s_ = decode_dss_signature(other.sign(payload.encode(), ec.ECDSA(hashes.SHA256())))
    bad = {"payload": payload, "sig": _b64(r_.to_bytes(32, "big") + s_.to_bytes(32, "big")),
           "partner_id": household["sam"]}
    assert client.post("/api/v1/witness/checkpoint", headers=auth, json=bad).status_code == 400
