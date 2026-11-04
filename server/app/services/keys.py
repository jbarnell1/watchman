"""Partner keys (ADR-053): what only a partner can open, and what only a partner can approve.

Each partner makes two P-256 key pairs in their own browser: one to sign (ECDSA, SHA-256) and one
to open sealed things (ECDH). The private halves never leave the browser unencrypted: the browser
locks them with a key derived from the partner's passphrase (PBKDF2-SHA256, 600 000 rounds) and
stores only that locked bundle here, so this server, and whoever runs it, holds nothing it can
open or sign with. The public halves and their fingerprint are on the record, where a partner's
own server pins them (ADR-054).

Sealing: a fresh content key encrypts the item (AES-256-GCM, the item's id as associated data);
for each partner the content key is wrapped with a key from ECDH between a fresh ephemeral key and
the partner's key, through HKDF-SHA256. The browser reverses it with WebCrypto.

Approvals: a partner's browser signs the canonical JSON of {"v": 1, "action", "params", "nonce",
"partner_id"}. The nonce comes from this server, is single use and lasts ten minutes, so an
approval works once, for the one change it names.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
from datetime import timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.clock import iso
from app.services.context import Context
from app.services.errors import ApiError, Forbidden
from app.store.ledger_store import canonical

HKDF_INFO = b"watchman seal v1"
NONCE_LIFE = timedelta(minutes=10)
# A replaced key cannot approve anything for this long, and everyone hears about it at once.
NEW_KEY_WAIT = timedelta(days=3)
MIN_PASSPHRASE = 12


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(text: str) -> bytes:
    return base64.b64decode(text)


def fingerprint(sign_pub: str, enc_pub: str) -> str:
    h = hashlib.sha256(unb64(sign_pub) + unb64(enc_pub)).hexdigest()[:32]
    return " ".join(h[i:i + 4] for i in range(0, 32, 4))


# Keys

def key_for(ctx: Context, partner_id: str) -> sqlite3.Row | None:
    return ctx.conn.execute("SELECT * FROM partner_keys WHERE partner_id = ?", (partner_id,)).fetchone()


def keyed_partners(ctx: Context, person_id: str | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT k.*, p.person_id, p.name, p.email, p.trusted_at FROM partner_keys k "
           "JOIN partners p ON p.id = k.partner_id")
    if person_id:
        return ctx.conn.execute(sql + " WHERE p.person_id = ?", (person_id,)).fetchall()
    return ctx.conn.execute(sql).fetchall()


def sealed(ctx: Context, person_id: str | None = None) -> bool:
    """A household is sealed once any partner holds a key: locks are then always on and loosening
    needs a partner's signed approval (ADR-053)."""
    return bool(keyed_partners(ctx, person_id))


def set_key(ctx: Context, partner: sqlite3.Row, sign_pub: str, enc_pub: str, wrapped: dict) -> str:
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(enc_pub))
        serialization.load_der_public_key(unb64(sign_pub))
    except Exception as exc:
        raise ApiError(f"not a P-256 public key: {exc}") from exc
    for field in ("salt", "iterations", "iv", "data"):
        if field not in wrapped:
            raise ApiError(f"the locked key bundle has no {field}")
    if int(wrapped["iterations"]) < 600_000:
        raise ApiError("the passphrase key needs at least 600000 rounds")
    fp = fingerprint(sign_pub, enc_pub)
    now = ctx.clock.now()
    replacing = key_for(ctx, partner["id"]) is not None
    active_from = iso(now + NEW_KEY_WAIT) if replacing else iso(now)
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO partner_keys(partner_id, sign_pub, enc_pub, wrapped, fingerprint, created_at, active_from) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(partner_id) DO UPDATE SET sign_pub = excluded.sign_pub, "
            "enc_pub = excluded.enc_pub, wrapped = excluded.wrapped, fingerprint = excluded.fingerprint, "
            "created_at = excluded.created_at, active_from = excluded.active_from",
            (partner["id"], sign_pub, enc_pub, json.dumps(wrapped), fp, iso(now), active_from))
        ctx.ledger.append("partner_key", {"partner_id": partner["id"], "name": partner["name"], "sign": sign_pub,
                                          "enc": enc_pub, "fingerprint": fp, "active_from": active_from,
                                          "replaced": replacing}, partner["person_id"], partner["id"])
    return fp


# Approvals

def challenge(ctx: Context, partner_id: str) -> str:
    nonce = secrets.token_urlsafe(18)
    now = ctx.clock.now()
    with ctx.conn:
        ctx.conn.execute("DELETE FROM challenges WHERE expires_at < ?", (iso(now),))
        ctx.conn.execute("INSERT INTO challenges(nonce, partner_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                         (nonce, partner_id, iso(now), iso(now + NONCE_LIFE)))
    return nonce


def verify_signature(sign_pub: str, payload: str, sig: str) -> bool:
    try:
        key = serialization.load_der_public_key(unb64(sign_pub))
        raw = unb64(sig)
        if len(raw) != 64 or not isinstance(key, ec.EllipticCurvePublicKey):
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        key.verify(der, payload.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def verify_approval(sign_pub: str, approval: dict) -> bool:
    """Signature check alone, for a partner's own server checking the record (ADR-054)."""
    try:
        body = json.loads(approval["payload"])
        return body.get("partner_id") == approval["partner_id"] and verify_signature(
            sign_pub, approval["payload"], approval["sig"])
    except (KeyError, TypeError, ValueError):
        return False


def check(ctx: Context, approval: dict, action: str, params: dict, partner_ids: set[str] | None = None) -> sqlite3.Row:
    """Accept a signed approval for exactly [action] with [params] from one of [partner_ids] (any
    partner when None). Consumes its nonce. Returns the partner's key row."""
    try:
        payload = str(approval["payload"])
        body = json.loads(payload)
        partner_id = str(approval["partner_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("the approval is malformed", code="approval_invalid") from exc
    if body.get("v") != 1 or body.get("action") != action or body.get("partner_id") != partner_id:
        raise ApiError("the approval is for something else", code="approval_invalid")
    if canonical(body.get("params")) != canonical(params):
        raise ApiError("the approval names a different change", code="approval_invalid")
    if partner_ids is not None and partner_id not in partner_ids:
        raise Forbidden("this partner cannot approve this")
    key = key_for(ctx, partner_id)
    now = iso(ctx.clock.now())
    if key is None:
        raise ApiError("this partner has no key", code="approval_invalid")
    if key["active_from"] > now:
        raise ApiError(f"this partner's new key counts from {key['active_from']}", code="approval_invalid")
    if not verify_signature(key["sign_pub"], payload, str(approval.get("sig", ""))):
        raise ApiError("the signature does not match the partner's key", code="approval_invalid")
    with ctx.conn:
        used = ctx.conn.execute(
            "UPDATE challenges SET used_at = ? WHERE nonce = ? AND partner_id = ? AND used_at IS NULL "
            "AND expires_at > ?", (now, body.get("nonce"), partner_id, now)).rowcount
    if not used:
        raise ApiError("the approval expired or was already used; approve again", code="approval_invalid")
    return key


def from_form(form) -> dict | None:
    payload, sig, partner_id = form.get("approval_payload"), form.get("approval_sig"), form.get("approval_partner")
    if not payload or not sig or not partner_id:
        return None
    return {"payload": str(payload), "sig": str(sig), "partner_id": str(partner_id)}


# Sealing

def seal(data: bytes, aad: str, recipients: list[sqlite3.Row]) -> bytes:
    """An envelope only the [recipients] (partner key rows) can open. JSON bytes."""
    cek = AESGCM.generate_key(bit_length=256)
    iv = secrets.token_bytes(12)
    out = {"v": 1, "iv": b64(iv), "ct": b64(AESGCM(cek).encrypt(iv, data, aad.encode())), "keys": {}}
    for r in recipients:
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(r["enc_pub"]))
        eph = ec.generate_private_key(ec.SECP256R1())
        shared = eph.exchange(ec.ECDH(), peer)
        kek = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"", info=HKDF_INFO).derive(shared)
        wiv = secrets.token_bytes(12)
        epk = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        out["keys"][r["partner_id"]] = {"epk": b64(epk), "iv": b64(wiv),
                                        "wk": b64(AESGCM(kek).encrypt(wiv, cek, aad.encode()))}
    return json.dumps(out, separators=(",", ":")).encode()


def open_sealed(envelope: bytes, aad: str, partner_id: str, enc_private: ec.EllipticCurvePrivateKey) -> bytes:
    """What a partner's browser does; here for tests and the interoperability check."""
    env = json.loads(envelope)
    k = env["keys"][partner_id]
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(k["epk"]))
    kek = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"", info=HKDF_INFO).derive(
        enc_private.exchange(ec.ECDH(), peer))
    cek = AESGCM(kek).decrypt(unb64(k["iv"]), unb64(k["wk"]), aad.encode())
    return AESGCM(cek).decrypt(unb64(env["iv"]), unb64(env["ct"]), aad.encode())
