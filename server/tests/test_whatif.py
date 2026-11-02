from __future__ import annotations

import json
import re
import uuid

from app.clock import iso
from app.services.whatif import Tally, judge, replay
from app.settings import Thresholds
from tests.conftest import ADMIN, PARTNER, PROTECTED, as_login, jpeg_bytes

NOW = Thresholds()


def ev(classifier: float, *, app: str = "a", detector: float | None = None, second: float | None = None,
       type_: str = "image_covered", labels: list[str] | None = None, review_state: str = "open",
       image_status: str = "stored") -> dict:
    scores = {"classifier": classifier, "labels": labels or []}
    if detector is not None:
        scores["detector"] = detector
    return {"type": type_, "app": app, "scores": json.dumps(scores), "second_opinion": second,
            "review_state": review_state, "image_status": image_status}


def test_sure_and_above_is_covered_and_reported_without_asking():
    assert judge(ev(0.98), NOW) == Tally(covered=1, reported=1, review=1)


def test_ask_band_reports_only_an_explicit_answer():
    assert judge(ev(0.8, second=0.9), NOW) == Tally(covered=1, asked=1, reported=1, explicit=1, review=1)
    assert judge(ev(0.8, second=0.2), NOW) == Tally(covered=1, asked=1)
    # No second opinion on record: it was reported, so it still counts as reported.
    assert judge(ev(0.8, second=None), NOW) == Tally(covered=1, asked=1, reported=1, review=1)


def test_under_ask_is_nothing_and_the_detector_raises_the_answer():
    assert judge(ev(0.4), NOW) == Tally()
    # Detector over its line covers; the score (max) is under ask, so it reports without asking.
    low = Thresholds(ask=0.9, detector_cover=0.55)
    assert judge(ev(0.1, detector=0.6), low) == Tally(covered=1, reported=1, review=1)


def test_report_only_and_no_asking():
    assert judge(ev(0.9, second=0.2), NOW, cover_on_device=False) == Tally(reported=1)
    assert judge(ev(0.9, second=0.2), NOW, ask_server=False) == Tally(covered=1, reported=1, review=1)


def test_repeats_carry_no_review_and_false_alarms_are_counted():
    assert judge(ev(0.98, image_status="repeat"), NOW).review == 0
    assert judge(ev(0.98, review_state="false_alarm"), NOW).false_alarms == 1
    assert judge(ev(0.8, second=0.2, review_state="false_alarm"), NOW).false_alarms == 0


def test_replay_counts_per_app_and_total_and_skips_other_types():
    events = [
        ev(0.98, app="reels", second=0.3, review_state="false_alarm"),
        ev(0.98, app="reels", second=0.9),
        ev(0.8, app="browser", second=0.9),
        ev(0.99, app="browser", labels=["known_bad"]),
        {"type": "url_blocked", "app": "browser", "scores": None},
    ]
    r = replay(events, NOW, Thresholds(sure=0.99))
    by_app = {a.app: a for a in r.apps}
    assert set(by_app) == {"reels", "browser"}
    reels = by_app["reels"]
    assert reels.current == Tally(covered=2, reported=2, explicit=1, review=2, false_alarms=1)
    # Raising sure past 0.98 sends both to the server: the safe one (a known false alarm) is dropped.
    assert reels.proposed == Tally(covered=2, asked=2, reported=1, explicit=1, review=1)
    assert by_app["browser"].known_bad == 1 and by_app["browser"].events == 2
    assert r.total.known_bad == 1 and r.total.events == 4
    assert r.total.current.reported == 3 and r.total.proposed.reported == 2


def test_replay_reads_sqlite_style_rows_with_dict_scores():
    rows = [{"type": "screen_blocked", "app": None, "scores": {"classifier": 0.97}, "second_opinion": None,
             "review_state": "open", "image_status": "none"}]
    r = replay(rows, NOW, NOW)
    assert r.apps[0].app == "(no app)" and r.total.current.reported == 1


def _post(client, headers, clock, classifier: float) -> str:
    ev_id = str(uuid.uuid4())
    r = client.post("/api/v1/events", headers=headers, json={"events": [{
        "id": ev_id, "type": "image_covered", "occurred_at": iso(clock.now()), "app": "com.instagram.android",
        "action": "covered", "scores": {"classifier": classifier}, "has_image": True,
        "region": {"w": 48, "h": 48, "screen_w": 1440, "screen_h": 3120}}]})
    assert r.status_code == 200, r.text
    return ev_id


def _cell(html: str, measure: str) -> str:
    m = re.search(rf'<tr class="total">.*?data-measure="{measure}">(.*?)</td>', html, re.S)
    assert m, measure
    return re.sub(r"<[^>]+>", "", m.group(1)).replace("&rarr;", "->").strip()


def test_whatif_page_moves_reported_to_asked_when_sure_is_raised(ctx, client, household, device, clock, opinion):
    _, headers = device
    ev_id = _post(client, headers, clock, 0.98)
    opinion.fixed = 0.3
    assert client.put(f"/api/v1/events/{ev_id}/image", headers=headers, content=jpeg_bytes()).status_code == 204
    person = household["person_id"]

    r = client.get(f"/whatif?person={person}", headers=as_login(ADMIN))
    assert r.status_code == 200 and "What if" in r.text and "com.instagram.android" in r.text
    assert _cell(r.text, "reported") == "1" and _cell(r.text, "asked") == "0"

    r = client.get(f"/whatif?person={person}&sure=0.99&days=1", headers=as_login(PARTNER))
    assert r.status_code == 200
    assert _cell(r.text, "asked") == "0 -> 1 (+1)"
    assert _cell(r.text, "reported") == "1 -> 0 (-1)"
    assert 'value="0.99"' in r.text


def test_whatif_is_for_the_admin_and_partners_only(ctx, client, household, device):
    person = household["person_id"]
    assert client.get(f"/whatif?person={person}", headers=as_login(PROTECTED)).status_code == 403
    r = client.get("/settings?area=protection", headers=as_login(ADMIN))
    assert r.status_code == 200 and 'href="/whatif' in r.text
