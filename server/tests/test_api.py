from __future__ import annotations

import io
import uuid

from PIL import Image

from app.clock import iso
from tests.conftest import jpeg_bytes


def heartbeat(client, headers, clock, **state):
    base = {"protection": True, "accessibility": True, "capture": "granted", "overlay": True,
            "device_admin": True, "extension": None, "vpn": True}
    base.update(state)
    return client.post("/api/v1/heartbeat", headers=headers, json={
        "sent_at": iso(clock.now()), "app_version": "0.1.0", "booted_at": iso(clock.now()),
        "safe_mode": False, "battery_pct": 80, "queued_events": 0, "state": base})


def test_heartbeat_metrics_merge_per_day_and_show_on_devices(ctx, client, device, clock):
    from tests.conftest import ADMIN
    from tests.test_portal import as_login
    dev, headers = device
    work = {"window_s": 300, "counters": {"passes_run": 3, "passes_skipped": 9},
            "timings": {"inference_ms": {"n": 3, "sum": 51.0, "max": 20.0,
                                          "buckets": [0, 0, 0, 0, 2, 1, 0, 0, 0, 0, 0, 0]}}}
    base = {"sent_at": iso(clock.now()), "app_version": "0.1.0", "booted_at": iso(clock.now()),
            "safe_mode": False, "battery_pct": 80, "queued_events": 0,
            "state": {"protection": True}, "metrics": work}
    assert client.post("/api/v1/heartbeat", headers=headers, json=base).status_code == 200
    clock.advance(minutes=5)
    assert client.post("/api/v1/heartbeat", headers=headers, json={**base, "sent_at": iso(clock.now())}).status_code == 200
    from app.services.heartbeat import household_day
    m = ctx.devices.metrics_for(dev["id"], household_day(ctx))
    assert m["window_s"] == 600 and m["counters"] == {"passes_run": 6, "passes_skipped": 18}
    t = m["timings"]["inference_ms"]
    assert t["n"] == 6 and t["buckets"][4] == 4 and t["max"] == 20.0
    # The metrics are not kept in the device's last state.
    assert "metrics" not in ctx.devices.get(dev["id"])["last_state"]
    # Junk is dropped, not stored.
    bad = {**base, "metrics": {"counters": {"x": -1, "y": "no"}, "timings": {"z": {"buckets": [1]}}}}
    assert client.post("/api/v1/heartbeat", headers=headers, json=bad).status_code == 200
    assert ctx.devices.metrics_for(dev["id"], household_day(ctx))["counters"] == {"passes_run": 6, "passes_skipped": 18}
    page = client.get("/devices", headers=as_login(ADMIN))
    assert "work today" in page.text and "inference" in page.text and "16 ms" in page.text


def test_metrics_percentiles_come_from_buckets():
    from app.services import metrics
    t = {"n": 100, "sum": 1500.0, "max": 300.0, "buckets": [0, 0, 0, 10, 70, 15, 0, 0, 5, 0, 0, 0]}
    assert metrics.percentile(t, 0.5) == 16 and metrics.percentile(t, 0.95) == 32 and metrics.percentile(t, 1.0) == 256
    over = {"n": 2, "sum": 3000.0, "max": 2000.0, "buckets": [0] * 11 + [2]}
    assert metrics.percentile(over, 0.5) == 2000.0


def test_pairing_is_single_use_and_times_out(ctx, client, household, clock):
    with ctx.conn:
        code = ctx.devices.create_pairing_code(household["person_id"])
    dev = {"name": "PC", "platform": "windows", "kind": "service", "app_version": "0.1.0"}
    assert client.post("/api/v1/pair", json={"code": code, "device": dev}).status_code == 200
    r = client.post("/api/v1/pair", json={"code": code, "device": dev})
    assert r.status_code == 400 and r.json()["error"]["code"] == "pairing_code_invalid"
    with ctx.conn:
        code2 = ctx.devices.create_pairing_code(household["person_id"])
    clock.advance(minutes=16)
    assert client.post("/api/v1/pair", json={"code": code2, "device": dev}).status_code == 400


def test_pairing_code_may_be_typed_without_dashes_or_capitals(ctx, client, household):
    dev = {"name": "Phone", "platform": "android", "kind": "app", "app_version": "0.1.0"}
    for typed in (lambda c: c.replace("-", "").lower(), lambda c: " " + c.replace("-", " ") + " "):
        with ctx.conn:
            code = ctx.devices.create_pairing_code(household["person_id"])
        assert client.post("/api/v1/pair", json={"code": typed(code), "device": dev}).status_code == 200
    assert client.post("/api/v1/pair", json={"code": "ABC", "device": dev}).status_code == 400


def test_pair_link_and_qr():
    from app.services import pairlink
    link = pairlink.pair_link("https://watchman.example.ts.net", "K7Q-4MZ-P2D")
    assert link == "https://watchman.example.ts.net/pair?code=K7Q4MZP2D"
    assert pairlink.app_link("https://watchman.example.ts.net", "K7Q-4MZ-P2D") == \
        "watchman://pair?server=https%3A%2F%2Fwatchman.example.ts.net&code=K7Q4MZP2D"
    svg = pairlink.qr_svg(link)
    assert svg.startswith("<svg") and "#0b1114" in svg
    assert pairlink.normalize_code(" k7q 4mz p2d ") == "K7Q-4MZ-P2D"


def test_a_malformed_event_is_rejected_alone(ctx, client, device):
    _, headers = device
    good = {"id": str(uuid.uuid4()), "type": "daily_stats", "occurred_at": "2026-11-02T12:00:00Z", "action": "none"}
    r = client.post("/api/v1/events", headers=headers, json={"events": [
        {"id": None, "type": None, "occurred_at": None}, {"id": "x1", "type": "nonsense", "occurred_at": "t"}, good]})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == [good["id"]]
    assert [x["id"] for x in body["rejected"]] == [None, "x1"]
    assert body["rejected"][0]["reason"] == "missing id, type, occurred_at"


def test_policy_carries_the_sure_line(ctx, client, device):
    _, headers = device
    t = client.get("/api/v1/policy", headers=headers).json()["thresholds"]
    assert t["sure"] == 0.97 and t["ask"] < t["cover"] < t["sure"]


def test_policy_version_is_cheap_and_follows_settings(ctx, client, device):
    _, headers = device
    v1 = client.get("/api/v1/policy/version", headers=headers).json()
    with ctx.conn:
        ctx.settings.set("coverage", "revealing", "owner")
    v2 = client.get("/api/v1/policy/version", headers=headers).json()
    assert v2["policy_version"] == v1["policy_version"] + 1 and v2["paused_until"] is None
    assert client.get("/api/v1/policy/version").status_code == 401


def test_peeks_last_three_seconds_at_most():
    import pytest
    from pydantic import ValidationError

    from app.settings import coerce
    assert coerce("peek_seconds", 3) == 3
    with pytest.raises(ValidationError):
        coerce("peek_seconds", 5)


def test_unknown_token_is_unauthorized(client):
    r = client.get("/api/v1/policy", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_policy_reflects_settings_and_version(ctx, client, device):
    _, headers = device
    p = client.get("/api/v1/policy", headers=headers).json()
    assert p["thresholds"]["cover"] == 0.85
    assert p["protected_browser"] == "org.mozilla.firefox"
    assert p["paused_until"] is None
    v = p["version"]
    with ctx.conn:
        ctx.settings.set("strict_mode", False, "owner")
    p2 = client.get("/api/v1/policy", headers=headers).json()
    assert p2["version"] == v + 1 and p2["strict_mode"] is False


def test_heartbeat_derives_protection_events_and_alerts(ctx, client, device, clock, mailer):
    _, headers = device
    r = heartbeat(client, headers, clock)
    assert r.status_code == 200 and r.json()["policy_version"] >= 1
    assert mailer.sent == []
    clock.advance(minutes=5)
    heartbeat(client, headers, clock, accessibility=False)
    assert len(mailer.sent) == 1
    mail = mailer.sent[0]
    assert "accessibility" in mail.text and set(mail.to) == {"alex@example.com", "sam@example.com", "pat@example.com"}
    events = ctx.events.timeline(device[0]["person_id"])
    assert events[0]["type"] == "protection_changed" and events[0]["severity"] == "protection"
    # Turning it back on is recorded but does not alert.
    clock.advance(minutes=5)
    heartbeat(client, headers, clock, accessibility=True)
    assert len(mailer.sent) == 1
    assert ctx.events.timeline(device[0]["person_id"])[0]["severity"] == "info"


def test_events_are_idempotent_and_ask_for_images(client, device, clock):
    _, headers = device
    ev_id = str(uuid.uuid4())
    body = {"events": [{
        "id": ev_id, "type": "image_covered", "occurred_at": iso(clock.now()), "app": "org.mozilla.firefox",
        "url": "https://example.com", "action": "covered", "scores": {"classifier": 0.93},
        "region": {"w": 48, "h": 48, "screen_w": 1440, "screen_h": 3120}, "hash": "9f3a0c1e5b7d2a46",
        "has_image": True, "detail": {}}]}
    r = client.post("/api/v1/events", headers=headers, json=body)
    assert r.json() == {"accepted": [ev_id], "duplicates": [], "upload": [ev_id], "rejected": []}
    r = client.post("/api/v1/events", headers=headers, json=body)
    assert r.json() == {"accepted": [], "duplicates": [ev_id], "upload": [], "rejected": []}


def test_image_upload_stores_blurs_and_never_the_original(ctx, client, device, clock, opinion, mailer):
    _, headers = device
    ev_id = str(uuid.uuid4())
    client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "image_covered", "occurred_at": iso(clock.now()), "app": "com.reddit.frontpage",
        "action": "covered", "scores": {"classifier": 0.9}, "hash": "abcdefabcdefabcd", "has_image": True}]})
    opinion.fixed = 0.2   # disagreement: stays a review item, no alert
    r = client.put(f"/api/v1/events/{ev_id}/image", headers={**headers, "Content-Type": "image/jpeg"},
                   content=jpeg_bytes())
    assert r.status_code == 204, r.text
    ev = ctx.events.get(ev_id)
    assert ev["severity"] == "review" and ev["image_status"] == "stored" and ev["second_opinion"] == 0.2
    assert ctx.blobs.exists(ev_id, "heavy") and ctx.blobs.exists(ev_id, "medium")
    stored = list(ctx.config.images_dir.iterdir())
    assert all(p.suffix == ".bin" for p in stored) and len(stored) == 2
    # The sealed files are not readable images.
    raw = stored[0].read_bytes()
    assert b"WEBP" not in raw and b"JFIF" not in raw
    assert mailer.sent == []


def test_confirmed_explicit_alerts_and_shares_the_hash(ctx, client, device, clock, opinion, mailer):
    _, headers = device
    ev_id = str(uuid.uuid4())
    client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "image_covered", "occurred_at": iso(clock.now()), "app": "org.mozilla.firefox",
        "action": "covered", "scores": {"classifier": 0.95}, "hash": "1111222233334444", "has_image": True}]})
    opinion.fixed = 0.91
    client.put(f"/api/v1/events/{ev_id}/image", headers=headers, content=jpeg_bytes())
    ev = ctx.events.get(ev_id)
    assert ev["severity"] == "explicit" and ev["alerted_at"]
    assert len(mailer.sent) == 1 and "explicit" in mailer.sent[0].subject
    assert mailer.sent[0].inline and mailer.sent[0].inline[0].mime == "image/webp"
    policy = client.get("/api/v1/policy", headers=headers).json()
    assert "1111222233334444" in policy["known_bad_hashes"]
    # A second event with the same hash does not need another upload.
    ev2 = str(uuid.uuid4())
    r = client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev2, "type": "image_covered", "occurred_at": iso(clock.now()), "action": "covered",
        "hash": "1111222233334444", "has_image": True}]})
    assert r.json()["upload"] == []


def test_unsolicited_image_is_refused(client, device, clock):
    _, headers = device
    ev_id = str(uuid.uuid4())
    client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "url_blocked", "occurred_at": iso(clock.now()), "action": "blocked"}]})
    r = client.put(f"/api/v1/events/{ev_id}/image", headers=headers, content=jpeg_bytes())
    assert r.status_code == 400


def test_unlock_code_pauses_and_is_single_use(ctx, client, device, household, clock, mailer):
    dev, headers = device
    r = client.post("/api/v1/unlock", headers=headers, json={"code": "000-000"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unlock_code_invalid"
    with ctx.conn:
        _, code = ctx.codes.create_code(household["person_id"], household["sam"], 30)
    r = client.post("/api/v1/unlock", headers=headers, json={"code": code})
    assert r.status_code == 200
    assert client.get("/api/v1/policy", headers=headers).json()["paused_until"] == r.json()["paused_until"]
    assert client.post("/api/v1/unlock", headers=headers, json={"code": code}).status_code == 400
    clock.advance(minutes=31)
    assert client.get("/api/v1/policy", headers=headers).json()["paused_until"] is None


def test_unlock_request_emails_codes_to_email_partners(client, device, mailer):
    _, headers = device
    r = client.post("/api/v1/unlock/request", headers=headers, json={"minutes": 20, "reason": "work upload"})
    assert r.status_code == 202
    by_to = {m.to[0]: m for m in mailer.sent}
    assert "pat@example.com" in by_to and "code:" in by_to["pat@example.com"].text
    assert "sam@example.com" in by_to and "code:" not in by_to["sam@example.com"].text


def test_pause_longer_than_the_cap_is_refused(ctx, client, device, household):
    _, headers = device
    r = client.post("/api/v1/unlock/request", headers=headers, json={"minutes": 999})
    assert r.status_code == 400


def test_models_manifest_rewrites_urls(ctx, client, device, tmp_path):
    _, headers = device
    ctx.config.models_dir.mkdir(parents=True, exist_ok=True)
    (ctx.config.models_dir / "manifest.json").write_text(
        '{"version": 1, "models": [{"role": "crop_classifier", "file": "a.onnx", "url": "https://x/a.onnx"}]}')
    (ctx.config.models_dir / "a.onnx").write_bytes(b"onnx")
    m = client.get("/api/v1/models/manifest", headers=headers).json()
    assert m["models"][0]["url"] == "/api/v1/models/a.onnx"
    assert client.get("/api/v1/models/a.onnx", headers=headers).content == b"onnx"
    assert client.get("/api/v1/models/../secret", headers=headers).status_code in (404, 400)


def test_policy_carries_the_coverage_label_set(ctx, client, device):
    _, headers = device
    p = client.get("/api/v1/policy", headers=headers).json()
    assert p["coverage"] == "explicit"
    assert "FEMALE_BREAST_EXPOSED" in p["explicit_labels"] and "FEMALE_BREAST_COVERED" not in p["explicit_labels"]
    with ctx.conn:
        ctx.settings.set("coverage", "revealing", "owner")
    p2 = client.get("/api/v1/policy", headers=headers).json()
    assert p2["version"] == p["version"] + 1
    assert "FEMALE_BREAST_COVERED" in p2["explicit_labels"] and "BELLY_EXPOSED" not in p2["explicit_labels"]
    with ctx.conn:
        ctx.settings.set("coverage", "strict", "owner")
    assert "BELLY_EXPOSED" in client.get("/api/v1/policy", headers=headers).json()["explicit_labels"]


def test_policy_carries_the_posture_and_one_persons_own_values(ctx, client, device):
    dev, headers = device
    p = client.get("/api/v1/policy", headers=headers).json()
    assert p["cover_on_device"] is True and p["ask_server"] is True
    with ctx.conn:
        ctx.settings.set("person_overrides", {dev["person_id"]: {"cover_on_device": False, "thresholds": {
            "cover": 0.9, "flag": 0.7, "ask": 0.5, "detector_cover": 0.55, "second_opinion": 0.7}}}, "owner")
    p2 = client.get("/api/v1/policy", headers=headers).json()
    assert p2["version"] == p["version"] + 1
    assert p2["cover_on_device"] is False and p2["thresholds"]["flag"] == 0.7 and p2["ask_server"] is True
    assert ctx.settings.load().cover_on_device is True  # the household itself is untouched


def test_judge_scores_in_memory_and_keeps_nothing(ctx, client, device, opinion):
    _, headers = device
    opinion.fixed = 0.2
    r = client.post("/api/v1/judge", headers={**headers, "Content-Type": "image/jpeg"}, content=jpeg_bytes())
    assert r.status_code == 200 and r.json() == {"score": 0.2, "verdict": "safe"}
    # The same crop again is answered from the hash cache without a second inference (ADR-023).
    opinion.fixed = 0.95
    assert client.post("/api/v1/judge", headers=headers, content=jpeg_bytes()).json()["verdict"] == "safe"
    assert opinion.calls == 1 and ctx.judge_cache.hits == 1
    gradient = io.BytesIO()
    Image.linear_gradient("L").resize((200, 200)).convert("RGB").save(gradient, format="JPEG")
    assert client.post("/api/v1/judge", headers=headers, content=gradient.getvalue()).json()["verdict"] == "explicit"
    assert list(ctx.config.images_dir.iterdir()) == []
    assert ctx.events.timeline(device[0]["person_id"]) == []
    assert client.get("/api/v1/policy", headers=headers).json()["thresholds"]["ask"] == 0.60
    assert client.post("/api/v1/judge", headers=headers, content=b"not an image").status_code == 400


def test_miss_reported_is_accepted_as_info_without_an_upload(ctx, client, device, clock):
    _, headers = device
    ev_id = str(uuid.uuid4())
    r = client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "miss_reported", "occurred_at": iso(clock.now()), "app": "com.instagram.android",
        "action": "none", "detail": {"regions": [{"w": 1344, "h": 2992, "classifier": 0.41, "detector": 0.2}]}}]})
    assert r.json()["accepted"] == [ev_id] and r.json()["upload"] == []
    assert ctx.events.get(ev_id)["severity"] == "info"
