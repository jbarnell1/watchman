"""Application factory. `create_app()` wires the real clients; tests pass fakes."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from app.api.v1 import router as api_router
from app.api.witness import router as witness_api_router
from app.clients.blobs import BlobStore
from app.clients.detector import Detector, OnnxDetector
from app.clients.identity import (
    IdentityProvider,
    OidcIdentity,
    SessionSigner,
    TailscaleIdentity,
    load_or_create_secret,
)
from app.clients.mailer import Mailer, SmtpMailer
from app.clients.oidc import OidcClient
from app.clients.second_opinion import FakeSecondOpinion, OnnxSecondOpinion, SecondOpinion
from app.clock import Clock
from app.config import Config
from app.portal.auth import router as auth_router
from app.portal.routes import router as portal_router
from app.portal.routes import templates as portal_templates
from app.portal.trust import router as trust_router
from app.services import health, jobs
from app.services.context import Context
from app.services.errors import ApiError
from app.store.db import connect

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "portal" / "static"


class SettingsMailer(Mailer):
    """Reads SMTP settings at send time so changes on the settings page take effect at once."""

    def __init__(self, ctx_getter) -> None:
        self._ctx = ctx_getter

    def send(self, mail) -> None:
        SmtpMailer(self._ctx().settings.load().smtp).send(mail)


def build_context(config: Config, clock: Clock | None = None, identity: IdentityProvider | None = None,
                  mailer: Mailer | None = None, second_opinion: SecondOpinion | None = None,
                  db_path: str | Path | None = None) -> Context:
    clock = clock or Clock()
    conn = connect(db_path if db_path is not None else config.db_path)
    blobs = BlobStore(config.images_dir, config.keys_dir)
    holder: dict = {}
    ctx = Context.build(
        config=config, conn=conn, clock=clock,
        identity=identity or real_identity(config),
        mailer=mailer or SettingsMailer(lambda: holder["ctx"]),
        second_opinion=second_opinion or FakeSecondOpinion(),
        blobs=blobs,
    )
    holder["ctx"] = ctx
    if second_opinion is None:
        device = ctx.settings.load().second_opinion_device
        ctx.second_opinion = _real_second_opinion(config, device)
        ctx.detector = _real_detector(config, device) or ctx.detector
    ctx.models_verified = health.verified_models(config.models_dir)
    return ctx


def real_identity(config: Config, http=None) -> IdentityProvider:
    """Tailscale headers by default; OIDC when WATCHMAN_AUTH=oidc (ADR-027)."""
    if config.auth == "oidc":
        missing = config.oidc_missing()
        if missing:
            raise SystemExit(f"WATCHMAN_AUTH=oidc needs {', '.join(missing)}")
        client = OidcClient(config.oidc_issuer, config.oidc_client_id, config.oidc_client_secret,
                            config.oidc_redirect_url, scopes=config.oidc_scopes, http=http)
        signer = SessionSigner(load_or_create_secret(config.keys_dir / "session.key"))
        return OidcIdentity(client, signer, session_hours=config.session_hours)
    return TailscaleIdentity(config.trusted_proxies)


def _real_second_opinion(config: Config, device: str) -> SecondOpinion:
    manifest = config.models_dir / "manifest.json"
    if config.fake_second_opinion:
        return FakeSecondOpinion()
    if not manifest.exists():
        log.warning("no models manifest at %s; using the fake second opinion", manifest)
        return FakeSecondOpinion()
    return OnnxSecondOpinion(manifest, config.models_dir, device=device)


def _real_detector(config: Config, device: str) -> Detector | None:
    """NudeNet 640m from the manifest (ADR-046); without it uploads get no server tags and covered
    pictures reach partners on the second opinion alone."""
    manifest = config.models_dir / "manifest.json"
    if config.fake_second_opinion or not manifest.exists():
        return None
    try:
        return OnnxDetector(manifest, config.models_dir, device=device)
    except Exception as exc:  # a missing or unreadable model file must not stop the server
        log.warning("server detector not loaded: %s", exc)
        return None


def create_app(ctx: Context | None = None, config: Config | None = None, run_jobs: bool = True) -> FastAPI:
    config = config or Config()
    ctx = ctx or build_context(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(jobs.run_forever(ctx)) if run_jobs else None
        try:
            yield
        finally:
            if task:
                task.cancel()

    app = FastAPI(title="Watchman", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.ctx = ctx

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        wants_html = "text/html" in request.headers.get("accept", "") and not request.url.path.startswith("/api/")
        signing_in = exc.code == "forbidden" and "sign in" in exc.message
        if wants_html and request.method == "GET" and signing_in and isinstance(ctx.identity, OidcIdentity):
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/auth/login?{urlencode({'next': target})}", status_code=303)
        if wants_html and request.method == "GET":
            title = {"forbidden": "Not signed in", "not_found": "Not here"}.get(exc.code, "Something went wrong")
            if signing_in:
                message = ("Open this page through Tailscale so the server knows who you are. "
                           "Nothing on Watchman has a password.")
            else:
                message = exc.message
            return portal_templates.TemplateResponse(
                request, "forbidden.html", {"title": title, "message": message}, status_code=exc.status
            )
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    app.include_router(api_router)
    app.include_router(witness_api_router)
    app.include_router(auth_router)
    app.include_router(portal_router)
    app.include_router(trust_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config()
    # proxy_headers off: the identity check needs the real peer (the Tailscale sidecar on loopback),
    # not the tailnet address that tailscale serve puts in X-Forwarded-For. OIDC mode does not read
    # the peer address at all; its redirect URL is configured, not derived from forwarded headers.
    uvicorn.run(create_app(config=config), host=config.bind_host, port=config.bind_port, log_level="info",
                proxy_headers=False)


if __name__ == "__main__":
    main()
