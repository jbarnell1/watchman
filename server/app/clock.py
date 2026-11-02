"""Time behind an interface so tests can move it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, when: datetime) -> None:
        self._now = when

    def advance(self, **kwargs: float) -> datetime:
        self._now += timedelta(**kwargs)
        return self._now


def iso(dt: datetime) -> str:
    """RFC 3339 UTC with second precision, the format the API contract uses."""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
