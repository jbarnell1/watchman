"""How this server reads another household's record (ADR-054). One interface, HTTP and a fake."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable


class WitnessError(Exception):
    pass


class WitnessClient:
    def get(self, base_url: str, token: str, path: str, params: dict | None = None) -> dict:
        raise NotImplementedError


class HttpWitnessClient(WitnessClient):
    def __init__(self, timeout: float = 20) -> None:
        self.timeout = timeout

    def get(self, base_url: str, token: str, path: str, params: dict | None = None) -> dict:
        url = base_url.rstrip("/") + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            raise WitnessError(str(exc)) from exc


class FakeWitnessClient(WitnessClient):
    """Hands each request to [handler] (a test client of the watched server, or a function)."""

    def __init__(self, handler: Callable[[str, str, str, dict | None], dict] | None = None) -> None:
        self.handler = handler
        self.down = False

    def get(self, base_url: str, token: str, path: str, params: dict | None = None) -> dict:
        if self.down or self.handler is None:
            raise WitnessError("unreachable")
        return self.handler(base_url, token, path, params)
