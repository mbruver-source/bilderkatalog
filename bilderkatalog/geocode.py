"""Offline-Ortsauflösung: GPS-Koordinaten → Stadt, Region, Land.

Nutzt den GeoNames-Datensatz von `reverse_geocoder`; es werden keine
Koordinaten an externe Dienste übertragen. Ländernamen werden erst in der
Oberfläche aus dem ISO-Code lokalisiert.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

BATCH = 2000

Lookup = Callable[[list[tuple[float, float]]], list[dict]]


def _default_lookup(coords: list[tuple[float, float]]) -> list[dict]:
    import reverse_geocoder  # lädt den Datensatz erst bei Bedarf (einige Sekunden)

    return reverse_geocoder.search(coords, mode=1, verbose=False)


def geocode_pending(conn: sqlite3.Connection, lookup: Lookup | None = None) -> int:
    """Löst alle noch nicht verorteten Koordinaten auf. Gibt die Anzahl zurück."""
    lookup = lookup or _default_lookup
    done = 0
    while True:
        rows = conn.execute(
            "SELECT id, lat, lon FROM photo WHERE geocoded = 0 AND lat IS NOT NULL AND lon IS NOT NULL LIMIT ?",
            (BATCH,),
        ).fetchall()
        if not rows:
            break
        results = lookup([(r["lat"], r["lon"]) for r in rows])
        conn.executemany(
            "UPDATE photo SET city = ?, region = ?, country_code = ?, geocoded = 1 WHERE id = ?",
            [
                (res.get("name") or None, res.get("admin1") or None, res.get("cc") or None, row["id"])
                for row, res in zip(rows, results)
            ],
        )
        conn.commit()
        done += len(rows)
    # Bilder ohne GPS gelten als erledigt, damit sie nicht erneut geprüft werden.
    conn.execute(
        "UPDATE photo SET geocoded = 1, city = NULL, region = NULL, country_code = NULL "
        "WHERE geocoded = 0 AND (lat IS NULL OR lon IS NULL)"
    )
    conn.commit()
    return done
