"""A development server with fakes and a seeded household, for looking at the portal.

    uv run python -m app.devserver            # http://127.0.0.1:8035

Identity is fake: /dev/as/owner, /dev/as/sam or /dev/as/pat switch who you are. Everything lives
in a temporary directory and is thrown away on exit. Never run this on the tailnet.
"""

from __future__ import annotations

import io
import json
import os
import random
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from PIL import Image, ImageDraw

from app.clients.identity import Identity, IdentityProvider
from app.clients.mailer import FakeMailer
from app.clients.second_opinion import FakeSecondOpinion
from app.clock import iso
from app.config import Config
from app.main import build_context, create_app
from app.services import intake
from app.services.context import Context

LOGINS = {"owner": "alex@example.com", "sam": "sam@example.com", "pat": "pat@example.com"}


class DevIdentity(IdentityProvider):
    def identify(self, request: Request) -> Identity | None:
        who = request.cookies.get("dev_login", "owner")
        login = LOGINS.get(who, LOGINS["owner"])
        return Identity(login=login, name=who.title())


def synthetic_image(seed: int, size: int = 240) -> bytes:
    rnd = random.Random(seed)
    img = Image.new("RGB", (size, size), (rnd.randint(120, 220), rnd.randint(100, 180), rnd.randint(90, 160)))
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x, y = rnd.randint(0, size), rnd.randint(0, size)
        r = rnd.randint(20, 90)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(rnd.randint(40, 230), rnd.randint(40, 200), rnd.randint(40, 200)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def seed(ctx: Context) -> None:
    now = ctx.clock.now()
    with ctx.conn:
        ctx.people.add_login(LOGINS["owner"], "admin")
        person = ctx.people.add_person("Alex", "alex@example.com", LOGINS["owner"])
        sam = ctx.people.add_partner(person, "Sam", "sam@example.com", "linked", LOGINS["sam"])
        ctx.people.add_partner(person, "Pat", "pat@example.com", "email")
        ctx.settings.set("household_name", "Example household")
        ctx.settings.set("timezone", "America/New_York")
        ctx.settings.set("digest_day", "wednesday")
        ctx.settings.set("digest_time", "07:00")
        ctx.settings.set("setup_complete", True)
        ctx.settings.set("adguard_url", "http://127.0.0.1:3053")
        pixel, _ = ctx.devices.add_device(person, "Pixel 10 Pro XL", "android", "app", "0.1.0")
        pc, _ = ctx.devices.add_device(person, "family-pc", "windows", "service", "0.1.0")
        ext, _ = ctx.devices.add_device(person, "Firefox on Pixel", "firefox-android", "extension", "0.1.0")

        # Seven days of heartbeats. The Pixel has a 3 hour silence two days ago and an hour with
        # accessibility off yesterday; the PC only checks in while it is awake.
        start = now - timedelta(days=7)
        t = start
        silent_from = now - timedelta(days=2, hours=5)
        silent_to = silent_from + timedelta(hours=3)
        off_from = now - timedelta(days=1, hours=9)
        off_to = off_from + timedelta(hours=1)
        while t < now:
            if not (silent_from <= t < silent_to):
                on = not (off_from <= t < off_to)
                ctx.conn.execute(
                    "INSERT OR REPLACE INTO heartbeats(device_id, at, protection) VALUES (?, ?, ?)",
                    (pixel, iso(t), 1 if on else 0))
            local_hour = (t - timedelta(hours=5)).hour
            if 8 <= local_hour < 23:
                ctx.conn.execute(
                    "INSERT OR REPLACE INTO heartbeats(device_id, at, protection) VALUES (?, ?, 1)", (pc, iso(t)))
            t += timedelta(minutes=5)
        state = {"protection": True, "accessibility": True, "capture": "granted", "overlay": True,
                 "device_admin": True, "extension": None, "vpn": True}
        for dev, st in ((pixel, state), (pc, {"protection": True, "capture": "granted", "overlay": True}),
                        (ext, {"protection": True, "extension": True})):
            ctx.conn.execute("UPDATE devices SET last_heartbeat_at = ?, last_state = ? WHERE id = ?",
                             (iso(now - timedelta(minutes=3)), json.dumps({"state": st}), dev))

        def ev(dev, type_, when, **kw):
            e = {"id": str(uuid.uuid4()), "type": type_, "occurred_at": iso(when), "action": kw.pop("action", "none")}
            e.update(kw)
            sev = {"image_covered": "review", "screen_blocked": "review", "tamper": "protection"}.get(type_, "info")
            if type_ == "protection_changed" and kw.get("detail", {}).get("to") in (False, "denied"):
                sev = "protection"
            ctx.events.insert(e, dev, person, sev, "wanted" if kw.get("has_image") else "none")
            return e["id"]

        imgs = []
        imgs.append((ev(pixel, "image_covered", now - timedelta(hours=14), app="com.google.android.youtube",
                        action="covered", scores={"classifier": 0.93, "detector": 0.71, "labels": ["FEMALE_BREAST_EXPOSED"]},
                        region={"w": 48, "h": 48, "screen_w": 1440, "screen_h": 3120}, hash="9f3a0c1e5b7d2a46",
                        has_image=True), 0.88, 1))
        imgs.append((ev(ext, "image_covered", now - timedelta(days=1, hours=2), app="org.mozilla.firefox",
                        url="https://www.reddit.com/r/pics/", action="covered", scores={"classifier": 0.81},
                        region={"w": 310, "h": 420, "screen_w": 1440, "screen_h": 3120}, hash="77aa0c1e5b7d2a46",
                        has_image=True), 0.42, 2))
        imgs.append((ev(pc, "image_flagged", now - timedelta(days=3, hours=4), app="chrome.exe",
                        url="https://news.ycombinator.com/", action="flagged", scores={"classifier": 0.64},
                        region={"w": 120, "h": 120, "screen_w": 2560, "screen_h": 1440}, has_image=True), 0.2, 3))
        ev(pixel, "app_blocked", now - timedelta(hours=20), app="com.android.chrome", action="blocked")
        ev(pixel, "url_blocked", now - timedelta(days=2, hours=1), app="org.mozilla.firefox",
           url="https://example-adult-site.test/", action="blocked")
        ev(pc, "search_flagged", now - timedelta(days=4), app="firefox.exe", title="search: example query", action="flagged")
        ev(pixel, "protection_changed", off_from, detail={"field": "accessibility", "from": True, "to": False})
        ev(pixel, "protection_changed", off_to, detail={"field": "accessibility", "from": False, "to": True})
        ev(pixel, "tamper", silent_from + timedelta(minutes=30), detail={"reason": "silent", "gap_minutes": 30})
        cid, _ = ctx.codes.create_code(person, sam, 20)
        ctx.conn.execute("UPDATE unlock_codes SET used_at = ? WHERE id = ?", (iso(now - timedelta(days=5)), cid))
        ctx.conn.execute("INSERT INTO pauses(id, person_id, code_id, started_at, ends_at) VALUES (?, ?, ?, ?, ?)",
                         (str(uuid.uuid4()), person, cid, iso(now - timedelta(days=5)), iso(now - timedelta(days=5) + timedelta(minutes=20))))
        ev(pixel, "pause_started", now - timedelta(days=5), detail={"minutes": 20, "partner": "Sam"})
        ctx.codes.add_request(person, pixel, 30, "work upload")
        # The checker test vector's hash, so an end-to-end run can see a known-bad image covered.
        ctx.events.add_known_bad("a55aa55aa55aa55a", "manual")
    for event_id, score, s in imgs:
        ctx.second_opinion.fixed = score
        device = ctx.devices.get(ctx.events.get(event_id)["device_id"])
        intake.store_image(ctx, device, event_id, synthetic_image(s))
    with ctx.conn:
        ctx.codes.add_peek(imgs[0][0], sam)
        ctx.conn.execute("UPDATE peeks SET at = ?", (iso(now - timedelta(hours=6)),))


def main() -> None:
    import uvicorn

    tmp = Path(tempfile.mkdtemp(prefix="watchman-dev-"))
    # Serve real weights to devices when models/fetch.py has run; the second opinion stays fake.
    repo_models = Path(__file__).resolve().parents[2] / "models" / "cache"
    models_dir = repo_models if (repo_models / "manifest.json").exists() else tmp / "models"
    config = Config(data_dir=tmp / "data", models_dir=models_dir, fake_second_opinion=True,
                    bind_host=os.environ.get("WATCHMAN_DEV_HOST", "127.0.0.1"), bind_port=int(os.environ.get("WATCHMAN_DEV_PORT", "8035")))
    ctx = build_context(config, identity=DevIdentity(), mailer=FakeMailer(), second_opinion=FakeSecondOpinion(0.0))
    seed(ctx)
    app = create_app(ctx=ctx, run_jobs=False)

    @app.get("/dev/pairing-code")
    async def pairing_code() -> dict:
        person = ctx.people.list_people()[0]
        with ctx.conn:
            code = ctx.devices.create_pairing_code(person["id"])
        return {"code": code}

    @app.get("/dev/as/{who}")
    async def switch(who: str) -> RedirectResponse:
        r = RedirectResponse("/", status_code=303)
        r.set_cookie("dev_login", who if who in LOGINS else "owner")
        return r

    print(f"dev data in {tmp}; identities: /dev/as/owner /dev/as/sam /dev/as/pat")
    uvicorn.run(app, host=config.bind_host, port=config.bind_port, log_level="warning")


if __name__ == "__main__":
    main()
