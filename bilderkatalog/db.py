"""SQLite-Datenbank: Schema und Verbindungen."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS photo (
    id            TEXT PRIMARY KEY,      -- OneDrive-Item-ID
    name          TEXT NOT NULL,
    parent_id     TEXT,
    parent_path   TEXT,
    size          INTEGER,
    mime          TEXT,
    is_video      INTEGER NOT NULL DEFAULT 0,
    taken_at      TEXT,                  -- ISO 8601, lokale Aufnahmezeit
    date_source   TEXT,                  -- 'exif' | 'filename' | 'file'
    year          INTEGER,
    month         INTEGER,
    camera_make   TEXT,
    camera_model  TEXT,
    width         INTEGER,
    height        INTEGER,
    lat           REAL,
    lon           REAL,
    city          TEXT,
    region        TEXT,
    country_code  TEXT,
    geocoded      INTEGER NOT NULL DEFAULT 0,
    web_url       TEXT,
    etag          TEXT,
    deleted       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_photo_taken   ON photo(taken_at);
CREATE INDEX IF NOT EXISTS ix_photo_ym      ON photo(year, month);
CREATE INDEX IF NOT EXISTS ix_photo_place   ON photo(country_code, city);
CREATE INDEX IF NOT EXISTS ix_photo_parent  ON photo(parent_id);
CREATE INDEX IF NOT EXISTS ix_photo_path    ON photo(parent_path);

CREATE TABLE IF NOT EXISTS folder (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_id   TEXT,
    path        TEXT,
    fixed_path  INTEGER NOT NULL DEFAULT 0  -- Pfad bekannt (Wurzel eines Bereichs)
);
CREATE INDEX IF NOT EXISTS ix_folder_parent ON folder(parent_id);

CREATE TABLE IF NOT EXISTS scope (
    item_id     TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    delta_link  TEXT,
    last_sync   TEXT
);

CREATE TABLE IF NOT EXISTS album (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS album_photo (
    album_id    INTEGER NOT NULL REFERENCES album(id) ON DELETE CASCADE,
    photo_id    TEXT NOT NULL REFERENCES photo(id) ON DELETE CASCADE,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (album_id, photo_id)
);
CREATE INDEX IF NOT EXISTS ix_album_photo_photo ON album_photo(photo_id);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    # Jede Anfrage/Aufgabe nutzt eine eigene Verbindung; FastAPI kann sie aber
    # in einem anderen Worker-Thread anlegen, als dem, der sie verwendet.
    conn = sqlite3.connect(path or config.db_path(), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
