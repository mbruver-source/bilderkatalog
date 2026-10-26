"""Bilder löschen – nur lokale Dateien, nur in den Papierkorb, nur nach ausdrücklicher Bestätigung.

Die einzige Funktion des Programms, die Dateien verändert. Sie wird ausschließlich
über die Oberfläche mit einem eigenen Bestätigungsdialog ausgelöst.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from . import local_source, winfiles

BATCH = 100


def summary(conn: sqlite3.Connection, photo_ids: list[str]) -> dict:
    """Was würde gelöscht? Für den Bestätigungsdialog."""
    rows = _rows(conn, photo_ids)
    local = [r for r in rows if _deletable(r)]
    return {
        "count": len(local),
        "bytes": sum(r["size"] or 0 for r in local),
        "online_only": sum(1 for r in local if r["online_only"]),
        "favorites": sum(1 for r in local if r["favorite"]),
        "in_album": sum(1 for r in local if r["in_album"]),
        "onedrive": sum(1 for r in local if _in_onedrive(r["local_path"])),
        "not_local": len(rows) - len(local),
        "examples": [{"name": r["name"], "folder": r["parent_path"]} for r in local[:8]],
    }


def delete(
    conn: sqlite3.Connection,
    photo_ids: list[str],
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    """Verschiebt die Dateien in den Papierkorb und markiert sie im Katalog als gelöscht."""
    local = [r for r in _rows(conn, photo_ids) if _deletable(r)]
    deleted, failed = [], []
    for start in range(0, len(local), BATCH):
        if cancelled():
            break
        chunk = local[start:start + BATCH]
        winfiles.move_to_recycle_bin([Path(r["local_path"]) for r in chunk if os.path.lexists(r["local_path"])])
        weg = [r for r in chunk if not os.path.lexists(r["local_path"])]
        failed += [r for r in chunk if r not in weg]
        deleted += weg
        # Wie beim Einlesen einer fehlenden Datei: im Katalog als gelöscht markieren.
        # Holt man sie aus dem Papierkorb zurück, findet "Aktualisieren" sie samt Alben wieder.
        conn.executemany("UPDATE photo SET deleted = 1 WHERE id = ?", [(r["id"],) for r in weg])
        conn.commit()
        if progress:
            progress(start + len(chunk), len(local))
    for r in deleted:
        for size in local_source.THUMB_PX:
            local_source.thumb_cache_file(r["id"], size).unlink(missing_ok=True)
    return {"deleted": len(deleted), "failed": [r["name"] for r in failed][:20], "failed_count": len(failed)}


def _deletable(row: sqlite3.Row) -> bool:
    return row["source"] == "local" and bool(row["local_path"])


def _rows(conn: sqlite3.Connection, photo_ids: list[str]) -> list[sqlite3.Row]:
    rows = []
    for start in range(0, len(photo_ids), 500):  # SQLite begrenzt die Zahl der Platzhalter
        chunk = photo_ids[start:start + 500]
        rows += conn.execute(
            f"""SELECT p.id, p.source, p.local_path, p.name, p.parent_path, p.size, p.online_only, p.favorite,
                       EXISTS (SELECT 1 FROM album_photo ap WHERE ap.photo_id = p.id) AS in_album
                FROM photo p WHERE p.deleted = 0 AND p.id IN ({",".join("?" * len(chunk))})""",
            chunk,
        ).fetchall()
    return rows


def _in_onedrive(path: str) -> bool:
    return any(Path(path).is_relative_to(root) for root in winfiles.onedrive_roots())
