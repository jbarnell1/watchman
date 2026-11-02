"""Who is looking at the portal and what they may see."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from starlette.requests import Request

from app.clients.identity import OidcIdentity
from app.services.context import Context
from app.services.errors import Forbidden


@dataclass
class Viewer:
    login: str
    name: str
    is_admin: bool = False
    protected_ids: set[str] = field(default_factory=set)
    partner_rows: dict[str, sqlite3.Row] = field(default_factory=dict)   # person_id -> partner row
    people: list[sqlite3.Row] = field(default_factory=list)              # people this login may see
    can_sign_out: bool = False                                           # OIDC sessions can end; Tailscale cannot

    def can_see(self, person_id: str) -> bool:
        return any(p["id"] == person_id for p in self.people)

    def is_partner_of(self, person_id: str) -> bool:
        return person_id in self.partner_rows

    def is_protected(self, person_id: str) -> bool:
        return person_id in self.protected_ids

    def role_for(self, person_id: str) -> str:
        if self.is_partner_of(person_id):
            return "partner"
        if self.is_protected(person_id):
            return "protected"
        return "admin" if self.is_admin else "none"


def viewer_for(ctx: Context, request: Request) -> Viewer | None:
    ident = ctx.identity.identify(request)
    if ident is None:
        return None
    # First run: the first person to reach the portal becomes the admin (ADR-014). The same rule for
    # Tailscale and OIDC sign-in: only the household's tailnet or identity provider gets this far.
    if not ctx.people.has_admin():
        with ctx.conn:
            ctx.people.add_login(ident.login, "admin")
    v = Viewer(login=ident.login, name=ident.name, can_sign_out=isinstance(ctx.identity, OidcIdentity))
    for role in ctx.people.roles_for(ident.login):
        if role["role"] == "admin":
            v.is_admin = True
        elif role["role"] == "protected" and role["person_id"]:
            v.protected_ids.add(role["person_id"])
        elif role["role"] == "partner" and role["partner_id"]:
            partner = ctx.people.get_partner(role["partner_id"])
            if partner:
                v.partner_rows[partner["person_id"]] = partner
    if v.is_admin:
        v.people = ctx.people.list_people()
    else:
        ids = v.protected_ids | set(v.partner_rows)
        v.people = [p for p in ctx.people.list_people() if p["id"] in ids]
    return v


def require_viewer(ctx: Context, request: Request) -> Viewer:
    v = viewer_for(ctx, request)
    if v is None:
        raise Forbidden("sign in first")
    return v


def pick_person(v: Viewer, request: Request) -> sqlite3.Row | None:
    wanted = request.query_params.get("person")
    if wanted:
        for p in v.people:
            if p["id"] == wanted:
                return p
        raise Forbidden("not your person")
    return v.people[0] if v.people else None
