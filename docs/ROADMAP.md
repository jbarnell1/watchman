# Roadmap

This page tracks build status; `docs/DECISIONS.md` has the reasons behind it.

## Status (2026-11-01, evening)

| Slice | Branch | State |
|---|---|---|
| Foundation: docs, ADRs, contract, CI skeleton | `main` | done |
| Models: manifest, fetch/export, dHash reference and vectors, eval harness | `main` | built; weights fetched and run on a desktop CPU |
| Server: API, intake, second opinion, blur store, portal, digest, alerts, settings, Compose | `main` | built, 27 tests; dev server used by the extension end-to-end run; not yet deployed |
| Browser extension: hold-back blur, pairing, events | `main` | built; 21 unit tests; end to end in Chromium with the real model |
| Windows: service, helper with capture and overlay, installer | `main` | built; 19 Core tests; self-test on a desktop PC (17 ms per pass, spread over the interval; game mode on the CPU); MSI built in CI; not yet installed as a service |
| Android: setup, accessibility, capture, overlay, scoring, queue | `main` | built; Dart and Kotlin unit tests; debug APK builds; not yet run on the Pixel |
| iOS: app, broadcast extension, Safari extension, CI build | `main` | built; Swift core tests and the unsigned simulator build pass in CI; untested on a device |
| Integration hardening: OIDC sign-in (ADR-027), gitleaks, Dependabot, `/healthz` for Gatus and Homepage, setup scripts, the `watchman-models` artifact | `slice/hardening` | built; server and models tests pass; OIDC tested against a fake provider only, not yet against Pocket ID |
| First household integration: server, Windows, Android, extension | | in progress |

All slices are built in one checkout on `slice/<name>` branches cut from `main`; the `C:\watchman-wt-*` worktrees and their `pr/*` branches are leftovers from the foundation session and can be deleted.

## Workload and power

ADR-023 is implemented on every client: viewport gating, in-flight dedupe, hash caches, video
sampling at the whole-screen interval and a per-source event limit in the extension; a sweep planner with change detection and
motion mode plus a battery interval on Windows; pulled frames, coalesced region passes, a
screen-off surface detach, a power budget and the ask band on Android; a judge cache on the server.
Unmeasured until the devices run: the Pixel's milliseconds per crop and battery per hour, and the
laptop's. The numbers go in the status pages.

## Tuning in daily use (from 2026-11-02)

The Pixel is the test device; the owner uses it normally and every change is measured, not
guessed. Numbers only: no picture leaves the phone for tuning beyond what the product already sends.

Where we start, on a looping Instagram reel, unplugged:

| Build | Watchman | Covenant Eyes |
|---|---|---|
| Before ADR-035 | 110 mAh an hour | about 4 |
| ADR-035 only | about 225 mAh an hour | |
| ADR-036 to 039 | 63 mAh an hour, 2 to 4 classifier runs a minute | 5.5 |

Targets: under 30 mAh an hour on a reel and under 10% of the phone's drain in a normal day; a
false-alarm share in the review queue under 10%; no picture the owner reports as missed.

Week 1, measure and label:
- The owner marks each review item talked or false alarm (false alarms also clear the known-bad
  list, ADR-035). The digest and Devices page show the false-alarm share per app.
- A "missed something" action in the phone's notification records the scores, sizes and app of
  what was on screen (numbers only) as a `miss_reported` event, so misses become data.
- Metrics per app, not only per device: classifier runs, covers, asks lifted, battery minutes.
- A what-if page on the server: replay the stored scores of the last days against other
  thresholds (ask, sure, cover, detector) and show how many covers, asks and reports each would
  have made, before anything is changed.
- Built: the what-if page (`/whatif`, ADR-041), linked from Settings beside the thresholds.
- Scan the screen as soon as the accessibility service reconnects (ADR-039 follow-up).

Week 2, tune from the data:
- Thresholds and watch levels per app from the false-alarm share and the cost per app; move
  low-risk apps to checked (ADR-037).
- Video cadence (5 s today) against battery and misses on reels and YouTube.
- A cheap pixel-difference check per track, so unchanged flat pictures skip the hash and cache.

Week 3, bigger steps:
- GPU through LiteRT for the classifier, measured against 68 ms on the CPU.
- Windows: desktop duplication capture (changed and moved rectangles from the OS), UI Automation
  regions, watch levels and strict mode.
- Extension: strict mode, force-install through browser policy, CSS background images, canvas,
  cross-origin frames.
- Research: picture embeddings in place of fingerprints, evaluated on the owner's local folder.

## Known-bad list (ADR-035)

The server lists a picture only on strong evidence and removes it on contrary evidence; the phone
matches flat hashes only exactly. Still to do: the same exact-match rule in Windows, the extension
and iOS; a portal view of the list with a way to clear it.

## Measurement and the phone's view

ADR-025 metrics on Windows, Android and the extension, merged per day on the server and shown on
the Devices page. Windows sweeps follow a scroll instead of treating it as motion (ADR-023
addendum). The phone's region passes screenshot the app window, without the overlay, on Android 14
and later (ADR-026 addendum). APKs are split per architecture. iOS is built in CI only and still
needs a Mac with Xcode and a signing identity for a first run on a real iPhone.

## After the first build
- Spike B from the plan: lift a patch for one frame to see underneath (strict mode).
- Device owner provisioning for kids' tablets.
- Self-hosting guide tested on a clean machine; first friend household.
