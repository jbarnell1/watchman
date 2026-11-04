# Architecture

This page is the engineering view; `docs/DECISIONS.md` has the reasons.

## Components

```
 devices (on the household tailnet)                         household server (Docker Compose)
 ┌──────────────────────────────┐                          ┌────────────────────────────────────┐
 │ Android app (Flutter+Kotlin) │──┐                       │ tailscale sidecar  (node "watchman")│
 │ Windows service + helper     │──┼─ HTTPS over tailnet ──│   serve :443 -> server:8030          │
 │ iOS app + Safari extension   │──┤   /api/v1 (bearer)    │ server (FastAPI, SQLite, ONNX)       │
 │ Browser extension (all)      │──┘                       │   intake, second opinion, blur store │
 └──────────────────────────────┘                          │   heartbeat watch, digest, alerts    │
                                                           │   portal (Tailscale identity)        │
 partners: node share (portal) or email only  <────────────│   SMTP out                           │
                                                           └────────────────────────────────────┘
```

## The image pipeline (every client)

1. **Find regions**: accessibility tree (Android), UI Automation (Windows), DOM (extension),
   or the tiled region finder over the full-resolution frame when the platform gives no positions.
2. **Hash** each region (64-bit dHash of a 9x8 grayscale). Known-safe hashes skip scoring; hashes in
   `known_bad_hashes` are covered at once.
3. **Score**: crops up to 160 px on the long side go to the small classifier (Marqo 384, input
   upscaled); larger regions go to the classifier and to NudeNet for boxes.
4. **Act** by threshold from policy: `>= cover` covers the region (or holds the image back in the
   extension) and reports; `>= ask` covers first and asks the server's bigger model, unblurring on a
   safe answer (ADR-022); `>= flag` (equal to cover by default) records accountability only.
5. **Report**: events go to the local encrypted queue and then to `/api/v1/events`; the crop is
   uploaded when the server asks.

Whole-screen pass every `whole_screen_interval_s` seconds for content that is not a tidy image.

### Workload and power (ADR-023)

Nothing runs a model on a picture it has already judged at that place. Every client keeps a map
from place (image element, screen tile, accessibility rectangle, video element) to the dHash seen
there last; a hash within 2 bits is unchanged and the last answer stands. On top of that:

- Extension: images are judged when they come within one viewport of the screen (nearest first),
  one fetch and one inference per URL however many copies a page has, and `video` elements that
  are playing on screen are sampled once per `whole_screen_interval_s` with unchanged frames dropped
  before the model.
  `canvas` is never sampled.
- Windows: a sweep runs only the passes whose tile changed since the last sweep; when more than
  half changed at once (video, game, fast scroll) it runs the whole-frame pass only. A still desktop
  runs nothing. On battery the interval doubles.
- Android: frames are pulled when there is a reason, never copied as they arrive; region passes are
  coalesced; the capture surface is detached while the screen is off; a power budget stretches the
  interval and drops the on-device detector as the battery falls, with the ask band sending the
  doubt to the server instead.
- Server: the judge endpoint caches verdicts by hash.

## Server

- `app/api`: device API (contract in `docs/api-contract.md`).
- `app/portal`: Jinja templates and one stylesheet; a few lines of script for the timed peek. No SPA.
- `app/services`: pairing, intake, opinion (second model), blur, heartbeat watch, digest, alerts,
  unlock codes, settings. Business logic lives here only.
- `app/store`: SQLite repository modules; SQL lives only here.
- `app/clients`: SMTP mailer, Tailscale or OIDC identity (ADR-027), second-opinion model, encrypted blob store: each an interface with a fake. `app/clock.py` is the settable clock.
- Images: `data/images/<event_id>.{heavy,medium}.webp`, AES-GCM encrypted with a key in
  `data/keys/` (mode 600). Originals live only in memory during the second opinion.
- Jobs: one in-process loop every minute: heartbeat watch, digest when due, retention.
- Heartbeats are kept one row each (`heartbeats` table) so the portal draws coverage hour by hour
  and the digest counts protected hours; the server derives protection events from them (ADR-015).

## Threat model (short)

| Who | Wants | Answer |
|---|---|---|
| The protected person in a weak moment | turn it off quietly | every off path is a partner-visible event; pauses need partner codes |
| A partner | see more than intended | originals never stored; medium blur max; peeks rate limited and logged |
| Someone on the internet | anything | nothing is public; tailnet only |
| A friend's household | our data | separate servers; a witness token reads only the record, which holds digests, not pictures or addresses (ADR-054) |
| The protected person who also runs the server, perhaps with an AI agent | loosen, rewrite or switch off quietly | setup runs once, every loosening alerts at once (ADR-050); once partners hold keys loosening needs a partner's signature and what partners review is sealed (ADR-053); the database must match a hash-chained record whose head is in every email, in partner-signed checkpoints and on a partner's own server, which also notices the server going quiet (ADR-052, ADR-054). Not covered: someone who controls the server and replaces its code before partners set up keys, or whose partners neither host nor read their email |
| A thief with the server disk | images | blurred only, encrypted at rest |
