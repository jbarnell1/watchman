"""What a partner's own server reads from, and does for its partner on, this one (ADR-054, ADR-055).

Witness tokens only. Reading: the record and this server's check of it. Acting, for the one
partner the token belongs to: register the partner key their own server made, and pass on what
their browser signed there (approvals, reviews, checkpoints) and fetch what it opens there (sealed
review items and peeks). Everything that acts needs a signature from that key, checked here as on
the portal, so the token alone loosens nothing.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Request, Response

from app.services import approvals, intake, keys, peeks, witness
from app.services.context import Context
from app.services.errors import ApiError, Forbidden, NotFound

router = APIRouter(prefix="/api/v1/witness")
MAX_PAGE = 1000


def get_ctx(request: Request) -> Context:
    return request.app.state.ctx


def _partner(ctx: Context, request: Request) -> sqlite3.Row:
    token = witness.require_token(ctx, request.headers.get("authorization", ""))
    partner = ctx.people.get_partner(token["partner_id"])
    if partner is None:
        raise Forbidden("this partner was removed")
    return partner


def _login(partner: sqlite3.Row) -> str:
    return f"{partner['login'] or partner['email']} (from their own server)"


@router.get("/status")
async def status(request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    partner = _partner(ctx, request)
    key = keys.key_for(ctx, partner["id"])
    person = ctx.people.get_person(partner["person_id"])
    return witness.status(ctx) | {
        "partner": {"id": partner["id"], "name": partner["name"], "person_id": partner["person_id"],
                    "person_name": person["name"] if person else None},
        "key": {"fingerprint": key["fingerprint"], "active_from": key["active_from"]} if key else None,
    }


@router.get("/ledger")
async def ledger_page(request: Request, after: int = 0, limit: int = 500, ctx: Context = Depends(get_ctx)) -> dict:
    _partner(ctx, request)
    seq, h = ctx.ledger.head()
    return {"entries": ctx.ledger.entries(max(0, after), max(1, min(limit, MAX_PAGE))),
            "head": {"seq": seq, "hash": h}}


@router.post("/key")
async def register_key(request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    """The partner made their key on their own server; only the public halves come here."""
    partner = _partner(ctx, request)
    body = await request.json()
    replacing = keys.key_for(ctx, partner["id"]) is not None
    fp = keys.set_key(ctx, partner, str(body.get("sign_pub", "")), str(body.get("enc_pub", "")),
                      keys.HELD_ELSEWHERE)
    keys.announce(ctx, partner, fp, replacing, "on their own Watchman server")
    return {"fingerprint": fp, "active_from": keys.key_for(ctx, partner["id"])["active_from"]}


@router.post("/challenge")
async def challenge(request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    return {"nonce": keys.challenge(ctx, _partner(ctx, request)["id"])}


@router.get("/approvals")
async def waiting(request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    partner = _partner(ctx, request)
    out = []
    for row in approvals.waiting(ctx):
        if row["person_id"] not in (None, partner["person_id"]):
            continue
        out.append({"id": row["id"], "kind": row["kind"], "summary": row["summary"],
                    "params": json.loads(row["params"]), "signed": approvals.signed_params(row),
                    "created_by": row["created_by"], "created_at": row["created_at"],
                    "can_decide": bool(approvals.may_decide(ctx, row, {partner["id"]}))})
    return {"approvals": out}


@router.post("/approve/{aid}")
async def approve(aid: str, request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    partner = _partner(ctx, request)
    approval = await request.json()
    if approval.get("partner_id") != partner["id"]:
        raise Forbidden("this token belongs to another partner")
    approvals.approve(ctx, aid, approval, _login(partner))
    return {"ok": True}


@router.post("/decline/{aid}")
async def decline(aid: str, request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    partner = _partner(ctx, request)
    approvals.decline(ctx, aid, {partner["id"]}, _login(partner))
    return {"ok": True}


@router.post("/checkpoint")
async def checkpoint(request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    partner = _partner(ctx, request)
    approval = await request.json()
    params = json.loads(approval.get("payload", "{}")).get("params") or {}
    entry = ctx.ledger.entry(int(params.get("seq", 0)))
    if entry is None or entry["hash"] != params.get("hash"):
        raise ApiError("that is not an entry on this record", code="approval_invalid")
    keys.check(ctx, approval, "checkpoint", {"seq": entry["seq"], "hash": entry["hash"]}, {partner["id"]})
    with ctx.conn:
        ctx.ledger.append("checkpoint", {"seq": entry["seq"], "hash": entry["hash"], "approval": approval})
    return {"ok": True}


def _item(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "app": r["app"], "type": r["type"], "severity": r["severity"],
            "occurred_at": r["occurred_at"], "sealed": r["sealed"], "url": r["url"], "title": r["title"],
            "second_opinion": r["second_opinion"], "review_state": r["review_state"],
            "image_status": r["image_status"]}


@router.get("/review")
async def review_items(request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    partner = _partner(ctx, request)
    items = [_item(r) for r in ctx.events.needing_review(partner["person_id"])]
    return {"items": items, "peek_seconds": ctx.settings.load().peek_seconds,
            "next_peek_at": peeks.next_peek_at(ctx, partner["id"])}


def _event(ctx: Context, partner: sqlite3.Row, event_id: str) -> sqlite3.Row:
    event = ctx.events.get(event_id)
    if event is None or event["person_id"] != partner["person_id"]:
        raise NotFound("no such item")
    return event


@router.get("/image/{event_id}")
async def heavy(event_id: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    _event(ctx, _partner(ctx, request), event_id)
    data = ctx.blobs.get(event_id, "heavy")
    if data is None:
        raise NotFound("no such image")
    return Response(data, media_type="image/webp", headers={"Cache-Control": "no-store"})


@router.post("/peek/{event_id}")
async def peek(event_id: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    """A peek under the same rules as on the portal (one per cooldown, logged and shown to the
    protected person); the medium blur comes back sealed for the partner's key."""
    partner = _partner(ctx, request)
    _event(ctx, partner, event_id)
    started = peeks.start_peek(ctx, partner["id"], event_id)
    token = started["url"].split("t=")[1]
    with ctx.conn:
        if not ctx.codes.consume_peek_token(event_id, token):
            raise NotFound("peek expired")
    data = ctx.blobs.get(event_id, "medium")
    image = ctx.events.image(event_id)
    if data is None or image is None:
        raise NotFound("no such image")
    return Response(data, media_type="application/json" if image["sealed"] else "image/webp",
                    headers={"Cache-Control": "no-store", "X-Watchman-Sealed": "1" if image["sealed"] else "0",
                             "X-Watchman-Seconds": str(started["seconds"]),
                             "X-Watchman-Next-Peek": started["next_peek_at"]})


@router.post("/review/{event_id}")
async def review(event_id: str, request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    partner = _partner(ctx, request)
    event = _event(ctx, partner, event_id)
    body = await request.json()
    state = str(body.get("state", ""))
    if state not in ("talked", "false_alarm", "open"):
        raise ApiError("unknown review state")
    approval = body.get("approval")
    if not approval:
        raise ApiError("a review from a partner's own server must be signed", code="approval_needed")
    keys.check(ctx, approval, "review", {"event_id": event_id, "state": state}, {partner["id"]})
    with ctx.conn:
        ctx.events.review(event_id, state, _login(partner), approval)
        if state == "false_alarm":
            intake.forget_known_bad(ctx, event)
    return {"ok": True}
