"""Event intake and the image pipeline.

Severity at intake:
  image_covered, screen_blocked   review (until the second opinion says explicit)
  image_flagged                   info, promoted by the second opinion
  url_blocked, search_flagged, app_blocked, pause_*, daily_stats   info
  protection_changed (from a client)   info; the server derives its own from heartbeats
  tamper                          protection, alerts

An image is requested when the event has one and no stored image already carries its hash.
On upload the server scores it with the second opinion, writes heavy and medium blurs, stores
them encrypted and never writes the original (ADR-004).
"""

from __future__ import annotations

import io
import logging
import sqlite3

from PIL import Image

from app.services import alerts, blur
from app.services.context import Context
from app.services.errors import ApiError, NotFound
from app.services.imagehash import dhash
from app.store.events_store import SEVERITY_EXPLICIT, SEVERITY_INFO, SEVERITY_PROTECTION, SEVERITY_REVIEW

log = logging.getLogger(__name__)

MAX_BATCH = 200
IMAGE_TYPES = {"image_covered", "screen_blocked", "image_flagged"}
KNOWN_TYPES = IMAGE_TYPES | {
    "url_blocked", "search_flagged", "app_blocked", "protection_changed", "tamper",
    "pause_started", "pause_ended", "daily_stats",
}
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_SIDE = 512


def _initial_severity(event: dict) -> str:
    t = event["type"]
    if t in ("image_covered", "screen_blocked"):
        return SEVERITY_REVIEW
    if t == "tamper":
        return SEVERITY_PROTECTION
    return SEVERITY_INFO


def accept(ctx: Context, device: sqlite3.Row, events: list[dict]) -> dict:
    if len(events) > MAX_BATCH:
        raise ApiError(f"at most {MAX_BATCH} events per call")
    accepted, duplicates, upload, rejected = [], [], [], []
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
            severity = _initial_severity(ev)
            image_status = "none"
            if ev["type"] in IMAGE_TYPES and ev.get("has_image"):
                if ev.get("hash") and ctx.events.hash_has_image(device["person_id"], ev["hash"]):
                    image_status = "skipped"
                else:
                    image_status = "wanted"
                    upload.append(ev["id"])
            ctx.events.insert(ev, device["id"], device["person_id"], severity, image_status)
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
    heavy_webp = blur.to_webp(blur.heavy(image))
    medium_webp = blur.to_webp(blur.medium(image))
    del image, data  # the original lives only in this function

    settings = ctx.settings.load()
    confirmed = score >= settings.thresholds.second_opinion
    if confirmed:
        severity = SEVERITY_EXPLICIT
    elif event["type"] == "image_flagged":
        severity = SEVERITY_INFO
    else:
        severity = SEVERITY_REVIEW
    with ctx.conn:
        ctx.blobs.put(event_id, "heavy", heavy_webp)
        ctx.blobs.put(event_id, "medium", medium_webp)
        ctx.events.add_image(event_id, blur.OUTPUT_SIZE, blur.OUTPUT_SIZE)
        ctx.events.set_second_opinion(event_id, score, severity, "stored")
        if confirmed and event["hash"]:
            if ctx.events.add_known_bad(event["hash"], "confirmed"):
                ctx.settings.bump_policy()
    if confirmed and not event["alerted_at"]:
        alerts.explicit_alert(ctx, ctx.events.get(event_id), heavy_webp)
