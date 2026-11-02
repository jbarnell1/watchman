"""OpenID Connect sign-in for the portal (ADR-027).

Authorization code flow with PKCE against the household's own identity provider (Pocket ID, or any
provider that publishes discovery). One class, `OidcClient`, does discovery, builds the login URL,
redeems the code and validates the ID token. All HTTP goes through an injected `httpx.Client`, so
tests serve discovery, the JWKS and the token endpoint from memory with no network.

Discovery and the JWKS are fetched lazily and cached, so the server starts even when the provider
is down; an unknown key id refetches the JWKS once (key rotation).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

log = logging.getLogger(__name__)

# Asymmetric algorithms only. "none" and the HMAC family are never accepted for an ID token here.
ALLOWED_ALGORITHMS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}


class OidcError(Exception):
    """Sign-in could not complete. The message is for the log, not for the page."""


@dataclass(frozen=True)
class LoginStart:
    url: str
    state: str
    nonce: str
    verifier: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


class OidcClient:
    def __init__(self, issuer: str, client_id: str, client_secret: str, redirect_url: str,
                 scopes: str = "openid email profile", http: httpx.Client | None = None,
                 leeway_seconds: int = 60) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_url = redirect_url
        self.scopes = scopes
        self.http = http or httpx.Client(timeout=10.0, follow_redirects=False)
        self.leeway = leeway_seconds
        self._lock = threading.Lock()
        self._discovery: dict | None = None
        self._jwks: jwt.PyJWKSet | None = None

    # Provider metadata

    def discovery(self) -> dict:
        with self._lock:
            if self._discovery is None:
                url = f"{self.issuer}/.well-known/openid-configuration"
                doc = self._get_json(url)
                if str(doc.get("issuer", "")).rstrip("/") != self.issuer:
                    raise OidcError(f"discovery issuer {doc.get('issuer')!r} does not match {self.issuer!r}")
                for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                    if not doc.get(key):
                        raise OidcError(f"discovery document has no {key}")
                self._discovery = doc
            return self._discovery

    def _jwk_set(self, refresh: bool = False) -> jwt.PyJWKSet:
        doc = self.discovery()
        with self._lock:
            if self._jwks is None or refresh:
                try:
                    self._jwks = jwt.PyJWKSet.from_dict(self._get_json(doc["jwks_uri"]))
                except jwt.PyJWTError as exc:
                    raise OidcError(f"the provider's JWKS has no usable keys: {exc}") from exc
            return self._jwks

    def _get_json(self, url: str) -> dict:
        try:
            resp = self.http.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise OidcError(f"GET {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise OidcError(f"GET {url} answered {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise OidcError(f"GET {url} did not answer JSON") from exc

    # The flow

    def start(self) -> LoginStart:
        """A fresh state, nonce and PKCE verifier, and the URL to send the browser to."""
        doc = self.discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        query = urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_url,
            "scope": self.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        })
        sep = "&" if "?" in doc["authorization_endpoint"] else "?"
        return LoginStart(url=f"{doc['authorization_endpoint']}{sep}{query}", state=state, nonce=nonce,
                          verifier=verifier)

    def finish(self, code: str, verifier: str, nonce: str) -> dict:
        """Redeem the code at the token endpoint and return the validated ID token claims."""
        doc = self.discovery()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_url,
            "code_verifier": verifier,
            "client_id": self.client_id,
        }
        auth = (self.client_id, self.client_secret) if self.client_secret else None
        try:
            resp = self.http.post(doc["token_endpoint"], data=data, auth=auth,
                                  headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise OidcError(f"token request failed: {exc}") from exc
        if resp.status_code != 200:
            raise OidcError(f"token endpoint answered {resp.status_code}: {resp.text[:200]}")
        try:
            id_token = resp.json().get("id_token")
        except ValueError as exc:
            raise OidcError("token endpoint did not answer JSON") from exc
        if not id_token:
            raise OidcError("token response has no id_token")
        return self.validate_id_token(id_token, nonce)

    def validate_id_token(self, token: str, nonce: str) -> dict:
        """Signature against the JWKS, issuer, audience, expiry, issued-at and nonce."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OidcError(f"ID token is not a JWT: {exc}") from exc
        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise OidcError(f"ID token algorithm {alg!r} is not accepted")
        key = self._key_for(header.get("kid"))
        try:
            claims = jwt.decode(
                token, key=key, algorithms=[alg], audience=self.client_id,
                issuer=self.discovery()["issuer"], leeway=self.leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"ID token rejected: {exc}") from exc
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self.client_id:
            raise OidcError("ID token has several audiences and azp is not this client")
        if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("ID token nonce does not match this sign-in")
        return claims

    def _key_for(self, kid: str | None):
        for refresh in (False, True):
            keys = self._jwk_set(refresh=refresh)
            if kid is None and len(keys.keys) == 1:
                return keys.keys[0].key
            for k in keys.keys:
                if k.key_id == kid:
                    return k.key
        raise OidcError(f"no signing key {kid!r} in the provider's JWKS")
