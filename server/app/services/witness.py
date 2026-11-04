"""Witnesses (ADR-054): a partner's own server keeps watch on this household's record, and this
server keeps watch on the households its own people are partners for.

Served side: a partner creates a witness token on the Record page; `/api/v1/witness/*` reads the
record and this server's own check of it with that token. Nothing else is readable with it.

Watching side: every POLL_EVERY this server reads each watched record from where it left off,
checks that the entries it already holds are unchanged and the new ones link and hash, and emails
the watcher about anything a partner should hear even if the watched server's own mail was
changed or stopped:
- the record went backwards, was rewritten, or the watched server reports a mismatch;
- the watched server cannot be reached for DOWN_AFTER (a dead man's switch), again every
  REMIND_EVERY while it stays down;
- a setting loosened, a device paired or removed, a partner or login added or removed, a partner
  key replaced, records purged early, a pause or code without a partner's signature once partners
  hold keys (ADR-053).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import uuid
from datetime import timedelta

from app.clients.mailer import Mail
from app.clients.witness_client import WitnessClient, WitnessError
from app.clock import iso, parse_iso
from app.services import keys, ledger, locks
from app.services.context import Context
from app.services.errors import Unauthorized
from app.settings import Settings
from app.store.ledger_store import check_chain

log = logging.getLogger(__name__)

POLL_EVERY = timedelta(minutes=5)
DOWN_AFTER = timedelta(minutes=30)
REMIND_EVERY = timedelta(hours=6)
PAGE = 500
EARLY_PURGE = timedelta(days=30)


# Served side

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(ctx: Context, partner_id: str) -> str:
    token = "wmw_" + secrets.token_urlsafe(32)
    with ctx.conn:
        ctx.conn.execute("INSERT INTO witness_tokens(id, partner_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
                         (str(uuid.uuid4()), partner_id, hash_token(token), iso(ctx.clock.now())))
        ctx.ledger.append("witness_added", {"partner_id": partner_id})
    return token


def require_token(ctx: Context, header: str) -> sqlite3.Row:
    if not header.lower().startswith("bearer "):
        raise Unauthorized("witness token required")
    row = ctx.conn.execute("SELECT * FROM witness_tokens WHERE token_hash = ? AND revoked_at IS NULL",
                           (hash_token(header[7:].strip()),)).fetchone()
    if row is None:
        raise Unauthorized("witness token is unknown or revoked")
    with ctx.conn:
        ctx.conn.execute("UPDATE witness_tokens SET last_seen_at = ? WHERE id = ?", (iso(ctx.clock.now()), row["id"]))
    return row


def witnesses(ctx: Context) -> list[sqlite3.Row]:
    return ctx.conn.execute(
        "SELECT w.*, p.name AS partner_name FROM witness_tokens w LEFT JOIN partners p ON p.id = w.partner_id "
        "WHERE w.revoked_at IS NULL ORDER BY w.created_at").fetchall()


def status(ctx: Context) -> dict:
    seq, h = ctx.ledger.head()
    return {"head": {"seq": seq, "hash": h}, "household": ctx.settings.load().household_name,
            "problems": ledger.verify(ctx), "server_time": iso(ctx.clock.now())}


# Watching side

def add_watch(ctx: Context, name: str, url: str, token: str, email: str) -> str:
    wid = str(uuid.uuid4())
    with ctx.conn:
        ctx.conn.execute("INSERT INTO watching(id, name, url, token, email, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (wid, name, url.rstrip("/"), token, email, iso(ctx.clock.now())))
    return wid


def watched(ctx: Context) -> list[sqlite3.Row]:
    return ctx.conn.execute("SELECT * FROM watching ORDER BY created_at").fetchall()


def poll_all(ctx: Context, client: WitnessClient, force: bool = False) -> int:
    now = ctx.clock.now()
    polled = 0
    for row in watched(ctx):
        if not force and row["last_try_at"] and now - parse_iso(row["last_try_at"]) < POLL_EVERY:
            continue
        poll(ctx, client, row)
        polled += 1
    return polled


def poll(ctx: Context, client: WitnessClient, row: sqlite3.Row) -> list[str]:
    """Read one watched record. Returns the lines it emailed about (empty when all is well)."""
    now = ctx.clock.now()
    lines: list[str] = []
    pins = json.loads(row["keys"] or "{}")
    mine = json.loads(row["my_key"]) if row["my_key"] else None
    last_seq, last_hash = row["last_seq"], row["last_hash"]
    first = last_seq == 0
    try:
        st = client.get(row["url"], row["token"], "/api/v1/witness/status")
        head = st["head"]
        if head["seq"] < last_seq:
            lines.append(f"The record went backwards: it now ends at entry {head['seq']}; this server had seen "
                         f"entry {last_seq}. Entries were removed.")
        elif last_seq:
            got = client.get(row["url"], row["token"], "/api/v1/witness/ledger",
                             {"after": last_seq - 1, "limit": 1})["entries"]
            if not got or got[0]["hash"] != last_hash:
                lines.append(f"The record was rewritten: entry {last_seq} is not what this server saw before.")
        if lines:
            # Reported once; what is there now becomes what later polls are checked against.
            last_seq, last_hash, first = 0, None, True
        prev, seq = last_hash or "0" * 64, last_seq
        while seq < head["seq"]:
            page = client.get(row["url"], row["token"], "/api/v1/witness/ledger",
                              {"after": seq, "limit": PAGE})["entries"]
            if not page:
                break
            broken = check_chain(page, prev, seq + 1)
            if broken:
                lines.append(f"The record does not check out: {broken}.")
                break
            for e in page:
                note = notable(e, pins)
                if note and not first:
                    lines.append(note)
                foreign = not_mine(e, mine)
                if foreign:
                    lines.append(foreign)
            prev, seq = page[-1]["hash"], page[-1]["seq"]
        last_seq, last_hash = seq, (prev if seq else None)
        for p in st.get("problems") or []:
            lines.append(f"The watched server reports: {p}.")
        with ctx.conn:
            ctx.conn.execute(
                "UPDATE watching SET last_seq = ?, last_hash = ?, keys = ?, last_ok_at = ?, last_try_at = ?, "
                "last_error = NULL, down_alerted_at = NULL WHERE id = ?",
                (last_seq, last_hash, json.dumps(pins), iso(now), iso(now), row["id"]))
        if row["down_alerted_at"]:
            _mail(ctx, row, f"{row['name']} can be reached again",
                  [f"This server reached {row['name']} again at {iso(now)}. Anything that happened while it was "
                   "unreachable is on its record and was checked."])
    except (WitnessError, KeyError, TypeError, ValueError) as exc:
        with ctx.conn:
            ctx.conn.execute("UPDATE watching SET last_try_at = ?, last_error = ? WHERE id = ?",
                             (iso(now), str(exc)[:300], row["id"]))
        since = parse_iso(row["last_ok_at"] or row["created_at"])
        reminded = row["down_alerted_at"]
        if now - since >= DOWN_AFTER and (not reminded or now - parse_iso(reminded) >= REMIND_EVERY):
            _mail(ctx, row, f"{row['name']} cannot be reached", [
                f"This server has not been able to read {row['name']}'s record since {iso(since)}.",
                f"Last error: {str(exc)[:200]}",
                "A server that is off records nothing. If nobody you know switched it off, ask about it.",
            ])
            with ctx.conn:
                ctx.conn.execute("UPDATE watching SET down_alerted_at = ? WHERE id = ?", (iso(now), row["id"]))
        return []
    if lines:
        report = "\n".join(lines)
        repeat = (report == row["last_report"] and row["last_report_at"]
                  and now - parse_iso(row["last_report_at"]) < REMIND_EVERY)
        if not repeat:
            what = "the record changed" if lines[0].startswith("The record") else "changes"
            _mail(ctx, row, f"{row['name']}: {what}", lines)
            with ctx.conn:
                ctx.conn.execute("UPDATE watching SET last_report = ?, last_report_at = ? WHERE id = ?",
                                 (report, iso(now), row["id"]))
    return lines


def not_mine(e: dict, mine: dict | None) -> str | None:
    """This server made its partner's key (ADR-055), so any other key set for that partner after it
    was made is someone else acting as them: reported on the first read too."""
    if not mine or e["kind"] != "partner_key" or e["seq"] <= mine.get("registered_seq", 0):
        return None
    b = e["body"]
    if b.get("partner_id") == mine["partner_id"] and b.get("fingerprint") != mine["fingerprint"]:
        return (f"{e['at']}: a key for you ({b.get('fingerprint')}) was set up on that server, but this server "
                f"holds your key ({mine['fingerprint']}). Someone there is acting as you.")
    return None


def notable(e: dict, pins: dict) -> str | None:
    """A line for the watcher about one entry, or None. Updates [pins] with partner keys seen."""
    k, b = e["kind"], e["body"]
    at = e["at"]
    if k == "genesis":
        return f"{at}: the record started again from the beginning."
    if k == "setting":
        key = b.get("key")
        old, new = b.get("old"), b.get("new")
        if key == "smtp" or (key in Settings.model_fields and locks.loosens(key, old, new, None)
                             and (locks.group_of(key) or key in ("locks_on", "unlocked_groups"))):
            signed = " with a partner's signed approval" if b.get("approval") else ""
            return f"{at}: {b.get('by')} loosened {locks.LABELS.get(key, key)}{signed}."
        return None
    if k == "device_paired":
        return f"{at}: a device was paired: {b.get('name')} ({b.get('platform')} {b.get('kind')})."
    if k == "device_revoked":
        return f"{at}: a device was removed."
    if k == "partner_added":
        return f"{at}: {b.get('name')} ({b.get('email')}) was added as a partner."
    if k == "partner_removed":
        return f"{at}: {b.get('name')} ({b.get('email')}) was removed as a partner."
    if k in ("login_added", "login_removed") and b.get("role") in ("admin", "partner"):
        verb = "given" if k == "login_added" else "lost"
        return f"{at}: portal login {b.get('login')} {verb} the {b.get('role')} role."
    if k == "purge":
        try:
            if parse_iso(at) - parse_iso(b["before"]) < EARLY_PURGE:
                return f"{at}: {b.get('events')} events from before {b['before']} were deleted, under 30 days old."
        except (KeyError, ValueError):
            return f"{at}: events were deleted."
        return None
    if k == "partner_key":
        pid = b.get("partner_id")
        fp = b.get("fingerprint")
        old = pins.get(pid)
        pins[pid] = {"sign": b.get("sign"), "fingerprint": fp}
        if old and old.get("fingerprint") != fp:
            return f"{at}: the key of partner {b.get('name') or pid} was replaced (now {fp})."
        return None
    if k in ("code_issued", "pause", "review", "setting_approved", "partner_approved"):
        approval = b.get("approval")
        if approval and pins:
            pin = pins.get(approval.get("partner_id"))
            if not pin or not keys.verify_approval(pin["sign"], approval):
                return f"{at}: a {k.replace('_', ' ')} carries a signature that is not from a partner's key."
        elif pins and k in ("code_issued",):
            return f"{at}: a code was issued without a partner's signature."
    return None


def _mail(ctx: Context, row: sqlite3.Row, subject: str, lines: list[str]) -> None:
    text = "\n".join(lines) + (f"\n\nYou get this because this server watches {row['name']} "
                               f"({row['url']}). It checks every few minutes.")
    try:
        ctx.mailer.send(Mail(to=[row["email"]], subject=f"Watchman watch: {subject}", text=text))
    except Exception as exc:
        log.warning("watch email failed: %s", exc)
