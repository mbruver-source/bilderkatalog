"""Ort von Hand setzen, ändern oder entfernen – nur im Katalog, die Dateien bleiben unverändert.

Orte lassen sich suchen (bereits im Katalog vorhandene Orte und das GeoNames-
Ortsverzeichnis, offline) oder als Koordinaten eingeben, z. B. aus einer Karten-App.
Ein Trigger in db.py hält den Ort über erneutes Einlesen und Nachladen hinweg fest.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
import threading
import unicodedata
from collections.abc import Iterable

# Deutsche Namen bekannter Orte, die GeoNames englisch führt
_ALIASES = {
    "munchen": "munich", "wien": "vienna", "rom": "rome", "lissabon": "lisbon",
    "prag": "prague", "mailand": "milan", "venedig": "venice", "florenz": "florence", "neapel": "naples",
    "warschau": "warsaw", "moskau": "moscow", "brussel": "brussels", "kopenhagen": "copenhagen",
    "athen": "athens", "genf": "geneva", "den haag": "the hague", "nizza": "nice", "krakau": "krakow",
    "belgrad": "belgrade", "bukarest": "bucharest", "sankt petersburg": "saint petersburg",
    "peking": "beijing", "kairo": "cairo", "turin": "turin", "sevilla": "seville", "breslau": "wroclaw",
    "danzig": "gdansk", "posen": "poznan", "pressburg": "bratislava", "lowen": "leuven", "luttich": "liege", "antwerpen": "antwerp", "bozen": "bolzano", "meran": "merano", "triest": "trieste",
    "korfu": "corfu", "kreta": "heraklion", "nurnberg": "nuremberg", "hannover": "hanover",
}
# Bei gleich gutem Treffer: Europa vor dem Rest der Welt (der Datensatz kennt keine Einwohnerzahlen)
_EUROPE = set("AD AL AT BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GI GR HR HU IE IM IS IT LI LT LU LV MC MD "
              "ME MK MT NL NO PL PT RO RS RU SE SI SK SM UA VA XK".split())
_COORDS = re.compile(r"\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*")
_COORDS_COMMA = re.compile(r"\s*(-?\d{1,3}(?:,\d+)?)\s*[;\s]\s*(-?\d{1,3}(?:,\d+)?)\s*")

_cities: list[tuple[str, float, float, str, str, str]] | None = None
_cities_lock = threading.Lock()


def _norm(text: str) -> str:
    """Kleinbuchstaben, ohne Akzente; ä/ae → a usw., damit "Höhe", "Hoehe" und "Hohe" gleich sind."""
    text = unicodedata.normalize("NFKD", (text or "").lower().replace("ß", "ss"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"([aou])e", r"\1", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _city_table() -> list[tuple[str, float, float, str, str, str]]:
    """GeoNames-Orte (> 1000 Einwohner) aus dem Datensatz von reverse_geocoder, einmalig geladen."""
    global _cities
    with _cities_lock:
        if _cities is None:
            import reverse_geocoder

            path = os.path.join(os.path.dirname(reverse_geocoder.__file__), "rg_cities1000.csv")
            with open(path, encoding="utf-8", newline="") as fh:
                _cities = [(_norm(r["name"]), float(r["lat"]), float(r["lon"]), r["name"], r["admin1"], r["cc"])
                           for r in csv.DictReader(fh)]
        return _cities


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
    # Eingabe und passende deutsche Namen ("Lissa" → Lisbon); "Hannover" gibt es auch unter deutschem Namen
    queries = {query} | {_norm(v) for k, v in _ALIASES.items() if len(query) >= 3 and k.startswith(query)}

    # 1. Orte, die schon im Katalog vorkommen (mit Anzahl)
    known = []
    for row in conn.execute(
        "SELECT city, region, country_code, AVG(lat) AS lat, AVG(lon) AS lon, COUNT(*) AS n FROM photo "
        "WHERE deleted = 0 AND city IS NOT NULL AND lat IS NOT NULL "
        "GROUP BY city, region, country_code ORDER BY n DESC"
    ):
        name = _norm(row["city"])
        if any(name.startswith(q) or f" {q}" in f" {name}" for q in queries):
            known.append({"city": row["city"], "region": row["region"], "country_code": row["country_code"],
                          "lat": round(row["lat"], 6), "lon": round(row["lon"], 6), "n": row["n"],
                          "source": "catalog"})
        if len(known) >= 5:
            break

    # 2. Ortsverzeichnis; Länder, die im Katalog häufig vorkommen, zuerst
    countries = {r[0]: r[1] for r in conn.execute(
        "SELECT country_code, COUNT(*) FROM photo WHERE country_code IS NOT NULL GROUP BY country_code")}
    hits = []
    for name, lat, lon, label, admin1, cc in _city_table():
        rank = min(_rank(name, q) for q in queries)
        if rank < 3:
            hits.append((rank, -countries.get(cc, 0), cc not in _EUROPE, len(name), label, admin1, cc, lat, lon))
    hits.sort()
    seen = {(k["city"], k["country_code"]) for k in known}
    result = list(known)
    for *_, label, admin1, cc, lat, lon in hits:
        if len(result) >= limit:
            break
        if (label, cc) in seen and any(k["region"] == admin1 for k in known if k["city"] == label):
            continue
        result.append({"city": label, "region": admin1 or None, "country_code": cc,
                       "lat": lat, "lon": lon, "source": "geonames"})
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

    res = reverse_geocoder.search([(lat, lon)], mode=1, verbose=False)[0]
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


def is_manual(conn: sqlite3.Connection, photo_id: str) -> bool:
    return conn.execute("SELECT 1 FROM place_override WHERE photo_id = ?", (photo_id,)).fetchone() is not None
