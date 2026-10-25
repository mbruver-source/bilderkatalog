"""Virtuelle Ordner (Alben). Existieren nur in der lokalen Datenbank."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable


def list_albums(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.id, a.name, a.description, a.created_at,
               COUNT(p.id) AS n,
               (SELECT ap2.photo_id FROM album_photo ap2 JOIN photo p2 ON p2.id = ap2.photo_id
                WHERE ap2.album_id = a.id AND p2.deleted = 0
                ORDER BY p2.taken_at LIMIT 1) AS cover
        FROM album a
        LEFT JOIN album_photo ap ON ap.album_id = a.id
        LEFT JOIN photo p ON p.id = ap.photo_id AND p.deleted = 0
        GROUP BY a.id
        ORDER BY a.name COLLATE NOCASE
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_album(conn: sqlite3.Connection, album_id: int) -> dict | None:
    row = conn.execute("SELECT id, name, description, created_at FROM album WHERE id = ?", (album_id,)).fetchone()
    return dict(row) if row else None


def create_album(conn: sqlite3.Connection, name: str, description: str = "") -> dict:
    name = _clean_name(name)
    cur = conn.execute("INSERT INTO album(name, description) VALUES (?, ?)", (name, description))
    conn.commit()
    return get_album(conn, cur.lastrowid)


def update_album(conn: sqlite3.Connection, album_id: int, name: str | None = None, description: str | None = None) -> dict | None:
    if name is not None:
        conn.execute("UPDATE album SET name = ? WHERE id = ?", (_clean_name(name), album_id))
    if description is not None:
        conn.execute("UPDATE album SET description = ? WHERE id = ?", (description, album_id))
    conn.commit()
    return get_album(conn, album_id)


def delete_album(conn: sqlite3.Connection, album_id: int) -> None:
    """Löscht nur das Album, niemals die Bilder."""
    conn.execute("DELETE FROM album WHERE id = ?", (album_id,))
    conn.commit()


def add_photos(conn: sqlite3.Connection, album_id: int, photo_ids: Iterable[str]) -> int:
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO album_photo(album_id, photo_id) "
        "SELECT ?, id FROM photo WHERE id = ?",
        [(album_id, pid) for pid in photo_ids],
    )
    conn.commit()
    return conn.total_changes - before


def remove_photos(conn: sqlite3.Connection, album_id: int, photo_ids: Iterable[str]) -> int:
    before = conn.total_changes
    conn.executemany(
        "DELETE FROM album_photo WHERE album_id = ? AND photo_id = ?",
        [(album_id, pid) for pid in photo_ids],
    )
    conn.commit()
    return conn.total_changes - before


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("Der Albumname darf nicht leer sein.")
    return name[:200]
