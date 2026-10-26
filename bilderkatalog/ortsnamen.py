"""Deutsche Orts- und Regionsnamen für das GeoNames-Ortsverzeichnis von reverse_geocoder.

reverse_geocoder kennt nur ASCII-Namen ("Munich", "Furstenfeldbruck", "Bavaria").
data/ortsnamen_de.tsv.gz (erzeugt mit tools/ortsnamen.py, Daten © GeoNames, CC BY 4.0)
liefert dazu den deutschen Namen bzw. die richtige Schreibweise und die Einwohnerzahl.
"""

from __future__ import annotations

import csv
import gzip
import os
import threading
from dataclasses import dataclass
from pathlib import Path

DATEI = Path(__file__).parent / "data" / "ortsnamen_de.tsv.gz"


@dataclass(frozen=True, slots=True)
class Ort:
    lat: float
    lon: float
    name: str        # deutsch bzw. mit richtiger Schreibweise
    name_ascii: str  # wie im reverse_geocoder-Datensatz
    region: str | None
    cc: str
    einwohner: int


_orte: list[Ort] | None = None
_nach_schluessel: dict[str, Ort] = {}
_regionen: dict[tuple[str, str], str] = {}
_englisch: dict[tuple[str, str], list[Ort]] = {}  # (Land, Name alt oder neu) → Orte
_lock = threading.Lock()


def _laden() -> list[Ort]:
    global _orte
    with _lock:
        if _orte is None:
            import reverse_geocoder

            deutsch: dict[str, tuple[str, int]] = {}
            nachtrag: list[Ort] = []  # größere Orte, die reverse_geocoder fehlen – nur für die Suche
            if DATEI.exists():
                with gzip.open(DATEI, "rt", encoding="utf-8") as fh:
                    for zeile in fh:
                        f = zeile.rstrip("\n").split("\t")
                        if f[0] == "C":
                            deutsch[f[1]] = (f[2], int(f[3]))
                        elif f[0] == "R":
                            _regionen[(f[1], f[2])] = f[3]
                        elif f[0] == "S":
                            nachtrag.append(Ort(float(f[1]), float(f[2]), f[3], f[4], f[5] or None, f[6], int(f[7])))
            pfad = os.path.join(os.path.dirname(reverse_geocoder.__file__), "rg_cities1000.csv")
            orte = []
            with open(pfad, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    schluessel = f"{r['lat']},{r['lon']}"
                    name, einwohner = deutsch.get(schluessel, ("", 0))
                    ort = Ort(float(r["lat"]), float(r["lon"]), name or r["name"], r["name"],
                              region(r["cc"], r["admin1"]), r["cc"], einwohner)
                    orte.append(ort)
                    _nach_schluessel[schluessel] = ort
                    _englisch.setdefault((r["cc"], r["name"]), []).append(ort)
                    if ort.name != ort.name_ascii:
                        _englisch.setdefault((r["cc"], ort.name), []).append(ort)
            _orte = orte + nachtrag
        return _orte


def orte() -> list[Ort]:
    return _laden()


def region(cc: str, admin1: str | None) -> str | None:
    if not admin1:
        return None
    return _regionen.get((cc, admin1), admin1)


def eindeutschen(ergebnis: dict) -> dict:
    """Ein Ergebnis von reverse_geocoder.search (name, admin1, cc, lat, lon) auf Deutsch."""
    _laden()
    ort = _nach_schluessel.get(f"{ergebnis.get('lat')},{ergebnis.get('lon')}")
    cc = ergebnis.get("cc") or ""
    return {
        **ergebnis,
        "name": ort.name if ort else ergebnis.get("name"),
        "admin1": region(cc, ergebnis.get("admin1")),
    }


def ort_eindeutschen(city: str | None, region_name: str | None, cc: str | None,
                     lat: float | None, lon: float | None) -> tuple[str | None, str | None]:
    """Gespeicherter Ort (z. B. aus älteren Katalogen/Sicherungen mit englischen Namen) → deutsch."""
    if not city or not cc:
        return city, region(cc or "", region_name) if region_name else region_name
    _laden()
    kandidaten = _englisch.get((cc, city), [])
    if lat is not None and lon is not None and kandidaten:
        ort = min(kandidaten, key=lambda o: abs(o.lat - lat) + abs(o.lon - lon))
    else:
        ort = kandidaten[0] if len(kandidaten) == 1 else None
    if ort is None:
        return city, region(cc, region_name) if region_name else region_name
    return ort.name, region(cc, region_name) if region_name else ort.region
