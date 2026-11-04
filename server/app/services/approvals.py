"""Changes that wait for a partner's signed approval (ADR-053).

Once any partner holds a key, loosening a locked setting, adding a partner beside existing ones and
removing a partner no longer take a six-digit code that this server issues: the change is held
here, every keyed partner is emailed a link, and it is applied only when one of them signs it in
their own browser. The signature goes on the record with the change, where a partner's own server
checks it against the key it pinned (ADR-054).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import timedelta

from app.clients.mailer import Mail
from app.clock import iso
from app.services import alerts, keys
from app.services.context import Context
from app.services.errors import ApiError, Forbidden, NotFound
from app.store.ledger_store import digest

KINDS = ("settings", "partner_add", "partner_remove")
# A partner added through an approval still waits before their own codes and approvals count.
PARTNER_WAITING = timedelta(days=3)


class NeedsApproval(Exception):
    """Raised by a gate when the household is sealed and the change loosens something."""


def request(ctx: Context, kind: str, person_id: str | None, params: dict, summary: str, login: str,
            portal_url: str = "") -> str:
    assert kind in KINDS
    aid = str(uuid.uuid4())
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO approvals(id, kind, person_id, params, summary, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, kind, person_id, json.dumps(params), summary, login, iso(ctx.clock.now())))
        ctx.ledger.append("approval_requested", {"kind": kind, "params": params, "summary": summary, "by": login},
                          person_id, aid)
    link = f"{portal_url.rstrip('/')}/approve/{aid}"
    for k in _approvers(ctx, person_id):
        alerts._send(ctx, Mail(to=[k["email"]], subject="Watchman: a change is waiting for your approval",
                               text=f"{login} asked to {summary}.\n\nNothing changes unless a partner approves it. "
                                    f"To approve, open {link} and unlock your key. To refuse, do nothing or "
                                    "press Decline there."))
    return aid


def _approvers(ctx: Context, person_id: str | None) -> list[sqlite3.Row]:
    now = iso(ctx.clock.now())
    return [r for r in keys.keyed_partners(ctx, person_id)
            if r["active_from"] <= now and (not r["trusted_at"] or r["trusted_at"] <= now)]


def get(ctx: Context, aid: str) -> sqlite3.Row:
    row = ctx.conn.execute("SELECT * FROM approvals WHERE id = ?", (aid,)).fetchone()
    if row is None:
        raise NotFound("no such request")
    return row


def signed_params(row: sqlite3.Row) -> dict:
    """What the partner's browser signs: the request and a digest of exactly what it changes."""
    return {"id": row["id"], "digest": digest(json.loads(row["params"]))}


def may_decide(ctx: Context, row: sqlite3.Row, partner_ids: set[str]) -> set[str]:
    allowed = {r["partner_id"] for r in _approvers(ctx, row["person_id"])}
    return allowed & partner_ids


def approve(ctx: Context, aid: str, approval: dict, login: str) -> sqlite3.Row:
    row = get(ctx, aid)
    if row["status"] != "waiting":
        raise ApiError(f"this request was already {row['status']}")
    allowed = {r["partner_id"] for r in _approvers(ctx, row["person_id"])}
    keys.check(ctx, approval, "approve", signed_params(row), allowed)
    params = json.loads(row["params"])
    with ctx.conn:
        _apply(ctx, row["kind"], params, approval, login)
        ctx.conn.execute("UPDATE approvals SET status = 'approved', decided_by = ?, decided_at = ? WHERE id = ?",
                         (login, iso(ctx.clock.now()), aid))
    return row


def decline(ctx: Context, aid: str, partner_ids: set[str], login: str) -> None:
    row = get(ctx, aid)
    if not ({r["partner_id"] for r in keys.keyed_partners(ctx, row["person_id"])} & partner_ids):
        raise Forbidden("only a partner can decline")
    with ctx.conn:
        ctx.conn.execute("UPDATE approvals SET status = 'declined', decided_by = ?, decided_at = ? WHERE id = ? "
                         "AND status = 'waiting'", (login, iso(ctx.clock.now()), aid))
        ctx.ledger.append("approval_declined", {"by": login}, row["person_id"], aid)


def _apply(ctx: Context, kind: str, params: dict, approval: dict, login: str) -> None:
    by = f"{login} (approved)"
    if kind == "settings":
        for key, value in params["values"].items():
            ctx.settings.set(key, value, login=by, approval=approval)
    elif kind == "partner_add":
        ctx.people.add_partner(params["person_id"], params["name"], params["email"], params["link_type"],
                               params.get("login"), iso(ctx.clock.now() + PARTNER_WAITING), approval=approval)
    elif kind == "partner_remove":
        ctx.people.remove_partner(params["partner_id"], approval=approval)


def waiting(ctx: Context) -> list[sqlite3.Row]:
    return ctx.conn.execute("SELECT * FROM approvals WHERE status = 'waiting' ORDER BY created_at DESC").fetchall()
