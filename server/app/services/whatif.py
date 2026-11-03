"""What if: replay stored scores against other thresholds (ADR-041). Numbers only, never images.

Each image event carries the scores the device saw (`classifier`, sometimes `detector`) and, when
its crop reached the server, the second opinion. `replay` runs those numbers through the device's
decision (clients/android/.../core/Decide.kt and Pipeline.kt) once with the current thresholds and
once with proposed ones, and counts what each set would have produced per app.

The limitation, which the page states in these words: "This only replays pictures the device
already acted on. A lower line cannot show pictures that were never reported, so it can only tell
you what a lower line would add among these; a higher line shows what would no longer be covered or
reported." Events exist only for pictures the device covered, asked about and had confirmed, or
flagged. A picture that scored under the ask line, or that the server judged safe when asked
(ADR-022, nothing is stored), left no event, so lowering a threshold cannot reveal it. Lowering a
line is therefore an undercount; raising one is a fair estimate.

Other simplifications, also stated on the page:
- Events whose labels include `known_bad` were covered by fingerprint without a model run. They
  bypass thresholds and are counted on their own, the same under both sets.
- An asked picture is reported only on an explicit second opinion. When the event has no second
  opinion (its image was skipped as a repeat or already held), it is assumed reported, as it was.
- The current column is a replay too, not a count of history: if thresholds changed inside the
  window, it shows what today's values would have done.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from app.clients.detector import EXPLICIT
from app.settings import Thresholds

IMAGE_TYPES = frozenset({"image_covered", "image_flagged", "screen_blocked"})
MEASURES: tuple[tuple[str, str], ...] = (
    ("covered", "Covered on the device"),
    ("asked", "Asked of the server"),
    ("reported", "Reported"),
    ("explicit", "Explicit by second opinion"),
    ("review", "Partner review items"),
    ("false_alarms", "Known false alarms still raised"),
)
UNKNOWN_APP = "(no app)"


@dataclass
class Tally:
    covered: int = 0
    asked: int = 0
    reported: int = 0
    explicit: int = 0
    review: int = 0
    false_alarms: int = 0

    def add(self, other: Tally) -> None:
        for name in asdict(self):
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class AppReplay:
    app: str
    current: Tally = field(default_factory=Tally)
    proposed: Tally = field(default_factory=Tally)
    known_bad: int = 0
    events: int = 0


@dataclass
class Replay:
    apps: list[AppReplay]
    total: AppReplay


def _field(ev: Any, key: str) -> Any:
    if isinstance(ev, Mapping):
        return ev.get(key)
    try:
        return ev[key]          # sqlite3.Row
    except (IndexError, KeyError):
        return None


def _scores(ev: Any) -> dict:
    raw = _field(ev, "scores")
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def judge(ev: Any, t: Thresholds, cover_on_device: bool = True, ask_server: bool = True,
          covered_reports: str = "all") -> Tally:
    """What one non known-bad image event would have produced under `t`, as a tally of 0s and 1s."""
    scores = _scores(ev)
    classifier = _num(scores.get("classifier")) or 0.0
    detector = _num(scores.get("detector"))
    second = _num(_field(ev, "second_opinion"))
    score = max(classifier, detector or 0.0)

    # Decide.combine then Decide.settle: the detector only ever raises the answer.
    if classifier >= t.cover or (detector is not None and detector >= t.detector_cover):
        decision = "cover" if cover_on_device else "flag"
    elif classifier >= t.flag:
        decision = "flag"
    else:
        decision = "safe"

    out = Tally()
    asking = cover_on_device and ask_server and t.ask <= score < t.sure
    if asking:
        # Pipeline: cover first, ask; a safe answer lifts the blur and nothing is reported.
        out.covered = out.asked = 1
        reported = (second is not None and second >= t.second_opinion) or second is None
        covered_report = True
    else:
        out.covered = int(decision == "cover")
        reported = decision != "safe"
        covered_report = decision == "cover"
    if not reported:
        return out
    out.reported = 1
    # Covered pictures need bare nudity from the server's detector or the higher line to reach
    # partners, unless all are reported (ADR-046). Pictures from before the server tagged count on
    # the second opinion alone.
    if covered_report and covered_reports != "all":
        tags = scores.get("server_labels") or {}
        exposed = max((float(s) for label, s in tags.items() if label in EXPLICIT), default=0.0)
        out.explicit = int(exposed >= t.server_exposed or
                           (second is not None and second >= max(t.second_opinion, t.covered_report)))
    else:
        out.explicit = int(second is not None and second >= t.second_opinion)
    # Intake: covered pictures start as review, flagged ones as info until explicit; a look-alike of
    # one just reported is a repeat and carries no review of its own (ADR-035).
    if _field(ev, "image_status") != "repeat" and ((covered_report and covered_reports == "all") or out.explicit):
        out.review = 1
    out.false_alarms = int(_field(ev, "review_state") == "false_alarm")
    return out


def replay(events: Iterable[Any], current: Thresholds, proposed: Thresholds,
           cover_on_device: bool = True, ask_server: bool = True, covered_reports: str = "all") -> Replay:
    """Counts per app and in total for both threshold sets. Non-image events are ignored."""
    apps: dict[str, AppReplay] = {}
    total = AppReplay(app="All apps")
    for ev in events:
        if _field(ev, "type") not in IMAGE_TYPES:
            continue
        name = _field(ev, "app") or UNKNOWN_APP
        row = apps.setdefault(name, AppReplay(app=name))
        row.events += 1
        total.events += 1
        if "known_bad" in (_scores(ev).get("labels") or []):
            row.known_bad += 1
            total.known_bad += 1
            continue
        now = judge(ev, current, cover_on_device, ask_server, covered_reports)
        then = judge(ev, proposed, cover_on_device, ask_server, covered_reports)
        row.current.add(now)
        row.proposed.add(then)
        total.current.add(now)
        total.proposed.add(then)
    ordered = sorted(apps.values(), key=lambda a: (-a.events, a.app))
    return Replay(apps=ordered, total=total)
