"""How this server talks to another household's server (ADR-054, ADR-055). One interface, HTTP and
a fake."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field


class WitnessError(Exception):
    pass


@dataclass
class Reply:
    status: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> dict:
        return json.loads(self.content)


class WitnessClient:
    def call(self, method: str, base_url: str, token: str, path: str, params: dict | None = None,
             body: dict | None = None) -> Reply:
        raise NotImplementedError

    def get(self, base_url: str, token: str, path: str, params: dict | None = None) -> dict:
        return self.json("GET", base_url, token, path, params)

    def post(self, base_url: str, token: str, path: str, body: dict | None = None) -> dict:
        return self.json("POST", base_url, token, path, None, body or {})

    def json(self, method: str, base_url: str, token: str, path: str, params: dict | None = None,
             body: dict | None = None) -> dict:
        r = self.call(method, base_url, token, path, params, body)
        try:
            data = r.json()
        except ValueError as exc:
            raise WitnessError(f"{r.status}: not JSON") from exc
        if r.status >= 400:
            message = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
            raise WitnessError(message or f"the other server answered {r.status}")
        return data


class HttpWitnessClient(WitnessClient):
    def __init__(self, timeout: float = 20) -> None:
        self.timeout = timeout

    def call(self, method: str, base_url: str, token: str, path: str, params: dict | None = None,
             body: dict | None = None) -> Reply:
        url = base_url.rstrip("/") + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return Reply(resp.status, resp.read(), dict(resp.headers))
        except urllib.error.HTTPError as exc:
            return Reply(exc.code, exc.read(), dict(exc.headers))
        except Exception as exc:
            raise WitnessError(str(exc)) from exc


class FakeWitnessClient(WitnessClient):
    """Hands each request to [handler] (method, base, token, path, params, body) -> Reply."""

    def __init__(self, handler: Callable[..., Reply] | None = None) -> None:
        self.handler = handler
        self.down = False

    def call(self, method: str, base_url: str, token: str, path: str, params: dict | None = None,
             body: dict | None = None) -> Reply:
        if self.down or self.handler is None:
            raise WitnessError("unreachable")
        return self.handler(method, base_url, token, path, params, body)


def test_client_handler(client) -> Callable[..., Reply]:
    """A handler that sends requests to a FastAPI TestClient, for tests and the dev server."""
    def handler(method, base, token, path, params, body):
        r = client.request(method, path, params=params, json=body, headers={"Authorization": f"Bearer {token}"})
        return Reply(r.status_code, r.content, dict(r.headers))
    return handler
