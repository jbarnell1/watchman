"""The record (ADR-052): what goes on it, and whether the database still matches it.

Every store that changes something partners rely on appends an entry (app/store/ledger_store.py):
events and the server's judgements of them, reviews, images, settings, people, partners, logins,
devices, codes, pauses, digests and retention purges. On first start the record opens with a
snapshot of what was already there.

`verify` replays the record and compares it with the tables: an event, setting, partner, login,
device or image changed, added or deleted outside the server's own code shows up here. On its own
this catches edits to the database file; a person who also rewrites the record is caught by the
copies of its later hashes held elsewhere: a partner's own server (ADR-054), partner-signed
checkpoints (ADR-053) and the line at the foot of every email.
"""

from __future__ import annotations

import json
import logging

from app.services.context import Context
from app.store.ledger_store import check_chain, digest, event_fields
from app.store.settings_store import on_record

log = logging.getLogger(__name__)
MAX_PROBLEMS = 20


def start(ctx: Context) -> None:
    for store in (ctx.events, ctx.settings, ctx.people, ctx.devices, ctx.codes):
        store.ledger = ctx.ledger
    if ctx.ledger.head()[0] == 0:
        with ctx.conn:
            ctx.ledger.append("genesis", snapshot(ctx))


def snapshot(ctx: Context) -> dict:
    c = ctx.conn
    return {
        "settings": {r["key"]: on_record(r["key"], json.loads(r["value"]))
                     for r in c.execute("SELECT key, value FROM settings") if r["key"] != "policy_version"},
        "partners": {r["id"]: _partner(r) for r in c.execute("SELECT * FROM partners")},
        "logins": sorted([r["login"], r["role"], r["person_id"], r["partner_id"]]
                         for r in c.execute("SELECT * FROM logins")),
        "devices": {r["id"]: bool(r["revoked_at"]) for r in c.execute("SELECT * FROM devices")},
        "events": {r["id"]: {"digest": digest(event_fields(r)), "severity": r["severity"],
                             "review": r["review_state"], "occurred_at": r["occurred_at"]}
                   for r in c.execute("SELECT * FROM events")},
        "images": sorted(r["event_id"] for r in c.execute("SELECT event_id FROM images")),
    }


def _partner(r) -> dict:
    return {"person_id": r["person_id"], "email": r["email"], "login": r["login"], "trusted_at": r["trusted_at"]}


def head_line(ctx: Context) -> str:
    seq, h = ctx.ledger.head()
    return f"Record: entry {seq}, {h[:16]}"


def replay(entries: list[dict]) -> dict:
    state = {"settings": {}, "partners": {}, "logins": set(), "devices": {}, "events": {}, "images": set(),
             "files": {}, "purged_before": ""}
    for e in entries:
        try:
            _apply(state, e)
        except (KeyError, TypeError, AttributeError):
            pass   # a malformed entry: the chain check reports the alteration
    return state


def _apply(state: dict, e: dict) -> None:
    k, b, ref = e["kind"], e["body"], e["ref"]
    if k == "genesis":
        state["settings"] = dict(b["settings"])
        state["partners"] = dict(b["partners"])
        state["logins"] = {tuple(x) for x in b["logins"]}
        state["devices"] = dict(b["devices"])
        state["events"] = {i: dict(v) for i, v in b["events"].items()}
        state["images"] = set(b["images"])
    elif k == "event":
        state["events"][ref] = {"digest": b["digest"], "severity": b["severity"], "review": "open",
                                "occurred_at": b["occurred_at"]}
    elif k == "judged" and ref in state["events"]:
        state["events"][ref]["severity"] = b["severity"]
    elif k == "review" and ref in state["events"]:
        state["events"][ref]["review"] = b["state"]
    elif k == "image":
        state["images"].add(ref)
        state["files"][ref] = b.get("files") or {}
    elif k == "image_removed":
        state["images"].discard(ref)
    elif k == "purge":
        state["purged_before"] = max(state["purged_before"], b["before"])
    elif k == "setting":
        state["settings"][b["key"]] = b["new"]
    elif k == "partner_added":
        state["partners"][ref] = {"person_id": e["person_id"], "email": b["email"], "login": b["login"],
                                  "trusted_at": b["trusted_at"]}
    elif k == "partner_removed":
        state["partners"].pop(ref, None)
        state["logins"] = {x for x in state["logins"] if x[3] != ref}
    elif k == "login_added":
        state["logins"].add((b["login"], b["role"], e["person_id"], b["partner_id"]))
    elif k == "login_removed":
        state["logins"] = {x for x in state["logins"]
                           if not (x[0] == b["login"] and x[1] == b["role"] and x[2] == e["person_id"])}
    elif k == "device_paired":
        state["devices"][ref] = False
    elif k == "device_revoked":
        state["devices"][ref] = True


def verify(ctx: Context) -> list[str]:
    """What no longer matches the record, most important first. Empty when all is well."""
    entries = ctx.ledger.entries(0, limit=10**9)
    problems: list[str] = []
    broken = check_chain(entries)
    if broken:
        problems.append(f"the record itself was altered: {broken}")
    state = replay(entries)
    c = ctx.conn

    rows = {r["id"]: r for r in c.execute("SELECT * FROM events")}
    for eid, r in rows.items():
        rec = state["events"].get(eid)
        if rec is None:
            problems.append(f"event {eid} ({r['type']}) is in the database but not on the record")
        elif rec["digest"] != digest(event_fields(r)):
            problems.append(f"event {eid} ({r['type']}) was changed after it was recorded")
        elif rec["severity"] != r["severity"]:
            problems.append(f"event {eid} severity is {r['severity']}; the record says {rec['severity']}")
        elif rec["review"] != r["review_state"]:
            problems.append(f"event {eid} review is {r['review_state']}; the record says {rec['review']}")
    for eid, rec in state["events"].items():
        if eid not in rows and rec["occurred_at"] >= state["purged_before"]:
            problems.append(f"event {eid} was deleted before its retention ran out")

    stored = {r["key"]: on_record(r["key"], json.loads(r["value"]))
              for r in c.execute("SELECT key, value FROM settings") if r["key"] != "policy_version"}
    for key in set(stored) | set(state["settings"]):
        if stored.get(key) != state["settings"].get(key):
            problems.append(f"setting {key} was changed outside the portal")

    partners = {r["id"]: _partner(r) for r in c.execute("SELECT * FROM partners")}
    for pid in set(partners) | set(state["partners"]):
        if partners.get(pid) != state["partners"].get(pid):
            problems.append(f"partner {pid} was added, removed or changed outside the portal")
    logins = {(r["login"], r["role"], r["person_id"], r["partner_id"]) for r in c.execute("SELECT * FROM logins")}
    for x in logins ^ state["logins"]:
        problems.append(f"portal login {x[0]} ({x[1]}) was added or removed outside the portal")
    devices = {r["id"]: bool(r["revoked_at"]) for r in c.execute("SELECT * FROM devices")}
    for did in set(devices) | set(state["devices"]):
        if devices.get(did) != state["devices"].get(did):
            problems.append(f"device {did} was added, removed or changed outside the portal")
    images = {r["event_id"] for r in c.execute("SELECT event_id FROM images")}
    for eid in images ^ state["images"]:
        problems.append(f"the image of event {eid} was added or removed outside the server")
    for eid in images & state["images"]:
        for level, want in state["files"].get(eid, {}).items():
            if ctx.blobs.file_digest(eid, level) != want:
                problems.append(f"the {level} image of event {eid} was replaced or removed")
    if len(problems) > MAX_PROBLEMS:
        problems = problems[:MAX_PROBLEMS] + [f"and {len(problems) - MAX_PROBLEMS} more"]
    return problems
