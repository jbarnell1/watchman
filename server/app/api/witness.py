"""What a partner's own server reads from this one (ADR-054). Witness tokens only; read only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.services import witness
from app.services.context import Context

router = APIRouter(prefix="/api/v1/witness")
MAX_PAGE = 1000


def get_ctx(request: Request) -> Context:
    return request.app.state.ctx


@router.get("/status")
async def status(request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    witness.require_token(ctx, request.headers.get("authorization", ""))
    return witness.status(ctx)


@router.get("/ledger")
async def ledger_page(request: Request, after: int = 0, limit: int = 500, ctx: Context = Depends(get_ctx)) -> dict:
    witness.require_token(ctx, request.headers.get("authorization", ""))
    seq, h = ctx.ledger.head()
    return {"entries": ctx.ledger.entries(max(0, after), max(1, min(limit, MAX_PAGE))),
            "head": {"seq": seq, "hash": h}}
