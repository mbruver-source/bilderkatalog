"""Die Tabelle der deutschen Ortsnamen passt zur Ortsliste von reverse_geocoder.

Die Tabelle (bilderkatalog/data/ortsnamen_de.tsv.gz) ist über die Koordinaten
an rg_cities1000.csv gebunden. Ändert sich diese Liste (neue Version von
reverse_geocoder), zeigt das Programm ohne Fehlermeldung wieder englische Namen –
diese Tests machen das sichtbar. Abhilfe: tools/ortsnamen.py neu ausführen.
"""

import csv
import gzip
import os

import reverse_geocoder

from bilderkatalog import ortsnamen


def _tabelle() -> dict[str, int]:
    with gzip.open(ortsnamen.DATEI, "rt", encoding="utf-8") as fh:
        return {f[1]: int(f[3]) for f in (z.rstrip("\n").split("\t") for z in fh) if f[0] == "C"}


def _ortsliste() -> set[str]:
    pfad = os.path.join(os.path.dirname(reverse_geocoder.__file__), "rg_cities1000.csv")
    with open(pfad, encoding="utf-8", newline="") as fh:
        return {f"{r['lat']},{r['lon']}" for r in csv.DictReader(fh)}


def test_tabelle_gehoert_zur_ortsliste():
    tabelle, liste = _tabelle(), _ortsliste()
    assert set(tabelle) == liste, "Ortsliste von reverse_geocoder hat sich geändert – tools/ortsnamen.py neu ausführen"


def test_genug_orte_zugeordnet():
    # Heute 77 %: zugeordnet sind 83 %, aber nicht jeder Ort hat in GeoNames eine Einwohnerzahl
    tabelle = _tabelle()
    anteil = sum(1 for einwohner in tabelle.values() if einwohner) / len(tabelle)
    assert anteil >= 0.75, f"nur {anteil:.0%} der Orte mit Einwohnerzahl – Zuordnung in tools/ortsnamen.py prüfen"


def test_bekannte_namen():
    ergebnis = ortsnamen.eindeutschen({"lat": "48.13743", "lon": "11.57549", "name": "Munich", "admin1": "Bavaria", "cc": "DE"})
    assert (ergebnis["name"], ergebnis["admin1"]) == ("München", "Bayern")
    assert ortsnamen.ort_eindeutschen("Lisbon", "Lisbon", "PT", 38.7, -9.1) == ("Lissabon", "Lissabon")
    assert ortsnamen.ort_eindeutschen("Gibt es nicht", "Irgendwo", "DE", 50.0, 8.0) == ("Gibt es nicht", "Irgendwo")
