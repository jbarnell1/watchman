"""Event intake and the image pipeline.

Severity at intake:
  image_covered, screen_blocked   review until the second opinion says explicit; with
                                  covered_reports exposed, info unless the server's detector
                                  finds bare nudity or the second opinion reaches
                                  thresholds.covered_report (ADR-046)
  image_flagged                   info, promoted by the second opinion
  url_blocked, search_flagged, app_blocked, pause_*, daily_stats   info
  miss_reported (the person says something should have been covered; numbers only)   info
  protection_changed (from a client)   info; the server derives its own from heartbeats
  tamper                          protection, alerts
  protection_gap (sent after the fact by a device that was off, ADR-051)   protection when it
                                  lasted GAP_ALERT (a reboot: REBOOT_ALERT) or more, else info
  block_passed (Continue anyway on a whole-screen block, ADR-047)   protection, alerts

An image is requested when the event has one and no stored image already carries its hash.
On upload the server scores it with the second opinion, writes heavy and medium blurs, stores
them encrypted and never writes the original (ADR-004).
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
from datetime import timedelta

from PIL import Image

from app.services import alerts, blur, keys
from app.services.context import Context
from app.services.errors import ApiError, NotFound
from app.services.imagehash import MATCH_TOLERANCE, MIN_DECISIVE_BITS, decisive_bits, dhash, hamming
from app.store.events_store import SEVERITY_EXPLICIT, SEVERITY_INFO, SEVERITY_PROTECTION, SEVERITY_REVIEW

log = logging.getLogger(__name__)

MAX_BATCH = 200
IMAGE_TYPES = {"image_covered", "screen_blocked", "image_flagged"}
KNOWN_TYPES = IMAGE_TYPES | {
    "url_blocked", "search_flagged", "app_blocked", "protection_changed", "tamper",
    "pause_started", "pause_ended", "daily_stats", "miss_reported", "block_passed", "protection_gap",
}
GAP_ALERT = timedelta(minutes=3)
REBOOT_ALERT = timedelta(minutes=10)
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_SIDE = 512
# A look-alike of a picture this device reported within this window is stored for the record but
# not uploaded, judged or alerted again: one picture on a looping video is one event (ADR-035).
REPEAT_WINDOW = timedelta(minutes=10)
ALERT_WINDOW = timedelta(minutes=30)


COVERED_TYPES = {"image_covered", "screen_blocked"}


def _initial_severity(event: dict, covered_reports: str) -> str:
    t = event["type"]
    if t in COVERED_TYPES:
        return SEVERITY_REVIEW if covered_reports == "all" else SEVERITY_INFO
    if t in ("tamper", "block_passed"):
        return SEVERITY_PROTECTION
    if t == "protection_gap":
        return SEVERITY_PROTECTION if _gap(event) >= (REBOOT_ALERT if _detail(event).get("reason") == "reboot"
                                                      else GAP_ALERT) else SEVERITY_INFO
    return SEVERITY_INFO


def _detail(event: dict) -> dict:
    return event.get("detail") if isinstance(event.get("detail"), dict) else {}


def _gap(event: dict) -> timedelta:
    from app.clock import parse_iso
    try:
        return parse_iso(_detail(event)["to"]) - parse_iso(_detail(event)["from"])
    except (KeyError, TypeError, ValueError):
        return timedelta(0)


def accept(ctx: Context, device: sqlite3.Row, events: list[dict]) -> dict:
    if len(events) > MAX_BATCH:
        raise ApiError(f"at most {MAX_BATCH} events per call")
    accepted, duplicates, upload, rejected = [], [], [], []
    recent = ctx.events.recent_image_hashes(device["id"], ctx.clock.now() - REPEAT_WINDOW)
    covered_reports = ctx.settings.load().covered_reports
    # Where a page or search was is sealed for partners once they hold keys (ADR-053).
    sealed_for = keys.keyed_partners(ctx, device["person_id"])
    with ctx.conn:
        for ev in events:
            # A malformed event is named in `rejected` and skipped; the rest of the batch still
            # lands, so one bad event cannot block a device's queue for ever.
            if not isinstance(ev, dict):
                rejected.append({"id": None, "reason": "not an object"})
                continue
            missing = [f for f in ("id", "type", "occurred_at") if not ev.get(f)]
            if missing:
                rejected.append({"id": ev.get("id") or None, "reason": f"missing {', '.join(missing)}"})
                continue
            if ev["type"] not in KNOWN_TYPES:
                rejected.append({"id": ev["id"], "reason": f"unknown event type {ev['type']}"})
                continue
            if ctx.events.exists(ev["id"]):
                duplicates.append(ev["id"])
                continue
            severity = _initial_severity(ev, covered_reports)
            image_status = "none"
            if ev["type"] in IMAGE_TYPES and ev.get("has_image"):
                if ev.get("hash") and ctx.events.hash_has_image(device["person_id"], ev["hash"]):
                    image_status = "skipped"
                elif ev.get("hash") and any(hamming(ev["hash"], h) <= MATCH_TOLERANCE for h in recent):
                    image_status = "repeat"
                    severity = SEVERITY_INFO  # the first look-alike carries the review
                else:
                    image_status = "wanted"
                    upload.append(ev["id"])
            if sealed_for and (ev.get("url") or ev.get("title")):
                ev = dict(ev, sealed=keys.seal(json.dumps({"url": ev.get("url"), "title": ev.get("title")}).encode(),
                                               ev["id"], sealed_for).decode(), url=None, title=None)
            ctx.events.insert(ev, device["id"], device["person_id"], severity, image_status)
            if ev.get("hash"):
                recent.append(ev["hash"])
            accepted.append(ev["id"])
            if severity == SEVERITY_PROTECTION:
                alerts.protection_alert(ctx, device, ev)
    if rejected:
        log.warning("device %s sent %d malformed event(s): %s", device["id"], len(rejected),
                    "; ".join(r["reason"] for r in rejected[:5]))
    return {"accepted": accepted, "duplicates": duplicates, "upload": upload, "rejected": rejected}


def judge_image(ctx: Context, data: bytes) -> dict:
    """Score a crop with the second opinion and keep nothing (ADR-022). Devices use it for the band
    between the ask and cover thresholds: blurred first, unblurred on a safe answer."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError("image too large")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ApiError(f"not an image: {exc}") from exc
    if max(image.size) > MAX_IMAGE_SIDE:
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    key = dhash(image)
    cached = ctx.judge_cache.get(key)
    if cached is not None:
        del image, data
        return {"score": cached[0], "verdict": cached[1]}
    score = ctx.second_opinion.score(image)
    del image, data
    threshold = ctx.settings.load().thresholds.second_opinion
    answer = (round(score, 4), "explicit" if score >= threshold else "safe")
    ctx.judge_cache.put(key, answer)
    return {"score": answer[0], "verdict": answer[1]}


def store_image(ctx: Context, device: sqlite3.Row, event_id: str, data: bytes) -> None:
    event = ctx.events.get(event_id)
    if event is None or event["device_id"] != device["id"]:
        raise NotFound("no such event for this device")
    if event["image_status"] not in ("wanted", "stored"):
        raise ApiError("the server did not ask for this image")
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError("image too large")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ApiError(f"not an image: {exc}") from exc
    if max(image.size) > MAX_IMAGE_SIDE:
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))

    score = ctx.second_opinion.score(image)
    labels = ctx.detector.labels(image)
    decisive = decisive_bits(image)
    heavy_webp = blur.to_webp(blur.heavy(image))
    medium_webp = blur.to_webp(blur.medium(image))
    del image, data  # the original lives only in this function

    settings = ctx.settings.load()
    exposed = max((s for label, s in labels.items() if label in ctx.detector.explicit), default=0.0)
    confirmed = reaches_partners(settings, event["type"], score, exposed)
    if confirmed:
        severity = SEVERITY_EXPLICIT
    elif event["type"] == "image_flagged" or settings.covered_reports != "all":
        severity = SEVERITY_INFO
    else:
        severity = SEVERITY_REVIEW
    with ctx.conn:
        # The peekable blur is sealed for partners, unless the person may peek at their own (ADR-053).
        recipients = [] if settings.self_peek else keys.keyed_partners(ctx, event["person_id"])
        if recipients:
            medium_webp = keys.seal(medium_webp, event_id + ":medium", recipients)
        files = {"heavy": ctx.blobs.put(event_id, "heavy", heavy_webp),
                 "medium": ctx.blobs.put(event_id, "medium", medium_webp)}
        ctx.events.add_image(event_id, blur.OUTPUT_SIZE, blur.OUTPUT_SIZE, files, sealed=bool(recipients))
        ctx.events.set_second_opinion(event_id, score, severity, "stored")
        ctx.events.set_server_labels(event_id, labels)
        if event["hash"] and _update_known_bad(ctx, event, score, decisive, settings):
            ctx.settings.bump_policy()
    if confirmed and not event["alerted_at"] and not _alerted_lately(ctx, event):
        alerts.explicit_alert(ctx, ctx.events.get(event_id), heavy_webp)


def reaches_partners(settings, event_type: str, second: float, exposed: float) -> bool:
    """Whether an image event becomes explicit and reaches partners. A picture the device covered
    needs bare nudity from the server's detector ([exposed], its best explicit-label score) or a
    second opinion at thresholds.covered_report, unless every covered picture is reported (ADR-046)."""
    t = settings.thresholds
    if event_type in COVERED_TYPES and settings.covered_reports != "all":
        return exposed >= t.server_exposed or second >= max(t.second_opinion, t.covered_report)
    return second >= t.second_opinion


def _labels(event: sqlite3.Row) -> list[str]:
    try:
        return list((json.loads(event["scores"] or "{}") or {}).get("labels") or [])
    except (ValueError, AttributeError):
        return []


def _update_known_bad(ctx: Context, event: sqlite3.Row, score: float, decisive: int, settings) -> bool:
    """The known-bad list feeds back into what devices report, so it changes only on strong,
    independent evidence (ADR-035). Returns True when the list changed."""
    t = settings.thresholds
    if "known_bad" in _labels(event):
        # The device covered this by hash. A safe second opinion means the listed hash is wrong:
        # drop it and its look-alikes. A confirming one adds nothing: adding this variant would only
        # widen the circle of look-alikes, which is how the loop grew.
        if score < t.second_opinion:
            removed = ctx.events.remove_known_bad_near(event["hash"], MATCH_TOLERANCE)
            if removed:
                log.info("known-bad: removed %d hash(es) after a safe second opinion %.2f", removed, score)
            return removed > 0
        return False
    if score < t.known_bad:
        return False
    if decisive < MIN_DECISIVE_BITS:
        log.info("known-bad: not added, flat crop (%d of 64 decisive bits)", decisive)
        return False
    hubs = ctx.judge_cache.safe_near(event["hash"])
    if hubs:
        log.info("known-bad: not added, %d look-alike(s) were judged safe", hubs)
        return False
    return ctx.events.add_known_bad(event["hash"], "confirmed")


def _alerted_lately(ctx: Context, event: sqlite3.Row) -> bool:
    """One alert per look-alike group per half hour: a looping video is one thing to talk about."""
    if not event["hash"]:
        return False
    since = ctx.clock.now() - ALERT_WINDOW
    alerted = ctx.events.alerted_hashes(event["person_id"], since)
    return any(hamming(event["hash"], h) <= MATCH_TOLERANCE for h in alerted)


def forget_known_bad(ctx: Context, event: sqlite3.Row) -> int:
    """A partner marked this item a false alarm: its hash and look-alikes leave the known-bad list."""
    if not event["hash"]:
        return 0
    removed = ctx.events.remove_known_bad_near(event["hash"], MATCH_TOLERANCE)
    if removed:
        ctx.settings.bump_policy()
    return removed
