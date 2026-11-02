"""Every test runs against fakes: in-memory SQLite, a settable clock, a recording mailer, a fixed
second opinion and a fake identity. No network, no model files."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.clients.identity import FakeIdentity, Identity
from app.clients.mailer import FakeMailer
from app.clients.second_opinion import FakeSecondOpinion
from app.clock import FakeClock
from app.config import Config
from app.main import build_context, create_app

ADMIN = "owner@example.com"
PROTECTED = "alex@example.com"
PARTNER = "sam@example.com"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 2, 12, 0, tzinfo=UTC))   # a Monday


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def opinion() -> FakeSecondOpinion:
    return FakeSecondOpinion(0.0)


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity(Identity(login=ADMIN, name="Owner"))


@pytest.fixture
def ctx(tmp_path, clock, mailer, opinion, identity):
    config = Config(data_dir=tmp_path / "data", models_dir=tmp_path / "models", fake_second_opinion=True)
    return build_context(config, clock=clock, identity=identity, mailer=mailer, second_opinion=opinion,
                         db_path=":memory:")


@pytest.fixture
def app(ctx):
    return create_app(ctx=ctx, run_jobs=False)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def household(ctx):
    """A set-up household: admin, one protected person (alex), one linked partner (sam) and one
    email-only partner (pat)."""
    with ctx.conn:
        ctx.people.add_login(ADMIN, "admin")
        person_id = ctx.people.add_person("Alex", "alex@example.com", PROTECTED)
        sam = ctx.people.add_partner(person_id, "Sam", "sam@example.com", "linked", PARTNER)
        pat = ctx.people.add_partner(person_id, "Pat", "pat@example.com", "email")
        ctx.settings.set("setup_complete", True)
        ctx.settings.set("smtp", {"host": "mail.test", "port": 587, "username": "", "password": "",
                                  "from_address": "watchman@test", "starttls": True})
    return {"person_id": person_id, "sam": sam, "pat": pat}


@pytest.fixture
def device(ctx, client, household):
    """A paired Android device. Returns (device_row, auth_headers)."""
    with ctx.conn:
        code = ctx.devices.create_pairing_code(household["person_id"])
    r = client.post("/api/v1/pair", json={"code": code, "device": {
        "name": "Pixel", "platform": "android", "kind": "app", "app_version": "0.1.0"}})
    assert r.status_code == 200, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['token']}"}
    return ctx.devices.get(body["device_id"]), headers


def jpeg_bytes(size: int = 200, colour=(180, 120, 100)) -> bytes:
    img = Image.new("RGB", (size, size), colour)
    for x in range(0, size, 20):
        for y in range(0, size, 20):
            if (x + y) % 40 == 0:
                img.paste((40, 60, 90), (x, y, x + 10, y + 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def as_login(login: str) -> dict:
    return {"X-Fake-Login": login}
