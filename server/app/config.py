"""Process configuration: where data lives and how the server is reached.

Everything household-specific is a setting in the database (see settings.py), not here.
These values describe the process itself and come from the environment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WATCHMAN_", extra="ignore")

    data_dir: Path = Field(default=Path("data"))
    bind_host: str = "127.0.0.1"
    bind_port: int = 8030
    # Only requests from these addresses may carry the Tailscale identity headers.
    # In Docker Compose this is the tailscale sidecar; locally it is loopback.
    trusted_proxies: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])
    # Set to the models directory holding the ONNX files named in models/manifest.json.
    models_dir: Path = Field(default=Path("models"))
    # Use a fake second opinion (score 0.0) instead of the ONNX model. Development only.
    fake_second_opinion: bool = False

    # How the portal knows who you are (ADR-027). "tailscale": identity headers from the sidecar.
    # "oidc": sign in with the household's own OpenID Connect provider (Pocket ID, for example).
    # The device API is the same in both: devices carry bearer tokens from pairing.
    auth: Literal["tailscale", "oidc"] = "tailscale"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""          # empty for a public client (PKCE only)
    oidc_redirect_url: str = ""           # https://<portal>/auth/callback, as registered at the provider
    oidc_scopes: str = "openid email profile"
    session_hours: int = 168              # how long a portal sign-in lasts

    def oidc_missing(self) -> list[str]:
        """The OIDC settings that must be set before the server can start in oidc mode."""
        needed = {"WATCHMAN_OIDC_ISSUER": self.oidc_issuer, "WATCHMAN_OIDC_CLIENT_ID": self.oidc_client_id,
                  "WATCHMAN_OIDC_REDIRECT_URL": self.oidc_redirect_url}
        return [name for name, value in needed.items() if not value]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "watchman.sqlite3"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def keys_dir(self) -> Path:
        return self.data_dir / "keys"
