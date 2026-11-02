"""OIDC sign-in (ADR-027) against a fake provider: an RSA key made in the test, discovery, JWKS and
the token endpoint served through an httpx MockTransport. No network."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.clients.identity import OidcIdentity, SessionSigner
from app.clients.oidc import OidcClient, OidcError
from app.config import Config
from app.main import build_context, create_app, real_identity
from app.portal.auth import safe_next

ISSUER = "https://id.test"
CLIENT_ID = "watchman"
CLIENT_SECRET = "test-client-secret"
PORTAL = "https://watchman.test"
REDIRECT = f"{PORTAL}/auth/callback"


def _jwk(key, kid: str) -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid=kid, use="sig", alg="RS256")
    return jwk


class FakeProvider:
    """Discovery, JWKS, authorize (simulated) and token endpoints. Checks PKCE and client auth."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "k1"
        self.published = [_jwk(self.key, self.kid)]
        self.codes: dict[str, dict] = {}
        self.jwks_fetches = 0
        self.claims_override: dict = {}
        self.sign_with = None   # (key, kid) to sign with something other than the published key
        self.alg = "RS256"

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/api/oidc/token", "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
                "id_token_signing_alg_values_supported": ["RS256"],
            })
        if path == "/.well-known/jwks.json":
            self.jwks_fetches += 1
            return httpx.Response(200, json={"keys": self.published})
        if path == "/api/oidc/token":
            return self._token(request)
        return httpx.Response(404)

    def authorize(self, url: str, email: str, name: str = "Owner Person") -> dict:
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert url.startswith(f"{ISSUER}/authorize?")
        assert q["client_id"] == CLIENT_ID and q["redirect_uri"] == REDIRECT and q["response_type"] == "code"
        assert q["code_challenge_method"] == "S256" and "openid" in q["scope"].split()
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"challenge": q["code_challenge"], "nonce": q["nonce"], "email": email, "name": name}
        return {"code": code, "state": q["state"]}

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if request.headers.get("authorization") != f"Basic {basic}":
            return httpx.Response(401, json={"error": "invalid_client"})
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None or form.get("redirect_uri") != REDIRECT:
            return httpx.Response(400, json={"error": "invalid_grant"})
        digest = hashlib.sha256(form["code_verifier"].encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != grant["challenge"]:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE"})
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "user-1", "iat": now, "exp": now + 300,
                  "nonce": grant["nonce"], "email": grant["email"], "email_verified": True, "name": grant["name"]}
        claims.update(self.claims_override)
        key, kid = self.sign_with or (self.key, self.kid)
        token = jwt.encode(claims, key, algorithm=self.alg, headers={"kid": kid})
        return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer", "id_token": token})


def oidc_config(tmp_path) -> Config:
    return Config(data_dir=tmp_path / "data", models_dir=tmp_path / "models", fake_second_opinion=True,
                  auth="oidc", oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID, oidc_client_secret=CLIENT_SECRET,
                  oidc_redirect_url=REDIRECT)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def octx(tmp_path, clock, mailer, opinion, provider):
    config = oidc_config(tmp_path)
    identity = real_identity(config, http=httpx.Client(transport=httpx.MockTransport(provider.handle)))
    return build_context(config, clock=clock, identity=identity, mailer=mailer, second_opinion=opinion,
                         db_path=":memory:")


@pytest.fixture
def oclient(octx) -> TestClient:
    return TestClient(create_app(ctx=octx, run_jobs=False), base_url=PORTAL)


def sign_in(client: TestClient, provider: FakeProvider, email: str = "Owner@Example.com", next_path: str = "/"):
    r = client.get("/auth/login", params={"next": next_path}, follow_redirects=False)
    assert r.status_code == 302, r.text
    grant = provider.authorize(r.headers["location"], email)
    return client.get("/auth/callback", params=grant, follow_redirects=False)


def test_sign_in_sets_a_strict_session_cookie_and_first_login_becomes_admin(octx, oclient, provider):
    r = sign_in(oclient, provider)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookies = r.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("watchman_session="))
    lowered = session.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered and "path=/" in lowered
    r = oclient.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert octx.people.admins() == ["owner@example.com"]          # the email claim, lower-cased


def test_second_login_is_not_admin(octx, oclient, provider, tmp_path):
    sign_in(oclient, provider)
    oclient.get("/")
    other = TestClient(oclient.app, base_url=PORTAL)
    sign_in(other, provider, email="someone@example.com")
    r = other.get("/settings", headers={"Accept": "text/html"})
    assert r.status_code == 403
    assert octx.people.admins() == ["owner@example.com"]


def test_anonymous_page_redirects_to_login_with_next(oclient):
    r = oclient.get("/review?person=x", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login?next=%2Freview%3Fperson%3Dx"


def test_callback_honours_next_but_only_local_paths(oclient, provider):
    assert sign_in(oclient, provider, next_path="/devices").headers["location"] == "/devices"
    assert sign_in(oclient, provider, next_path="https://evil.test/").headers["location"] == "/"
    assert sign_in(oclient, provider, next_path="//evil.test/").headers["location"] == "/"


@pytest.mark.parametrize("value", ["", "x", "//evil.test", "/\\evil.test", "https://evil.test", "/\t/evil.test"])
def test_safe_next_rejects_other_hosts(value):
    assert safe_next(value) == "/"


def test_tailscale_headers_are_ignored_in_oidc_mode(oclient):
    r = oclient.get("/", headers={"Accept": "text/html", "Tailscale-User-Login": "owner@example.com"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/auth/login")


def test_state_mismatch_and_missing_login_cookie_fail(oclient, provider):
    r = oclient.get("/auth/login", follow_redirects=False)
    grant = provider.authorize(r.headers["location"], "owner@example.com")
    bad = oclient.get("/auth/callback", params={**grant, "state": "forged"}, follow_redirects=False)
    assert bad.status_code == 400 and "watchman_session" not in bad.headers.get("set-cookie", "")
    fresh = TestClient(oclient.app, base_url=PORTAL)                  # no login cookie at all
    assert fresh.get("/auth/callback", params=grant, follow_redirects=False).status_code == 400
    assert oclient.get("/auth/callback", params={"error": "access_denied"}).status_code == 400


@pytest.mark.parametrize("override", [
    {"aud": "someone-else"},
    {"iss": "https://other.test"},
    {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},
    {"nonce": "replayed"},
    {"email": ""},
    {"email_verified": False},
    {"email_verified": "false"},
])
def test_bad_id_tokens_are_refused(octx, oclient, provider, override):
    provider.claims_override = override
    r = sign_in(oclient, provider)
    assert r.status_code == 400
    assert not octx.people.has_admin()


def test_token_signed_by_an_unknown_key_is_refused(octx, oclient, provider):
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider.sign_with = (stranger, provider.kid)        # right kid, wrong key: the signature fails
    assert sign_in(oclient, provider).status_code == 400
    provider.sign_with = (stranger, "k-unknown")          # unknown kid: one JWKS refresh, then refused
    before = provider.jwks_fetches
    assert sign_in(oclient, provider).status_code == 400
    assert provider.jwks_fetches == before + 1
    assert not octx.people.has_admin()


def test_rotated_key_is_picked_up_with_one_jwks_refresh(oclient, provider):
    assert sign_in(oclient, provider).status_code == 303          # JWKS cached with k1
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider.published = [_jwk(rotated, "k2")]
    provider.sign_with = (rotated, "k2")
    assert sign_in(oclient, provider).status_code == 303


def test_symmetric_and_none_algorithms_are_refused(provider):
    client = OidcClient(ISSUER, CLIENT_ID, CLIENT_SECRET, REDIRECT,
                        http=httpx.Client(transport=httpx.MockTransport(provider.handle)))
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "u", "iat": now, "exp": now + 60, "nonce": "n"}
    hs = jwt.encode(claims, CLIENT_SECRET, algorithm="HS256", headers={"kid": provider.kid})
    with pytest.raises(OidcError):
        client.validate_id_token(hs, "n")
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(OidcError):
        client.validate_id_token(f"{header}.{body}.", "n")


def test_discovery_issuer_must_match(provider):
    client = OidcClient("https://id.test/other", CLIENT_ID, "", REDIRECT,
                        http=httpx.Client(transport=httpx.MockTransport(provider.handle)))
    with pytest.raises(OidcError):
        client.start()


def test_tampered_or_expired_session_cookie_is_anonymous(octx, oclient, provider):
    sign_in(oclient, provider)
    value = oclient.cookies.get("watchman_session")
    payload, sig = value.split(".")
    forged = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    forged["login"] = "intruder@example.com"
    forged_payload = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    oclient.cookies.set("watchman_session", f"{forged_payload}.{sig}", domain="watchman.test")
    r = oclient.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/auth/login")

    signer = SessionSigner(b"k" * 32, now=lambda: 1000.0)
    token = signer.dumps("session", {"login": "a@b"}, ttl_seconds=10)
    assert signer.loads("session", token)["login"] == "a@b"
    assert signer.loads("login", token) is None                    # purposes do not cross
    later = SessionSigner(b"k" * 32, now=lambda: 2000.0)
    assert later.loads("session", token) is None


def test_logout_clears_the_session(oclient, provider):
    sign_in(oclient, provider)
    assert oclient.get("/", follow_redirects=False).status_code == 303
    page = oclient.get("/devices")
    assert "Sign out" in page.text
    r = oclient.post("/auth/logout")
    assert r.status_code == 200 and "Signed out" in r.text
    assert 'watchman_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]
    assert oclient.get("/", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 303


def test_session_key_is_created_once_in_the_data_dir(tmp_path, provider):
    config = oidc_config(tmp_path)
    http = httpx.Client(transport=httpx.MockTransport(provider.handle))
    config.keys_dir.mkdir(parents=True)
    first = real_identity(config, http=http)
    second = real_identity(config, http=http)
    assert isinstance(first, OidcIdentity)
    assert (config.keys_dir / "session.key").stat().st_size == 32
    assert first.signer.secret == second.signer.secret


def test_oidc_mode_needs_its_settings(tmp_path):
    with pytest.raises(SystemExit, match="WATCHMAN_OIDC_ISSUER"):
        real_identity(Config(data_dir=tmp_path, auth="oidc"))


def test_device_api_is_untouched_by_oidc(octx, oclient):
    with octx.conn:
        octx.people.add_login("owner@example.com", "admin")
        person_id = octx.people.add_person("Alex", "alex@example.com", "alex@example.com")
        code = octx.devices.create_pairing_code(person_id)
    r = oclient.post("/api/v1/pair", json={"code": code, "device": {
        "name": "Pixel", "platform": "android", "kind": "app", "app_version": "0.1.0"}})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    assert oclient.get("/api/v1/policy", headers=headers).status_code == 200
    anonymous = oclient.get("/api/v1/policy", headers={"Accept": "text/html"}, follow_redirects=False)
    assert anonymous.status_code == 401 and anonymous.json()["error"]["code"] == "unauthorized"


def test_provider_down_shows_a_page_not_an_error(tmp_path, clock, mailer, opinion):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    config = oidc_config(tmp_path)
    identity = real_identity(config, http=httpx.Client(transport=httpx.MockTransport(down)))
    ctx = build_context(config, clock=clock, identity=identity, mailer=mailer, second_opinion=opinion,
                        db_path=":memory:")
    client = TestClient(create_app(ctx=ctx, run_jobs=False), base_url=PORTAL)
    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 503 and "Sign-in unavailable" in r.text
    assert client.get("/healthz").json()["ok"] is True      # the server starts and answers without it


def test_auth_paths_are_absent_in_tailscale_mode(client):
    assert client.get("/auth/login", follow_redirects=False).status_code == 404
    assert client.post("/auth/logout").status_code == 404
