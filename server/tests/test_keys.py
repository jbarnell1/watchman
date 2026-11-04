"""ADR-053: partner keys seal what partners review and sign every loosening. A Python stand-in
plays the partner's browser; test_seal_js_and_the_server_agree runs the real seal.js in Node."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import unquote

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.clock import iso
from app.services import keys, ledger
from app.store.ledger_store import canonical
from tests.conftest import ADMIN, PARTNER, as_login, jpeg_bytes

WRAPPED = {"salt": "AAAA", "iterations": 600000, "iv": "AAAA", "data": "AAAA"}


class Browser:
    """What a partner's browser does with seal.js, in Python."""

    def __init__(self, client, partner_id: str, login: str = PARTNER) -> None:
        self.client, self.partner_id, self.login = client, partner_id, login
        self.sign_key = ec.generate_private_key(ec.SECP256R1())
        self.enc_key = ec.generate_private_key(ec.SECP256R1())

    def set_up(self):
        sign_pub = self.sign_key.public_key().public_bytes(serialization.Encoding.DER,
                                                           serialization.PublicFormat.SubjectPublicKeyInfo)
        enc_pub = self.enc_key.public_key().public_bytes(serialization.Encoding.X962,
                                                         serialization.PublicFormat.UncompressedPoint)
        return self.client.post("/keys", headers=as_login(self.login), json={
            "partner_id": self.partner_id, "sign_pub": base64.b64encode(sign_pub).decode(),
            "enc_pub": base64.b64encode(enc_pub).decode(), "wrapped": WRAPPED})

    def approval(self, action: str, params: dict, nonce: str | None = None, key=None) -> dict:
        if nonce is None:
            nonce = self.client.post("/keys/challenge", headers=as_login(self.login),
                                     json={"partner_id": self.partner_id}).json()["nonce"]
        payload = canonical({"v": 1, "action": action, "params": params, "nonce": nonce,
                             "partner_id": self.partner_id})
        r, s = decode_dss_signature((key or self.sign_key).sign(payload.encode(), ec.ECDSA(hashes.SHA256())))
        sig = base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()
        return {"payload": payload, "sig": sig, "partner_id": self.partner_id}

    def form(self, action: str, params: dict, **kw) -> dict:
        a = self.approval(action, params, **kw)
        return {"approval_payload": a["payload"], "approval_sig": a["sig"], "approval_partner": a["partner_id"]}


@pytest.fixture
def sam(client, household, mailer):
    b = Browser(client, household["sam"])
    assert b.set_up().status_code == 200
    mailer.sent.clear()
    return b


def test_setting_up_a_key_seals_the_household_and_tells_everyone(ctx, client, household, mailer):
    b = Browser(client, household["sam"])
    fp = b.set_up().json()["fingerprint"]
    assert keys.sealed(ctx) and len(fp.split()) == 8
    assert any("set up their partner key" in m.subject and "alex@example.com" in m.to for m in mailer.sent)
    assert [e for e in ctx.ledger.of_kind("partner_key")][0]["body"]["fingerprint"] == fp
    # Only that partner can set their key.
    assert Browser(client, household["sam"], ADMIN).set_up().status_code == 403


def test_loosening_waits_for_a_signed_approval(ctx, client, household, device, sam, mailer):
    from tests.test_portal import LOCK_IDS, PROTECTION_FORM
    form = {**PROTECTION_FORM, **{f"lock.{g}": "on" for g in LOCK_IDS}, "thresholds.cover": "0.95"}
    r = client.post("/settings/protection", data=form, headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=pending" in r.headers["location"]
    assert ctx.settings.load().thresholds.cover == 0.85                      # not applied
    req = ctx.conn.execute("SELECT * FROM approvals").fetchone()
    assert any(f"/approve/{req['id']}" in m.text for m in mailer.sent)
    params = {"id": req["id"], "digest": ledger.digest(json.loads(req["params"]))}
    # The admin acting with a forged partner login but no key cannot approve.
    stranger = Browser(client, household["sam"])
    r = client.post(f"/approve/{req['id']}", data=stranger.form("approve", params), headers=as_login(PARTNER),
                    follow_redirects=False)
    assert "signature" in unquote(r.headers["location"]) and ctx.settings.load().thresholds.cover == 0.85
    # A signature for some other change does not count.
    r = client.post(f"/approve/{req['id']}", data=sam.form("approve", {"id": req["id"], "digest": "x"}),
                    headers=as_login(PARTNER), follow_redirects=False)
    assert "different change" in unquote(r.headers["location"])
    good = sam.form("approve", params)
    r = client.post(f"/approve/{req['id']}", data=good, headers=as_login(PARTNER), follow_redirects=False)
    assert "done=approved" in r.headers["location"] and ctx.settings.load().thresholds.cover == 0.95
    setting = [e for e in ctx.ledger.of_kind("setting") if e["body"]["key"] == "thresholds"][-1]
    assert keys.verify_approval(keys.key_for(ctx, household["sam"])["sign_pub"], setting["body"]["approval"])
    assert client.get(f"/approve/{req['id']}", headers=as_login(PARTNER)).status_code == 200
    # The same approval cannot be used twice.
    with ctx.conn:
        ctx.conn.execute("UPDATE approvals SET status = 'waiting' WHERE id = ?", (req["id"],))
    r = client.post(f"/approve/{req['id']}", data=good, headers=as_login(PARTNER), follow_redirects=False)
    assert "already used" in unquote(r.headers["location"])


def test_partner_changes_and_settings_codes_need_approval(ctx, client, household, sam):
    r = client.post("/settings/partners/add", data={"person": household["person_id"], "name": "Sock",
                                                     "email": "sock@x.com", "link_type": "email"},
                    headers=as_login(ADMIN), follow_redirects=False)
    assert "saved=pending" in r.headers["location"] and len(ctx.people.partners_for(household["person_id"])) == 2
    r = client.post("/codes", data={"person": household["person_id"], "purpose": "settings"},
                    headers=as_login(PARTNER))
    assert r.status_code == 400 and "approval" in r.json()["error"]["message"]


def test_pause_codes_and_reviews_carry_signatures(ctx, client, household, device, sam, clock, mailer):
    pid = household["person_id"]
    r = client.post("/codes", data={"person": pid, "purpose": "pause", "minutes": "10"}, headers=as_login(PARTNER))
    assert r.status_code == 400                                                  # unsigned
    r = client.post("/codes", data={"person": pid, "purpose": "pause", "minutes": "10",
                                    **sam.form("pause_code", {"person_id": pid, "minutes": 10})},
                    headers=as_login(PARTNER), follow_redirects=False)
    code = unquote(r.headers["location"]).split("code=")[1].split("&")[0]
    assert client.post("/api/v1/unlock", headers=device[1], json={"code": code}).status_code == 200
    assert not [m for m in mailer.sent if "no partner signed" in m.subject]
    # A code the server makes on its own (emailed to a partner without a key) pauses, loudly.
    client.post("/api/v1/unlock/request", headers=device[1], json={"minutes": 5, "reason": "x"})
    pat_code = [m for m in mailer.sent if m.to == ["pat@example.com"]][-1].text.split("code: ")[1].split()[0]
    assert client.post("/api/v1/unlock", headers=device[1], json={"code": pat_code}).status_code == 200
    assert any("no partner signed" in m.subject for m in mailer.sent)

    ev = str(uuid.uuid4())
    client.post("/api/v1/events", headers=device[1], json={"events": [{
        "id": ev, "type": "image_covered", "occurred_at": iso(clock.now()), "action": "covered",
        "url": "https://example.com/x", "has_image": True, "hash": "1f1f1f1f1f1f1f1f"}]})
    with ctx.conn:
        ctx.conn.execute("UPDATE events SET severity = 'review' WHERE id = ?", (ev,))
        ctx.ledger.append("judged", {"second_opinion": 0.9, "severity": "review"}, pid, ev)
    assert client.post(f"/review/{ev}", data={"state": "talked"}, headers=as_login(PARTNER)).status_code == 400
    r = client.post(f"/review/{ev}", data={"state": "talked", **sam.form("review", {"event_id": ev, "state": "talked"})},
                    headers=as_login(PARTNER), follow_redirects=False)
    assert r.status_code == 303 and ctx.events.get(ev)["review_state"] == "talked"
    assert ledger.verify(ctx) == []


def test_addresses_and_peekable_pictures_are_sealed(ctx, client, household, device, sam, clock):
    ev = str(uuid.uuid4())
    r = client.post("/api/v1/events", headers=device[1], json={"events": [{
        "id": ev, "type": "image_covered", "occurred_at": iso(clock.now()), "action": "covered",
        "url": "https://example.com/secret", "title": "a page", "has_image": True, "hash": "2f2f2f2f2f2f2f2f"}]})
    row = ctx.events.get(ev)
    assert row["url"] is None and row["title"] is None and "secret" not in (row["sealed"] or "")
    opened = json.loads(keys.open_sealed(row["sealed"].encode(), ev, household["sam"], sam.enc_key))
    assert opened == {"url": "https://example.com/secret", "title": "a page"}
    client.put(f"/api/v1/events/{ev}/image", headers=device[1], content=jpeg_bytes())
    assert ctx.events.image(ev)["sealed"] == 1
    token = client.post(f"/peek/{ev}", headers=as_login(PARTNER)).json()["url"]
    r = client.get(token, headers=as_login(PARTNER))
    assert r.headers["x-watchman-sealed"] == "1"
    webp = keys.open_sealed(r.content, ev + ":medium", household["sam"], sam.enc_key)
    assert webp[:4] == b"RIFF"
    assert r.text.count(household["sam"]) == 1 and "RIFF" not in r.text
    assert ledger.verify(ctx) == []


def test_a_replaced_key_waits_three_days(ctx, client, household, sam, clock, mailer):
    new = Browser(client, household["sam"])
    new.set_up()
    assert any("replaced their partner key" in m.subject for m in mailer.sent)
    with pytest.raises(Exception, match="counts from"):
        keys.check(ctx, new.approval("x", {}), "x", {})
    clock.advance(days=3, minutes=1)
    assert keys.check(ctx, new.approval("x", {}), "x", {})["partner_id"] == household["sam"]


def test_checkpoints_are_signed_heads(ctx, client, household, sam):
    seq, h = ctx.ledger.head()
    r = client.post("/record/checkpoint", headers=as_login(PARTNER), json=sam.approval("checkpoint",
                                                                                       {"seq": seq, "hash": h}))
    assert r.status_code == 200
    cp = ctx.ledger.of_kind("checkpoint")[-1]
    assert cp["body"]["seq"] == seq and keys.verify_approval(keys.key_for(ctx, household["sam"])["sign_pub"],
                                                             cp["body"]["approval"])
    bad = client.post("/record/checkpoint", headers=as_login(PARTNER),
                      json=sam.approval("checkpoint", {"seq": seq, "hash": "0" * 64}))
    assert bad.status_code == 400
    assert client.get("/record", headers=as_login(PARTNER)).status_code == 200
    assert client.get("/keys", headers=as_login(PARTNER)).status_code == 200


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_seal_js_and_the_server_agree(ctx, client, household):
    script = Path(__file__).parent / "js" / "seal_interop.mjs"
    partner = household["sam"]

    def run(data):
        out = subprocess.run(["node", str(script)], input=json.dumps(data), capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    first = run({"phase": "setup", "partner_id": partner, "passphrase": "correct horse battery",
                 "action": "approve", "params": {"id": "a1", "digest": "d"}, "nonce": "n1"})
    body, approval = first["body"], first["approval"]
    assert keys.verify_approval(body["sign_pub"], approval)
    assert json.loads(approval["payload"]) == {"v": 1, "action": "approve", "params": {"id": "a1", "digest": "d"},
                                               "nonce": "n1", "partner_id": partner}
    fp = keys.set_key(ctx, ctx.people.get_partner(partner), body["sign_pub"], body["enc_pub"], body["wrapped"])
    assert fp
    envelope = keys.seal(b"hello from the server", "ev1:medium", keys.keyed_partners(ctx))
    second = run({"phase": "open", "partner_id": partner, "passphrase": "correct horse battery",
                  "wrapped": body["wrapped"], "envelope": envelope.decode(), "aad": "ev1:medium"})
    assert second == {"unlocked": 1, "text": "hello from the server"}
