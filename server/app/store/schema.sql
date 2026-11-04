-- Watchman server schema. Applied in full on first run; later changes are numbered migrations
-- in app/store/migrations/ (none yet). Times are RFC 3339 UTC strings. IDs are UUIDv4 strings.

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL            -- JSON
);

CREATE TABLE IF NOT EXISTS settings_changes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  login      TEXT NOT NULL,
  key        TEXT NOT NULL,
  old_value  TEXT,
  new_value  TEXT,
  reported   INTEGER NOT NULL DEFAULT 0   -- included in a digest
);

CREATE TABLE IF NOT EXISTS people (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  email      TEXT,
  created_at TEXT NOT NULL
);

-- Who a Tailscale login is on the portal. A login may hold several rows.
-- role: admin (person_id NULL), protected (person_id), partner (person_id, partner_id).
CREATE TABLE IF NOT EXISTS logins (
  login      TEXT NOT NULL,
  role       TEXT NOT NULL,
  person_id  TEXT,
  partner_id TEXT,
  PRIMARY KEY (login, role, person_id)
);

CREATE TABLE IF NOT EXISTS partners (
  id          TEXT PRIMARY KEY,
  person_id   TEXT NOT NULL REFERENCES people(id),
  name        TEXT NOT NULL,
  email       TEXT NOT NULL,
  link_type   TEXT NOT NULL,       -- linked | email
  login       TEXT,                -- Tailscale login for linked partners
  created_at  TEXT NOT NULL,
  trusted_at  TEXT                 -- settings codes from this partner count from here (ADR-050)
);

CREATE TABLE IF NOT EXISTS pairing_codes (
  code        TEXT PRIMARY KEY,
  person_id   TEXT NOT NULL REFERENCES people(id),
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  id                TEXT PRIMARY KEY,
  person_id         TEXT NOT NULL REFERENCES people(id),
  name              TEXT NOT NULL,
  platform          TEXT NOT NULL,
  kind              TEXT NOT NULL,
  app_version       TEXT,
  token_hash        TEXT NOT NULL UNIQUE,
  created_at        TEXT NOT NULL,
  revoked_at        TEXT,
  last_heartbeat_at TEXT,
  last_state        TEXT,          -- JSON of the heartbeat body
  silent_reported_at TEXT,         -- when a "device silent" tamper event was raised for the current gap
  reminded_at       TEXT,          -- last alert while this device is off or silent (ADR-050)
  not_judging_at    TEXT           -- when "screen on, watched, nothing judged" was raised (ADR-051)
);

-- One row per heartbeat, so the portal can draw coverage and the digest can count protected hours.
-- Purged with event retention.
CREATE TABLE IF NOT EXISTS heartbeats (
  device_id   TEXT NOT NULL REFERENCES devices(id),
  at          TEXT NOT NULL,
  protection  INTEGER NOT NULL,     -- 1 when state.protection was true
  -- The heartbeat's judge block (ADR-051): seconds the screen was on, of those the seconds nothing
  -- was meant to be judged, and how many checks ran a model. NULL when the device does not say.
  screen_on_s INTEGER,
  unwatched_s INTEGER,
  judged      INTEGER,
  PRIMARY KEY (device_id, at)
);

-- Seconds per app that a screen Watchman cannot see was in front, per device per household day
-- (ADR-051). Purged with event retention.
CREATE TABLE IF NOT EXISTS secure_apps (
  device_id   TEXT NOT NULL REFERENCES devices(id),
  person_id   TEXT NOT NULL REFERENCES people(id),
  day         TEXT NOT NULL,
  app         TEXT NOT NULL,
  seconds     INTEGER NOT NULL,
  PRIMARY KEY (device_id, day, app)
);

-- Apps already announced for hiding their screen, so each one alerts once (ADR-051).
CREATE TABLE IF NOT EXISTS secure_apps_seen (
  person_id   TEXT NOT NULL REFERENCES people(id),
  app         TEXT NOT NULL,
  first_at    TEXT NOT NULL,
  PRIMARY KEY (person_id, app)
);

-- Work metrics from heartbeats (ADR-025), merged per device per household day. Counts and
-- milliseconds only. Purged with event retention.
CREATE TABLE IF NOT EXISTS device_metrics (
  device_id   TEXT NOT NULL REFERENCES devices(id),
  day         TEXT NOT NULL,        -- YYYY-MM-DD in the household time zone
  data        TEXT NOT NULL,        -- JSON snapshot, see services/metrics.py
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (device_id, day)
);

CREATE TABLE IF NOT EXISTS events (
  id            TEXT PRIMARY KEY,
  device_id     TEXT NOT NULL REFERENCES devices(id),
  person_id     TEXT NOT NULL REFERENCES people(id),
  type          TEXT NOT NULL,
  occurred_at   TEXT NOT NULL,
  received_at   TEXT NOT NULL,
  app           TEXT,
  url           TEXT,
  title         TEXT,
  action        TEXT NOT NULL,
  scores        TEXT,              -- JSON
  region        TEXT,              -- JSON
  hash          TEXT,
  sealed        TEXT,              -- url and title sealed for partners (ADR-053), JSON envelope
  has_image     INTEGER NOT NULL DEFAULT 0,
  detail        TEXT,              -- JSON
  -- Server-side judgement. image_status: none | wanted | stored | skipped.
  image_status  TEXT NOT NULL DEFAULT 'none',
  second_opinion REAL,
  severity      TEXT NOT NULL DEFAULT 'info',   -- info | review | explicit | protection
  review_state  TEXT NOT NULL DEFAULT 'open',   -- open | talked | false_alarm
  reviewed_by   TEXT,
  reviewed_at   TEXT,
  alerted_at    TEXT
);
CREATE INDEX IF NOT EXISTS events_person_time ON events(person_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS events_severity ON events(person_id, severity, review_state);

CREATE TABLE IF NOT EXISTS images (
  event_id    TEXT PRIMARY KEY REFERENCES events(id),
  created_at  TEXT NOT NULL,
  width       INTEGER NOT NULL,
  height      INTEGER NOT NULL,
  sealed      INTEGER NOT NULL DEFAULT 0   -- the medium blur is sealed for partners (ADR-053)
);

CREATE TABLE IF NOT EXISTS known_bad_hashes (
  hash        TEXT PRIMARY KEY,
  person_id   TEXT,                -- NULL applies to everyone on this server
  added_at    TEXT NOT NULL,
  source      TEXT NOT NULL        -- confirmed | manual
);

CREATE TABLE IF NOT EXISTS unlock_codes (
  id          TEXT PRIMARY KEY,
  code_hash   TEXT NOT NULL UNIQUE,
  person_id   TEXT NOT NULL REFERENCES people(id),
  partner_id  TEXT NOT NULL REFERENCES partners(id),
  minutes     INTEGER NOT NULL,
  purpose     TEXT NOT NULL DEFAULT 'pause',   -- pause | settings
  signed      INTEGER NOT NULL DEFAULT 0,      -- issued with a partner's signed approval (ADR-053)
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT
);

CREATE TABLE IF NOT EXISTS pauses (
  id          TEXT PRIMARY KEY,
  person_id   TEXT NOT NULL REFERENCES people(id),
  code_id     TEXT NOT NULL REFERENCES unlock_codes(id),
  started_at  TEXT NOT NULL,
  ends_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unlock_requests (
  id          TEXT PRIMARY KEY,
  person_id   TEXT NOT NULL REFERENCES people(id),
  device_id   TEXT NOT NULL REFERENCES devices(id),
  minutes     INTEGER NOT NULL,
  reason      TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS peeks (
  id          TEXT PRIMARY KEY,
  event_id    TEXT NOT NULL REFERENCES events(id),
  partner_id  TEXT NOT NULL REFERENCES partners(id),
  at          TEXT NOT NULL,
  token_hash  TEXT NOT NULL,
  token_expires_at TEXT NOT NULL,
  token_used_at TEXT
);

-- Peeks by the protected person at their own items, when self_peek is on (ADR-048).
CREATE TABLE IF NOT EXISTS self_peeks (
  id          TEXT PRIMARY KEY,
  event_id    TEXT NOT NULL REFERENCES events(id),
  person_id   TEXT NOT NULL REFERENCES people(id),
  at          TEXT NOT NULL,
  token_hash  TEXT NOT NULL,
  token_expires_at TEXT NOT NULL,
  token_used_at TEXT
);

-- Wrong partner codes, to slow guessing (ADR-050). scope is a device id for pause codes, or
-- "settings" for settings codes typed on the portal.
CREATE TABLE IF NOT EXISTS code_attempts (
  scope       TEXT NOT NULL,
  at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS code_attempts_scope ON code_attempts(scope, at);

-- The record (ADR-052): an append-only hash chain, see app/store/ledger_store.py.
CREATE TABLE IF NOT EXISTS ledger (
  seq         INTEGER PRIMARY KEY,
  at          TEXT NOT NULL,
  kind        TEXT NOT NULL,
  person_id   TEXT,
  ref         TEXT,
  body        TEXT NOT NULL,        -- canonical JSON
  prev        TEXT NOT NULL,
  hash        TEXT NOT NULL
);

-- Tokens a partner's own server uses to read the record (ADR-054). Stored hashed.
CREATE TABLE IF NOT EXISTS witness_tokens (
  id          TEXT PRIMARY KEY,
  partner_id  TEXT NOT NULL,
  token_hash  TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL,
  last_seen_at TEXT,
  revoked_at  TEXT
);

-- Households this server watches for its own people (ADR-054): another household's server,
-- read with a witness token, checked every few minutes.
CREATE TABLE IF NOT EXISTS watching (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  url         TEXT NOT NULL,
  token       TEXT NOT NULL,
  email       TEXT NOT NULL,        -- who hears about problems
  last_seq    INTEGER NOT NULL DEFAULT 0,
  last_hash   TEXT,
  keys        TEXT NOT NULL DEFAULT '{}',   -- partner keys seen on that record, pinned (JSON)
  last_ok_at  TEXT,
  last_try_at TEXT,
  last_error  TEXT,
  down_alerted_at TEXT,
  last_report TEXT,                 -- the last problem emailed, so it is not repeated every poll
  last_report_at TEXT,
  created_at  TEXT NOT NULL
);

-- Partner keys (ADR-053). The private halves are here only locked with the partner's passphrase.
CREATE TABLE IF NOT EXISTS partner_keys (
  partner_id  TEXT PRIMARY KEY,
  sign_pub    TEXT NOT NULL,        -- ECDSA P-256, SPKI DER, base64
  enc_pub     TEXT NOT NULL,        -- ECDH P-256, uncompressed point, base64
  wrapped     TEXT NOT NULL,        -- JSON: salt, iterations, iv, data (the locked private keys)
  fingerprint TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  active_from TEXT NOT NULL         -- a replaced key approves nothing for three days
);

-- Single-use nonces a partner's browser signs into an approval (ADR-053).
CREATE TABLE IF NOT EXISTS challenges (
  nonce       TEXT PRIMARY KEY,
  partner_id  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT
);

-- Changes waiting for a partner's signed approval (ADR-053).
CREATE TABLE IF NOT EXISTS approvals (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,        -- settings | partner_add | partner_remove
  person_id   TEXT,                 -- NULL for household settings
  params      TEXT NOT NULL,        -- JSON
  summary     TEXT NOT NULL,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'waiting',   -- waiting | approved | declined
  decided_by  TEXT,
  decided_at  TEXT
);

CREATE TABLE IF NOT EXISTS digests (
  id           TEXT PRIMARY KEY,
  person_id    TEXT NOT NULL REFERENCES people(id),
  period_start TEXT NOT NULL,
  period_end   TEXT NOT NULL,
  sent_at      TEXT NOT NULL
);
