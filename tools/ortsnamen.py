"""Erzeugt bilderkatalog/data/ortsnamen_de.tsv.gz – deutsche Orts- und Regionsnamen.

Das Ortsverzeichnis von reverse_geocoder enthält nur ASCII-Namen ("Furstenfeldbruck",
"Munich", "Bavaria"). Dieses Skript ergänzt aus den Originaldaten von GeoNames
(CC BY 4.0) die richtige Schreibweise bzw. den deutschen Namen und die Einwohnerzahl.

Nur für Entwickler, einmalig bzw. nach einem Update von reverse_geocoder:

    1. Von https://download.geonames.org/export/dump/ herunterladen (zusammen ~220 MB):
       cities1000.zip, alternateNamesV2.zip, admin1CodesASCII.txt
    2. python tools/ortsnamen.py <Ordner mit den Dateien>

Ausgabe (gzip, UTF-8, tabulatorgetrennt):
    C <lat,lon wie im reverse_geocoder-Datensatz> <deutscher Name oder leer> <Einwohner>
    R <Ländercode> <Regionsname laut reverse_geocoder> <deutscher Name>
    S <lat> <lon> <Name> <ASCII-Name> <Region> <Ländercode> <Einwohner>
      – größere Orte, die im reverse_geocoder-Datensatz fehlen (z. B. Genf); nur für die Suche
"""

from __future__ import annotations

import csv
import gzip
import io
import os
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

ZIEL = Path(__file__).resolve().parent.parent / "bilderkatalog" / "data" / "ortsnamen_de.tsv.gz"
DACH = {"DE", "AT", "CH", "LI"}  # dort ist der Originalname bereits deutsch
GROSSSTADT = 50_000  # nur so große Orte bekommen einen nicht ausdrücklich bevorzugten deutschen Namen
NACHTRAG = 10_000  # fehlende Orte ab dieser Größe kommen zusätzlich in die Suche


def rg_zeilen() -> list[dict]:
    import reverse_geocoder

    pfad = os.path.join(os.path.dirname(reverse_geocoder.__file__), "rg_cities1000.csv")
    with open(pfad, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _norm(text: str) -> str:
    """Vergleichsform: "Zweibrücken", "Zweibruecken" und "Zweibrucken" sind gleich."""
    text = unicodedata.normalize("NFKD", text.lower().replace("ß", "ss"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", re.sub(r"([aou])e", r"\1", text))


def staedte(ordner: Path) -> tuple[dict, dict]:
    """Einträge (lat, lon, geonameid, Name, Einwohner, Vergleichsname), einmal nach
    (Land, ASCII-Name) und einmal nach Raster (Land, lat/lon auf 0,1° gerundet)."""
    nach_name: dict[tuple, list[tuple]] = {}
    raster: dict[tuple, list[tuple]] = {}
    with zipfile.ZipFile(ordner / "cities1000.zip") as zf, zf.open("cities1000.txt") as roh:
        for zeile in io.TextIOWrapper(roh, encoding="utf-8"):
            f = zeile.rstrip("\n").split("\t")
            eintrag = (float(f[4]), float(f[5]), f[0], f[1], int(f[14] or 0), _norm(f[2]), f[2], f"{f[8]}.{f[10]}")
            nach_name.setdefault((f[8], f[2]), []).append(eintrag)
            raster.setdefault((f[8], round(eintrag[0], 1), round(eintrag[1], 1)), []).append(eintrag)
    return nach_name, raster


def _naechster(kandidaten, lat: float, lon: float, grenze: float = 0.1):
    bester = min(kandidaten, key=lambda c: abs(c[0] - lat) + abs(c[1] - lon), default=None)
    return bester if bester and abs(bester[0] - lat) + abs(bester[1] - lon) <= grenze else None


def regionen(ordner: Path) -> dict[tuple, tuple]:
    """(Land, Name, ASCII-Name) → (geonameid, Code wie "DE.05") der Region"""
    ergebnis = {}
    for zeile in (ordner / "admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
        code, name, ascii_name, geonameid = zeile.split("\t")
        ergebnis[(code.split(".")[0], name, ascii_name)] = (geonameid, code)
    return ergebnis


def deutsche_namen(ordner: Path, ids: set[str]) -> dict[str, list[tuple]]:
    """geonameid → [(Name, bevorzugt, kurz)] für Sprache "de", ohne umgangssprachliche/historische."""
    ergebnis: dict[str, list[tuple]] = {}
    with zipfile.ZipFile(ordner / "alternateNamesV2.zip") as zf, zf.open("alternateNamesV2.txt") as roh:
        for zeile in io.TextIOWrapper(roh, encoding="utf-8"):
            f = zeile.rstrip("\n").split("\t")
            if f[2] != "de" or f[1] not in ids or f[6] == "1" or f[7] == "1" or "(" in f[3]:
                continue
            ergebnis.setdefault(f[1], []).append((f[3], f[4] == "1", f[5] == "1"))
    return ergebnis


def waehle(original: str, land: str, einwohner: int, kandidaten: list[tuple]) -> str:
    """Orte: bevorzugter deutscher Name; in DACH sonst der Originalname (mit Umlauten);
    anderswo ein deutscher Name nur bei Großstädten ("Lissabon"), sonst der Originalname
    mit Akzenten – historische Namen kleiner Orte ("Mährisch Neustadt") wären irreführend."""
    bevorzugt = [n for n, pref, _kurz in kandidaten if pref]
    if bevorzugt:
        return bevorzugt[0]
    if einwohner < GROSSSTADT:
        return original
    if land in DACH:  # nur französisch-/italienischsprachige Schweizer Städte ("Genf", "Neuenburg")
        lang = [n for n, _pref, kurz in kandidaten if not kurz and _norm(n) != _norm(original)]
        return lang[0] if land == "CH" and lang else original
    lang = [n for n, _pref, kurz in kandidaten if not kurz]
    return lang[0] if lang else original


_VORSATZ = re.compile(r"^(Kanton|Land|Bundesland|Freistaat|Freie und Hansestadt|Freie Hansestadt|Provinz|Region|Distrikt|Fürstentum|"
                      r"Autonome Region|Autonome Gemeinschaft|Landes|Bezirk|Gouvernement|Oblast|"
                      r"Departement|Präfektur|Woiwodschaft) ")


def waehle_region(original: str, kandidaten: list[tuple]) -> str:
    """Regionen: bevorzugter deutscher Name ohne Amtsvorsatz ("Hamburg" statt
    "Freie und Hansestadt Hamburg", "Wallis" statt "Kanton Wallis")."""
    namen = [n for n, pref, _kurz in kandidaten if pref and not n.isupper()]
    namen = namen or [n for n, _pref, kurz in kandidaten if not kurz and not n.isupper()]
    return _VORSATZ.sub("", namen[0]) if namen else original


def main(ordner: Path) -> None:
    rg = rg_zeilen()
    nach_name, raster = staedte(ordner)
    regions_ids = regionen(ordner)

    treffer: list[tuple[dict, tuple | None]] = []
    for zeile in rg:
        lat, lon, cc = float(zeile["lat"]), float(zeile["lon"]), zeile["cc"]
        bester = _naechster(nach_name.get((cc, zeile["name"]), []), lat, lon)
        if bester is None:  # ASCII-Schreibweise geändert: über Lage und Vergleichsnamen zuordnen
            vergleich = _norm(zeile["name"])
            nah = [e for dlat in (-0.1, 0, 0.1) for dlon in (-0.1, 0, 0.1)
                   for e in raster.get((cc, round(lat + dlat, 1), round(lon + dlon, 1)), [])]
            bester = _naechster([e for e in nah if e[5] == vergleich], lat, lon)
            if bester is None:  # Name ganz anders ("Nuernberg"/"Nuremberg"), aber praktisch dieselbe Stelle
                bester = _naechster([e for e in nah if e[5][:3] == vergleich[:3]], lat, lon, 0.01)
        treffer.append((zeile, bester))

    # Regionsnamen laut reverse_geocoder über die Vergleichsform zuordnen ("Baden-Wuerttemberg")
    region_namen = {(z["cc"], _norm(z["admin1"])): z["admin1"] for z in rg if z["admin1"]}
    region_ids = {}
    for (cc, name, ascii_name), (gid, _code) in regions_ids.items():
        for vergleich in {_norm(name), _norm(ascii_name)}:
            if (cc, vergleich) in region_namen:
                region_ids[(cc, region_namen[(cc, vergleich)])] = gid
    region_nach_code = {code: (gid, name) for (_cc, name, _a), (gid, code) in regions_ids.items()}

    zugeordnet = {b[2] for _, b in treffer if b}
    nachtrag = [e for liste in nach_name.values() for e in liste if e[4] >= NACHTRAG and e[2] not in zugeordnet]
    ids = zugeordnet | set(region_ids.values()) | {e[2] for e in nachtrag} | {g for g, _n in region_nach_code.values()}
    de = deutsche_namen(ordner, ids)

    zeilen_c = gefunden = geaendert = 0
    with gzip.open(ZIEL, "wt", encoding="utf-8", newline="\n", compresslevel=9) as aus:
        for zeile, bester in treffer:
            name, einwohner = "", 0
            if bester:
                gefunden += 1
                _lat, _lon, gid, original, einwohner, *_ = bester
                deutsch = waehle(original, zeile["cc"], einwohner, de.get(gid, []))
                if deutsch != zeile["name"]:
                    name = deutsch
                    geaendert += 1
            aus.write(f"C\t{zeile['lat']},{zeile['lon']}\t{name}\t{einwohner}\n")
            zeilen_c += 1
        for (cc, englisch), gid in sorted(region_ids.items()):
            deutsch = waehle_region(englisch, de.get(gid, []))
            if deutsch != englisch:
                aus.write(f"R\t{cc}\t{englisch}\t{deutsch}\n")
        for lat, lon, gid, original, einwohner, _v, ascii_name, code in sorted(nachtrag, key=lambda e: -e[4]):
            cc = code.split(".")[0]
            rgid, rname = region_nach_code.get(code, (None, ""))
            region_de = waehle_region(rname, de.get(rgid, [])) if rgid else ""
            name = waehle(original, cc, einwohner, de.get(gid, []))
            aus.write(f"S\t{lat}\t{lon}\t{name}\t{ascii_name}\t{region_de}\t{cc}\t{einwohner}\n")

    print(f"{len(nachtrag)} größere Orte für die Suche nachgetragen")
    print(f"{zeilen_c} Orte, {gefunden} in GeoNames gefunden, {geaendert} mit deutschem Namen bzw. Umlauten")
    print(f"{ZIEL} ({ZIEL.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
