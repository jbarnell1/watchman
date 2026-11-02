from __future__ import annotations

from app.clock import iso
from app.services.context import Context
from app.settings import effective

# NudeNet labels that count as explicit at each coverage level. The policy carries the set so
# every client covers the same things; the manifest's own list is only the fallback.
EXPLICIT = ["FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED",
            "BUTTOCKS_EXPOSED"]
REVEALING = EXPLICIT + ["FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED", "BUTTOCKS_COVERED", "ANUS_COVERED",
                        "MALE_BREAST_EXPOSED"]
STRICT = REVEALING + ["BELLY_EXPOSED", "ARMPITS_EXPOSED"]
COVERAGE_LABELS = {"explicit": EXPLICIT, "revealing": REVEALING, "strict": STRICT}


def explicit_labels(coverage: str) -> list[str]:
    return COVERAGE_LABELS.get(coverage, EXPLICIT)


def paused_until(ctx: Context, person_id: str) -> str | None:
    pause = ctx.codes.active_pause(person_id)
    return pause["ends_at"] if pause else None


def policy_for(ctx: Context, person_id: str) -> dict:
    s = effective(ctx.settings.load(), person_id)
    return {
        "version": s.policy_version,
        "thresholds": {
            "cover": s.thresholds.cover,
            "flag": s.thresholds.flag,
            "detector_cover": s.thresholds.detector_cover,
            "ask": s.thresholds.ask,
        },
        "strict_mode": s.strict_mode,
        "cover_on_device": s.cover_on_device,
        "ask_server": s.ask_server,
        "coverage": s.coverage,
        "explicit_labels": explicit_labels(s.coverage),
        "risky_apps": s.risky_apps,
        "blocked_apps": s.blocked_apps,
        "exempt_apps": s.exempt_apps,
        "protected_browser": s.protected_browser,
        "sample_interval_s": s.sample_interval_s,
        "whole_screen_interval_s": s.whole_screen_interval_s,
        "paused_until": paused_until(ctx, person_id),
        "known_bad_hashes": ctx.events.known_bad(person_id),
        "models_manifest_url": "/api/v1/models/manifest",
        "server_time": iso(ctx.clock.now()),
    }
