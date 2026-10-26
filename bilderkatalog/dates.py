"""Aufnahmedatum von Hand setzen (z. B. für eingescannte Fotos).

Das Datum gilt nur im Katalog – die Dateien bleiben unverändert. Ein Trigger in
db.py sorgt dafür, dass erneutes Einlesen es nicht überschreibt.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime

# 1998 | 07.1998 | 1998-07 | 14.07.1998 | 1998-07-14 – jeweils optional mit Uhrzeit 10:30[:15]
_TIME = r"(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
_PATTERNS = [
    (re.compile(r"(\d{4})"), "year", ("y",)),
    (re.compile(r"(\d{1,2})[./](\d{4})"), "month", ("m", "y")),
    (re.compile(r"(\d{4})-(\d{1,2})"), "month", ("y", "m")),
    (re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})" + _TIME), "day", ("d", "m", "y")),
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})" + _TIME), "day", ("y", "m", "d")),
]


def parse(text: str) -> tuple[str, str]:
    """Freie Eingabe → (ISO-Zeitpunkt, Genauigkeit). Unbekannte Teile werden zum Monats-/Jahresanfang."""
    text = (text or "").strip()
    for pattern, precision, order in _PATTERNS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        parts = dict(zip(order, (int(g) for g in match.groups()[:len(order)])))
        hour, minute, second = (int(g) if g else None for g in match.groups()[len(order):] or (None,) * 3)
        if hour is not None:
            precision = "time"
        try:
            value = datetime(parts["y"], parts.get("m", 1), parts.get("d", 1),
                             12 if hour is None else hour, minute or 0, second or 0)
        except ValueError:
            break
        if not 1826 <= value.year <= datetime.now().year + 1:
            break
        return value.isoformat(timespec="seconds"), precision
    raise ValueError("Datum nicht erkannt. Beispiele: 1998, 07.1998, 14.07.1998 oder 14.07.1998 10:30")


def set_date(conn: sqlite3.Connection, photo_ids: Iterable[str], text: str) -> int:
    taken_at, precision = parse(text)
    count = 0
    for photo_id in photo_ids:
        row = conn.execute("SELECT taken_at, date_source FROM photo WHERE id = ?", (photo_id,)).fetchone()
        if row is None:
            continue
        conn.execute(
            """
            INSERT INTO date_override (photo_id, taken_at, precision, auto_taken_at, auto_date_source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(photo_id) DO UPDATE SET taken_at = excluded.taken_at, precision = excluded.precision
            """,
            (photo_id, taken_at, precision, row["taken_at"], row["date_source"]),
        )
        conn.execute(
            "UPDATE photo SET taken_at = ?, date_source = 'manual', year = ?, month = ? WHERE id = ?",
            (taken_at, int(taken_at[:4]), int(taken_at[5:7]), photo_id),
        )
        count += 1
    conn.commit()
    return count


def reset(conn: sqlite3.Connection, photo_ids: Iterable[str]) -> int:
    """Stellt das automatisch ermittelte Datum wieder her."""
    count = 0
    for photo_id in photo_ids:
        row = conn.execute(
            "SELECT auto_taken_at, auto_date_source FROM date_override WHERE photo_id = ?", (photo_id,)
        ).fetchone()
        if row is None:
            continue
        # erst den Eintrag löschen, sonst stellt der Trigger das manuelle Datum sofort wieder her
        conn.execute("DELETE FROM date_override WHERE photo_id = ?", (photo_id,))
        taken = row["auto_taken_at"]
        conn.execute(
            "UPDATE photo SET taken_at = ?, date_source = ?, year = ?, month = ? WHERE id = ?",
            (taken, row["auto_date_source"], int(taken[:4]) if taken else None,
             int(taken[5:7]) if taken else None, photo_id),
        )
        count += 1
    conn.commit()
    return count


def precision_of(conn: sqlite3.Connection, photo_id: str) -> str | None:
    row = conn.execute("SELECT precision FROM date_override WHERE photo_id = ?", (photo_id,)).fetchone()
    return row["precision"] if row else None
