"""Portal sign-in with OpenID Connect (ADR-027): /auth/login, /auth/callback, /auth/logout.

Only active when WATCHMAN_AUTH=oidc; in Tailscale mode these paths answer 404. The device API
(/api/v1, bearer tokens) never passes through here.

Cookies are HttpOnly, Secure and SameSite=Lax. The login cookie holds state, nonce and the PKCE
verifier for ten minutes, signed and scoped to /auth. The session cookie holds the login (the
lower-cased `email` claim) and display name, signed with the key in data/keys/session.key.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from app.clients.identity import Identity, OidcIdentity
from app.clients.oidc import OidcError
from app.portal.routes import get_ctx, templates
from app.services.context import Context
from app.services.errors import NotFound

log = logging.getLogger(__name__)
router = APIRouter()

LOGIN_COOKIE = "watchman_login"
LOGIN_PURPOSE = "login"
LOGIN_SECONDS = 600


def _oidc(ctx: Context) -> OidcIdentity:
    if not isinstance(ctx.identity, OidcIdentity):
        raise NotFound("no such page")
    return ctx.identity


def safe_next(value: str | None) -> str:
    """Only a path on this server: no scheme, no host, no protocol-relative //host."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return "/"
    return value


def _cookie(resp: Response, name: str, value: str, max_age: int, path: str = "/") -> None:
    resp.set_cookie(name, value, max_age=max_age, path=path, httponly=True, secure=True, samesite="lax")


def _page(request: Request, title: str, message: str, status: int, link: dict | None = None) -> Response:
    return templates.TemplateResponse(request, "forbidden.html",
                                      {"title": title, "message": message, "link": link}, status_code=status)


def _failed(request: Request) -> Response:
    return _page(request, "Sign-in failed",
                 "The identity provider did not confirm who you are. Try again; if it keeps failing, "
                 "the server log says why.", 400, {"href": "/auth/login", "text": "Sign in again"})


@router.get("/auth/login")
def login(request: Request, next: str = "/", ctx: Context = Depends(get_ctx)) -> Response:
    ident = _oidc(ctx)
    try:
        start = ident.client.start()
    except OidcError as exc:
        log.warning("OIDC sign-in could not start: %s", exc)
        return _page(request, "Sign-in unavailable",
                     "The identity provider could not be reached. Try again in a minute.", 503,
                     {"href": "/auth/login", "text": "Try again"})
    state = ident.signer.dumps(LOGIN_PURPOSE, {"state": start.state, "nonce": start.nonce,
                                               "verifier": start.verifier, "next": safe_next(next)},
                               LOGIN_SECONDS)
    resp = RedirectResponse(start.url, status_code=302)
    _cookie(resp, LOGIN_COOKIE, state, LOGIN_SECONDS, path="/auth")
    return resp


@router.get("/auth/callback")
def callback(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    ident = _oidc(ctx)
    params = request.query_params
    pending = ident.signer.loads(LOGIN_PURPOSE, request.cookies.get(LOGIN_COOKIE))
    if params.get("error"):
        log.warning("OIDC provider returned an error: %s", params.get("error"))
        return _failed(request)
    if not pending or not params.get("code") or not params.get("state"):
        log.warning("OIDC callback without a matching sign-in in progress")
        return _failed(request)
    if not secrets.compare_digest(str(pending.get("state", "")), params["state"]):
        log.warning("OIDC callback state does not match")
        return _failed(request)
    try:
        claims = ident.client.finish(params["code"], str(pending["verifier"]), str(pending["nonce"]))
    except OidcError as exc:
        log.warning("OIDC sign-in rejected: %s", exc)
        return _failed(request)
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        log.warning("OIDC ID token has no email claim; ask for the email scope")
        return _failed(request)
    # The email is the login and the admin is whoever holds it: an address the provider says it has
    # not verified could belong to anyone who typed it at sign-up. Absent means the provider does not
    # say (Pocket ID sets it), and is accepted.
    if claims.get("email_verified") is False or str(claims.get("email_verified")).lower() == "false":
        log.warning("OIDC sign-in refused: the provider has not verified %s", email)
        return _failed(request)
    name = str(claims.get("name") or claims.get("preferred_username") or email)
    resp = RedirectResponse(safe_next(pending.get("next")), status_code=303)
    _cookie(resp, OidcIdentity.SESSION_COOKIE, ident.session_value(Identity(login=email, name=name)),
            ident.session_seconds)
    resp.delete_cookie(LOGIN_COOKIE, path="/auth", httponly=True, secure=True, samesite="lax")
    return resp


@router.api_route("/auth/logout", methods=["GET", "POST"])
def logout(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    _oidc(ctx)
    resp = _page(request, "Signed out", "You are signed out of Watchman on this browser.", 200,
                 {"href": "/auth/login", "text": "Sign in"})
    resp.delete_cookie(OidcIdentity.SESSION_COOKIE, path="/", httponly=True, secure=True, samesite="lax")
    return resp
