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
  created_at  TEXT NOT NULL
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
  silent_reported_at TEXT          -- when a "device silent" tamper event was raised for the current gap
);

-- One row per heartbeat, so the portal can draw coverage and the digest can count protected hours.
-- Purged with event retention.
CREATE TABLE IF NOT EXISTS heartbeats (
  device_id   TEXT NOT NULL REFERENCES devices(id),
  at          TEXT NOT NULL,
  protection  INTEGER NOT NULL,     -- 1 when state.protection was true
  PRIMARY KEY (device_id, at)
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
  height      INTEGER NOT NULL
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

CREATE TABLE IF NOT EXISTS digests (
  id           TEXT PRIMARY KEY,
  person_id    TEXT NOT NULL REFERENCES people(id),
  period_start TEXT NOT NULL,
  period_end   TEXT NOT NULL,
  sent_at      TEXT NOT NULL
);
