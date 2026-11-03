"""Portal pages (docs/api-contract.md, "Portal"). Server-rendered, Tailscale identity, no SPA."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.clients.second_opinion import FakeSecondOpinion
from app.clock import iso, parse_iso
from app.portal.viewer import Viewer, pick_person, require_viewer
from app.services import alerts, digest, health, heartbeat, intake, locks, metrics, pairlink, peeks, unlock, whatif
from app.services.context import Context
from app.services.errors import ApiError, Forbidden, NotFound
from app.settings import PERSON_KEYS, PRESET_LABELS, PRESETS, Thresholds, coerce, effective
from app.store.events_store import SEVERITY_PROTECTION

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def get_ctx(request: Request) -> Context:
    return request.app.state.ctx


def _tz(ctx: Context) -> ZoneInfo:
    try:
        return ZoneInfo(ctx.settings.load().timezone)
    except Exception:
        return ZoneInfo("UTC")


def _page(request: Request, ctx: Context, v: Viewer, template: str, person: sqlite3.Row | None,
          **extra) -> HTMLResponse:
    settings = ctx.settings.load()
    tz = _tz(ctx)
    now = ctx.clock.now()

    def when(ts: str | None, fmt: str = "%a %d %b %H:%M") -> str:
        if not ts:
            return ""
        return parse_iso(ts).astimezone(tz).strftime(fmt)

    def ago(ts: str | None) -> str:
        if not ts:
            return "never"
        delta = now - parse_iso(ts)
        s = int(delta.total_seconds())
        if s < 60:
            return "just now"
        if s < 3600:
            return f"{s // 60} min ago"
        if s < 86400:
            return f"{s // 3600} h ago"
        return f"{s // 86400} d ago"

    review_count = len(ctx.events.needing_review(person["id"])) if person else 0
    context = {
        "request": request, "viewer": v, "person": person, "people": v.people, "settings": settings,
        "when": when, "ago": ago, "json": json, "review_count": review_count,
        "role": v.role_for(person["id"]) if person else ("admin" if v.is_admin else "none"),
        "nav": template.split(".")[0], **extra,
    }
    return templates.TemplateResponse(request, template, context)


def _need_setup(ctx: Context, v: Viewer) -> bool:
    return v.is_admin and not ctx.settings.load().setup_complete


# Timeline

@router.get("/", response_class=HTMLResponse)
async def timeline(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if _need_setup(ctx, v):
        return RedirectResponse("/setup", status_code=303)
    person = pick_person(v, request)
    if person is None:
        return _page(request, ctx, v, "timeline.html", None, devices=[], events=[], coverage=[], pause=None)
    now = ctx.clock.now()
    devices = ctx.devices.list_for(person["id"])
    for_days = 7
    start = now - timedelta(days=for_days)
    coverage = _coverage(ctx, person["id"], devices, start, now)
    events = ctx.events.timeline(person["id"], limit=60)
    return _page(
        request, ctx, v, "timeline.html", person, devices=devices, events=events, coverage=coverage,
        pause=ctx.codes.active_pause(person["id"]),
        hours=round(ctx.devices.protected_minutes(person["id"], start, now) / 60, 1),
        counts={
            "covered": (ctx.events.count_since(person["id"], start, "explicit")
                        + ctx.events.count_since(person["id"], start, "review")),
            "explicit": ctx.events.count_since(person["id"], start, "explicit"),
            "gaps": ctx.events.count_since(person["id"], start, "protection"),
        },
        peeks=ctx.codes.peeks_for_person(person["id"], limit=10),
        states=_device_states(devices, now),
        gap_minutes=ctx.settings.load().heartbeat_gap_minutes,
    )


def _device_states(devices: list[sqlite3.Row], now) -> dict[str, dict]:
    out = {}
    for d in devices:
        state = (json.loads(d["last_state"]) if d["last_state"] else {}).get("state") or {}
        silent = (not d["last_heartbeat_at"]) or (now - parse_iso(d["last_heartbeat_at"])) > timedelta(minutes=30)
        off = [k for k, val in state.items() if val is not None and val not in (True, "granted", "not_applicable")]
        out[d["id"]] = {"state": state, "silent": silent, "off": off,
                        "ok": not silent and not off and state.get("protection") is True}
    return out


def _coverage(ctx: Context, person_id: str, devices: list[sqlite3.Row], start, end) -> list[dict]:
    """Per device, 7 days of 1 hour cells: 'on', 'off', 'none' (no heartbeat)."""
    hours = int((end - start).total_seconds() // 3600)
    rows = ctx.devices.heartbeats_between(person_id, start, end)
    per: dict[str, list[str]] = {d["id"]: ["none"] * hours for d in devices}
    for h in rows:
        idx = int((parse_iso(h["at"]) - start).total_seconds() // 3600)
        if h["device_id"] in per and 0 <= idx < hours:
            cell = per[h["device_id"]][idx]
            if h["protection"]:
                per[h["device_id"]][idx] = "on" if cell != "off" else "mixed"
            else:
                per[h["device_id"]][idx] = "off" if cell != "on" else "mixed"
    days = []
    for i in range(7):
        days.append((start + timedelta(days=i)).astimezone(_tz(ctx)).strftime("%a"))
    return [{"device": d, "cells": per[d["id"]], "days": days} for d in devices]


# Review

@router.get("/review", response_class=HTMLResponse)
async def review(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    person = pick_person(v, request)
    items = ctx.events.needing_review(person["id"]) if person else []
    selected = None
    wanted = request.query_params.get("item")
    if wanted:
        selected = next((i for i in items if i["id"] == wanted), None) or ctx.events.get(wanted)
        if selected is not None and (person is None or selected["person_id"] != person["id"]):
            raise Forbidden("not your item")
    elif items:
        selected = items[0]
    partner = v.partner_rows.get(person["id"]) if person else None
    next_peek = peeks.next_peek_at(ctx, partner["id"]) if partner else None
    recent = [e for e in (ctx.events.timeline(person["id"], limit=200) if person else [])
              if e["severity"] in ("review", "explicit") and e["review_state"] != "open"][:20]
    return _page(request, ctx, v, "review.html", person, items=items, selected=selected,
                 partner=partner, next_peek=next_peek, recent=recent)


@router.post("/review/{event_id}")
async def review_mark(event_id: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    event = ctx.events.get(event_id)
    if event is None or not v.is_partner_of(event["person_id"]):
        raise Forbidden("only a partner can review")
    form = await request.form()
    state = str(form.get("state", ""))
    if state not in ("talked", "false_alarm", "open"):
        raise ApiError("unknown review state")
    with ctx.conn:
        ctx.events.review(event_id, state, v.login)
        if state == "false_alarm":
            intake.forget_known_bad(ctx, event)
    return RedirectResponse(f"/review?person={event['person_id']}", status_code=303)


@router.post("/peek/{event_id}")
async def peek(event_id: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    event = ctx.events.get(event_id)
    if event is None:
        raise NotFound("no such item")
    partner = v.partner_rows.get(event["person_id"])
    if partner is None:
        raise Forbidden("only a linked partner can peek")
    return JSONResponse(peeks.start_peek(ctx, partner["id"], event_id))


@router.get("/images/{event_id}/heavy")
async def image_heavy(event_id: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    event = ctx.events.get(event_id)
    if event is None or not v.can_see(event["person_id"]):
        raise NotFound("no such image")
    data = ctx.blobs.get(event_id, "heavy")
    if data is None:
        raise NotFound("no such image")
    return Response(data, media_type="image/webp", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/images/{event_id}/medium")
async def image_medium(event_id: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    event = ctx.events.get(event_id)
    token = request.query_params.get("t", "")
    if event is None or not v.is_partner_of(event["person_id"]):
        raise NotFound("no such image")
    with ctx.conn:
        ok = ctx.codes.consume_peek_token(event_id, token)
    if not ok:
        raise NotFound("peek expired")
    data = ctx.blobs.get(event_id, "medium")
    if data is None:
        raise NotFound("no such image")
    return Response(data, media_type="image/webp", headers={"Cache-Control": "no-store"})


# What if (ADR-041): replay stored scores against other thresholds. Numbers only.

WHATIF_DAYS = (1, 7, 30)
WHATIF_KEYS = ("ask", "sure", "cover", "flag", "detector_cover", "second_opinion")


def _proposed_thresholds(current: Thresholds, params) -> Thresholds:
    values = current.model_dump()
    for key in WHATIF_KEYS:
        raw = params.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if 0.0 <= value <= 1.0:
            values[key] = value
    return Thresholds.model_validate(values)


@router.get("/whatif", response_class=HTMLResponse)
async def whatif_page(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    person = pick_person(v, request)
    if person is None or not (v.is_admin or v.is_partner_of(person["id"])):
        raise Forbidden("only the admin or a partner can try thresholds")
    try:
        days = int(request.query_params.get("days", "7"))
    except ValueError:
        days = 7
    if days not in WHATIF_DAYS:
        days = 7
    mine = effective(ctx.settings.load(), person["id"])
    proposed = _proposed_thresholds(mine.thresholds, request.query_params)
    now = ctx.clock.now()
    result = whatif.replay(ctx.events.between(person["id"], now - timedelta(days=days), now),
                           mine.thresholds, proposed, mine.cover_on_device, mine.ask_server,
                           ctx.settings.load().covered_reports)
    return _page(request, ctx, v, "whatif.html", person, days=days, day_choices=WHATIF_DAYS,
                 current=mine.thresholds, proposed=proposed, keys=WHATIF_KEYS, result=result,
                 measures=whatif.MEASURES, changed=proposed != mine.thresholds,
                 cover_on_device=mine.cover_on_device, ask_server=mine.ask_server)


# Pairing hand-off. No sign-in: the code in the address is the secret, and it only fills in a form.

@router.get("/pair", response_class=HTMLResponse)
async def pair_page(request: Request) -> Response:
    code = pairlink.normalize_code(request.query_params.get("code", ""))
    if len(code) != 11:
        code = ""
    server = pairlink.portal_url(request)
    return templates.TemplateResponse(request, "pair.html", {
        "code": code, "server": server, "app_link": pairlink.app_link(server, code) if code else "",
    }, headers={"Cache-Control": "no-store"})


# Devices

@router.get("/devices", response_class=HTMLResponse)
async def devices(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    person = pick_person(v, request)
    rows = ctx.devices.list_for(person["id"], include_revoked=True) if person else []
    codes = ctx.devices.open_pairing_codes(person["id"]) if person else []
    now = ctx.clock.now()
    day = heartbeat.household_day(ctx, now)
    new_code = request.query_params.get("code")
    pair_url = pairlink.portal_url(request)
    pair_qr = pairlink.qr_svg(pairlink.pair_link(pair_url, new_code)) if new_code else None
    work = {}
    for d in rows:
        m = None if d["revoked_at"] else ctx.devices.metrics_for(d["id"], day)
        if m:
            work[d["id"]] = metrics.summary(m)
    return _page(request, ctx, v, "devices.html", person, devices=rows, pairing_codes=codes, work=work,
                 states=_device_states([d for d in rows if not d["revoked_at"]], now),
                 can_manage=person is not None and (v.is_admin or v.is_protected(person["id"])),
                 new_code=new_code, pair_qr=pair_qr, pair_url=pair_url)


@router.post("/devices/pairing-code")
async def new_pairing_code(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    form = await request.form()
    person_id = str(form.get("person", ""))
    if not (v.can_see(person_id) and (v.is_admin or v.is_protected(person_id))):
        raise Forbidden("not allowed")
    with ctx.conn:
        code = ctx.devices.create_pairing_code(person_id)
    return RedirectResponse(f"/devices?person={person_id}&code={code}", status_code=303)


@router.post("/devices/{device_id}/revoke")
async def revoke_device(device_id: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    device = ctx.devices.get(device_id)
    if device is None or not v.is_admin:
        raise Forbidden("only the admin can revoke a device")
    ev = {"id": str(uuid.uuid4()), "type": "protection_changed", "occurred_at": iso(ctx.clock.now()),
          "action": "none", "detail": {"field": "device", "from": "paired", "to": "revoked", "by": v.login}}
    with ctx.conn:
        ctx.devices.revoke(device_id)
        ctx.events.insert(ev, device_id, device["person_id"], SEVERITY_PROTECTION, "none")
    alerts.protection_alert(ctx, device, ev)
    return RedirectResponse(f"/devices?person={device['person_id']}", status_code=303)


# Codes

@router.get("/codes", response_class=HTMLResponse)
async def codes(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    person = pick_person(v, request)
    partner = v.partner_rows.get(person["id"]) if person else None
    return _page(request, ctx, v, "codes.html", person, partner=partner,
                 open_codes=ctx.codes.open_codes(person["id"]) if person else [],
                 requests=ctx.codes.recent_requests(person["id"]) if person else [],
                 issued=request.query_params.get("code"), issued_minutes=request.query_params.get("minutes"),
                 issued_purpose=request.query_params.get("purpose"))


@router.post("/codes")
async def issue_code(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    form = await request.form()
    person_id = str(form.get("person", ""))
    partner = v.partner_rows.get(person_id)
    if partner is None:
        raise Forbidden("only a partner can issue codes")
    purpose = str(form.get("purpose", "pause"))
    minutes = int(form.get("minutes", 0) or 0)
    with ctx.conn:
        if purpose == "settings":
            _, code = ctx.codes.create_code(person_id, partner["id"], 0, purpose="settings")
        else:
            code = unlock.issue_code(ctx, person_id, partner["id"], minutes)
    return RedirectResponse(
        f"/codes?person={person_id}&code={code}&minutes={minutes}&purpose={purpose}", status_code=303
    )


# Settings

AREAS: dict[str, list[str]] = {
    "general": ["household_name", "timezone", "digest_day", "digest_time", "image_retention_days",
                "event_retention_days", "ntfy_url", "adguard_url"],
    "protection": ["locks_on", "unlocked_groups", "cover_on_device", "ask_server", "strict_mode", "coverage",
                   "follow_scroll", "block_whole_screen",
                   "thresholds", "covered_reports",
                   "risky_apps",
                   "blocked_apps", "exempt_apps", "protected_browser",
                   "watch_default", "realtime_apps", "checked_apps", "checked_interval_s",
                   "sample_interval_s", "whole_screen_interval_s", "max_pause_minutes",
                   "heartbeat_gap_minutes", "peek_seconds", "peek_cooldown_hours", "second_opinion_device"],
    "email": ["smtp"],
    "people": [],
}


def _parse_setting(key: str, form) -> object:
    if key == "thresholds":
        keys = ("cover", "flag", "detector_cover", "second_opinion", "ask", "sure", "known_bad", "covered_report",
                "server_exposed")
        return {k: float(form.get(f"thresholds.{k}", 0)) for k in keys}
    if key == "smtp":
        return {
            "host": str(form.get("smtp.host", "")).strip(), "port": int(form.get("smtp.port", 587) or 587),
            "username": str(form.get("smtp.username", "")).strip(),
            "password": str(form.get("smtp.password", "")),
            "from_address": str(form.get("smtp.from_address", "")).strip(),
            "starttls": form.get("smtp.starttls") == "on",
        }
    if key == "covered_reports":
        value = str(form.get(key, "exposed"))
        if value not in ("exposed", "all"):
            raise ApiError("unknown choice for covered pictures")
        return value
    if key == "watch_default":
        value = str(form.get(key, "realtime"))
        if value not in ("realtime", "checked"):
            raise ApiError("unknown watch level")
        return value
    if key in ("risky_apps", "blocked_apps", "exempt_apps", "realtime_apps", "checked_apps"):
        return [line.strip() for line in str(form.get(key, "")).splitlines() if line.strip()]
    if key in ("strict_mode", "cover_on_device", "ask_server", "locks_on", "follow_scroll", "block_whole_screen"):
        return form.get(key) == "on"
    if key == "unlocked_groups":
        # Checked means locked; the setting stores the groups left unlocked.
        return [g for g in locks.LOCK_GROUPS if form.get(f"lock.{g}") != "on"]
    if key == "coverage":
        value = str(form.get(key, "explicit"))
        if value not in ("explicit", "revealing", "strict"):
            raise ValueError("coverage must be explicit, revealing or strict")
        return value
    value = form.get(key)
    return coerce(key, value)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin can change settings")
    area = request.query_params.get("area", "general")
    if area not in AREAS:
        raise NotFound("no such settings area")
    person = pick_person(v, request)
    locked = ctx.devices.protection_on() and ctx.settings.load().locks_on
    people = ctx.people.list_people()
    for_person = ctx.people.get_person(request.query_params.get("for", "")) if request.query_params.get("for") else None
    household = ctx.settings.load()
    overrides = household.person_overrides.get(for_person["id"], {}) if for_person else {}
    partners = {p["id"]: ctx.people.partners_for(p["id"]) for p in people}
    logins = {p["id"]: [r["login"] for r in ctx.conn.execute(
        "SELECT login FROM logins WHERE role = 'protected' AND person_id = ?", (p["id"],))] for p in people}
    return _page(request, ctx, v, "settings.html", person, area=area, areas=list(AREAS), keys=AREAS[area],
                 locked=locked, locked_keys=locks.locked_keys(household), lock_groups=locks.LOCK_GROUPS,
                 locked_groups=locks.locked_groups(household),
                 people_rows=people, partners=partners, logins=logins,
                 admins=ctx.people.admins(), saved=request.query_params.get("saved"),
                 settings=effective(household, for_person["id"]) if for_person else household,
                 for_person=for_person, override_keys=sorted(overrides), overridden=set(household.person_overrides),
                 presets=PRESETS, preset_labels=PRESET_LABELS,
                 error=request.query_params.get("error"), timezones=sorted(available_timezones()),
                 last_digest=ctx.codes.last_digest(person["id"]) if person else None)


def _check_settings_code(ctx: Context, form, changes: dict, group: str | None = None) -> None:
    """ADR-028: tightening is free; loosening a locked setting (or acting on a locked group, such as
    removing a partner) needs a partner's settings code once a device is paired."""
    current = ctx.settings.load()
    # Locks apply once a device is paired and the household has turned them on.
    if not ctx.devices.protection_on() or not current.locks_on:
        return
    loosened = locks.needs_code(current, changes)
    if group is not None and group in locks.locked_groups(current):
        loosened.append(group)
    if not loosened:
        return
    code = str(form.get("partner_code", "")).strip()
    if not code:
        what = ", ".join(locks.LABELS.get(k, "removing a partner" if k == "partners" else k) for k in loosened)
        raise ApiError(f"loosening {what} needs a partner's settings code; tightening never does",
                       code="locked_setting")
    # A settings code from any partner of any person on this server unlocks the change.
    for person in ctx.people.list_people():
        if ctx.codes.consume_code(person["id"], code, purpose="settings"):
            return
    raise ApiError("the partner code is wrong or used", code="unlock_code_invalid")


@router.post("/settings/{area}")
async def settings_save(area: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin can change settings")
    if area not in AREAS:
        raise NotFound("no such settings area")
    form = await request.form()
    for_person = str(form.get("for_person", "")).strip()
    try:
        values = {}
        current = ctx.settings.load()
        for key in AREAS[area]:
            if for_person and key not in PERSON_KEYS:
                continue
            new = _parse_setting(key, form)
            typed = coerce(key, new)
            old = getattr(current, key)
            old_cmp = old.model_dump() if hasattr(old, "model_dump") else old
            new_cmp = typed.model_dump() if hasattr(typed, "model_dump") else typed
            if old_cmp != new_cmp:
                values[key] = new_cmp
        if for_person:
            # A person's own values are whatever differs from the household; the rest follows it.
            if ctx.people.get_person(for_person) is None:
                raise NotFound("no such person")
            overrides = dict(current.person_overrides)
            if values:
                overrides[for_person] = values
            else:
                overrides.pop(for_person, None)
            values = {"person_overrides": overrides} if overrides != current.person_overrides else {}
        with ctx.conn:
            _check_settings_code(ctx, form, values)
            for key, value in values.items():
                ctx.settings.set(key, value, login=v.login)
    except ApiError as exc:
        return RedirectResponse(f"/settings?area={area}&error={exc.message}&for={for_person}", status_code=303)
    except (ValueError, TypeError) as exc:
        return RedirectResponse(f"/settings?area={area}&error={exc}&for={for_person}", status_code=303)
    suffix = f"&for={for_person}" if for_person else ""
    return RedirectResponse(f"/settings?area={area}&saved=1{suffix}", status_code=303)


@router.post("/settings/protection/reset")
async def settings_reset_person(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    """Drop one person's own protection values so they follow the household again."""
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin can change settings")
    form = await request.form()
    for_person = str(form.get("for_person", "")).strip()
    current = ctx.settings.load()
    overrides = {k: v2 for k, v2 in current.person_overrides.items() if k != for_person}
    try:
        with ctx.conn:
            if overrides != current.person_overrides:
                _check_settings_code(ctx, form, {"person_overrides": overrides})
                ctx.settings.set("person_overrides", overrides, login=v.login)
    except ApiError as exc:
        return RedirectResponse(f"/settings?area=protection&for={for_person}&error={exc.message}", status_code=303)
    return RedirectResponse(f"/settings?area=protection&for={for_person}&saved=1", status_code=303)


@router.post("/settings/people/add")
async def add_person(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin can add people")
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return RedirectResponse("/settings?area=people&error=a name is needed", status_code=303)
    with ctx.conn:
        ctx.people.add_person(name, str(form.get("email", "")).strip() or None,
                              str(form.get("login", "")).strip() or None)
    return RedirectResponse("/settings?area=people&saved=1", status_code=303)


@router.post("/settings/partners/add")
async def add_partner(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin can add partners")
    form = await request.form()
    try:
        with ctx.conn:
            ctx.people.add_partner(
                str(form.get("person", "")), str(form.get("name", "")).strip(),
                str(form.get("email", "")).strip(), str(form.get("link_type", "email")),
                str(form.get("login", "")).strip() or None,
            )
    except (ValueError, sqlite3.IntegrityError) as exc:
        return RedirectResponse(f"/settings?area=people&error={exc}", status_code=303)
    return RedirectResponse("/settings?area=people&saved=1", status_code=303)


@router.post("/settings/partners/{partner_id}/remove")
async def remove_partner(partner_id: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin can remove partners")
    form = await request.form()
    partner = ctx.people.get_partner(partner_id)
    if partner is None:
        raise NotFound("no such partner")
    try:
        with ctx.conn:
            # Removing a partner weakens accountability, so it is locked like a protection setting.
            _check_settings_code(ctx, form, {}, group="partners")
            ctx.people.remove_partner(partner_id)
            ctx.conn.execute(
                "INSERT INTO settings_changes(at, login, key, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
                (iso(ctx.clock.now()), v.login, "partner_removed", json.dumps(partner["name"]), "null"),
            )
    except ApiError as exc:
        return RedirectResponse(f"/settings?area=people&error={exc.message}", status_code=303)
    return RedirectResponse("/settings?area=people&saved=1", status_code=303)


@router.post("/settings/digest/send")
async def send_digest_now(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin can send a digest")
    form = await request.form()
    person_id = str(form.get("person", ""))
    ok = digest.send_digest(ctx, person_id)
    return RedirectResponse(f"/settings?area=general&person={person_id}&saved={'digest' if ok else 'digest_failed'}",
                            status_code=303)


# Setup

@router.get("/setup", response_class=HTMLResponse)
async def setup(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin can run setup")
    return _page(request, ctx, v, "setup.html", None, lock_groups=locks.LOCK_GROUPS,
                 timezones=sorted(available_timezones()),
                 error=request.query_params.get("error"))


@router.post("/setup")
async def setup_save(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin can run setup")
    form = await request.form()
    name = str(form.get("person_name", "")).strip()
    partner_name = str(form.get("partner_name", "")).strip()
    partner_email = str(form.get("partner_email", "")).strip()
    if not name or not partner_name or not partner_email:
        return RedirectResponse("/setup?error=the protected person and one partner are needed", status_code=303)
    try:
        with ctx.conn:
            household = str(form.get("household_name", "")).strip() or "Watchman household"
            ctx.settings.set("household_name", household, v.login)
            ctx.settings.set("timezone", str(form.get("timezone", "UTC")), v.login)
            ctx.settings.set("digest_day", str(form.get("digest_day", "sunday")), v.login)
            ctx.settings.set("digest_time", str(form.get("digest_time", "19:00")), v.login)
            smtp = _parse_setting("smtp", form)
            if smtp["host"]:
                ctx.settings.set("smtp", smtp, v.login)
            person_login = str(form.get("person_login", "")).strip() or (v.login if form.get("person_is_me") else None)
            person_id = ctx.people.add_person(name, str(form.get("person_email", "")).strip() or None, person_login)
            link_type = str(form.get("partner_link_type", "email"))
            ctx.people.add_partner(person_id, partner_name, partner_email, link_type,
                                   str(form.get("partner_login", "")).strip() or None)
            ctx.settings.set("unlocked_groups", _parse_setting("unlocked_groups", form), v.login)
            ctx.settings.set("setup_complete", True, v.login)
    except (ValueError, sqlite3.IntegrityError) as exc:
        return RedirectResponse(f"/setup?error={exc}", status_code=303)
    return RedirectResponse("/devices", status_code=303)


@router.get("/healthz")
async def healthz(ctx: Context = Depends(get_ctx)) -> dict:
    """Unauthenticated, for Gatus and Homepage. No personal data: version and model state only."""
    ctx.conn.execute("SELECT 1").fetchone()
    return {
        "ok": True,
        "version": health.server_version(),
        "models": ctx.models_verified,
        "second_opinion": "missing" if isinstance(ctx.second_opinion, FakeSecondOpinion) else "loaded",
    }
