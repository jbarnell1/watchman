# Watchman API contract, v1

The seam between clients and the server. Change it in the same PR as the code on both sides.

Base URL: the household server, `https://<node>.<tailnet>.ts.net` (default node name `watchman`).
All bodies are JSON (UTF-8) unless noted. Times are RFC 3339 UTC. IDs are UUIDv4 strings.

## Auth

- **Devices** (Android app, Windows service, iOS app, each browser extension install) send
  `Authorization: Bearer <device_token>`. Tokens come from pairing and can be revoked.
- **People on the portal** (protected person, linked partners, admin) are identified one of two
  ways, chosen by `WATCHMAN_AUTH` (ADR-027):
  - `tailscale` (default): the `Tailscale-User-Login` header set by the Tailscale sidecar
    (`tailscale serve`). The server trusts that header only from the sidecar (loopback / the
    compose network).
  - `oidc`: a signed session cookie set by `/auth/callback` after an OpenID Connect sign-in with
    the household's own provider. The login is the ID token's `email` claim, lower-cased.
  Unknown logins get 403. In tests and local dev an `IdentityProvider` fake supplies the login.
  The device API below is the same in both modes.

## Errors

`{"error": {"code": "string", "message": "string"}}` with a 4xx/5xx status. Codes:
`unauthorized`, `forbidden`, `not_found`, `invalid`, `pairing_code_invalid`, `unlock_code_invalid`,
`rate_limited`, `locked_setting` (setting change needs a partner code).

## Device endpoints

### POST /api/v1/pair
No auth. Body:
```json
{"code": "K7Q-4MZ-P2D", "device": {"name": "Pixel 10 Pro XL", "platform": "android",
 "kind": "app", "app_version": "0.1.0"}}
```
`platform`: `android | windows | ios | chrome | edge | firefox | firefox-android | safari`.
`kind`: `app | service | extension`.
Response 200:
```json
{"device_id": "uuid", "token": "opaque string", "person_id": "uuid",
 "server_name": "Example household", "api_version": 1}
```
Pairing codes are created on the portal by the admin for a person, single use, valid 15 minutes.

Pairing codes are nine characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, shown as three groups
of three. The server ignores case, dashes and spaces when it compares them. The Devices page also
shows the code as a QR code of `<portal URL>/pair?code=<code>`. The Android app's Scan code button
reads that address directly; a phone camera opens it in the browser, where `/pair` (no sign-in, the
code is the secret) offers one button to the app link `watchman://pair?server=<portal URL>&code=<code>`.
Either way the app fills in the form and the person still taps Pair.

### POST /api/v1/heartbeat
Every 5 minutes, and immediately on any protection state change. Body:
```json
{"sent_at": "...", "app_version": "0.1.0", "booted_at": "...", "safe_mode": false,
 "battery_pct": 81, "queued_events": 0,
 "state": {"protection": true, "accessibility": true, "capture": "granted",
           "overlay": true, "device_admin": true, "extension": null, "vpn": true}}
```
`capture`: `granted | denied | not_applicable`. A state field a platform does not have is `null`.
Optional `metrics` (ADR-025): the work done since the last heartbeat, counts and milliseconds only:
```json
"metrics": {"window_s": 300,
  "counters": {"passes_run": 12, "passes_skipped": 48},
  "timings": {"inference_ms": {"n": 12, "sum": 204.5, "max": 31.2,
                                 "buckets": [0, 0, 0, 0, 9, 3, 0, 0, 0, 0, 0, 0]}}}
```
`buckets` has 12 counts: samples at or below 1, 2, 4, 8, 16, 32, 64, 128, 256, 512 and 1024 ms, then
everything slower. The server merges these per device per household day and drops anything not in
this shape; names are at most 48 characters and 64 of each kind. They are not kept in `last_state`.
Response 200: `{"policy_version": 7, "paused_until": null, "server_time": "..."}`.
The server compares `state` with the previous heartbeat: a field turning off raises a
`protection_changed` event and an alert; `safe_mode: true` after `false` raises `tamper`.
The client fetches `/api/v1/policy` when `policy_version` changed.

### GET /api/v1/policy/version
Response 200: `{"policy_version": 7, "paused_until": null}`. No writes. Devices poll it every 30 s
or so and fetch `/api/v1/policy` when the version or the pause changed, so settings apply within
about a minute instead of at the next five-minute heartbeat (ADR-031).

### GET /api/v1/policy
Response 200:
```json
{"version": 7,
 "thresholds": {"cover": 0.85, "flag": 0.85, "detector_cover": 0.55, "ask": 0.60, "sure": 0.97},
 "strict_mode": true,
 "cover_on_device": true,
 "ask_server": true,
 "coverage": "explicit",
 "explicit_labels": ["FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
                     "ANUS_EXPOSED", "BUTTOCKS_EXPOSED"],
 "risky_apps": ["com.google.android.youtube", "org.mozilla.firefox"],
 "blocked_apps": ["com.android.chrome"],
 "exempt_apps": [],
 "follow_scroll": false,
 "watch_default": "realtime",
 "realtime_apps": ["com.instagram.android"],
 "checked_apps": [],
 "checked_interval_s": 60,
 "protected_browser": "org.mozilla.firefox",
 "sample_interval_s": 180,
 "whole_screen_interval_s": 5,
 "paused_until": null,
 "known_bad_hashes": ["9f3a0c1e5b7d2a46"],
 "models_manifest_url": "/api/v1/models/manifest"}
```
Devices cover anything at or above `ask` and ask `/api/v1/judge` unless the score is at or above
`sure` or the hash is known bad; a safe answer lifts the blur and nothing is reported (ADR-022,
ADR-033). Absent `sure` means `cover`, the earlier behaviour.

`coverage` is `explicit`, `revealing` or `strict`; `explicit_labels` is the detector label set it
means, which clients use instead of the manifest's list when present (ADR-021).
`cover_on_device: false` is report only: the device blurs nothing and reports scores at or above
`flag` as `image_flagged`; `ask_server: false` turns the ask band off so the device decides alone
(ADR-022). Both default to true when absent. The policy is per person: the server merges the
household's settings with that person's own values before answering, so two people on one server
can run different postures.

`exempt_apps` are apps not watched at all (ADR-030): while one is in front nothing is judged,
covered or reported. Android applies it today.

`follow_scroll` (ADR-040, default false): true moves blurs with a scroll the app reports before the
next check; false or absent leaves them in place until the next check. `strict_mode` (ADR-040): in `risky_apps`, a picture
that appears is covered at once from its position alone and stays covered until it is judged.
Android applies both.

Watch levels (ADR-037): an app in `exempt_apps` is not watched; else one in `checked_apps` is
checked, else one in `realtime_apps` is real time, else it gets `watch_default` (`realtime` or
`checked`). Real time is today's behaviour: every picture judged as it appears. Checked means no
per-picture passes, only a whole-screen look every `checked_interval_s` seconds (stretched by the
power budget) that still covers and reports what it finds. Android applies it today.

App identifiers are platform specific (Android package names, Windows process names, iOS bundle
IDs); clients ignore entries that are not theirs. `known_bad_hashes` are 64-bit dHash hex strings.

### POST /api/v1/events
Batched, idempotent by `id` (the server ignores IDs it has). Max 200 events per call. Body:
```json
{"events": [{
  "id": "uuid", "type": "image_covered", "occurred_at": "...",
  "app": "com.google.android.youtube", "url": null, "title": null,
  "action": "covered",
  "scores": {"classifier": 0.93, "detector": 0.71, "labels": ["FEMALE_BREAST_EXPOSED"]},
  "region": {"w": 48, "h": 48, "screen_w": 1440, "screen_h": 3120},
  "hash": "9f3a0c1e5b7d2a46",
  "has_image": true,
  "detail": {}
}]}
```
Response 200: `{"accepted": ["uuid"], "duplicates": ["uuid"], "upload": ["uuid"], "rejected":
[{"id": "uuid", "reason": "missing type"}]}`. `upload` lists events whose image the server wants (it
may skip images for hashes it already holds, and for look-alikes of an image the same device
reported in the last 10 minutes, which it records with image status `repeat` and severity info,
ADR-035). A malformed event (no `id`, `type` or `occurred_at`,
or an unknown `type`) is listed in `rejected` with the reason and skipped; the rest of the batch is
taken. Clients drop rejected events from their queue rather than send them again.

Event `type` values and their `action`:

| type | when | action |
|---|---|---|
| `image_covered` | an image was blurred or held back in real time | `covered` |
| `screen_blocked` | the full block screen was shown | `blocked` |
| `image_flagged` | scored over `flag` but under `cover` (accountability only) | `flagged` |
| `url_blocked` | navigation to a blocked domain or category | `blocked` |
| `search_flagged` | a search matched the word list | `flagged` |
| `app_blocked` | a blocked app was opened | `blocked` |
| `protection_changed` | optional from clients; the server derives its own from heartbeats (ADR-015) and only those alert; `detail` has `{"field","from","to"}` | `none` |
| `tamper` | safe mode boot, capture declined, extension removed, service killed, clock jump | `none` |
| `pause_started` / `pause_ended` | an unlock code took effect / expired | `none` |
| `daily_stats` | once a day: screens examined, images scored, minutes protected | `none` |

### PUT /api/v1/events/{id}/image
Body: `image/jpeg`, the crop, longest side at most 512 px, quality 85. 204 on success. The server
runs the second opinion, stores heavy and medium blurs, and deletes the original (ADR-004).

### POST /api/v1/judge
Body: `image/jpeg`, a crop, longest side at most 512 px. Response 200:
`{"score": 0.12, "verdict": "safe"}` or `"explicit"` (score at or above the `second_opinion` threshold).
Scored in memory, nothing stored, no event. Devices use it for crops between `thresholds.ask` and
`thresholds.cover`: cover first, ask, unblur on `safe`; on `explicit` keep it covered and report an
`image_covered` event as usual (ADR-022).

### POST /api/v1/unlock
Body: `{"code": "482-913"}`. Response 200: `{"paused_until": "..."}`. Codes are single use, issued
by a partner, and carry their own length (capped by the `max_pause_minutes` setting).

### POST /api/v1/unlock/request
Body: `{"minutes": 30, "reason": "work upload"}`. Emails each email-only partner a code and
notifies linked partners on the portal. Response 202.

### GET /api/v1/models/manifest
The contents of `models/manifest.json` with each `url` rewritten to `/api/v1/models/<file>` so
clients download weights from their own server. `GET /api/v1/models/<file>` serves the file.

## Portal (HTML, Tailscale or OIDC identity)

Server-rendered pages under `/`. Roles: `admin`, `protected`, `partner`. A login can hold several.

| Path | Who | What |
|---|---|---|
| `/` | all | Timeline for the people this login can see |
| `/review` | partner | Items needing review; mark "talked about" or "false alarm" |
| `/review/{event_id}` (POST) | partner | `state=talked`, `false_alarm` or `open` |
| `/peek/{event_id}` (POST) | linked partner | JSON `{url, seconds, next_peek_at}`; the URL is one-time, valid 5 s; 429 during the cooldown |
| `/images/{event_id}/heavy` | anyone who may see the person | the heavy blur |
| `/images/{event_id}/medium?t=` | linked partner, with a peek token | the medium blur, once |
| `/codes` | partner | Issue a pause code (length up to the cap) or a settings code (ADR-016) |
| `/devices` | all who see the person | Devices, last heartbeat, state; admin and protected create pairing codes; admin revokes (alerts) |
| `/settings?area=general|protection|email|people` | admin | All settings; locked ones need a partner settings code once a device is paired |
| `/setup` | first login | First-run wizard |
| `/auth/login?next=` | anyone (OIDC mode only) | Starts the sign-in: redirect to the provider with state, nonce and a PKCE challenge |
| `/auth/callback` | the provider's redirect (OIDC mode only) | Checks state, redeems the code, validates the ID token, sets the session cookie, goes to `next` |
| `/auth/logout` (GET or POST) | anyone (OIDC mode only) | Clears the session cookie on this browser |
| `/healthz` | none | `{"ok": true, "version": "0.1.0", "models": 5, "second_opinion": "loaded"}`: liveness, the server version, how many manifest files were present with the right sha256 at startup, and whether the second opinion model is loaded (`"missing"` means the fake is in use). No personal data |

## Image hash (shared by every client and the server)

64-bit difference hash: convert to grayscale with `0.299 R + 0.587 G + 0.114 B`, downscale to 9 wide
by 8 high by area averaging (each output cell is the mean of the source pixels it covers, with
fractional coverage at cell edges), round each cell to the nearest integer (half up), and for each
row set bit `row*8 + col` when cell `col` is brighter than cell `col + 1`. Encode big-endian as 16
lowercase hex digits. The reference implementation is `models/watchman_models/dhash.py`; every
client's implementation must reproduce `models/test-vectors/dhash/vectors.json` exactly. Two hashes
match when their Hamming distance is 6 or less, which absorbs the small differences left by each
platform's colour conversion and scaling. A hash with fewer than 40 decisive bits (bits whose two
cells differ by 3 grey levels or more) comes from a flat crop and matches only exactly, both
against `known_bad_hashes` and in a client's own verdict cache (ADR-035).
