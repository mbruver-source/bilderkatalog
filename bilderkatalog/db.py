"""SQLite-Datenbank: Schema und Verbindungen."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS photo (
    id            TEXT PRIMARY KEY,      -- OneDrive-Item-ID bzw. "L:<Laufwerk>:<Datei-Index>"
    source        TEXT NOT NULL DEFAULT 'graph',  -- 'graph' | 'local'
    local_path    TEXT,                  -- nur lokal: absoluter Pfad
    mtime         REAL,                  -- nur lokal: Änderungszeit
    online_only   INTEGER NOT NULL DEFAULT 0,
    meta_level    TEXT NOT NULL DEFAULT 'full',  -- 'full' | 'partial' (nur Datum bekannt)
    scope_id      TEXT,
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
    deleted       INTEGER NOT NULL DEFAULT 0,
    favorite      INTEGER NOT NULL DEFAULT 0,
    dup_group     INTEGER,               -- gleiche Datei mehrfach vorhanden (siehe duplicates.py)
    dup_rank      INTEGER                -- 0 = Hauptexemplar, 1.. = Kopien
);
CREATE INDEX IF NOT EXISTS ix_photo_taken   ON photo(taken_at);
CREATE INDEX IF NOT EXISTS ix_photo_ym      ON photo(year, month);
CREATE INDEX IF NOT EXISTS ix_photo_place   ON photo(country_code, city);
CREATE INDEX IF NOT EXISTS ix_photo_parent  ON photo(parent_id);
CREATE INDEX IF NOT EXISTS ix_photo_path    ON photo(parent_path);
CREATE INDEX IF NOT EXISTS ix_photo_local   ON photo(local_path);
CREATE INDEX IF NOT EXISTS ix_photo_meta    ON photo(source, meta_level);
CREATE INDEX IF NOT EXISTS ix_photo_dup     ON photo(dup_group, dup_rank);
CREATE INDEX IF NOT EXISTS ix_photo_fav     ON photo(favorite) WHERE favorite = 1;

-- Von Hand gesetztes Aufnahmedatum. Gilt nur im Katalog, die Datei bleibt unverändert.
-- auto_* hält das automatisch ermittelte Datum für "zurücksetzen" fest.
CREATE TABLE IF NOT EXISTS date_override (
    photo_id          TEXT PRIMARY KEY REFERENCES photo(id) ON DELETE CASCADE,
    taken_at          TEXT NOT NULL,
    precision         TEXT NOT NULL DEFAULT 'time',  -- 'year' | 'month' | 'day' | 'time'
    auto_taken_at     TEXT,
    auto_date_source  TEXT
);

-- Einlesen, Nachladen und Online-Abgleich schreiben das automatische Datum.
-- Der Trigger merkt es sich und stellt danach das manuelle Datum wieder her.
CREATE TRIGGER IF NOT EXISTS tr_photo_keep_manual_date
AFTER UPDATE OF taken_at, date_source ON photo
WHEN NEW.date_source IS NOT 'manual' AND EXISTS (SELECT 1 FROM date_override WHERE photo_id = NEW.id)
BEGIN
    UPDATE date_override SET auto_taken_at = NEW.taken_at, auto_date_source = NEW.date_source
    WHERE photo_id = NEW.id;
    UPDATE photo SET
        taken_at = (SELECT taken_at FROM date_override WHERE photo_id = NEW.id),
        date_source = 'manual',
        year = CAST(substr((SELECT taken_at FROM date_override WHERE photo_id = NEW.id), 1, 4) AS INTEGER),
        month = CAST(substr((SELECT taken_at FROM date_override WHERE photo_id = NEW.id), 6, 2) AS INTEGER)
    WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS folder (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_id   TEXT,
    path        TEXT,
    fixed_path  INTEGER NOT NULL DEFAULT 0  -- Pfad bekannt (Wurzel eines Bereichs)
);
CREATE INDEX IF NOT EXISTS ix_folder_parent ON folder(parent_id);

CREATE TABLE IF NOT EXISTS scope (
    item_id     TEXT PRIMARY KEY,         -- Graph-Item-ID bzw. lokaler Ordnerpfad
    kind        TEXT NOT NULL DEFAULT 'graph',  -- 'graph' | 'local'
    path        TEXT NOT NULL,            -- Anzeigepfad
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

-- Von Hand gesetzter oder entfernter Ort (alle Ortsspalten NULL = "kein Ort").
-- auto_lat/auto_lon halten die GPS-Position aus der Datei für "zurücksetzen" fest.
CREATE TABLE IF NOT EXISTS place_override (
    photo_id      TEXT PRIMARY KEY REFERENCES photo(id) ON DELETE CASCADE,
    lat           REAL,
    lon           REAL,
    city          TEXT,
    region        TEXT,
    country_code  TEXT,
    auto_lat      REAL,
    auto_lon      REAL
);

-- Wie beim Datum: Einlesen, Nachladen und Ortsauflösung schreiben den automatischen Ort,
-- der Trigger merkt sich die GPS-Position aus der Datei und stellt den eigenen Ort wieder her.
CREATE TRIGGER IF NOT EXISTS tr_photo_keep_manual_place
AFTER UPDATE OF lat, lon, city, region, country_code ON photo
WHEN EXISTS (SELECT 1 FROM place_override o WHERE o.photo_id = NEW.id AND (
    NEW.lat IS NOT o.lat OR NEW.lon IS NOT o.lon OR NEW.city IS NOT o.city
    OR NEW.region IS NOT o.region OR NEW.country_code IS NOT o.country_code))
BEGIN
    UPDATE place_override SET auto_lat = NEW.lat, auto_lon = NEW.lon
    WHERE photo_id = NEW.id AND (NEW.lat IS NOT lat OR NEW.lon IS NOT lon);
    UPDATE photo SET
        lat = (SELECT lat FROM place_override WHERE photo_id = NEW.id),
        lon = (SELECT lon FROM place_override WHERE photo_id = NEW.id),
        city = (SELECT city FROM place_override WHERE photo_id = NEW.id),
        region = (SELECT region FROM place_override WHERE photo_id = NEW.id),
        country_code = (SELECT country_code FROM place_override WHERE photo_id = NEW.id),
        geocoded = 1
    WHERE id = NEW.id;
END;
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


MIGRATIONS = {
    2: [
        "ALTER TABLE photo ADD COLUMN source TEXT NOT NULL DEFAULT 'graph'",
        "ALTER TABLE photo ADD COLUMN local_path TEXT",
        "ALTER TABLE photo ADD COLUMN mtime REAL",
        "ALTER TABLE photo ADD COLUMN online_only INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE photo ADD COLUMN meta_level TEXT NOT NULL DEFAULT 'full'",
        "ALTER TABLE photo ADD COLUMN scope_id TEXT",
        "ALTER TABLE scope ADD COLUMN kind TEXT NOT NULL DEFAULT 'graph'",
    ],
    3: [
        "ALTER TABLE photo ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE photo ADD COLUMN dup_group INTEGER",
        "ALTER TABLE photo ADD COLUMN dup_rank INTEGER",
    ],
    4: [],  # nur neue Tabelle und Trigger (place_override), legt SCHEMA an
}


def init(conn: sqlite3.Connection) -> bool:
    """Legt das Schema an bzw. migriert es. True, wenn sich etwas geändert hat."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    has_tables = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'photo'").fetchone()
    if has_tables and version == SCHEMA_VERSION:
        return False  # aktuell – nur lesen, damit der Start nie auf eine Schreibsperre wartet
    if has_tables:
        for target in sorted(MIGRATIONS):
            if version < target:
                for statement in MIGRATIONS[target]:
                    conn.execute(statement)
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return True
