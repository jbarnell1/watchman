"""One-way locks (ADR-028): tightening protection is always free; loosening a locked setting needs a
partner's settings code once a device is paired.

Settings fall into lock groups. Each group is locked unless the household unlocked it (setup, or
Settings, Protection, Locks); new groups are locked by default, so the list fails closed. Unlocking a
group is itself loosening and needs a code; locking one again is free.

`loosens(key, old, new)` says whether a change weakens protection or accountability. Anything not
clearly stronger counts as weaker: replacing the protected browser, removing a blocked app even
while adding another, any threshold raised.
"""

from __future__ import annotations

from typing import Any

from app.settings import PERSON_KEYS, Settings, effective

# Group id -> (label for the settings page, setting keys in it). "partners" has no keys: it guards
# removing a partner, which the partner pages check by name.
LOCK_GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "protection": ("Thresholds, coverage and posture",
                   ("thresholds", "strict_mode", "coverage", "cover_on_device", "ask_server", "person_overrides")),
    "apps": ("Blocked, risky and not-watched apps, watch levels, protected browser",
             ("blocked_apps", "risky_apps", "exempt_apps", "protected_browser", "watch_default", "realtime_apps",
              "checked_apps")),
    "timing": ("Timing and silence alerts", ("sample_interval_s", "whole_screen_interval_s", "heartbeat_gap_minutes",
                                              "checked_interval_s")),
    "pauses": ("Pauses and peeks", ("max_pause_minutes", "peek_seconds", "peek_cooldown_hours")),
    "retention": ("How long blurred images are kept", ("image_retention_days",)),
    "partners": ("Removing a partner", ()),
}

COVERAGE_RANK = {"explicit": 0, "revealing": 1, "strict": 2}
# Lower is stronger: more images covered, more often looked at, sooner noticed, shorter pauses.
LOWER_IS_STRONGER = {"sample_interval_s", "whole_screen_interval_s", "heartbeat_gap_minutes", "max_pause_minutes",
                     "checked_interval_s",
                     "peek_cooldown_hours"}
# Higher is stronger: partners see more for longer.
HIGHER_IS_STRONGER = {"image_retention_days", "peek_seconds"}
# On is stronger.
FLAGS = {"strict_mode", "cover_on_device", "ask_server", "locks_on"}
# Lists where adding is stronger and removing anything is weaker.
LISTS = {"blocked_apps", "risky_apps", "realtime_apps"}
WATCH_RANK = {"checked": 0, "realtime": 1}


def group_of(key: str) -> str | None:
    for gid, (_, keys) in LOCK_GROUPS.items():
        if key in keys:
            return gid
    return None


def locked_groups(settings: Settings) -> set[str]:
    return {g for g in LOCK_GROUPS if g not in set(settings.unlocked_groups)}


def locked_keys(settings: Settings) -> set[str]:
    """Keys whose loosening needs a code: every key of every locked group, and the lock list itself."""
    out = {"unlocked_groups", "locks_on"}
    for g in locked_groups(settings):
        out.update(LOCK_GROUPS[g][1])
    return out


def _dump(v: Any) -> Any:
    return v.model_dump() if hasattr(v, "model_dump") else v


def loosens(key: str, old: Any, new: Any, current: Settings | None = None) -> bool:
    old, new = _dump(old), _dump(new)
    if old == new:
        return False
    if key == "thresholds":
        # Every line covers or reports more when it is lower; raising any of them loosens.
        return any(float(new.get(k, 0)) > float(old.get(k, 0)) for k in set(old) | set(new))
    if key == "coverage":
        return COVERAGE_RANK.get(new, 0) < COVERAGE_RANK.get(old, 0)
    if key in FLAGS:
        return bool(old) and not bool(new)
    if key in LISTS:
        return bool(set(old or []) - set(new or []))
    if key == "watch_default":
        return WATCH_RANK.get(new, 0) < WATCH_RANK.get(old, 0)
    if key == "checked_apps":
        # Every app added is watched less, unless it was not watched at all before.
        was_off = set(current.exempt_apps) if current is not None else set()
        return bool(set(new or []) - set(old or []) - was_off)
    if key == "exempt_apps":
        # The opposite list: every app added is one less app watched.
        return bool(set(new or []) - set(old or []))
    if key in LOWER_IS_STRONGER:
        return float(new) > float(old)
    if key in HIGHER_IS_STRONGER:
        return float(new) < float(old)
    if key == "unlocked_groups":
        return bool(set(new or []) - set(old or []))
    if key == "person_overrides" and current is not None:
        return _overrides_loosen(current, old or {}, new or {})
    # protected_browser and anything without a rule: a change is not clearly stronger.
    return True


def _overrides_loosen(current: Settings, old: dict, new: dict) -> bool:
    """A person's effective values may not loosen, whether by a new override or by dropping one."""
    before = current.model_copy(update={"person_overrides": old})
    after = current.model_copy(update={"person_overrides": new})
    for pid in set(old) | set(new):
        b, a = effective(before, pid), effective(after, pid)
        for k in PERSON_KEYS:
            if loosens(k, getattr(b, k), getattr(a, k)):
                return True
    return False


def needs_code(current: Settings, changes: dict[str, Any]) -> list[str]:
    """The changed keys that loosen a locked setting; empty when every change is free."""
    locked = locked_keys(current)
    return [k for k, new in changes.items()
            if k in locked and loosens(k, getattr(current, k), new, current)]


LABELS = {
    "thresholds": "thresholds", "strict_mode": "strict mode", "coverage": "coverage",
    "cover_on_device": "blur on the device", "ask_server": "asking the server",
    "person_overrides": "a person's own values", "blocked_apps": "blocked apps", "risky_apps": "risky apps",
    "protected_browser": "the protected browser", "exempt_apps": "apps not watched",
    "watch_default": "how other apps are watched",
    "realtime_apps": "apps watched in real time", "checked_apps": "apps checked now and then",
    "checked_interval_s": "how often checked apps are looked at",
    "sample_interval_s": "the sample interval",
    "whole_screen_interval_s": "the whole-screen interval", "heartbeat_gap_minutes": "the silence alert",
    "max_pause_minutes": "the longest pause", "peek_seconds": "peek length", "peek_cooldown_hours": "peek cooldown",
    "image_retention_days": "image retention", "unlocked_groups": "the locks", "locks_on": "turning locks off",
}
