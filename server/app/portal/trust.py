"""Portal pages for partner keys, approvals and the record (ADR-052 to 054).

/keys          a partner sets up, unlocks or replaces their key (the browser does the cryptography)
/approve/{id}  a change waiting for a partner's signature
/record        the record's head, this server's own check, witnesses and checkpoints
/watching      households this server watches (admin)
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.clock import iso
from app.portal.routes import _page, get_ctx
from app.portal.viewer import pick_person, require_viewer
from app.services import alerts, approvals, keys, ledger, witness
from app.services.context import Context
from app.services.errors import ApiError, Forbidden, NotFound

router = APIRouter()


def _my_partner_rows(v) -> dict:
    return {row["id"]: row for row in v.partner_rows.values()}


# Keys

@router.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    person = pick_person(v, request)
    partner = v.partner_rows.get(person["id"]) if person else None
    if partner is None:
        raise Forbidden("only a partner has a key")
    key = keys.key_for(ctx, partner["id"])
    return _page(request, ctx, v, "keys.html", person, partner=partner, key=key,
                 min_passphrase=keys.MIN_PASSPHRASE, now=iso(ctx.clock.now()))


@router.post("/keys")
async def keys_set(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    body = await request.json()
    partner = _my_partner_rows(v).get(str(body.get("partner_id", "")))
    if partner is None:
        raise Forbidden("only a partner can set their own key")
    replacing = keys.key_for(ctx, partner["id"]) is not None
    fp = keys.set_key(ctx, partner, str(body.get("sign_pub", "")), str(body.get("enc_pub", "")),
                      body.get("wrapped") or {})
    keys.announce(ctx, partner, fp, replacing)
    return JSONResponse({"fingerprint": fp})


@router.get("/keys/mine")
async def keys_mine(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    out = []
    for partner in v.partner_rows.values():
        key = keys.key_for(ctx, partner["id"])
        if key and json.loads(key["wrapped"]) != keys.HELD_ELSEWHERE:
            out.append({"partner_id": partner["id"], "person_id": partner["person_id"],
                        "wrapped": json.loads(key["wrapped"]), "fingerprint": key["fingerprint"],
                        "sign_pub": key["sign_pub"], "enc_pub": key["enc_pub"]})
    return JSONResponse({"keys": out}, headers={"Cache-Control": "no-store"})


@router.post("/keys/challenge")
async def keys_challenge(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    body = await request.json()
    partner = _my_partner_rows(v).get(str(body.get("partner_id", "")))
    if partner is None:
        raise Forbidden("only a partner can approve")
    return JSONResponse({"nonce": keys.challenge(ctx, partner["id"])})


# Approvals

@router.get("/approve/{aid}", response_class=HTMLResponse)
async def approve_page(aid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    row = approvals.get(ctx, aid)
    mine = set(_my_partner_rows(v))
    if not (v.is_admin or mine):
        raise Forbidden("not yours to see")
    can = approvals.may_decide(ctx, row, mine)
    person = ctx.people.get_person(row["person_id"]) if row["person_id"] else None
    return _page(request, ctx, v, "approve.html", person, req=row, params=json.loads(row["params"]),
                 signed=approvals.signed_params(row), can_decide=sorted(can))


@router.post("/approve/{aid}")
async def approve_post(aid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    form = await request.form()
    approval = keys.from_form(form)
    if approval is None or approval["partner_id"] not in _my_partner_rows(v):
        raise Forbidden("only a partner can approve, with their key")
    try:
        approvals.approve(ctx, aid, approval, v.login)
    except ApiError as exc:
        return RedirectResponse(f"/approve/{aid}?error={exc.message}", status_code=303)
    return RedirectResponse(f"/approve/{aid}?done=approved", status_code=303)


@router.post("/approve/{aid}/decline")
async def approve_decline(aid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    approvals.decline(ctx, aid, set(_my_partner_rows(v)), v.login)
    return RedirectResponse(f"/approve/{aid}?done=declined", status_code=303)


# The record

@router.get("/record", response_class=HTMLResponse)
async def record_page(request: Request, ctx: Context = Depends(get_ctx), new_token: str | None = None,
                      person_id: str | None = None) -> Response:
    v = require_viewer(ctx, request)
    person = ctx.people.get_person(person_id) if person_id and v.can_see(person_id) else pick_person(v, request)
    seq, h = ctx.ledger.head()
    checkpoints = [e for e in ctx.ledger.of_kind("checkpoint")][-10:]
    names = {p["id"]: p["name"] for pp in (ctx.people.partners_for(x["id"]) for x in ctx.people.list_people())
             for p in pp}
    return _page(request, ctx, v, "record.html", person, head_seq=seq, head_hash=h, problems=ledger.verify(ctx),
                 witnesses=witness.witnesses(ctx), checkpoints=checkpoints, names=names,
                 waiting=approvals.waiting(ctx), partner=v.partner_rows.get(person["id"]) if person else None,
                 new_token=new_token)


@router.get("/record/head")
async def record_head(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    require_viewer(ctx, request)
    seq, h = ctx.ledger.head()
    return JSONResponse({"seq": seq, "hash": h}, headers={"Cache-Control": "no-store"})


@router.get("/record/entry/{seq}")
async def record_entry(seq: int, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    require_viewer(ctx, request)
    e = ctx.ledger.entry(seq)
    if e is None:
        raise NotFound("no such entry")
    return JSONResponse({"seq": e["seq"], "hash": e["hash"]}, headers={"Cache-Control": "no-store"})


@router.post("/record/checkpoint")
async def record_checkpoint(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    """A partner's browser signs the head it saw. The record can then only change after that point
    without the signature giving it away (ADR-053)."""
    v = require_viewer(ctx, request)
    approval = await request.json()
    if approval.get("partner_id") not in _my_partner_rows(v):
        raise Forbidden("only a partner signs checkpoints")
    params = json.loads(approval.get("payload", "{}")).get("params") or {}
    entry = ctx.ledger.entry(int(params.get("seq", 0)))
    if entry is None or entry["hash"] != params.get("hash"):
        raise ApiError("that is not an entry on this record", code="approval_invalid")
    keys.check(ctx, approval, "checkpoint", {"seq": entry["seq"], "hash": entry["hash"]}, {approval["partner_id"]})
    with ctx.conn:
        ctx.ledger.append("checkpoint", {"seq": entry["seq"], "hash": entry["hash"], "approval": approval})
    return JSONResponse({"ok": True})


@router.post("/record/mismatch")
async def record_mismatch(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    """A partner's browser found an entry it signed earlier changed."""
    v = require_viewer(ctx, request)
    body = await request.json()
    alerts.change_alert(ctx, "the record no longer matches what a partner signed", [
        f"{v.login}'s browser signed entry {body.get('seq')} as {str(body.get('hash'))[:16]}; "
        f"the server now shows {str(body.get('now'))[:16]}.",
        "The record was rewritten after a partner checked it.",
    ])
    return JSONResponse({"ok": True})


@router.post("/record/witness")
async def record_witness(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    form = await request.form()
    partner = _my_partner_rows(v).get(str(form.get("partner_id", "")))
    if partner is None:
        raise Forbidden("only a partner can let their server watch")
    if keys.key_for(ctx, partner["id"]) is not None:
        approval = keys.from_form(form)
        if approval is None:
            raise ApiError("unlock your key first", code="approval_needed")
        keys.check(ctx, approval, "witness_token", {"partner_id": partner["id"]}, {partner["id"]})
    token = witness.create_token(ctx, partner["id"])
    # Shown once on this response, never put in an address where history and logs would keep it.
    return await record_page(request, ctx, new_token=token, person_id=partner["person_id"])


@router.post("/record/witness/{wid}/revoke")
async def record_witness_revoke(wid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    row = ctx.conn.execute("SELECT * FROM witness_tokens WHERE id = ?", (wid,)).fetchone()
    if row is None or row["partner_id"] not in _my_partner_rows(v):
        raise Forbidden("only the partner who made it can revoke it")
    with ctx.conn:
        ctx.conn.execute("UPDATE witness_tokens SET revoked_at = ? WHERE id = ?", (iso(ctx.clock.now()), wid))
        ctx.ledger.append("witness_removed", {"partner_id": row["partner_id"]})
    return RedirectResponse("/record", status_code=303)


# Watching other households

@router.get("/watching", response_class=HTMLResponse)
async def watching_page(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin sets up watching")
    return _page(request, ctx, v, "watching.html", pick_person(v, request), rows=witness.watched(ctx),
                 error=request.query_params.get("error"))


@router.post("/watching")
async def watching_add(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin sets up watching")
    form = await request.form()
    name, url = str(form.get("name", "")).strip(), str(form.get("url", "")).strip()
    token, email = str(form.get("token", "")).strip(), str(form.get("email", "")).strip()
    if not (name and url.startswith(("http://", "https://")) and token.startswith("wmw_") and "@" in email):
        return RedirectResponse("/watching?error=a name, the server address, its witness token and your email "
                                "are needed", status_code=303)
    wid = witness.add_watch(ctx, name, url, token, email)
    row = ctx.conn.execute("SELECT * FROM watching WHERE id = ?", (wid,)).fetchone()
    await asyncio.to_thread(witness.poll, ctx, ctx.witness, row)
    return RedirectResponse("/watching", status_code=303)


@router.post("/watching/{wid}/remove")
async def watching_remove(wid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    if not v.is_admin:
        raise Forbidden("only the admin sets up watching")
    with ctx.conn:
        ctx.conn.execute("DELETE FROM watching WHERE id = ?", (wid,))
    return RedirectResponse("/watching", status_code=303)


# The partner's own server acts for them on the household they watch (ADR-055). The key is made,
# kept and unlocked here, on pages this server sends; the watched server sees only public keys and
# signatures.

def _watch_row(ctx: Context, v, wid: str):
    if not v.is_admin:
        raise Forbidden("only the admin of this server uses its watch pages")
    row = ctx.conn.execute("SELECT * FROM watching WHERE id = ?", (wid,)).fetchone()
    if row is None:
        raise NotFound("not watching that household")
    return row


def _call(ctx: Context, row, method: str, path: str, body: dict | None = None):
    try:
        return ctx.witness.json(method, row["url"], row["token"], path, None, body)
    except witness.WitnessError as exc:
        raise ApiError(f"{row['name']}: {exc}") from exc


async def _acall(*args):
    # The other household's server can take seconds; keep this server's event loop free meanwhile.
    return await asyncio.to_thread(_call, *args)


async def _arelay(*args):
    return await asyncio.to_thread(_relay, *args)


@router.get("/watching/{wid}", response_class=HTMLResponse)
async def watching_detail(wid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    row = _watch_row(ctx, v, wid)
    error, status, waiting, review = request.query_params.get("error"), None, [], None
    try:
        status = await _acall(ctx, row, "GET", "/api/v1/witness/status")
        waiting = (await _acall(ctx, row, "GET", "/api/v1/witness/approvals"))["approvals"]
        review = await _acall(ctx, row, "GET", "/api/v1/witness/review")
    except ApiError as exc:
        error = error or exc.message
    mine = json.loads(row["my_key"]) if row["my_key"] else None
    return _page(request, ctx, v, "watching_detail.html", pick_person(v, request), row=row, status=status,
                 waiting=waiting, review=review, mine=mine, error=error, done=request.query_params.get("done"),
                 min_passphrase=keys.MIN_PASSPHRASE)


@router.post("/watching/{wid}/key")
async def watching_key(wid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    row = _watch_row(ctx, v, wid)
    body = await request.json()
    wrapped = body.get("wrapped") or {}
    if int(wrapped.get("iterations", 0)) < 600_000:
        raise ApiError("the passphrase key needs at least 600000 rounds")
    got = await _acall(ctx, row, "POST", "/api/v1/witness/key", {"sign_pub": body.get("sign_pub"),
                                                          "enc_pub": body.get("enc_pub")})
    head = (await _acall(ctx, row, "GET", "/api/v1/witness/status"))["head"]["seq"]
    mine = {"partner_id": str(body.get("partner_id")), "sign_pub": body.get("sign_pub"), "enc_pub": body.get("enc_pub"),
            "wrapped": wrapped, "fingerprint": got["fingerprint"], "registered_seq": head}
    with ctx.conn:
        ctx.conn.execute("UPDATE watching SET my_key = ? WHERE id = ?", (json.dumps(mine), wid))
    return JSONResponse({"fingerprint": got["fingerprint"]})


@router.get("/watching/{wid}/key")
async def watching_key_get(wid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    row = _watch_row(ctx, v, wid)
    mine = json.loads(row["my_key"]) if row["my_key"] else None
    keys_out = [{k: mine[k] for k in ("partner_id", "wrapped", "fingerprint")}] if mine else []
    return JSONResponse({"keys": keys_out}, headers={"Cache-Control": "no-store"})


@router.post("/watching/{wid}/challenge")
async def watching_challenge(wid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    return JSONResponse(await _acall(ctx, _watch_row(ctx, v, wid), "POST", "/api/v1/witness/challenge", {}))


@router.get("/watching/{wid}/head")
async def watching_head(wid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    return JSONResponse((await _acall(ctx, _watch_row(ctx, v, wid), "GET", "/api/v1/witness/status"))["head"])


@router.post("/watching/{wid}/checkpoint")
async def watching_checkpoint(wid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    return JSONResponse(await _acall(ctx, _watch_row(ctx, v, wid), "POST", "/api/v1/witness/checkpoint",
                              await request.json()))


def _back(wid: str, done: str | None = None, error: str | None = None) -> RedirectResponse:
    q = f"?done={done}" if done else f"?error={error}"
    return RedirectResponse(f"/watching/{wid}{q}", status_code=303)


@router.post("/watching/{wid}/approve/{aid}")
async def watching_approve(wid: str, aid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    row = _watch_row(ctx, v, wid)
    approval = keys.from_form(await request.form())
    if approval is None:
        return _back(wid, error="unlock your key first")
    try:
        await _acall(ctx, row, "POST", f"/api/v1/witness/approve/{aid}", approval)
    except ApiError as exc:
        return _back(wid, error=exc.message)
    return _back(wid, "approved")


@router.post("/watching/{wid}/decline/{aid}")
async def watching_decline(wid: str, aid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    try:
        await _acall(ctx, _watch_row(ctx, v, wid), "POST", f"/api/v1/witness/decline/{aid}", {})
    except ApiError as exc:
        return _back(wid, error=exc.message)
    return _back(wid, "declined")


@router.post("/watching/{wid}/review/{eid}")
async def watching_review(wid: str, eid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    row = _watch_row(ctx, v, wid)
    form = await request.form()
    approval = keys.from_form(form)
    if approval is None:
        return _back(wid, error="unlock your key first")
    try:
        await _acall(ctx, row, "POST", f"/api/v1/witness/review/{eid}", {"state": str(form.get("state", "")),
                                                                     "approval": approval})
    except ApiError as exc:
        return _back(wid, error=exc.message)
    return _back(wid, "reviewed")


def _relay(ctx: Context, row, method: str, path: str) -> Response:
    try:
        r = ctx.witness.call(method, row["url"], row["token"], path)
    except witness.WitnessError as exc:
        raise ApiError(f"{row['name']}: {exc}") from exc
    keep = {k: v for k, v in r.headers.items() if k.lower().startswith("x-watchman-")}
    return Response(r.content, status_code=r.status, media_type=r.headers.get("content-type", "application/json"),
                    headers=keep | {"Cache-Control": "no-store"})


@router.get("/watching/{wid}/image/{eid}")
async def watching_image(wid: str, eid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    return await _arelay(ctx, _watch_row(ctx, v, wid), "GET", f"/api/v1/witness/image/{eid}")


@router.post("/watching/{wid}/peek/{eid}")
async def watching_peek(wid: str, eid: str, request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    v = require_viewer(ctx, request)
    return await _arelay(ctx, _watch_row(ctx, v, wid), "POST", f"/api/v1/witness/peek/{eid}")
