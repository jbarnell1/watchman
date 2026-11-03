# Decisions

Newest first. Each ADR states the choice, why, and what was rejected.

## Open questions
- Q-1: Default peek cooldown: 24 hours, or longer? (default 24 h until answered)
- Q-2: Should the repo be public now? Public makes macOS CI minutes free for the iOS build.

## Decisions

### ADR-046 - Covered pictures reach partners when the server sees bare nudity
**2026-11-03.** The phone covers on the side of caution (ADR-033), so most covered pictures are
buttons, skin tones and revealing pictures, and putting every one in partner review buried the
few that matter: 821 covered pictures in the first 29 hours. A covered picture was never seen
sharp; what is left to talk about is nudity. The phone's labels could not decide this: its small
detector had not run on 621 of the 821. So the server now runs its own detector (NudeNet 640m,
the manifest's `detector_large`) on every uploaded picture before the original is deleted, and
stores the labels it finds (exposed, covered, faces and so on, at 0.25 or above) with the event
as `server_labels`; the review page shows them. New setting `covered_reports`: `exposed`
(default) keeps covered pictures on the record as info and makes them explicit, alerted and in
the digest, when the server finds a bare breast, genitals, buttocks or anus at
`thresholds.server_exposed` (0.50) or above, or, as a backstop for what the detector does not
tag, when the second opinion reaches `thresholds.covered_report` (0.95). `all` restores review
for every covered picture. A picture that was not covered is reported as before. Both settings
are in the protection lock group: switching to `exposed` or raising a line loosens. The what-if
page replays with the household's choice. Pictures from before the server tagged cannot be
tagged now (only blurred copies are kept, ADR-004). Not done: counting many covers in a short
time as a search in its own right.

### ADR-045 - Without the finger, blurs lift while a list scrolls
**2026-11-03.** Blurs moved by scroll events never lined up with the picture (ADR-042), and
moving them cost battery for nothing. Now a scroll event lifts the blurs inside the list that
moved, unless the household turned `follow_scroll` on (ADR-042). The region pass and strict-mode covers wait
until the list has had no scroll event for 150 ms, then cover the pictures where they are; a
picture judged before is a cache hit, so the cover comes back without a model run, and the
screenshot is taken with no blur in the way. The cost: a covered picture shows while its list is
moving. It is still judged and reported when the list stops, and the owner chose this trade.
Metric `patches_lifted_on_scroll`.

### ADR-044 - Blurs follow the finger when Shizuku runs (tried and removed)
**2026-11-02.** Prediction between scroll events (ADR-042) still trails a list, because the only
signal an app gets is Android's scroll event every 100 ms. The finger itself is the signal that
leads: while a finger drags a list, the content moves with it one to one past the touch slop.
An app cannot read other apps' touches, but the shell user can read the touchscreen's input
device. Shizuku (Apache-2.0) runs a helper with shell rights, started once through Wireless
debugging after each restart, and lets an allowed app start its own code there. Watchman starts
a small reader (`TouchService`) that only reads the touchscreen and writes one finger's down,
move and up into a pipe; it never injects, records or sends touches. On the phone a finger that
comes down inside the list that last scrolled moves the blurs on every touch sample; scroll
events then only correct drift (a collapsing header, the end of the list), ignoring drift smaller
than 50 ms of travel; a drag that no scroll event confirms within 250 ms is undone; at lift the
finger's speed hands over to the predictor as a fling. Landscape and the first scroll in an app
stay on scroll events. It only moves blurs while `follow_scroll` is on and changes nothing that is
judged or covered, so losing Shizuku loses smoothness, not protection, and is not a partner
event. Risk: Wireless debugging left on gives anyone holding the phone adb; reporting that, and
how it fits with a future Device Owner mode that could turn debugging off, is follow-up work.
Metrics: `finger_touches`, `finger_gestures`, `finger_unconfirmed`, `finger_correction_px`.
A custom Android build was considered and set aside: an unlocked bootloader breaks Play
Integrity, and the household would carry Android's monthly security updates.
**Removed the same day.** On the Pixel the owner found it no smoother than prediction (ADR-042),
and it needed Wireless debugging left on. The reader, the Shizuku dependency and the setup card
are gone; blurs lift while a list scrolls instead (ADR-045).

### ADR-043 - Misses become data; the screen is scanned when accessibility reconnects
**2026-11-02.** Tuning needs misses, and a miss leaves no event. The phone's notification now has
a Missed something action: it scores the whole screen and every reported picture at once and sends
the numbers (sizes, classifier and detector scores, decisive hash bits, any cached verdict, the
app's watch level and the thresholds) as a `miss_reported` event, severity info. No image is sent
or kept, so a miss of explicit content never travels. Also, after the accessibility service
(re)connects nothing reported the screen until the app changed something, and a playing video
changes nothing the tree shows (ADR-039); the service now scans the screen 500 ms after it
connects.

### ADR-042 - Blurs that follow a scroll are predicted between Android's scroll events
**2026-11-02.** Blurs that follow a scroll (ADR-032, a setting since ADR-040) jumped behind the
finger. Cause: a view sends its accessibility scroll event at most every 100 ms, with the distance
since the last one, so on a 120 Hz screen the blur moved once every 12 frames, after the content.
Better signals are closed to an app: an accessibility service that asks for touchscreen motion
events takes them away from the app under the finger (the observe-only form is a hidden test
API); an overlay that lets touches through gets no coordinates for other apps' touches; a
launcher controls only the home screen; frame-rate screenshots need MediaProjection and its
recording indicator (ADR-026). So the phone predicts: each real event sets the speed and corrects
the blur to where the content is; between events the blur moves every display frame at that
speed, never more than one event's travel ahead; with no event the speed eases out like a fling.
While moving, each blur is stretched along the scroll by 60 ms of travel each side (at most 240
px). Metrics `scroll_event_gap_ms`, `scroll_events` and `scroll_unknown_delta` measure what apps
really send. Still behind `follow_scroll`, off by default. Not pursued yet: shell-level input
access through Shizuku or ADB (raw touch at full rate), which needs pairing after every reboot.
### ADR-041 - A what-if page replays stored scores against other thresholds
**2026-11-02.** Tuning week 1 (ROADMAP) needs to see what a threshold change would do before it is
made. The portal's What if page (`/whatif`, the admin or a partner of the person) takes proposed
values for ask, sure, cover, flag, detector cover and second opinion, prefilled with the person's
current ones, and a window of 1, 7 or 30 days. It runs the scores stored on each image event
(`image_covered`, `image_flagged`, `screen_blocked`) through the device's decision once with each
set (`server/app/services/whatif.py`, mirroring `Decide.kt` and `Pipeline.kt`) and shows per app
and in total: covered, asked, reported, explicit by second opinion, partner review items, and the
known false alarms each set would still raise. Pictures covered by the known-bad list bypass
thresholds and are counted on their own. It reads numbers only, never an image, and changes nothing.
The limitation is stated on the page: events exist only for pictures the device already acted on,
and a safe answer to an ask stores nothing (ADR-022). Raising a line is a fair estimate; lowering
one can only count what it adds among these pictures, never the ones that were not reported. An
asked picture with no second opinion on record counts as reported, as it was. Rejected: logging
every score on the device to replay everything, which sends far more about what is on screen than
the product needs (CLAUDE.md, privacy); and simulating on the phone, which has no history.

### ADR-040 - Scroll-following is a setting; strict mode covers new pictures until judged
**2026-11-02.** The owner found blurs that move with a scroll (ADR-032) not effective enough to
keep on unconditionally: the app's reported distance often lags the picture. `follow_scroll`, a
protection lock key, is now off by default (the owner's call; an older server that sends no value
also means off); blurs then stay put until the next check moves them. The settings page has offered strict mode ("cover new images until each one scores safe" in
risky apps) since the first build, but no client did it. The phone now does: in `risky_apps`, a
picture that appears and has not been judged on this screen is covered at once with a plain patch
from its position alone, before any screenshot; the region pass that follows lifts it when it is
safe or swaps in a pixelated patch when it is not, and an unjudged cover expires after 5 s.
Videos are not strict-covered (they are judged on their cadence, ADR-038). It costs no model run.
Strict mode stays on by default, which turns this on for the default risky apps (YouTube, Firefox,
Reddit). Windows and the extension still ignore strict mode (ROADMAP).

### ADR-039 - The phone's check chain starts itself and cannot stall on a missing callback
**2026-11-02.** After an accessibility rebind (here caused by a UI dump, which suppresses every
accessibility service for a moment) the phone reported protection on but judged nothing for six
minutes on a playing reel. The watcher scheduled its first whole-screen check in its constructor
before the timer object existed, so the call posted an empty message; the chain only ever started
when a screen-on broadcast or an app switch scheduled it. A playing reel sends almost no
accessibility events, so nothing else ran. The first check is now posted after construction, and
each check books its successor before asking for a screenshot, so a callback the system never
delivers cannot end the chain. The rebind itself was partner-visible (protection off, then on).

### ADR-038 - Pictures are tracks over time; video surfaces are judged at the video cadence
**2026-11-02.** On a looping reel the phone judged the 132 px buttons drawn over the video on
nearly every pass: their pixels change with each frame, so every pass was a new fingerprint and a
classifier run, while the video itself was rarely judged at all (it was not a reported picture).
Of 122,489 picture checks that day, 93% repeated one already judged. Now each reported picture is
a track (node id or class plus bounds). Video surfaces (SurfaceView, TextureView, video and player
views at least 160 px a side) are reported as pictures and judged once per whole-screen interval,
stretched by the power budget like everything else. A picture at least 90% inside a video surface
is not judged on its own. A picture whose fingerprint changed on 3 of its last 4 judgements is
live and also waits for the video cadence. A held track keeps its patch until its next judgement,
so nothing is uncovered by waiting. Metrics: `video_regions`, `regions_inside_video`,
`regions_held_live`, `region_passes_skipped`.
Rejected: judging every frame of a video (the cost we are removing), skipping pictures over a
video without judging the video (a miss).

### ADR-037 - Watch levels per app: real time, checked now and then, not watched
**2026-11-02.** Unplugged for 29 minutes on a looping reel, Watchman used 53 mAh and Covenant Eyes
1.9 mAh. Covenant Eyes looks at the screen now and then; Watchman judges every picture as it
appears. Blurring is not the cost (a pixelated patch is a few milliseconds); judging is, and
report-only judges just as much. So the household now chooses per app: real time (today's
behaviour), checked (no per-picture passes; one whole-screen look every `checked_interval_s`,
default 60, that still blurs and reports what it finds), or not watched (ADR-030). An app's level
is Not watched, else `checked_apps`, else `realtime_apps` (social, video and the protected browser by
default), else `watch_default` (real time by default, so nothing loosens until a household chooses).
Moving an app to a lighter level, raising the checked interval or turning the default to checked
is loosening and needs a partner code with locks on; moving one from Not watched to checked does
not. Opening an app triggers an immediate look at any level. Per-person values work as for every
protection key. Android applies it; Windows next.

### ADR-036 - Only sure covers take over the screen, and the block screen says why
**2026-11-02.** The owner saw "Instagram is blocked on this phone" and "Gboard is blocked on this
phone" at random, though neither is on the blocklist. Both came from the phone's whole-screen rule
(covered pictures over 40% of the screen show the block screen), which passed the app's name and so
used the blocklist wording. All three such blocks that day were unsure whole-screen detector hits
(0.56 to 0.75) that the server scored 0.01 to 0.11; one named the keyboard because its window had
become the "app in front". Now only sure covers count toward the 40% (unsure ones stay patches
while the server is asked, ADR-022), overlapping covers count once, the block screen says most of
the screen was explicit, and the keyboard's window never becomes the app in front.

### ADR-035 - The known-bad list changes only on strong, independent evidence
**2026-11-02.** Every picture the server's second opinion confirmed (0.70 or more) joined the
known-bad list, and every device then covered and reported anything within six bits of it
without running a model. On an Instagram reel the like, comment and save buttons sit on a plain
background over the video; the second opinion rated those crops around 0.74, so they joined the
list, and because a flat crop's hash is decided mostly by noise, each listed hash matched the next
frame's buttons, whose reports were confirmed and listed in turn. In one day: 228 hashes added,
1,453 covers by hash, 488 of 641 events the 132 px buttons. Seen as a graph, the list was a
feedback cycle with no damping, and flat hashes were hubs joined to everything near them.
Now: a picture joins only at `thresholds.known_bad` (default 0.90), only when its hash has at
least 40 of 64 decisive bits (neighbouring cells differ by 3 grey levels or more), never when the
judge cache holds a look-alike answered safe, and never from a device's own known-bad cover (that
would widen the circle). A safe second opinion on a known-bad cover removes the hash and its
look-alikes; so does a partner marking the item a false alarm. A look-alike of something the same
device reported in the last 10 minutes is recorded as info with image status `repeat`, not
uploaded, judged or alerted; alerts go out once per look-alike group per 30 minutes. On the
phone, a flat hash matches the known-bad list and its own verdict cache only exactly, so it can
neither be covered by an unrelated listed hash nor inherit another crop's safe verdict.
Trade-off: a genuinely explicit picture with little detail (a close crop of skin) no longer joins
the list. It is still judged by the models every time it appears; the list is an accelerator,
not the protection. Windows, the extension and iOS still near-match flat hashes (ROADMAP).
Rejected: raising the second-opinion line (would lose real reviews), deleting the existing list
(the owner's call; the phone's exact-match rule already neutralises its flat entries).

### ADR-034 - A small sorter model on the phone; the classifier on XNNPACK with four threads
**2026-11-02.** On the owner's Pixel the crop classifier took about 250 ms a picture through NNAPI
on two threads, so a screen of a dozen new pictures took seconds and covers appeared in screen
order. Two changes. First, NNAPI is dropped (it ran few of these models' operators) and each model
is opened at load on both ONNX Runtime's XNNPACK backend and the plain CPU backend, four threads
each, timed on a plain picture, and kept on the faster one; the times go to the metrics. On the
Pixel, XNNPACK made the int8 classifier slower (290 ms a picture), because XNNPACK cannot run its
quantized operators and they fall back to one CPU thread, so no one fixed backend suits every
model. Second, a small
MobileNetV4 NSFW classifier (`crop_gate`, about 10 MB, Apache-2.0, pinned upstream revision) scores
every new picture first, and the real classifier then judges them most suspicious first, with each
cover drawn the moment it is decided (`first_cover_ms` measures it). Measured on four real
screenshots (1,546 windows, numbers only): the sorter costs about 6 ms a window on the desktop
against 76 ms for the classifier, but agrees with it poorly (correlation 0.06), would have cleared
11 to 13 of the 28 windows the server confirmed, and fires on 313 windows the server did not. So it
only orders the work: every picture is still classified and nothing is cleared on its say-so.
Measured on the Pixel after install: the classifier and detector both chose plain CPU (68 and 31 ms
on a plain picture; XNNPACK 183 and 69), and live classifier runs fell from about 250 ms to 120 ms.
The sorter took 36 ms there (64 ms live), only half the classifier's time rather than the desktop's
thirteenth, so sorting a screen of N new pictures delayed the first classifier run by N sorter runs
and added half again to the work. The phone therefore keeps the sorter only when it measures at
least four times cheaper than the classifier at load (`gate_off` counts the phones where it is not);
on the owner's Pixel it is off. The code and manifest entry stay for phones where it pays.
Rejected: the sorter as a gate that skips the classifier (misses too many), a single faster model
in place of the classifier (no candidate matched it), NNAPI or the Tensor TPU (not reachable from
ONNX Runtime for these models).

### ADR-033 - Ask the server about every cover that is not sure; blank the app switcher's thumbnails
**2026-11-02.** On the first day of real use the phone and the server disagreed on 18 of 63 crops
the phone covered, so a phone cover at 0.85 to 0.97 is often a false alarm, and it stayed blurred
for as long as the picture was on screen. A new `sure` line (default 0.97) extends the ask band
(ADR-022): from `ask` up to `sure` the device covers first and asks; a safe answer lifts the blur
within about a second on the tailnet and nothing is reported. At `sure` or above, or on a
known-bad hash, it covers and reports as before. Raising `sure` is loosening under ADR-028.
The app switcher showed covered pictures: it is drawn by the launcher from snapshots of each app's
own window, which never include another app's overlay. While the launcher is in front, the phone
finds the thumbnails (`:id/snapshot` views) and blanks the thumbnail of any app that had covered
pictures in the last 15 minutes. Scroll events now arrive about every frame (16 ms) instead of
every 100 ms, so patches step less while following a scroll.
Rejected: lifting phone covers on a timer (a timer does not know whether the picture is explicit),
a solid overlay over the whole switcher (it would hide every app, not only the risky ones).

### ADR-032 - Phone patches move with the scroll the app reports
**2026-11-02.** On the phone a patch stayed where the picture had been until the next pass found
the picture again, which at today's model speed could be several seconds, so a scrolled picture
slid out from under its blur. Android's accessibility events already say how far a view scrolled
(`scrollDeltaX/Y`, -1 when the app does not say). Each scroll event now moves the patches inside the
scrolled view by that distance at once and drops the ones that leave it; the next region pass,
from the app window, confirms each picture by hash (a cache hit) and lifts a patch only when its
picture is gone, so a re-check never uncovers. This is the phone's version of the Windows scroll
estimate (ADR-023 addendum), with the distance given rather than guessed. Apps that report no
distance still rely on the next pass.

### ADR-031 - Patches stay while their picture does; heavy blur, a 3 s light peek; settings in a minute
**2026-11-02.** On a still screen a phone patch vanished after eight seconds: patches expired unless
a region pass found the picture again, and region passes only ran on accessibility events, which a
still screen does not send. The timed whole-screen tick now also re-checks the pictures the app last
reported (a hash and a cache hit each), patches hold for 30 s between refreshes, and a region pass
made from the app window (which shows what is under the patches) lifts at once any patch whose
picture was not found again. With real catches coming in, the owner asked for a heavy default blur
for partners and a light blur only for a peek of at most three seconds; ADR-029's scaling stays.
Settings reached devices only with the five-minute heartbeat; devices now poll a write-free
`/api/v1/policy/version` on their 30 s loop.

### ADR-030 - Pictures the accessibility tree does not call images; apps a household does not watch
**2026-11-02.** On a YouTube channel list the avatars are reported as `Button` nodes with a
description and no text, so the region rule (image views, image buttons, described `View` leaves)
never handed them to the model; measured on the owner's phone, three of the six skipped avatars
scored 0.89 to 0.93 or tripped the detector and would have been covered at once. The rule now also
takes any leaf with a description and no text whose sides are within a factor of two of each other
(`RegionRule`); text rows and toolbars stay out because they are wide and short. A real region
finder over the pixels for apps that report nothing useful remains the next step.
Households also need to leave some apps alone, a photo library for example: `exempt_apps` (Not
watched) stops judging, covering and reporting while one of them is in front. It is a lock-group
setting where adding an app is loosening, so with locks on it needs a partner code, and the list
reaches partners through the digest's settings changes. Android applies it today.
Rejected: treating every leaf as a picture (thousands of crops on a busy screen at today's
classifier speed), an exempt list that needs no code (an exemption is exactly what a partner should
see).

### ADR-029 - Partners see a medium blur, and a light one when they peek
**2026-11-02.** The first real event, a 54 px avatar the phone scored 0.89 and the second opinion
0.07, was unreadable on the review page: the default view squeezed every crop to a few pixels before
blurring, and small crops were never scaled up, so a partner could not tell a false alarm from
anything else. Every crop is now scaled to 256 px first; the default view is a medium Gaussian blur
(colours and shapes readable, detail gone) and the timed peek a light one. ADR-004 still holds: the
original is never stored, only these two. Stored names stay `heavy` and `medium` so existing images
keep working; images stored before this change keep their stronger blur. Rejected: showing the
original on a peek (ADR-004), a single level (no way to look closer when it matters).

### ADR-028 - Locks are one-way: tightening is free, loosening needs a partner code
**2026-11-02.** ADR-008 locked whole settings: once a device was paired, adding a blocked app needed
a partner code just like removing one. The owner's rule is that strengthening protection must never
need anyone's permission and anything else must. `services/locks.py` now says, per setting, whether
a change loosens: a threshold raised, coverage narrowed, a flag such as strict mode turned off, any
app removed from the blocked or risky list (even alongside an addition), a longer interval, pause or
silence alert, a shorter peek or retention, a person's own values loosened or dropped, the protected
browser replaced. Only loosening a locked setting needs a code. Settings sit in lock groups
(protection, apps, timing, pauses and peeks, retention, removing a partner); the household chooses at
setup and under Settings, Protection, Locks which groups are locked, and every group is locked by
default. Locks start off (`locks_on`) so a household can set up and test freely; turning them on is
free and turning them off again, or unlocking a group, is itself loosening and needs a code. Every
change still reaches the partners in the digest, so testing with locks off is never silent.
Rejected: a time-limited trial that relocks by itself (surprising, and it fails the household that
is still testing), per-key lock choices (too many switches to reason about).

### ADR-027 - Portal sign-in is pluggable: Tailscale headers or the household's own OIDC provider
**2026-11-02.** ADR-014 identified people only by Tailscale identity headers, which assumes every
admin, protected person and partner runs Tailscale on every device they open the portal from. Some
households already run an identity provider (Pocket ID, for example) and a reverse proxy,
and would rather sign in with a passkey than install Tailscale on a partner's work laptop. So the
portal's identity is chosen by one process setting, `WATCHMAN_AUTH`:

- `tailscale` (the default, unchanged): `tailscale serve` headers, trusted only from the sidecar on
  loopback. uvicorn keeps `proxy_headers=False` so the trusted-proxy check sees the real peer.
- `oidc`: the authorization code flow with PKCE (S256) against `WATCHMAN_OIDC_ISSUER`, found by
  discovery. The ID token is checked for signature against the provider's JWKS (asymmetric
  algorithms only; `none` and HMAC are refused; an unknown key id refetches the JWKS once), issuer,
  audience (and `azp` when there are several), expiry, issued-at and the nonce. The login is the
  `email` claim, lower-cased, so it matches the logins already stored for partners. A session is a
  cookie signed with HMAC-SHA256 under a 32-byte key generated into `data/keys/session.key` on first
  start; it is HttpOnly, Secure and SameSite=Lax, lasts `WATCHMAN_SESSION_HOURS` (168 by default)
  and is cleared by `/auth/logout`. State, nonce and the PKCE verifier ride in a second signed
  cookie scoped to `/auth` for ten minutes. Tailscale headers are ignored in this mode.

Both modes keep the first-login-becomes-admin rule (ADR-014): it lives in the portal's viewer
lookup, after identity, so it does not care which provider answered. In OIDC mode the gate is the
provider instead of the tailnet, so the household should limit the Watchman client to its own
group in Pocket ID before the first start. SameSite=Lax keeps the portal's form posts from being
forged by another site. The device API (`/api/v1/*`, bearer tokens from pairing) does not change.
Households without Tailscale on every device use `deploy/compose.oidc.yaml`, which publishes the
server port to their reverse proxy instead of running the sidecar. Every external call goes to the
household's own provider; nothing leaves the household's network.
Rejected: passwords stored by Watchman (one more secret store to protect), an OIDC library with a
web framework integration (more dependencies than PyJWT plus httpx, and harder to fake), trusting
`X-Forwarded-*` from the reverse proxy for identity (a header any misconfigured proxy would pass
through), and requiring OIDC and Tailscale together (most households need only one).
### ADR-025 - Every client measures its own work and the portal shows it
**2026-11-02.** ADR-023 made the clients skip work they think is unnecessary; without numbers there
is no way to tell whether that pays, what a phone or laptop spends, or where time goes. Every client
now keeps counters and timing histograms for each step: Windows times capture, the thumbnail and
fingerprints, the scroll estimate and each inference, and counts sweeps, passes planned, run,
skipped and unchanged, motion, game and battery sweeps, scrolls and carried findings; Android times
screenshots, the bitmap copy, hashing, the cache lookup, the classifier, the detector, each judge and
each ask, and counts frames taken, reused and refused, secure windows, cache hits, known-bad hits,
asks and their answers; the extension times each judge, the engine and each ask, and counts
judgements, URL, hash and in-flight cache hits, video frames judged and dropped. Histograms use fixed
buckets (1, 2, 4 ... 1024 ms and slower) so windows merge by adding. Each client writes a local log
(Windows: `%LocalAppData%\Watchman\metrics.jsonl`, one line per sweep and one per minute; Android:
`files/metrics.jsonl` and Logcat tag `WatchmanMetrics`, one line per minute; the extension keeps the
day in its storage and logs each judge when `watchmanDebug` is set) and sends the window since the
last heartbeat in the heartbeat's optional `metrics` field. The server merges those per device per
household day (`device_metrics`, purged with event retention) and the Devices page shows each
device's day: runs, median, 95th percentile and slowest for every step, and every counter. Counts
and milliseconds only, never content. Rejected: a metrics service or exporter (another thing to run
for one household), exact percentiles (needs every sample), sending per-event records (volume).

### ADR-026 - The phone watches the screen through the accessibility service, not MediaProjection
**2026-11-01.** MediaProjection is Android's screen-recording API. Running it keeps the red
recording indicator in the status bar, collapses every notification behind a "screen is being
recorded" banner, and demands a consent dialog after every restart. Covenant Eyes and Canopy ask
for none of that; they see the screen through the accessibility service, which on Android 11 and
up may take its own screenshots. Watchman now does the same: the accessibility service owns a
`ScreenWatcher` that calls `takeScreenshot` when it has a reason (a region pass, the whole-screen
tick), reuses a frame younger than a second because the API is rate-limited, and gets nothing from
a secure window (banking apps, DRM video), which is also what a human partner would see. There is
no capture service, no consent activity, no projection permission, and the only notification is
the quiet "Watchman is on" line of the always-on service. The permissions the walkthrough asks
for are the same set those two products ask for: accessibility, draw over other apps, device
admin, unrestricted battery, notifications.
Rejected: MediaProjection with a smaller virtual display (the indicator is the problem, not the
cost), the Camera-style one-time consent (Android has none for projection).
Addendum, the strict-mode "patch lift" spike: a screenshot that contains Watchman's own patches
cannot see the picture under a patch, so the patch can neither follow the picture nor know it has
gone; lifting it for one frame would show the picture. On Android 14 and later the region passes
screenshot only the app window in front (`takeScreenshotOfWindow`), which does not contain the
overlay: the picture under a patch is judged like any other, the patch is refreshed while it is
there and expires when it is not. The whole-screen tick still takes the display so the shade and
picture-in-picture are seen. Before Android 14 a patch is held while the accessibility tree still
reports its rectangle. No patch is ever lifted to look.

### ADR-024 - Posture is a set of knobs with presets, and it is per person
**2026-11-02.** "Blur first and ask the server" (ADR-022) is one way to run Watchman, not the
product. Households differ: some want report only (Covenant Eyes), some want the device to blur
and decide alone (Canopy), some want blur and a second opinion; and the same household may want
a child on the strict end and an adult on report only. So there is no `mode` field. The posture is
two plain settings next to the thresholds: `cover_on_device` (off is report only: nothing is
blurred anywhere, scores at or above `flag` are reported as `image_flagged`) and `ask_server`
(off means the device decides alone and the ask threshold is unused). The settings page offers
three presets, Report only, Cover on the device, Cover and ask this server, which fill the fields
and save nothing until Save, so a first-time admin starts from a sensible posture and can loosen
or tighten later with the knobs. Every protection setting is also settable per person: the
settings page has a "Settings for" selector; a person's own values are stored as the differences
from the household (`person_overrides`), and `/api/v1/policy` answers each device with its
person's merged values. Clients read `cover_on_device` and `ask_server` from the policy and
default both to true when absent.
Rejected: a single `mode` enum (hides which knob does what and cannot express mixtures), a
household-only posture (one server, one household, several people with different needs).

### ADR-023 - Work only on what changed: fingerprints per place, viewport gating, video sampling, a power budget
**2026-11-02.** The first build judged everything it could see: every `img` on a page whether or
not it was on screen, every tile of every monitor each interval even on a desktop nobody touched,
every accessibility event during a scroll, and on the phone a copy of every composed frame (18 MB
at up to 60 Hz) whether anything used it. Video was either not judged at all (the extension) or
judged frame by frame as if each frame were a new picture (screen capture). The fix is one rule on
every client: keep a fingerprint of what was last judged at each *place*, and run a model only when
the fingerprint changed.

The data structures, shared by all clients:
- *Place to last fingerprint*: a map from a place (an image element, a screen tile, an accessibility
  rectangle, a video element) to the 64-bit dHash of what was there at the last pass. A new hash
  within 2 bits of the old one is "unchanged": nothing runs and the last answer stands. A tile's
  hash is a 9x8 downsample, microseconds; a model pass is tens of milliseconds.
- *Decision cache* keyed by hash (exact first, then Hamming 6), already present, so the same picture
  at a new place or a new URL costs nothing.
- *In-flight map* keyed by URL in the extension, so fifty copies of one avatar on a page are one
  fetch and one inference, not fifty.
- *Token bucket per source* for events: a video that stays explicit produces one `image_covered`
  per source per minute, not one per sampled frame.

Per client:
- *Extension.* A `MutationObserver` finds new and changed images (already) and an
  `IntersectionObserver` with one viewport of margin decides when to judge them: an image far down
  an infinite feed stays hidden (it is off screen anyway) until it comes within a screen of view,
  nearest first. The engine keeps a hash to score cache so CDN variants of one picture are scored
  once. Video: `video` elements that are playing and on screen are sampled once per `whole_screen_interval_s` (5 s by
  default) into a 384 px canvas; a sample whose hash is within 2 bits of the previous one is dropped
  before any model runs (a paused or static scene costs one hash per interval). A cover on a video sticks for that source until
  a tap asks the server. `canvas` is never sampled: games and WebGL render there, and sampling them
  would be both expensive and pointless. DRM video (Netflix, Prime) cannot be read from a canvas and
  is not the threat. A cross-origin video served without CORS taints the canvas; the element is
  marked unsampleable once and left alone.
- *Windows.* A `SweepPlanner` hashes each pass's region when a sweep starts. Passes whose region is
  unchanged since the last sweep are skipped and their last findings are re-applied, so a patch over
  a picture that is still there does not expire. When more than half the tiles changed at once
  (video, a game, a fast scroll) the sweep collapses to the whole-frame pass only ("motion mode"),
  which generalises game mode to anything moving everywhere. A static desktop runs zero inferences.
  On battery the interval doubles.
- *Android.* Frames are pulled, not pushed: no `ImageReader` listener copying every frame. The
  capture service calls `acquireLatestImage` only when it has a reason (a region pass, a whole-screen
  tick). No new frame since the last pull means the screen did not change, which means no work.
  Region passes from the accessibility service are coalesced to one per 150 ms with the latest
  rectangles. While the screen is off the virtual display's surface is detached so the compositor
  stops producing frames; it is reattached on screen on. A `PowerBudget` scales the work: plugged in,
  the policy's interval and the detector on crops over 160 px; on battery, double the interval; under
  20 percent, four times the interval and the detector stays off.
- *Server.* `POST /api/v1/judge` keeps a hash to verdict cache so the same borderline crop asked by
  two devices (or the same device twice) is answered without a second inference.

Where the quality comes from when a device does less: the ask band (ADR-022). The device model is
the gate and the server model is the judge. A battery device runs the smallest classifier only and
sends anything between `ask` and `cover` to the server, which is plugged in. Safety does not scale
with battery: unsure is covered everywhere; what degrades when the server is far or unreachable is
the false-blur rate and the time to unblur, never what gets through.

Rejected: frame differencing on every phone frame (keeps a core awake to notice that nothing
changed; pulling on demand gets the same answer for free), scoring every video frame, lowering
protection on low battery (a bypass without a partner code, ADR-007), judging `canvas` content.
Addendum, scrolling (2026-11-02): Windows tiles are fixed screen positions, so a page scrolled by
half a tile looked like motion everywhere. Each sweep now keeps a grayscale thumbnail of every
screen at one eighth size, estimates the vertical shift since the last sweep by comparing the two
thumbnails at every shift (about 5 ms per 2048 by 1152 screen on a desktop PC), moves the overlay's
patches and the kept findings by that shift, and compares each tile with the content that scrolled
into its place. Only the newly revealed strip runs. A wrong estimate costs passes, not coverage:
any tile whose content does not match at the estimated shift runs as before.

### ADR-022 - Unsure means covered: blur first, ask the server's bigger model, then revise
**2026-11-01.** The small on-device model is allowed to be jumpy because the cost no longer lands
on anyone. Between the `ask` threshold (default 0.60) and `cover` (0.85) a device covers the image
at once and posts the crop to `POST /api/v1/judge`, where the second-opinion model scores it in
memory and stores nothing. A safe answer unblurs the image and no event exists; an explicit answer
keeps it covered and records the usual `image_covered` event. Above `cover` the device covers and
reports without asking; below `ask` it shows the image. If the server cannot be reached the image
stays covered (fails closed). The browser extension does this now (the background revises the tab
when the answer arrives), and a click on any covered image asks the server again on demand, then
either unblurs it or says "kept covered"; the phone and Windows helper follow once they have run on
real devices.
Cost accepted: borderline crops leave the device even when harmless, over the tailnet only, and are
dropped the moment they are scored. Rejected: a lower device threshold alone (more false blurs that
never clear), sending everything to the server (every image would leave the device).

### ADR-021 - Tuning without a positives set: the household's own screens, numbers only, and a coverage lever
**2026-11-01.** Watchman is tuned on the screens of the household that runs it rather than on a benchmark, and no
explicit material is collected for it: nothing in the repo or the tooling downloads any, the
models are weight files only, and nobody (the assistant included) looks at originals. Three tools:
`models/eval/score.py` measures false alarms on the household's own photo library in place;
`models/eval/screenshots.py` runs the phone's pipeline over a folder of phone screenshots (whole
screen, then avatar-sized windows) and prints positions, sizes, hashes and scores, saving nothing;
the extension popup's "Score this page" lists every image on the open tab with its score, kept
locally. Thresholds are then set from those numbers on the settings page. The second lever is
`coverage`: which NudeNet labels count as explicit (explicit only; revealing, adding underwear and
swimwear; strict, adding bare bellies and armpits). The policy carries the label set and every
client uses it instead of the manifest default. Rejected: a curated positives folder (households
do not want that material on the household's hardware, and the dataset behind NudeNet has a
documented contamination problem), sending samples through chat.

### ADR-020 - iOS: a broadcast extension that reports, Safari that blurs, everything else by Screen Time
**2026-11-01.** iOS lets no app see or draw over another, so the client is three pieces. The
ReplayKit broadcast extension takes the latest frame every whole-screen interval, scores it whole
and in six tiles with the Marqo int8 classifier on ONNX Runtime (CoreML or CPU), records flagged
tiles as `image_flagged` events with a 512 px crop and `detail.covered = false`, so the server's
second opinion decides what partners see, and heartbeats as capture granted while it runs; starting
and stopping it are protection events. The Safari web extension is the shared WebExtension from
`clients/extension` (the Firefox build, whose background page carries the engine iframe). Blocked
apps and web limits come from Screen Time with a partner-held passcode, not from code, until the
Family Controls entitlement is granted. The app and both extensions share one app group container
through the `WatchmanCore` Swift package (API, hash, queue, decisions), which `swift test` checks on
macOS; the app itself is built on a macOS runner without signing (ADR-012). Rejected: a VPN or
content filter (needs supervision), scoring on the server (frames must not leave the phone).

### ADR-019 - Android: accessibility finds the images, MediaProjection sees them, Kotlin does the work
**2026-11-01.** The Flutter app is setup, status and pause codes only, over one MethodChannel. The
accessibility service knows the foreground app, the image rectangles on screen (ImageView nodes)
and the browser address bar, and blocks apps from the policy with the block screen plus a home
action. The capture service holds a MediaProjection session (consent asked by an invisible
activity, declining is a tamper event), keeps the latest frame, crops each reported rectangle and
judges it at its own size: dHash, known-bad list, decision cache, Marqo int8 classifier for every
crop and NudeNet 320n as well for crops over 160 px, ONNX Runtime with NNAPI when available. Covered
regions get pixelated patches on a touch-transparent overlay; over 40% explicit shows the block
screen. A foreground service heartbeats and flushes the queue, models come verified from the
household server, and pairing, policy and the queue live in EncryptedSharedPreferences. Device
admin reports its own removal. Rejected: scoring in Dart (no ONNX on the UI thread, and the
services must outlive the Flutter engine), a VPN-based blocker (would fight Tailscale, ADR-006).

### ADR-018 - Windows: GDI capture, a DirectML detector over tiles, an overlay excluded from capture
**2026-11-01.** The per-user helper captures each monitor with GDI (CopyFromScreen) every
whole-screen interval, runs NudeNet 640m with ONNX Runtime on DirectML over the whole frame and
over 640 px tiles at native resolution (so small images are seen at their own size, ADR-002), merges
the explicit boxes and covers each with a pixelated patch on a topmost click-through window that
carries WDA_EXCLUDEFROMCAPTURE, so the next frame still shows what is underneath. Over 40% of the
screen explicit becomes a full block. Findings go to the SYSTEM service over a named pipe as JSON
lines with a 512 px JPEG crop; the service queues events (DPAPI-sealed files under ProgramData),
heartbeats, downloads and verifies the model, and restarts the helper in the console session when
it dies. The sweep is spread over the interval: one inference per tick (every 384 ms at the
default 5 s), never a burst, because the Covenant Eyes stutter came from bursts. When a full-screen
window that is not a browser is in front (a game), the sweep collapses to one whole-frame pass per
interval with NudeNet 320n on the CPU at below-normal thread priority, so the GPU is never shared
with the game. Measured on a desktop PC: 15 to 18 ms per 640 px inference on the RTX 4070 through
DirectML (295 ms for the first, warm-up), 17 ms for the game-mode CPU pass, capture 24 to 34 ms.
Rejected for now: Windows Graphics Capture (needs a D3D device and WinRT interop for little gain at
this rate), UI Automation image positions (later; the browser extension already handles browsers),
a native-messaging host for the extension (later).
### ADR-017 - Extension: an engine page scores, the content script holds images back, errors blur
**2026-11-01.** The content script's stylesheet hides every `img` until the background has judged
it, so nothing explicit is painted. Scoring needs a canvas and WebAssembly, which Chrome's service
worker lacks, so an "engine page" does it: an offscreen document on Chrome, an iframe inside the
background page on Firefox. The engine fetches the image itself (extension host permissions), hashes
it, runs the Marqo classifier with ONNX Runtime Web (WASM, `wasm-unsafe-eval` in the extension CSP)
and keeps a 512 px JPEG for the server. When the engine cannot answer within six seconds the image
is shown blurred, never sharp: protection fails closed. The int8 model is quantised on MatMul only
because ONNX Runtime Web has no WASM kernel for ConvInteger. Verified end to end in Chromium with
Playwright against the dev server (`clients/extension/e2e/run.mjs`). Rejected: scoring in the
content script (cross-origin canvases are tainted), asking a native helper first (later, as an
optimisation on Windows).

### ADR-016 - Settings codes: partners unlock protection settings the same way they unlock a pause
**2026-11-01.** Once any device is paired, the keys in `LOCKED_KEYS` (thresholds, strict mode, app
lists, intervals, retention, pause and peek limits) and partner removal need a one-time settings
code issued by a partner on the Codes page. Same code table as pause codes, `purpose = settings`,
single use, 24 hours. Rejected: an admin password (the admin is usually the protected person),
locking the whole settings page (household name and SMTP must stay editable).

### ADR-015 - The server derives protection events from heartbeats
**2026-11-01.** Every heartbeat carries the device's state. The server compares it with the last
one and raises `protection_changed` (severity `protection`, alerts) when any field turns off, and
`tamper` on a safe-mode boot or when a device goes silent past the gap. Client-sent
`protection_changed` events are stored for the record but do not alert, so a device cannot be
silenced by not sending them and partners never get two alerts for one change. Clients send a
heartbeat at once on any state change (contract unchanged).

### ADR-014 - First login becomes admin; no passwords anywhere
**2026-11-01.** The portal identifies people only by Tailscale identity headers from the sidecar.
On a fresh server the first login to reach the portal becomes the admin and is sent to setup.
Safe because the node is reachable only on the household's own tailnet (ADR-006). Rejected: a
bootstrap token in the compose file (one more secret to lose; the tailnet already gates access).

### ADR-013 - Portal look: achromatic ledger, Harbour colours, self-hosted Geist
**2026-11-01.** The portal and the emails follow one style: ink and white chrome with no brand
colour on buttons or navigation, one hue (petrol, `#156d7a`) on the mark and on the main data
element, pale status pills, flat panels with a tone step and no shadows, Geist served from the
server (OFL 1.1) so no partner's browser calls Google. The signature element is the watch strip:
seven days by 24 hours of heartbeat coverage per device. Rejected: purple or indigo accents, the
olive greens, a dark navy sidebar, icon-topped KPI tiles. The direction was chosen
from `docs/brand/logo-options.html`. The mark, chosen from Gemini candidates on
2026-11-01, is a W whose outer strokes are ink and whose inner V is the light of a small
watchtower standing in the valley: `docs/brand/mark.svg` (traced), `mark-reference.jpg` (the
Gemini original).

### ADR-012 - iOS is built and checked in CI, tested by friends through TestFlight
**2026-11-01.** The project has no Apple device to build on. The iOS app (Swift, XcodeGen project) is built on a
GitHub macOS runner; the Safari extension shares its code with `clients/extension`, whose logic is
tested in Playwright WebKit on Windows/Linux. Device testing happens with an iPhone-owning friend
through TestFlight from a maintainer's Apple developer account. Rejected: Flutter for iOS (extensions
must be native anyway), skipping iOS (most interested friends use iPhone).

### ADR-011 - One model format: ONNX everywhere
**2026-11-01.** All platforms run ONNX models through ONNX Runtime (DirectML on Windows, NNAPI or
XNNPACK on Android, CoreML on iOS, WebGPU or WASM in the extension, CPU or CUDA on the server).
One export, one preprocessing spec in `models/manifest.json`. Rejected: LiteRT on Android plus ONNX
elsewhere (two conversions to keep in step).

### ADR-010 - Models
**2026-11-01.** Small crops and the extension: Marqo nsfw-image-detection-384 (Apache-2.0, about
6M parameters) exported to ONNX. Larger images and region boxes: NudeNet 320n (phone) and 640m (PC,
server), MIT per PyPI 3.4.2. Server second opinion: AdamCodd vit-base-nsfw-detector (Apache-2.0,
ONNX int8). Rejected: a single whole-screen classifier (misses small images, the Covenant Eyes flaw).

### ADR-009 - AGPL-3.0
**2026-11-01.** Watchman is AGPL-3.0. Anyone running it can read what watches their
screen, and modified hosted copies must share source.

### ADR-008 - Settings, not constants; one server per household
**2026-11-01.** Every household-specific value is a server setting with a default, edited on the
settings page; protection-weakening settings need a partner code once protection is on. Each
household runs its own server; there is no central service and no server talks to another.

### ADR-007 - Nothing pauses protection without a partner code
**2026-11-01.** Pauses come only from one-time partner codes with a configurable maximum length. No
allow-listed apps, no admin override. Every pause appears in the digest.

### ADR-006 - Partners connect per partner: Tailscale node share or email only
**2026-11-01.** The server runs as its own Tailscale node (default name `watchman`). A linked
partner gets that one node shared into their tailnet and uses the portal (peeks, review, codes).
An email-only partner gets digests and alerts with heavy blur and receives pause codes by email
when the protected person requests one. Identity on the portal comes from Tailscale identity
headers; there are no passwords. Rejected: public hosting, Funnel, a central relay.

### ADR-005 - Reports
**2026-11-01.** Digest on a configurable day and time (default Sunday 19:00) to the protected person and all partners. Immediate alerts to the same people only for
explicit content confirmed by the second opinion and for protection turned off.

### ADR-004 - Partners never see originals
**2026-11-01.** After the second opinion the server stores a heavy blur and a medium blur and
deletes the original. Email and lists show heavy blur. Linked partners may press and hold to see
the medium blur for 3 seconds, then a cooldown (default 24 hours per partner). Every peek is logged
and shown to the protected person. Images are kept 7 days by default, event text 1 year.

### ADR-003 - Real-time blur in the first version, two methods
**2026-11-01.** Browsers: the extension holds every image back until scored, so explicit images
are never painted. Apps: cover on sight, an overlay patch over just the offending image (Android
overlay window; Windows click-through overlay excluded from capture). A full block screen when
most of the screen is explicit.

### ADR-002 - Every image is judged at its own size
**2026-11-01.** Clients find image regions (accessibility tree, UI Automation, DOM, or a tiled
region finder), crop them at full resolution and score each crop. A whole-screen pass still runs
every few seconds. A perceptual-hash cache skips images already judged and covers known-bad ones
instantly.

### ADR-001 - Local judging, tailnet only
**2026-11-01.** Screens are judged on the device. Only flagged crops and events go to the
household's own server over Tailscale. No cloud models, no telemetry.
