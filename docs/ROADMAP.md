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
