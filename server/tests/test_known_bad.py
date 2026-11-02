"""The known-bad list changes only on strong, independent evidence (ADR-035)."""
from __future__ import annotations

import io
import uuid

import numpy as np
from PIL import Image, ImageDraw

from app.clock import iso
from app.services.imagehash import decisive_bits
from tests.conftest import PARTNER, as_login, jpeg_bytes


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def icon_on_tan() -> Image.Image:
    img = Image.new("RGB", (132, 132), (205, 170, 125))
    ImageDraw.Draw(img).ellipse((40, 40, 92, 92), outline=(255, 255, 255), width=6)
    return img


def _report(client, headers, clock, hash_, labels=None, score=0.9):
    ev_id = str(uuid.uuid4())
    r = client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "image_covered", "occurred_at": iso(clock.now()), "app": "com.instagram.android",
        "action": "covered", "scores": {"classifier": score, "labels": labels or []}, "hash": hash_, "has_image": True}]})
    return ev_id, r.json()


def _known_bad(client, headers):
    return client.get("/api/v1/policy", headers=headers).json()["known_bad_hashes"]


def test_decisive_bits_tell_flat_crops_from_detailed_ones():
    assert decisive_bits(icon_on_tan()) < 40
    assert decisive_bits(Image.open(io.BytesIO(jpeg_bytes()))) >= 40
    ramp = np.tile(np.linspace(0, 255, 120), (120, 1)).astype("uint8")
    assert decisive_bits(Image.fromarray(np.stack([ramp] * 3, -1))) == 64


def test_only_strong_detailed_confirmations_join_the_list(ctx, client, device, clock, opinion, mailer):
    _, headers = device
    # Explicit by the second opinion but under the known-bad line: alerted, not listed.
    ev, _ = _report(client, headers, clock, "00000000ffffffff")
    opinion.fixed = 0.80
    client.put(f"/api/v1/events/{ev}/image", headers=headers, content=jpeg_bytes())
    assert ctx.events.get(ev)["severity"] == "explicit" and len(mailer.sent) == 1
    assert "00000000ffffffff" not in _known_bad(client, headers)
    # Over the line but a flat crop: still not listed.
    clock.advance(minutes=40)
    ev, _ = _report(client, headers, clock, "ff00ff00ff00ff00")
    opinion.fixed = 0.95
    client.put(f"/api/v1/events/{ev}/image", headers=headers, content=_jpeg(icon_on_tan()))
    assert "ff00ff00ff00ff00" not in _known_bad(client, headers)
    # Over the line with detail: listed.
    ev, _ = _report(client, headers, clock, "0f0f0f0f0f0f0f0f")
    client.put(f"/api/v1/events/{ev}/image", headers=headers, content=jpeg_bytes())
    assert "0f0f0f0f0f0f0f0f" in _known_bad(client, headers)


def test_a_hash_with_safe_look_alikes_is_a_hub_and_stays_off(ctx, client, device, clock, opinion):
    _, headers = device
    ctx.judge_cache.put("0f0f0f0f0f0f0f0e", (0.1, "safe"))   # one bit away, judged safe
    ev, _ = _report(client, headers, clock, "0f0f0f0f0f0f0f0f")
    opinion.fixed = 0.95
    client.put(f"/api/v1/events/{ev}/image", headers=headers, content=jpeg_bytes())
    assert "0f0f0f0f0f0f0f0f" not in _known_bad(client, headers)


def test_a_safe_second_opinion_on_a_hash_cover_removes_it_and_its_look_alikes(ctx, client, device, clock, opinion):
    _, headers = device
    with ctx.conn:
        ctx.events.add_known_bad("1234123412341234", "confirmed")
        ctx.events.add_known_bad("1234123412341235", "confirmed")   # a look-alike
        ctx.events.add_known_bad("abcdabcdabcdabcd", "confirmed")   # unrelated
    ev, _ = _report(client, headers, clock, "1234123412341234", labels=["known_bad"], score=1.0)
    opinion.fixed = 0.05
    client.put(f"/api/v1/events/{ev}/image", headers=headers, content=jpeg_bytes())
    assert _known_bad(client, headers) == ["abcdabcdabcdabcd"]


def test_a_confirming_opinion_on_a_hash_cover_does_not_widen_the_list(ctx, client, device, clock, opinion):
    _, headers = device
    with ctx.conn:
        ctx.events.add_known_bad("1234123412341234", "confirmed")
    ev, _ = _report(client, headers, clock, "1234123412341237", labels=["known_bad"], score=1.0)
    opinion.fixed = 0.99
    client.put(f"/api/v1/events/{ev}/image", headers=headers, content=jpeg_bytes())
    assert _known_bad(client, headers) == ["1234123412341234"]


def test_look_alike_repeats_are_recorded_but_not_uploaded_or_reviewed(ctx, client, device, clock, opinion, mailer):
    _, headers = device
    first, r = _report(client, headers, clock, "5555aaaa5555aaaa")
    assert r["upload"] == [first]
    clock.advance(minutes=2)
    second, r = _report(client, headers, clock, "5555aaaa5555aaab")
    assert r["accepted"] == [second] and r["upload"] == []
    ev = ctx.events.get(second)
    assert ev["image_status"] == "repeat" and ev["severity"] == "info"
    # After the window the same picture is a new event again.
    clock.advance(minutes=11)
    third, r = _report(client, headers, clock, "5555aaaa5555aaab")
    assert r["upload"] == [third]


def test_one_alert_per_look_alike_group_per_half_hour(ctx, client, device, clock, opinion, mailer):
    _, headers = device
    opinion.fixed = 0.95
    first, _ = _report(client, headers, clock, "7777000077770000")
    client.put(f"/api/v1/events/{first}/image", headers=headers, content=jpeg_bytes())
    clock.advance(minutes=11)   # past the repeat window, inside the alert window
    second, _ = _report(client, headers, clock, "7777000077770001")
    client.put(f"/api/v1/events/{second}/image", headers=headers, content=jpeg_bytes())
    assert ctx.events.get(second)["severity"] == "explicit"
    assert len(mailer.sent) == 1


def test_false_alarm_takes_the_hash_off_the_list(ctx, client, household, device, clock, opinion):
    _, headers = device
    ev, _ = _report(client, headers, clock, "0f0f0f0f0f0f0f0f")
    opinion.fixed = 0.95
    client.put(f"/api/v1/events/{ev}/image", headers=headers, content=jpeg_bytes())
    assert "0f0f0f0f0f0f0f0f" in _known_bad(client, headers)
    r = client.post(f"/review/{ev}", data={"state": "false_alarm"}, headers=as_login(PARTNER), follow_redirects=False)
    assert r.status_code == 303
    assert "0f0f0f0f0f0f0f0f" not in _known_bad(client, headers)
