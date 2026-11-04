# Runbook

Operating a Watchman server. For installing one, see `docs/self-host.md`.

## Running locally without Docker

```bash
cd server
uv sync
WATCHMAN_DATA_DIR=./data uv run python -m app.main          # 127.0.0.1:8030, real identity headers
uv run python -m app.devserver                              # 127.0.0.1:8035, fakes and seeded data
```

The dev server switches identity with `/dev/as/owner`, `/dev/as/sam` (linked partner) and
`/dev/as/pat` (email partner). Its data lives in a temp folder and is gone on exit.

## Tests

```bash
cd server && uv run pytest        # fakes only: in-memory SQLite, settable clock, recording mailer
cd server && uv run ruff check app tests
```

## Where things are

| What | Where |
|---|---|
| Database | `data/watchman.sqlite3` (WAL mode) |
| Blurred images, AES-GCM sealed | `data/images/<event_id>.{heavy,medium}.bin` |
| Image key | `data/keys/images.key`, mode 600, created on first start |
| Models | `models/cache/` on the host, `/models` in the container, read-only |
| Settings | the `settings` table; edited on the portal; every change is listed in the next digest |

## Jobs

One in-process loop runs every minute (`app/services/jobs.py`):

- heartbeat watch: a device silent past `heartbeat_gap_minutes` (default 30) raises one `tamper`
  event and alerts, once per gap;
- digest: sent when the configured local day and time has passed since the last digest, or since
  the person was added for the first one;
- retention: blurred images older than `image_retention_days`, events and heartbeats older than
  `event_retention_days`;
- reminders: devices still off or silent are announced again every `realert_hours` (ADR-050);
- not judging: a device on and watched for 15 minutes in an hour with nothing judged (ADR-051);
- record check, hourly: the database against the record; new problems alert everyone (ADR-052);
- watching: every 5 minutes, the records of households this server watches (ADR-054).

## When something is wrong

| Symptom | Look at |
|---|---|
| Portal says "Not signed in" | Tailscale mode: the request did not come through the Tailscale sidecar. Either mode: the login is not a known admin, protected person or partner. On a fresh server the first visitor becomes admin. |
| OIDC: "Sign-in failed" or "Sign-in unavailable" | The server log says why (issuer mismatch, wrong client secret, redirect URL not registered, no `email` claim, clock skew over a minute). The redirect URL registered at the provider must equal `WATCHMAN_OIDC_REDIRECT_URL` exactly. |
| Device gets 401 | Its token was revoked on the Devices page, or the data directory was replaced. Pair again. |
| No digest arrived | Settings, Email must have a host and from address. Use "Send the digest now" on Settings, General and check the server log for the SMTP error. |
| Alerts for a silent device while it is on | The device is not reaching the server: Tailscale on the device, or the tailnet ACL grant for the watchman node. |
| Second opinion scores are all 0 | No `manifest.json` in the models directory, so the fake is in use. The log says so at startup. Run `models/fetch.py`. |
| Images do not open after a restore | `data/keys/images.key` was not restored with the images. |

## Restore

Stop the stack, replace `./data` with the backup copy (database, images and keys), start it. The
Tailscale state volume can be recreated; the node then needs a new auth key and partners' shares
are unaffected because the node name is the same.

## Releasing

Tag `vX.Y.Z` on main. CI builds the server image, the extension zips, the Windows installer and the
Android APK into a GitHub release. Households update by pulling the new compose image; phones with
Obtainium pick up the APK from the release page.

## Windows

```bash
cd clients/windows
dotnet test                                   # Core tests, including the dHash vectors
dotnet build
src/Watchman.Helper/bin/Debug/net10.0-windows10.0.19041.0/WatchmanHelper.exe selftest ../../models/cache/nudenet-640m.onnx
```

Install from the MSI CI builds (Actions, windows, artifact `watchman-windows`), then pair from an
elevated prompt:

```
"C:\Program Files\Watchman\service\WatchmanService.exe" pair --server https://watchman.<tailnet>.ts.net --code K7Q-4MZ-P2D
"C:\Program Files\Watchman\service\WatchmanService.exe" status
```

The service (SYSTEM) keeps pairing, policy, the model and the queue under `C:\ProgramData\Watchman`
(DPAPI-sealed). It starts `WatchmanHelper.exe` in the signed-in session and restarts it within 15 s
if it goes away; the helper's tray icon shows the status and takes partner codes. Stopping the
service or killing the helper shows up as capture denied on the next heartbeat and as a silent
device after the gap, and partners are told.
## Browser extension

```bash
cd clients/extension
npm ci
npm test                 # unit tests, including the dHash vectors
node build.mjs           # dist/chrome and dist/firefox (--zip for release zips)
npm run e2e              # real Chromium against the dev server; needs `npx playwright install chromium`
```

Load `dist/chrome` as an unpacked extension (chrome://extensions, Developer mode) or
`dist/firefox/manifest.json` as a temporary add-on (about:debugging). Pair it from its Settings page
with a code from the portal's Devices page. The service worker console shows `watchmanDiagnose()`
for engine problems.

## Android

CI builds one APK per CPU architecture; phones take `app-arm64-v8a-*.apk` (about 50 MB, against
141 MB for the universal one). Locally: `flutter build apk --release --split-per-abi`.

Screens are taken through the accessibility service's screenshot API (ADR-026): no recording
indicator, no consent dialog. If the status page says protection is off, accessibility is the
switch to check (Settings, Accessibility, Watchman; on a fresh install allow restricted settings
from App info first). The watcher reuses a frame younger than one second and skips secure windows.

```bash
cd clients/android
flutter test                                   # widget tests against a fake bridge
flutter build apk --debug                      # build/app/outputs/flutter-apk/app-debug.apk
(cd android && ./gradlew :app:testDebugUnitTest)   # Kotlin: dHash vectors, decisions, queue, YOLO decode
```

Install the APK (`adb install -r build/app/outputs/flutter-apk/app-debug.apk`), pair with a code
from the portal, then follow the walkthrough: accessibility (on Android 13 and later, open App info
and allow restricted settings first), draw over other apps, device admin, unrestricted battery.
There is no screen capture consent: the accessibility service takes the screenshots (ADR-026).
The status page shows every part and the last heartbeat.

## Homelab deployment notes

The server runs on any always-on box; the second opinion is cheap on CPU. A dashboard tile
belongs with the admin tools rather than the family's everyday links. Each Windows PC uses its
own GPU through DirectML for the real-time pass.

### Health checks: Gatus and Homepage

`GET /healthz` needs no sign-in and carries no personal data:

```json
{"ok": true, "version": "0.1.0", "models": 5, "second_opinion": "loaded"}
```

`models` is how many files from the manifest were in the models folder with the right sha256 when
the server started (5 when everything is fetched); `second_opinion` is `missing` when the fake is in
use. Both are read at startup, so restart the server after fetching models.

- Gatus: `deploy/gatus/watchman.yaml` checks `${WATCHMAN_URL}/healthz` every minute for status 200
  and `[BODY].ok == true`. Set `WATCHMAN_URL=https://watchman.<tailnet>.ts.net` in the Gatus
  container's environment (Gatus expands `${...}` in its config), then merge the endpoint into the
  Gatus config or mount the file into its config directory and restart Gatus.
- Homepage: `deploy/homepage/services.yaml` is a Watchman tile for the Admin group with a
  `customapi` widget showing status, version and models from `/healthz`. Set
  `HOMEPAGE_VAR_WATCHMAN_URL=https://watchman.<tailnet>.ts.net` in the Homepage container's
  environment, merge the entry under the existing `Admin` group in `services.yaml`, and restart.

If they run on another machine they reach the portal over the tailnet: that machine must
be allowed to reach the watchman node on 443 in the tailnet policy, and the container must resolve
the MagicDNS name. Check from inside the container (`docker exec gatus wget -qO- $WATCHMAN_URL/healthz`);
if the name does not resolve there, use the node's tailnet IP in the URL. A red Gatus check, or `models` below 5 on
the tile, means the server is down or a model file is missing or does not match the manifest:
run `models/fetch.py` and restart the server.

The server serves `models/cache/manifest.json`, which `fetch.py` writes only after every file
checks out. After pulling a change to `models/manifest.json`, run `models/fetch.py` after pulling
and restart the server, or devices keep seeing the old manifest.

## iOS

Built only in CI (`ios.yml`): `swift test` for `clients/ios/WatchmanCore`, then XcodeGen and an
unsigned simulator build of the app with the Safari extension. Device builds go through TestFlight from
your Apple developer account: set `DEVELOPMENT_TEAM` in `clients/ios/project.yml`, add the
app group `group.app.watchman` to the two identifiers, and archive from Xcode or a signed CI job.
On the phone: pair, then turn the Watchman extension on under Settings, Apps, Safari, Extensions,
allowed on every website. Screen Time locks are set by the
partner with their own passcode.

## Locks

Settings, Protection, Locks (ADR-028). Every loosening emails everyone at once, locks on or off
(ADR-050). Once a partner sets up a key, locks are on for good and loosening waits for a partner's
signed approval instead of a code (ADR-053). Locks start off. While they are off, any setting can change
and each change is listed in the next digest. Turn them on when setup and testing are done. With
locks on, tightening anything is still free; loosening anything in a checked group (raising a
threshold, removing a blocked app, a longer pause, removing a partner, unchecking a group, turning
locks off) needs a settings code from a partner, made on the partner's Codes page.

## Posture

Settings, Protection. Two knobs decide what a device does with what it finds: "Blur on the
device" (off is report only) and "Ask this server about unsure images" (off means the device
decides alone). The three preset buttons fill the fields; Save applies them. "Settings for" at the
top switches between the household's values and one person's own; a person's own values are the
differences from the household, and "Use the household's values" drops them (ADR-024).

## Work metrics

The Devices page shows, per device, today's work: each step's runs, median, 95th percentile and
slowest time, and every counter (ADR-025). The times come from coarse buckets, so read them as
bounds. Raw logs, newest last, one JSON object per line:

- Windows: `%LocalAppData%\Watchman\metrics.jsonl` (and `.1`), a `sweep` line every sweep with
  capture and fingerprint time, scroll in pixels and passes run and skipped, and a `minute` line.
- Android: `files/metrics.jsonl` in the app's data, and Logcat: `adb logcat -s WatchmanMetrics`.
- Extension: set the debug flag from the service worker console,
  `chrome.storage.local.set({watchmanDebug: true})`, and reload the extension; each judge is then
  logged with its time and reason. The popup shows the day's line.

What to look for: passes skipped against run on Windows (a still desktop should be nearly all
skipped, a scrolled page mostly skipped), `cache_hits` and `frames_reused` on the phone, and the 95th
percentile of `classify_ms` and `screenshot_ms` on battery.

## Workload and battery

Every client judges a picture once per place and skips what has not changed (ADR-023). Things to
know when something looks slow or hungry:

- Extension: an image far down a long page is judged only as it comes within a screen of view.
  Playing videos are sampled once per whole-screen interval (Settings, Timing; 5 s by default); a
  paused video costs one hash per interval and nothing else. `canvas` (games, maps, WebGL) is never looked at. A video served cross-origin without CORS
  cannot be sampled and is left alone (the element gets `data-wm-video="unsampleable"`).
- Windows: the tray tooltip shows passes run and skipped for the last sweep. A still desktop should
  show 0 run. In motion mode (video or game in front) one whole-frame pass runs per interval.
- Android: the status page shows the interval in force and why (plugged, battery, low battery).
  Under 20 percent the on-device detector is off and anything the classifier is unsure about is
  covered and sent to the server, so coverage does not drop; false blurs may rise until charged.

## Tuning on your own screens

No explicit material is collected. Three tools, all numbers only:

```bash
cd models
uv run python eval/score.py --role crop_classifier_int8 --negatives /path/to/photos   # false alarms on your own photos
uv run python eval/screenshots.py /path/to/phone-screenshots --top 10               # what the phone would decide, per avatar window
```

In the browser, the extension popup's "Score this page" lists every image on the open tab with its
size, hash and score. Paste any of these outputs into chat to tune `thresholds` and `coverage`
under Settings, Protection. A hash from a screenshot window can go straight into the known-bad list
(a confirmed explicit event adds it automatically).
