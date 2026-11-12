-- SQLite schema for the Moda configuration UI.
-- The database is the source of truth; config/*.json and .env files are generated from it.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('admin', 'viewer')),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cameras (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT    NOT NULL UNIQUE,
    name         TEXT    NOT NULL,
    purpose      TEXT    NOT NULL CHECK (purpose IN ('lane', 'weight', 'cargo')),
    env_name     TEXT    NOT NULL UNIQUE,
    rtsp_url_enc TEXT    NOT NULL,
    frame_width  INTEGER,
    frame_height INTEGER,
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reference_plate_lists (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS reference_plates (
    list_id INTEGER NOT NULL REFERENCES reference_plate_lists (id) ON DELETE CASCADE,
    plate   TEXT    NOT NULL,
    UNIQUE (list_id, plate)
);

CREATE TABLE IF NOT EXISTS sites (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    code                       TEXT    NOT NULL UNIQUE,
    name                       TEXT    NOT NULL,
    vehicle_confidence         REAL    NOT NULL CHECK (vehicle_confidence BETWEEN 0 AND 1),
    plate_character_confidence REAL    NOT NULL CHECK (plate_character_confidence BETWEEN 0 AND 1),
    plate_collection_seconds   REAL    NOT NULL CHECK (plate_collection_seconds > 0),
    video_fps                  REAL    NOT NULL CHECK (video_fps > 0),
    output_topic               TEXT    NOT NULL,
    output_partition           INTEGER NOT NULL CHECK (output_partition >= 0),
    publish_timeout_seconds    REAL    NOT NULL CHECK (publish_timeout_seconds > 0),
    vehicle_image_root         TEXT    NOT NULL,
    model_tracker              TEXT    NOT NULL,
    model_vehicle_detection    TEXT    NOT NULL,
    model_plate_detection      TEXT    NOT NULL,
    model_plate_recognition    TEXT    NOT NULL,
    reference_plate_list_id    INTEGER NOT NULL REFERENCES reference_plate_lists (id),
    updated_at                 TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS site_sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id      INTEGER NOT NULL REFERENCES sites (id) ON DELETE CASCADE,
    source_index INTEGER NOT NULL CHECK (source_index BETWEEN 0 AND 3),
    camera_id    INTEGER NOT NULL REFERENCES cameras (id),
    trigger_code TEXT    NOT NULL,
    UNIQUE (site_id, source_index)
);

CREATE TABLE IF NOT EXISTS zones (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    site_source_id INTEGER NOT NULL REFERENCES site_sources (id) ON DELETE CASCADE,
    kind           TEXT    NOT NULL CHECK (kind IN ('capture', 'inference')),
    points_json    TEXT    NOT NULL,
    UNIQUE (site_source_id, kind)
);

-- Values are JSON encoded so numbers keep their type.
CREATE TABLE IF NOT EXISTS system_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyzer_settings (
    analyzer TEXT NOT NULL CHECK (analyzer IN ('weight', 'cargo')),
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    PRIMARY KEY (analyzer, key)
);

CREATE TABLE IF NOT EXISTS analyzer_lanes (
    analyzer      TEXT    NOT NULL CHECK (analyzer IN ('weight', 'cargo')),
    trigger_code  TEXT    NOT NULL,
    analysis_code TEXT    NOT NULL,
    crop_x_min    INTEGER,
    crop_x_max    INTEGER,
    crop_y_min    INTEGER,
    crop_y_max    INTEGER,
    camera_id     INTEGER REFERENCES cameras (id),
    PRIMARY KEY (analyzer, trigger_code)
);

CREATE TABLE IF NOT EXISTS config_revisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    user_id       INTEGER REFERENCES users (id),
    note          TEXT    NOT NULL DEFAULT '',
    snapshot_json TEXT    NOT NULL,
    files_diff    TEXT    NOT NULL DEFAULT '',
    services      TEXT    NOT NULL DEFAULT '[]',
    status        TEXT    NOT NULL CHECK (status IN ('applied', 'failed', 'rolled_back')),
    log           TEXT    NOT NULL DEFAULT ''
);
