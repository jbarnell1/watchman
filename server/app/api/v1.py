"""Device API, /api/v1 (docs/api-contract.md)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from app.services import heartbeat, intake, pairing, policy, unlock
from app.services.context import Context
from app.services.errors import NotFound, Unauthorized

router = APIRouter(prefix="/api/v1")


def get_ctx(request: Request) -> Context:
    return request.app.state.ctx


def current_device(request: Request, ctx: Context = Depends(get_ctx)) -> sqlite3.Row:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise Unauthorized("device token required")
    device = ctx.devices.by_token(auth[7:].strip())
    if device is None:
        raise Unauthorized("device token is unknown or revoked")
    return device


@router.post("/pair")
async def pair(body: dict, ctx: Context = Depends(get_ctx)) -> dict:
    return pairing.pair(ctx, str(body.get("code", "")), body.get("device") or {})


@router.post("/heartbeat")
async def post_heartbeat(body: dict, device: sqlite3.Row = Depends(current_device),
                         ctx: Context = Depends(get_ctx)) -> dict:
    return heartbeat.record(ctx, device, body)


@router.get("/policy")
async def get_policy(device: sqlite3.Row = Depends(current_device), ctx: Context = Depends(get_ctx)) -> dict:
    return policy.policy_for(ctx, device["person_id"])


@router.post("/events")
async def post_events(body: dict, device: sqlite3.Row = Depends(current_device),
                      ctx: Context = Depends(get_ctx)) -> dict:
    return intake.accept(ctx, device, body.get("events") or [])


@router.put("/events/{event_id}/image", status_code=204)
async def put_image(event_id: str, request: Request, device: sqlite3.Row = Depends(current_device),
                    ctx: Context = Depends(get_ctx)) -> Response:
    data = await request.body()
    intake.store_image(ctx, device, event_id, data)
    return Response(status_code=204)


@router.post("/judge")
async def post_judge(request: Request, device: sqlite3.Row = Depends(current_device),
                     ctx: Context = Depends(get_ctx)) -> dict:
    return intake.judge_image(ctx, await request.body())


@router.post("/unlock")
async def post_unlock(body: dict, device: sqlite3.Row = Depends(current_device),
                      ctx: Context = Depends(get_ctx)) -> dict:
    return unlock.redeem(ctx, device, str(body.get("code", "")))


@router.post("/unlock/request", status_code=202)
async def post_unlock_request(body: dict, device: sqlite3.Row = Depends(current_device),
                              ctx: Context = Depends(get_ctx)) -> Response:
    unlock.request_code(ctx, device, int(body.get("minutes", 0)), body.get("reason"))
    return JSONResponse({"status": "requested"}, status_code=202)


def _manifest_path(ctx: Context) -> Path:
    return ctx.config.models_dir / "manifest.json"


@router.get("/models/manifest")
async def models_manifest(device: sqlite3.Row = Depends(current_device), ctx: Context = Depends(get_ctx)) -> dict:
    path = _manifest_path(ctx)
    if not path.exists():
        raise NotFound("no models manifest on this server")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for model in manifest.get("models", []):
        model["url"] = f"/api/v1/models/{model['file']}"
    return manifest


@router.get("/models/{filename}")
async def model_file(filename: str, device: sqlite3.Row = Depends(current_device),
                     ctx: Context = Depends(get_ctx)) -> FileResponse:
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise NotFound("no such model")
    path = ctx.config.models_dir / filename
    if not path.is_file():
        raise NotFound("no such model")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)
