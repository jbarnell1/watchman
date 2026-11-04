"""Slows guessing of six-digit partner codes (ADR-050).

Five wrong codes in 15 minutes from one device (or, for settings codes, from the portal) and the
next try is refused until the window passes. The tenth wrong code in an hour raises a tamper
event, which alerts partners.
"""

from __future__ import annotations

from datetime import timedelta

from app.clock import iso
from app.services.context import Context
from app.services.errors import RateLimited

WINDOW = timedelta(minutes=15)
LIMIT = 5
ALARM_WINDOW = timedelta(hours=1)
ALARM_AT = 10


def check(ctx: Context, scope: str) -> None:
    since = iso(ctx.clock.now() - WINDOW)
    n = ctx.conn.execute("SELECT COUNT(*) FROM code_attempts WHERE scope = ? AND at > ?", (scope, since)).fetchone()[0]
    if n >= LIMIT:
        raise RateLimited("too many wrong codes; try again in 15 minutes")


def failed(ctx: Context, scope: str) -> bool:
    """Record a wrong code. True when this one reaches the alarm count for the hour."""
    now = ctx.clock.now()
    with ctx.conn:
        ctx.conn.execute("INSERT INTO code_attempts(scope, at) VALUES (?, ?)", (scope, iso(now)))
        ctx.conn.execute("DELETE FROM code_attempts WHERE at < ?", (iso(now - ALARM_WINDOW),))
    n = ctx.conn.execute("SELECT COUNT(*) FROM code_attempts WHERE scope = ?", (scope,)).fetchone()[0]
    return n == ALARM_AT
