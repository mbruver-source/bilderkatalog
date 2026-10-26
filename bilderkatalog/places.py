"""Ort von Hand setzen, ändern oder entfernen – nur im Katalog, die Dateien bleiben unverändert.

Orte lassen sich suchen (bereits im Katalog vorhandene Orte und das GeoNames-
Ortsverzeichnis mit deutschen Namen, offline) oder als Koordinaten eingeben, z. B. aus
einer Karten-App. Ein Trigger in db.py hält den Ort über erneutes Einlesen hinweg fest.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from collections.abc import Iterable

from . import ortsnamen

_COORDS = re.compile(r"\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*")
_COORDS_COMMA = re.compile(r"\s*(-?\d{1,3}(?:,\d+)?)\s*[;\s]\s*(-?\d{1,3}(?:,\d+)?)\s*")

_index: list[tuple[str, str, ortsnamen.Ort]] | None = None
_index_lock = threading.Lock()


def _norm(text: str) -> str:
    """Kleinbuchstaben, ohne Akzente; ä/ae → a usw., damit "Höhe", "Hoehe" und "Hohe" gleich sind."""
    text = unicodedata.normalize("NFKD", (text or "").lower().replace("ß", "ss"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"([aou])e", r"\1", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _suchindex() -> list[tuple[str, str, ortsnamen.Ort]]:
    """(deutscher Name, ASCII-Name, Ort) in Vergleichsform, einmalig aufgebaut.
    Gesucht wird in beiden, damit "Lissabon" und "Lisbon" gleichermaßen funktionieren."""
    global _index
    with _index_lock:
        if _index is None:
            _index = [(_norm(o.name), _norm(o.name_ascii), o) for o in ortsnamen.orte()]
        return _index


def parse_coords(text: str) -> tuple[float, float] | None:
    """"48.8566, 2.3522" oder "48,8566 2,3522" → (Breite, Länge)."""
    match = _COORDS.fullmatch(text or "")
    if not match:
        match = _COORDS_COMMA.fullmatch(text or "")
    if not match:
        return None
    lat, lon = (float(g.replace(",", ".")) for g in match.groups())
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def search(conn: sqlite3.Connection, text: str, limit: int = 12) -> list[dict]:
    text = (text or "").strip()
    coords = parse_coords(text)
    if coords:
        place = _reverse(*coords)
        return [{**place, "lat": coords[0], "lon": coords[1], "source": "coords"}]
    query = _norm(text)
    if len(query) < 2:
        return []

    # 1. Orte, die schon im Katalog vorkommen (mit Anzahl)
    known = []
    for row in conn.execute(
        "SELECT city, region, country_code, AVG(lat) AS lat, AVG(lon) AS lon, COUNT(*) AS n FROM photo "
        "WHERE deleted = 0 AND city IS NOT NULL AND lat IS NOT NULL "
        "GROUP BY city, region, country_code ORDER BY n DESC"
    ):
        if _rank(_norm(row["city"]), query) < 3:
            known.append({"city": row["city"], "region": row["region"], "country_code": row["country_code"],
                          "lat": round(row["lat"], 6), "lon": round(row["lon"], 6), "n": row["n"],
                          "source": "catalog"})
        if len(known) >= 5:
            break

    # 2. Ortsverzeichnis: beste Übereinstimmung, dann größere Orte zuerst
    hits = []
    for name_de, name_ascii, ort in _suchindex():
        rank = min(_rank(name_de, query), _rank(name_ascii, query))
        if rank < 3:
            hits.append((rank, -ort.einwohner, len(name_de), ort))
    hits.sort(key=lambda h: h[:3])
    seen = {(k["city"], k["region"], k["country_code"]) for k in known}
    result = list(known)
    for *_, ort in hits:
        if len(result) >= limit:
            break
        if (ort.name, ort.region, ort.cc) in seen:
            continue
        result.append({"city": ort.name, "region": ort.region, "country_code": ort.cc,
                       "lat": ort.lat, "lon": ort.lon, "source": "geonames"})
    return result


def _rank(name: str, query: str) -> int:
    """0 = genau, 1 = Anfang, 2 = Wortanfang im Namen, 3 = kein Treffer."""
    if name == query:
        return 0
    if name.startswith(query):
        return 1
    return 2 if f" {query}" in f" {name}" else 3


def _reverse(lat: float, lon: float) -> dict:
    import reverse_geocoder

    res = ortsnamen.eindeutschen(reverse_geocoder.search([(lat, lon)], mode=1, verbose=False)[0])
    return {"city": res.get("name") or None, "region": res.get("admin1") or None, "country_code": res.get("cc") or None}


def set_place(conn: sqlite3.Connection, photo_ids: Iterable[str], place: dict | None) -> int:
    """Setzt den Ort (dict mit lat, lon, city, region, country_code) oder entfernt ihn (None)."""
    if place is None:
        values = {"lat": None, "lon": None, "city": None, "region": None, "country_code": None}
    else:
        try:
            lat, lon = float(place["lat"]), float(place["lon"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Ort ohne gültige Koordinaten.") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Koordinaten außerhalb des gültigen Bereichs.")
        values = {"lat": lat, "lon": lon}
        if place.get("city") or place.get("country_code"):
            values.update({k: place.get(k) or None for k in ("city", "region", "country_code")})
            # z. B. aus älteren Sicherungen mit englischen Namen ("Munich")
            values["city"], values["region"] = ortsnamen.ort_eindeutschen(
                values["city"], values["region"], values["country_code"], lat, lon)
        else:
            values.update(_reverse(lat, lon))
    count = 0
    for photo_id in photo_ids:
        row = conn.execute("SELECT lat, lon FROM photo WHERE id = ?", (photo_id,)).fetchone()
        if row is None:
            continue
        conn.execute(
            """
            INSERT INTO place_override (photo_id, lat, lon, city, region, country_code, auto_lat, auto_lon)
            VALUES (:id, :lat, :lon, :city, :region, :country_code, :auto_lat, :auto_lon)
            ON CONFLICT(photo_id) DO UPDATE SET lat = excluded.lat, lon = excluded.lon, city = excluded.city,
                region = excluded.region, country_code = excluded.country_code
            """,
            {"id": photo_id, **values, "auto_lat": row["lat"], "auto_lon": row["lon"]},
        )
        # Der Trigger überträgt den Ort aus place_override auf das Bild
        conn.execute(
            "UPDATE photo SET lat = :lat, lon = :lon, city = :city, region = :region, "
            "country_code = :country_code, geocoded = 1 WHERE id = :id",
            {"id": photo_id, **values},
        )
        count += 1
    conn.commit()
    return count


def reset(conn: sqlite3.Connection, photo_ids: Iterable[str]) -> int:
    """Stellt die GPS-Position aus der Datei wieder her; der Ort wird danach neu bestimmt."""
    count = 0
    for photo_id in photo_ids:
        row = conn.execute("SELECT auto_lat, auto_lon FROM place_override WHERE photo_id = ?", (photo_id,)).fetchone()
        if row is None:
            continue
        conn.execute("DELETE FROM place_override WHERE photo_id = ?", (photo_id,))
        conn.execute(
            "UPDATE photo SET lat = ?, lon = ?, city = NULL, region = NULL, country_code = NULL, geocoded = 0 "
            "WHERE id = ?",
            (row["auto_lat"], row["auto_lon"], photo_id),
        )
        count += 1
    conn.commit()
    return count


def eindeutschen_katalog(conn: sqlite3.Connection) -> int:
    """Einmalige Umstellung älterer Kataloge auf deutsche Ortsnamen (Schema 5).

    Von Hand gesetzte Orte werden direkt umbenannt; alle übrigen Bilder mit GPS werden
    zum erneuten Verorten markiert (geocode_pending). Gibt deren Anzahl zurück."""
    for row in conn.execute("SELECT * FROM place_override WHERE city IS NOT NULL").fetchall():
        city, region = ortsnamen.ort_eindeutschen(row["city"], row["region"], row["country_code"], row["lat"], row["lon"])
        conn.execute("UPDATE place_override SET city = ?, region = ? WHERE photo_id = ?", (city, region, row["photo_id"]))
        conn.execute("UPDATE photo SET city = ?, region = ? WHERE id = ?", (city, region, row["photo_id"]))
    count = conn.execute(
        "UPDATE photo SET geocoded = 0 WHERE lat IS NOT NULL AND id NOT IN (SELECT photo_id FROM place_override)"
    ).rowcount
    conn.commit()
    return count


def is_manual(conn: sqlite3.Connection, photo_id: str) -> bool:
    return conn.execute("SELECT 1 FROM place_override WHERE photo_id = ?", (photo_id,)).fetchone() is not None
