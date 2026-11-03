# CLAUDE.md: Watchman

Read this first every session.

## What this is
Watchman is a self-hosted accountability and real-time protection system: a replacement for
Covenant Eyes (screenshot accountability) and Canopy (real-time image blocking). Each household
runs its own server on its own Tailscale tailnet; there is no central service. AGPL-3.0.
Nothing about any one household is hard-coded: household values are settings.

## Canonical documents (link to these, never duplicate them)
| Topic | Source of truth |
|---|---|
| Why we chose X | `docs/DECISIONS.md` (ADRs, newest first) |
| Components, data flow, threat model | `docs/ARCHITECTURE.md` |
| Device to server API | `docs/api-contract.md` |
| Models: files, preprocessing, thresholds | `docs/models.md` and `models/manifest.json` |
| What is built, what is next | `docs/ROADMAP.md` |
| Running, restoring, releasing | `docs/RUNBOOK.md`, `docs/self-host.md` |
| Borrowed code and model licenses | `docs/THIRD_PARTY.md` |

## Layout
```
server/              Python 3.12, FastAPI, SQLite, ONNX Runtime. Docker Compose with a Tailscale sidecar.
clients/extension/   WebExtension (MV3, TypeScript): Chrome, Edge, Firefox desktop + Android, Safari (via clients/ios).
clients/windows/     C# .NET 10: SYSTEM service, per-user helper (capture + overlay), installer.
clients/android/     Flutter UI + Kotlin services (accessibility, capture, overlay, ONNX Runtime).
clients/ios/         Swift: app and Safari web extension wrapper (ADR-049). XcodeGen project; built in CI on macOS.
models/              Model manifest, fetch/export scripts, evaluation harness. Weights are never committed.
```

## Working agreement
1. **Decisions** go into `docs/DECISIONS.md` as a new ADR at the top; other docs link (`see ADR-0NN`).
2. **One PR per slice**, branch `pr/<slug>`, squash-merged after CI passes and the change is
   verified on a real device where it touches one. The owner does not review code; tests and
   verification are the review.
3. **The API contract is the seam.** Clients and server change `docs/api-contract.md` in the same
   PR as the code. Version the API under `/api/v1`.
4. **Every external system sits behind one interface with a fake** (SMTP, Tailscale identity,
   ONNX models, screen capture, clocks). Unit tests run with fakes and no network.
5. **Settings, not constants.** Anything that differs between households (partners, schedules,
   retention, thresholds, app lists) is a server setting with a default. No household values in code.
6. **Privacy is the product.** No telemetry, no analytics SDKs, no cloud model calls, no phoning
   home. Images are judged on the device; only flagged crops travel. The server deletes originals
   after the second opinion and keeps only blurred versions (ADR-004).
7. **Protection fails closed and loud.** Any way protection turns off must produce a partner-visible
   event. Never add a pause or bypass that does not require a partner code (ADR-007).
8. **Prose style:** no em dashes in docs, PRs, commits, comments or UI copy. Plain, specific language.

## Hard constraints
- Tailnet only. Nothing is exposed to the public internet; partners off the tailnet get email or a
  Tailscale node share (ADR-006).
- Model weights are downloaded by script and verified by SHA-256 from `models/manifest.json`.
- Do not download or store explicit imagery in the repo, CI or test fixtures. Tests use fake
  classifiers; real-model evaluation of positives uses an owner-supplied local folder only.
- The bare dev server uses port 8035 (`WATCHMAN_DEV_PORT`); in Docker the server binds 8030 only
  inside the Tailscale sidecar's network namespace.

## Commands
```bash
cd server && uv run pytest                    # server tests
cd clients/extension && npm test              # extension tests
cd clients/windows && dotnet test             # Windows tests
cd clients/android && flutter test && (cd android && ./gradlew :app:testDebugUnitTest)
```
