from __future__ import annotations

import hashlib

from app.clock import iso
from app.services import digest, downloads

V = "0.2.1"
FILES = {
    f"watchman-android-{V}-arm64-v8a.apk": b"apk arm64",
    f"watchman-android-{V}-armeabi-v7a.apk": b"apk v7a",
    f"watchman-windows-{V}-x64.msi": b"msi",
    f"watchman-extension-chrome-{V}.zip": b"chrome zip",
    f"watchman-extension-firefox-{V}.zip": b"firefox zip",
}


def bundle_dir(path, files=FILES, sums=None):
    path.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, data in files.items():
        (path / name).write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    (path / "SHA256SUMS").write_text("\n".join(sums if sums is not None else lines) + "\n", encoding="utf-8")
    return path


def use_bundle(ctx, tmp_path, **kw):
    downloads.load.cache_clear()   # read once per server run; a test changes it mid-run
    ctx.config.downloads_dir = bundle_dir(tmp_path / "downloads", **kw)


def test_versions_order_and_who_needs_an_update():
    b = downloads.Bundle(V, (downloads.ClientFile("x.apk", "android", "arm64-v8a", V, "0", 1),
                             downloads.ClientFile("y.zip", "extension", "chrome", V, "0", 1)))
    assert downloads.parse_version("0.2.1-rc.1") < downloads.parse_version("0.2.1") < downloads.parse_version("0.2.2")
    assert downloads.update_for(b, "android", "0.2.0") == V
    assert downloads.update_for(b, "android", "0.2.1-rc.1") == V
    assert downloads.update_for(b, "android", V) is None
    assert downloads.update_for(b, "android", "0.3.0") is None
    assert downloads.update_for(b, "edge", "0.1.0") == V            # Edge installs the Chrome zip
    assert downloads.update_for(b, "windows", "0.1.0") is None      # not in this bundle
    assert downloads.update_for(b, "ios", "0.1.0") is None
    assert downloads.update_for(b, "android", "dev") is None
    assert downloads.update_for(None, "android", "0.1.0") is None


def test_only_files_that_match_their_checksum_are_offered(tmp_path):
    d = bundle_dir(tmp_path / "a")
    (d / f"watchman-windows-{V}-x64.msi").write_bytes(b"changed after the sums were written")
    (d / "notes.txt").write_text("not a client", encoding="utf-8")
    b = downloads.load(d)
    assert b is not None and b.version == V
    assert {f.name for f in b.files} == set(FILES) - {f"watchman-windows-{V}-x64.msi"}
    assert [f.variant for f in b.of("android")] == ["arm64-v8a", "armeabi-v7a"]


def test_no_sums_or_mixed_versions_offer_nothing(tmp_path):
    assert downloads.load(tmp_path / "missing") is None
    mixed = dict(FILES) | {"watchman-android-0.2.0-x86_64.apk": b"old"}
    assert downloads.load(bundle_dir(tmp_path / "b", files=mixed)) is None


def test_a_source_build_says_where_to_get_the_clients(client):
    r = client.get("/downloads")
    assert r.status_code == 200
    assert "built from source" in r.text
    assert client.get(f"/downloads/watchman-windows-{V}-x64.msi").status_code == 404


def test_downloads_need_no_sign_in_and_serve_only_listed_files(ctx, client, tmp_path):
    use_bundle(ctx, tmp_path)
    page = client.get("/downloads", headers={"X-Fake-Login": ""})
    assert page.status_code == 200
    assert f"watchman-android-{V}-arm64-v8a.apk" in page.text
    assert hashlib.sha256(b"msi").hexdigest() in page.text

    r = client.get(f"/downloads/watchman-android-{V}-arm64-v8a.apk")
    assert r.status_code == 200
    assert r.content == b"apk arm64"
    assert r.headers["content-type"] == "application/vnd.android.package-archive"
    assert "attachment" in r.headers["content-disposition"]

    assert client.get("/downloads/SHA256SUMS").status_code == 404
    assert client.get("/downloads/..%2Fwatchman.sqlite3").status_code == 404


def test_devices_and_the_digest_name_a_device_that_is_behind(ctx, client, device, household, clock, tmp_path):
    use_bundle(ctx, tmp_path)                    # the paired phone reports 0.1.0
    page = client.get("/devices", params={"person": household["person_id"]})
    assert f"update to {V}" in page.text

    start, end = digest.period_for(ctx, household["person_id"], clock.now())
    summary = digest.summarise(ctx, household["person_id"], start, end)
    assert summary["outdated"] == [("Pixel", "0.1.0", V)]
    text, html, _ = digest.render(ctx, "Alex", summary, start, end, digest.ZoneInfo("UTC"))
    assert f"Pixel: 0.1.0, {V} is available" in text


def beat(client, headers, clock, version):
    state = {"protection": True, "accessibility": True, "capture": "granted", "overlay": True, "device_admin": True}
    r = client.post("/api/v1/heartbeat", headers=headers, json={
        "sent_at": iso(clock.now()), "app_version": version, "booted_at": iso(clock.now()),
        "safe_mode": False, "battery_pct": 80, "queued_events": 0, "state": state})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_heartbeat_offers_a_device_that_is_behind_its_files(ctx, client, device, clock, tmp_path):
    _, headers = device
    assert beat(client, headers, clock, "0.1.0")["update"] is None      # a source build carries nothing
    use_bundle(ctx, tmp_path)
    offer = beat(client, headers, clock, "0.1.0")["update"]
    assert offer["version"] == V
    assert [f["variant"] for f in offer["files"]] == ["arm64-v8a", "armeabi-v7a"]
    first = offer["files"][0]
    assert first["url"] == f"/downloads/watchman-android-{V}-arm64-v8a.apk"
    assert first["sha256"] == hashlib.sha256(b"apk arm64").hexdigest() and first["size"] == len(b"apk arm64")
    assert client.get(first["url"]).content == b"apk arm64"

    assert beat(client, headers, clock, V)["update"] is None             # current
    with ctx.conn:
        ctx.settings.set("auto_update", False)
    assert beat(client, headers, clock, "0.1.0")["update"] is None       # the household turned it off


def test_a_new_version_is_a_line_on_the_timeline_and_no_alert(ctx, client, device, household, clock, mailer):
    dev, headers = device
    beat(client, headers, clock, "0.1.0")
    beat(client, headers, clock, "0.1.0")
    beat(client, headers, clock, V)
    rows = ctx.conn.execute("SELECT type, detail, severity FROM events WHERE device_id = ? AND type = 'app_updated'",
                            (dev["id"],)).fetchall()
    assert [(r["detail"], r["severity"]) for r in rows] == [(f'{{"from": "0.1.0", "to": "{V}"}}', "info")]
    assert mailer.sent == []
    page = client.get("/", params={"person": household["person_id"]})
    assert f"Updated to {V}" in page.text
