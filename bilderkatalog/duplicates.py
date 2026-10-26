"""Doppelte Dateien erkennen – nur anhand der Metadaten, ohne Dateien zu öffnen.

Zwei Dateien gelten als gleich, wenn sie gleich groß sind und
  - dasselbe Aufnahmedatum aus der Kamera (EXIF) haben, oder
  - denselben Dateinamen tragen.
Beides zusammen ist bei verschiedenen Fotos praktisch ausgeschlossen, trifft aber
die typischen Kopien (Kamera-Upload, Handy-Sicherung, SD-Karte, Ordner-Kopien).
Neu komprimierte Fassungen (z. B. WhatsApp) sind andere Dateien und zählen nicht.

Je Gruppe wird ein Hauptexemplar bestimmt (dup_rank = 0); die übrigen sind Kopien.
Gelöscht wird nichts – die Kopien lassen sich nur ausblenden.
"""

from __future__ import annotations

import re
import sqlite3

# Namen, die nach Kopie aussehen: "Bild (1).jpg", "Bild - Kopie.jpg", "Bild_copy2.jpg"
_COPY_NAME = re.compile(r"(\s\(\d+\)|[\s_-]+(kopie|copy)(\s?\(?\d+\)?)?)$", re.IGNORECASE)

# Ordner, in denen meist nur Weitergaben oder Sicherungen liegen
_SECONDARY = ("whatsapp", "backup", "sicherung", "kopie", "copy", "download", "temp")


def refresh(conn: sqlite3.Connection) -> dict:
    """Berechnet alle Gruppen neu. Gibt Anzahl der Gruppen und Kopien zurück."""
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.size, p.taken_at, p.date_source, p.meta_level, p.parent_path, p.favorite,
               EXISTS (SELECT 1 FROM album_photo ap WHERE ap.photo_id = p.id) AS in_album,
               o.auto_taken_at, o.auto_date_source
        FROM photo p LEFT JOIN date_override o ON o.photo_id = p.id
        WHERE p.deleted = 0 AND p.size > 0
        """
    ).fetchall()

    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_with: dict[tuple, int] = {}
    for i, row in enumerate(rows):
        taken, source = row["taken_at"], row["date_source"]
        if source == "manual":  # Vergleich mit dem Datum aus der Datei, nicht dem von Hand gesetzten
            taken, source = row["auto_taken_at"], row["auto_date_source"]
        keys = [("n", row["size"], row["name"].lower())]
        if source == "exif" and taken:
            keys.append(("t", row["size"], taken))
        for key in keys:
            j = first_with.setdefault(key, i)
            if j != i:
                parent[find(i)] = find(j)

    groups: dict[int, list[sqlite3.Row]] = {}
    for i, row in enumerate(rows):
        groups.setdefault(find(i), []).append(row)

    updates: list[tuple[int, int, str]] = []
    group_no = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        group_no += 1
        for rank, row in enumerate(sorted(members, key=_preference)):
            updates.append((group_no, rank, row["id"]))

    conn.execute("UPDATE photo SET dup_group = NULL, dup_rank = NULL WHERE dup_group IS NOT NULL")
    conn.executemany("UPDATE photo SET dup_group = ?, dup_rank = ? WHERE id = ?", updates)
    conn.commit()
    return {"groups": group_no, "copies": len(updates) - group_no}


def _preference(row: sqlite3.Row) -> tuple:
    """Sortierschlüssel: das am besten geeignete Exemplar zuerst."""
    path = (row["parent_path"] or "").lower()
    return (
        -row["favorite"],
        -row["in_album"],
        row["meta_level"] != "full",
        row["date_source"] not in ("exif", "manual"),
        any(word in path for word in _SECONDARY),
        bool(_COPY_NAME.search(row["name"].rsplit(".", 1)[0])),
        path.count("/"),
        path,
        row["name"].lower(),
    )


def copies_of(conn: sqlite3.Connection, photo_id: str) -> list[dict]:
    """Alle Exemplare derselben Gruppe (für die Großansicht)."""
    rows = conn.execute(
        """
        SELECT id, name, parent_path, dup_rank FROM photo
        WHERE deleted = 0 AND dup_group = (SELECT dup_group FROM photo WHERE id = ?)
        ORDER BY dup_rank
        """,
        (photo_id,),
    ).fetchall()
    return [dict(r) for r in rows]
