"""Sicherung und Wiederherstellung der eigenen Einträge: Alben, Favoriten, Datumskorrekturen.

Diese Angaben gibt es nur in der lokalen Datenbank. Die Sicherung ist eine kleine
JSON-Datei, die sich auch auf einem anderen PC wieder einspielen lässt: Bilder
werden dort über Ordner und Dateinamen bzw. Name und Größe wiedergefunden.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import __version__, config, dates

FORMAT = "bilderkatalog-sicherung"
AUTO_KEEP = 20  # so viele automatische Sicherungen bleiben erhalten


def collect(conn: sqlite3.Connection) -> dict:
    refs: dict[str, dict] = {}

    def ref(photo_id: str) -> str:
        if photo_id not in refs:
            row = conn.execute("SELECT name, parent_path, size FROM photo WHERE id = ?", (photo_id,)).fetchone()
            refs[photo_id] = {"id": photo_id, "path": row["parent_path"], "name": row["name"], "size": row["size"]}
        return photo_id

    albums = []
    for album in conn.execute("SELECT id, name, description, created_at FROM album ORDER BY id").fetchall():
        photos = [ref(r["photo_id"]) for r in conn.execute(
            "SELECT photo_id FROM album_photo WHERE album_id = ? ORDER BY added_at, photo_id", (album["id"],))]
        albums.append({"name": album["name"], "description": album["description"],
                       "created_at": album["created_at"], "photos": photos})
    favorites = [ref(r["id"]) for r in conn.execute("SELECT id FROM photo WHERE favorite = 1 ORDER BY id")]
    manual = [{"photo": ref(r["photo_id"]), "taken_at": r["taken_at"], "precision": r["precision"]}
              for r in conn.execute("SELECT photo_id, taken_at, precision FROM date_override ORDER BY photo_id")]
    return {
        "format": FORMAT,
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "app_version": __version__,
        "photos": list(refs.values()),
        "albums": albums,
        "favorites": favorites,
        "dates": manual,
    }


def counts(data: dict) -> dict:
    return {"albums": len(data["albums"]), "favorites": len(data["favorites"]), "dates": len(data["dates"])}


def write(conn: sqlite3.Connection, target_dir: Path) -> dict:
    data = collect(conn)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"Bilderkatalog-Sicherung_{datetime.now():%Y-%m-%d_%H%M%S}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"path": str(path), **counts(data)}


# -- Automatische Sicherung (im Datenverzeichnis) -----------------------------

def auto_dir() -> Path:
    return config.data_dir() / "sicherungen"


def auto_backup(conn: sqlite3.Connection) -> Path | None:
    """Sichert, wenn es eigene Einträge gibt und sich seit der letzten Sicherung etwas geändert hat."""
    data = collect(conn)
    if not any(counts(data).values()):
        return None
    folder = auto_dir()
    existing = sorted(folder.glob("Bilderkatalog-Sicherung_*.json"))
    if existing:
        try:
            last = json.loads(existing[-1].read_text(encoding="utf-8"))
            if _content(last) == _content(data):
                return None
        except (OSError, ValueError):
            pass
    path = Path(write(conn, folder)["path"])
    for old in sorted(folder.glob("Bilderkatalog-Sicherung_*.json"))[:-AUTO_KEEP]:
        old.unlink(missing_ok=True)
    return path


def _content(data: dict) -> tuple:
    return tuple(json.dumps(data.get(k), sort_keys=True) for k in ("photos", "albums", "favorites", "dates"))


def list_auto() -> list[dict]:
    result = []
    for path in sorted(auto_dir().glob("Bilderkatalog-Sicherung_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append({"file": path.name, "created": data.get("created"), **counts(data)})
        except (OSError, ValueError, KeyError):
            continue
    return result


def read_auto(file: str) -> dict:
    path = auto_dir() / Path(file).name  # nur Dateien aus dem Sicherungsordner
    if not path.name.startswith("Bilderkatalog-Sicherung_") or not path.is_file():
        raise ValueError("Sicherung nicht gefunden.")
    return json.loads(path.read_text(encoding="utf-8"))


# -- Wiederherstellen ----------------------------------------------------------

def restore(conn: sqlite3.Connection, data: dict) -> dict:
    """Spielt eine Sicherung ein, ohne Vorhandenes zu löschen (Alben gleichen Namens werden ergänzt)."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Das ist keine Sicherung des Bilderkatalogs.")
    found = _match(conn, data.get("photos", []))
    missing: set[str] = {r["id"] for r in data.get("photos", []) if r["id"] not in found}

    albums_new = assigned = 0
    for album in data.get("albums", []):
        row = conn.execute("SELECT id FROM album WHERE name = ?", (album["name"],)).fetchone()
        if row is None:
            album_id = conn.execute("INSERT INTO album(name, description) VALUES (?, ?)",
                                    (album["name"], album.get("description", ""))).lastrowid
            albums_new += 1
        else:
            album_id = row["id"]
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO album_photo(album_id, photo_id) VALUES (?, ?)",
                         [(album_id, found[p]) for p in album.get("photos", []) if p in found])
        assigned += conn.total_changes - before
    conn.commit()

    favorites = [found[p] for p in data.get("favorites", []) if p in found]
    conn.executemany("UPDATE photo SET favorite = 1 WHERE id = ?", [(i,) for i in favorites])
    conn.commit()

    dated = 0
    for entry in data.get("dates", []):
        if entry["photo"] in found:
            dated += dates.set_date(conn, [found[entry["photo"]]], entry["taken_at"])
            conn.execute("UPDATE date_override SET precision = ? WHERE photo_id = ?",
                         (entry.get("precision", "time"), found[entry["photo"]]))
    conn.commit()
    return {"albums_new": albums_new, "assigned": assigned, "favorites": len(favorites),
            "dates": dated, "not_found": len(missing)}


def _match(conn: sqlite3.Connection, refs: list[dict]) -> dict[str, str]:
    """Ordnet gesicherte Bilder den Bildern im Katalog zu: ID → Ordner+Name → Name+Größe."""
    rows = conn.execute("SELECT id, name, parent_path, size FROM photo WHERE deleted = 0").fetchall()
    by_id = {r["id"] for r in rows}
    by_path: dict[tuple, list[str]] = {}
    by_name: dict[tuple, list[str]] = {}
    for r in rows:
        by_path.setdefault(((r["parent_path"] or "").lower(), r["name"].lower()), []).append(r["id"])
        by_name.setdefault((r["name"].lower(), r["size"]), []).append(r["id"])
    result = {}
    for ref in refs:
        if ref["id"] in by_id:
            result[ref["id"]] = ref["id"]
            continue
        for candidates in (by_path.get(((ref.get("path") or "").lower(), ref["name"].lower())),
                           by_name.get((ref["name"].lower(), ref.get("size")))):
            if candidates and len(candidates) == 1:
                result[ref["id"]] = candidates[0]
                break
    return result
