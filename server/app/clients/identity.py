"""Who is on the portal. Tailscale identity headers, an OIDC session cookie (ADR-027), or a fake for
tests and local development. The device API never uses these: devices carry bearer tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from starlette.requests import Request

from app.clients.oidc import OidcClient


@dataclass(frozen=True)
class Identity:
    login: str
    name: str


class IdentityProvider:
    def identify(self, request: Request) -> Identity | None:
        raise NotImplementedError


class TailscaleIdentity(IdentityProvider):
    """Reads the headers `tailscale serve` sets, only when the request comes from a trusted proxy."""

    def __init__(self, trusted_proxies: list[str]) -> None:
        self.trusted = set(trusted_proxies)

    def identify(self, request: Request) -> Identity | None:
        client = request.client.host if request.client else None
        if client not in self.trusted:
            return None
        login = request.headers.get("tailscale-user-login")
        if not login:
            return None
        return Identity(login=login.lower(), name=request.headers.get("tailscale-user-name", login))


class FakeIdentity(IdentityProvider):
    """The test and dev provider. Set `current` to act as someone; None means anonymous.
    A request may also carry X-Fake-Login to switch identity per request in tests."""

    def __init__(self, current: Identity | None = None) -> None:
        self.current = current

    def identify(self, request: Request) -> Identity | None:
        header = request.headers.get("x-fake-login")
        if header:
            return Identity(login=header.lower(), name=header.split("@")[0].title())
        return self.current


def load_or_create_secret(path: Path) -> bytes:
    """32 random bytes kept in the data directory (mode 600), created on first start."""
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionSigner:
    """Signed, expiring cookie values: base64url(JSON) "." base64url(HMAC-SHA256).
    Each purpose (the session, the short-lived login state) signs with its own derived key."""

    def __init__(self, secret: bytes, now: Callable[[], float] = time.time) -> None:
        self.secret = secret
        self.now = now

    def _key(self, purpose: str) -> bytes:
        return hmac.new(self.secret, purpose.encode(), hashlib.sha256).digest()

    def dumps(self, purpose: str, data: dict, ttl_seconds: int) -> str:
        body = dict(data, exp=int(self.now()) + ttl_seconds)
        payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
        sig = _b64(hmac.new(self._key(purpose), payload.encode(), hashlib.sha256).digest())
        return f"{payload}.{sig}"

    def loads(self, purpose: str, value: str | None) -> dict | None:
        if not value or value.count(".") != 1:
            return None
        payload, sig = value.split(".")
        expected = _b64(hmac.new(self._key(purpose), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            body = json.loads(_unb64(payload))
        except ValueError:
            return None
        if not isinstance(body, dict) or int(body.get("exp", 0)) < self.now():
            return None
        return body


class OidcIdentity(IdentityProvider):
    """Reads the session cookie that /auth/callback sets after a validated OIDC sign-in.
    Tailscale identity headers are ignored in this mode."""

    SESSION_COOKIE = "watchman_session"
    SESSION_PURPOSE = "session"

    def __init__(self, client: OidcClient, signer: SessionSigner, session_hours: int = 168) -> None:
        self.client = client
        self.signer = signer
        self.session_seconds = session_hours * 3600

    def session_value(self, identity: Identity) -> str:
        return self.signer.dumps(self.SESSION_PURPOSE, {"login": identity.login, "name": identity.name},
                                 self.session_seconds)

    def identify(self, request: Request) -> Identity | None:
        body = self.signer.loads(self.SESSION_PURPOSE, request.cookies.get(self.SESSION_COOKIE))
        if not body or not body.get("login"):
            return None
        return Identity(login=str(body["login"]), name=str(body.get("name") or body["login"]))
