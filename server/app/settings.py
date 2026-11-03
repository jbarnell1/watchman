"""Household settings: every value that differs between households, with its default.

Stored one row per key in the settings table as JSON. `Settings` is the typed view of the
whole set. Which keys need a partner code to loosen is services/locks.py (ADR-028; tightening is free).
Keys in POLICY_KEYS bump the policy version so devices refetch /api/v1/policy.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class Thresholds(BaseModel):
    # Defaults from real phone screenshots (ADR-021): explicit avatars scored 0.93 to
    # 0.95, text and UI windows up to 0.82. Flag equals cover by default: whatever is worth sending
    # to the server is covered on the device first; the second opinion sorts explicit from review.
    cover: float = 0.85
    flag: float = 0.85
    # Below cover but at or above ask, a device covers the image and asks the server's bigger model;
    # the server answers in memory and stores nothing unless the answer is explicit (ADR-022).
    ask: float = 0.60
    # At or above cover but below sure, a device still covers first and asks the server, lifting the
    # blur on a safe answer; only at sure or above (or a known-bad hash) does it report without asking.
    # On the first day of real use the phone and the server disagreed on 18 of 63 covers (ADR-033).
    sure: float = 0.97
    detector_cover: float = 0.55
    second_opinion: float = 0.70
    # A confirmed picture joins the known-bad list (covered by hash on every device, no model run)
    # only at this second opinion or above. At 0.70 Instagram's buttons on a tan reel joined it and
    # pulled their look-alikes in after them (ADR-035).
    known_bad: float = 0.90
    # A picture the device covered reaches partners only at this second opinion or above, when
    # covered_reports is high_score (ADR-046). The blur already did its job; what is left to talk
    # about is a picture that was unmistakable.
    covered_report: float = 0.90


class Smtp(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    from_address: str = ""
    starttls: bool = True


class Settings(BaseModel):
    household_name: str = "Watchman household"
    timezone: str = "UTC"
    digest_day: str = "sunday"          # monday ... sunday
    digest_time: str = "19:00"          # HH:MM in the household time zone
    image_retention_days: int = 7
    event_retention_days: int = 365
    peek_seconds: int = Field(default=3, ge=1, le=3)   # the light-blur peek, three seconds at most (ADR-031)
    # Between two peeks by the same viewer. A day by default (ADR-031); seconds so a household that
    # is testing can set it short (ADR-048).
    peek_cooldown_seconds: int = Field(default=86400, ge=5)
    # The protected person may peek at their own covered pictures, to tell true from false
    # positives while a household tunes. Off by default, own peeks are not reported, and turning it
    # on is loosening (ADR-048).
    self_peek: bool = False
    thresholds: Thresholds = Field(default_factory=Thresholds)
    strict_mode: bool = True
    # Which detector labels count as explicit: explicit (bare only), revealing (adds underwear and
    # swimwear), strict (adds bare belly and armpits). See services/policy.py for the label sets.
    coverage: str = "explicit"
    # What a device does with what it finds. cover_on_device off is report only: nothing is blurred
    # anywhere and scores at or above flag are reported. ask_server off means the device decides
    # alone and the ask band is unused (ADR-022). The settings page offers presets that fill these.
    cover_on_device: bool = True
    ask_server: bool = True
    # Blurs move with a scroll the app reports, before the next check confirms them (ADR-032).
    # Off by default: the owner found it not effective; blurs then wait for the next check (ADR-040).
    follow_scroll: bool = False
    # What partners see of pictures the device covered (ADR-046): all puts every one in review;
    # high_score keeps them on the record but reports only those the second opinion scores at
    # thresholds.covered_report or above. Pictures that were not covered are reported as before.
    covered_reports: str = "high_score"   # high_score | all
    # A screen mostly covered by sure covers gives way to the full block screen (ADR-036). Off by
    # default: it stopped a health video on 2026-11-03. When on, the block screen offers Continue
    # anyway, which keeps the blurs and alerts partners (ADR-047).
    block_whole_screen: bool = False
    risky_apps: list[str] = Field(default_factory=lambda: [
        "com.google.android.youtube", "org.mozilla.firefox", "com.reddit.frontpage",
    ])
    blocked_apps: list[str] = Field(default_factory=lambda: [
        "com.android.chrome", "com.brave.browser", "com.opera.browser", "com.microsoft.emmx",
        "com.duckduckgo.mobile.android", "com.sec.android.app.sbrowser",
    ])
    protected_browser: str = "org.mozilla.firefox"
    # Not watched (ADR-030): while one of these apps is in front nothing is judged, covered or
    # reported. Adding one is loosening, so with locks on it needs a partner code.
    exempt_apps: list[str] = Field(default_factory=list)
    # Watch levels (ADR-037). Real time judges every picture as it appears and blurs it; checked
    # looks at the whole screen every checked_interval_s and still blurs and reports what it finds,
    # for a fraction of the battery. An app's level: Not watched, else checked_apps, else
    # realtime_apps, else watch_default. Moving an app to a lighter level is loosening.
    watch_default: str = "realtime"     # realtime | checked
    realtime_apps: list[str] = Field(default_factory=lambda: [
        "com.instagram.android", "com.google.android.youtube", "org.mozilla.firefox", "com.reddit.frontpage",
        "com.zhiliaoapp.musically", "com.twitter.android", "com.facebook.katana", "com.snapchat.android",
        "com.pinterest", "com.tumblr",
    ])
    checked_apps: list[str] = Field(default_factory=list)
    checked_interval_s: int = Field(default=60, ge=10, le=900)
    sample_interval_s: int = 180
    whole_screen_interval_s: int = 5
    # Per-person protection settings: person id to {key: value} for keys in PERSON_KEYS. A person
    # with no entry uses the household values; effective() merges them for /api/v1/policy.
    person_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Lock groups the household chose to leave unlocked (services/locks.py, ADR-028). Every other
    # group needs a partner code to loosen once a device is paired; tightening is always free.
    unlocked_groups: list[str] = Field(default_factory=list)
    # Off while a household sets up and tests: nothing needs a partner code. Turning locks on is
    # free; turning them off again is loosening and needs one. Every change is in the digest.
    locks_on: bool = False
    max_pause_minutes: int = 60
    heartbeat_gap_minutes: int = 30
    smtp: Smtp = Field(default_factory=Smtp)
    ntfy_url: str = ""                  # optional push for the household admin
    adguard_url: str = ""               # informational, shown on the devices page
    second_opinion_device: str = "cpu"  # cpu | cuda
    setup_complete: bool = False
    policy_version: int = 1

    @model_validator(mode="before")
    @classmethod
    def _cooldown_in_seconds(cls, data: Any) -> Any:
        # Stored settings from before ADR-048 have the cooldown in hours.
        if isinstance(data, dict) and "peek_cooldown_hours" in data:
            data = dict(data)
            hours = data.pop("peek_cooldown_hours")
            data.setdefault("peek_cooldown_seconds", int(hours) * 3600)
        return data


LOCKED_KEYS = frozenset({
    "block_whole_screen", "follow_scroll", "covered_reports",
    "watch_default", "realtime_apps", "checked_apps", "checked_interval_s",
    "image_retention_days", "peek_seconds", "peek_cooldown_seconds",
    "self_peek", "thresholds", "strict_mode", "coverage",
    "cover_on_device", "ask_server", "person_overrides", "unlocked_groups", "locks_on", "exempt_apps",
    "risky_apps", "blocked_apps", "protected_browser", "sample_interval_s",
    "whole_screen_interval_s", "max_pause_minutes", "heartbeat_gap_minutes",
})

POLICY_KEYS = frozenset({
    "block_whole_screen", "follow_scroll",
    "watch_default", "realtime_apps", "checked_apps", "checked_interval_s",
    "exempt_apps",
    "thresholds", "strict_mode", "coverage", "cover_on_device", "ask_server", "person_overrides",
    "risky_apps", "blocked_apps", "protected_browser", "sample_interval_s", "whole_screen_interval_s",
})

# The protection keys a person may have their own values for.
PERSON_KEYS = frozenset({
    "block_whole_screen", "follow_scroll",
    "watch_default", "realtime_apps", "checked_apps", "checked_interval_s",
    "exempt_apps",
    "thresholds", "strict_mode", "coverage", "cover_on_device", "ask_server", "risky_apps", "blocked_apps",
    "protected_browser", "sample_interval_s", "whole_screen_interval_s",
})

# Starting points for the settings page; each fills a few fields, nothing is saved until Save.
PRESETS: dict[str, dict[str, Any]] = {
    "report": {"cover_on_device": False, "ask_server": False, "thresholds.flag": 0.85},
    "cover": {"cover_on_device": True, "ask_server": False, "thresholds.flag": 0.85},
    "cover_and_ask": {"cover_on_device": True, "ask_server": True, "thresholds.flag": 0.85, "thresholds.ask": 0.60},
}
PRESET_LABELS = {"report": "Report only", "cover": "Cover on the device", "cover_and_ask": "Cover and ask this server"}


def effective(settings: Settings, person_id: str | None) -> Settings:
    """The household settings with one person's overrides applied."""
    overrides = settings.person_overrides.get(person_id or "", {})
    if not overrides:
        return settings
    merged = settings.model_dump()
    merged.update({k: v for k, v in overrides.items() if k in PERSON_KEYS})
    return Settings.model_validate(merged)

# Keys the settings page may edit, in page order. policy_version is internal.
EDITABLE_KEYS: tuple[str, ...] = tuple(
    k for k in Settings.model_fields if k not in {"policy_version", "setup_complete", "person_overrides"}
)


def coerce(key: str, value: Any) -> Any:
    """Validate one value against the Settings model and return it in its typed form."""
    if key not in Settings.model_fields:
        raise KeyError(key)
    model = Settings.model_validate({key: value})
    return getattr(model, key)
