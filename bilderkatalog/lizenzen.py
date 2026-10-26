"""Lizenzhinweise der mitgelieferten Fremdsoftware (THIRD_PARTY_LICENSES.txt).

Wird beim Build erzeugt (build.spec) und mit dem Programm ausgeliefert: BSD- und
MIT-Lizenzen verlangen, dass ihr Text der Binärverteilung beiliegt; reverse_geocoder
steht unter der LGPL, der Ortsdatensatz von GeoNames unter CC BY 4.0.

Die Liste entsteht automatisch aus den tatsächlich installierten Abhängigkeiten des
Pakets "bilderkatalog" (rekursiv, nur für die aktuelle Plattform), damit sie nicht
veraltet, wenn sich Abhängigkeiten ändern.

    python -m bilderkatalog.lizenzen [ZIELDATEI]
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, distribution, requires
from pathlib import Path

DATEINAME = "THIRD_PARTY_LICENSES.txt"
_LIZENZDATEI = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE)", re.I)
_TRENNER = "=" * 78

GEONAMES = """Ortsdaten (Offline-Ortsauflösung)
Die Zuordnung von GPS-Koordinaten zu Orten verwendet den Datensatz "cities1000" von
GeoNames (https://www.geonames.org), mitgeliefert durch das Paket reverse_geocoder.
Lizenz: Creative Commons Namensnennung 4.0 International (CC BY 4.0),
https://creativecommons.org/licenses/by/4.0/
Ortsdaten © GeoNames.org. Die Daten wurden nicht verändert."""

LGPL_HINWEIS = """Hinweis zur LGPL (reverse_geocoder)
reverse_geocoder steht unter der GNU Lesser General Public License 2.1 (Volltext unten).
Die Bibliothek liegt als unveränderter Python-Quellcode im Programmordner
(_internal\\reverse_geocoder) und kann dort durch eine eigene Version ersetzt werden.
Quellcode: https://github.com/thampiman/reverse-geocoder
Der Quellcode des Bilderkatalogs selbst: https://github.com/mbruver-source/bilderkatalog"""

PYINSTALLER = """PyInstaller-Bootloader
Das Startprogramm (Bilderkatalog.exe) basiert auf dem Bootloader von PyInstaller
(https://pyinstaller.org). Er steht unter der GPL 2.0 mit einer ausdrücklichen Ausnahme,
die die Weitergabe damit erstellter Programme unter beliebiger Lizenz erlaubt.
Lizenz: https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt"""

WEBVIEW2 = """Microsoft Edge WebView2 (Microsoft.Web.WebView2.Core.dll, ...WinForms.dll)
Mitgeliefert durch pywebview für das Programmfenster. Copyright (C) Microsoft Corporation.
Lizenz des WebView2 SDK: https://www.nuget.org/packages/Microsoft.Web.WebView2/ (License Info)
Die WebView2-Laufzeit selbst ist Bestandteil von Windows und wird nicht mitgeliefert."""


def abhaengigkeiten(paket: str = "bilderkatalog") -> list:
    """Alle installierten Laufzeit-Abhängigkeiten (rekursiv, Marker der Plattform beachtet)."""
    from packaging.markers import default_environment
    from packaging.requirements import Requirement

    umgebung = {**default_environment(), "extra": ""}
    gefunden: dict[str, object] = {}
    offen = [paket]
    while offen:
        name = offen.pop()
        schluessel = re.sub(r"[-_.]+", "-", name).lower()
        if schluessel in gefunden:
            continue
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        gefunden[schluessel] = dist
        for zeile in requires(name) or []:
            anforderung = Requirement(zeile)
            if anforderung.marker is None or anforderung.marker.evaluate(umgebung):
                offen.append(anforderung.name)
    gefunden.pop(re.sub(r"[-_.]+", "-", paket).lower(), None)
    return sorted(gefunden.values(), key=lambda d: d.metadata["Name"].lower())


def _lizenztexte(dist) -> list[tuple[str, str]]:
    texte = []
    for datei in dist.files or []:
        name = Path(str(datei)).name
        if _LIZENZDATEI.match(name) and not name.endswith(".py"):
            try:
                texte.append((str(datei), dist.locate_file(datei).read_text(encoding="utf-8", errors="replace").strip()))
            except OSError:
                continue
    return texte


def _lizenzname(dist) -> str:
    meta = dist.metadata
    angabe = meta.get("License-Expression") or (meta.get("License") or "").splitlines()[0:1]
    if isinstance(angabe, list):
        angabe = angabe[0] if angabe else ""
    klassen = [k.split("::")[-1].strip() for k in meta.get_all("Classifier") or [] if k.startswith("License ::")]
    return (angabe or ", ".join(klassen) or "siehe Projektseite")[:120]


def _projektseite(dist) -> str:
    meta = dist.metadata
    for url in meta.get_all("Project-URL") or []:
        if "," in url:
            return url.split(",", 1)[1].strip()
    return meta.get("Home-page") or ""


def _lgpl_volltext() -> str | None:
    """LGPL 2.1 im Original – liegt pywin32 (adodbapi) bei, sonst aus anderen Paketen."""
    try:
        for datei in distribution("pywin32").files or []:
            if str(datei).replace("\\", "/").endswith("adodbapi/license.txt"):
                return distribution("pywin32").locate_file(datei).read_text(encoding="utf-8", errors="replace").strip()
    except PackageNotFoundError:
        pass
    return None


def _python_lizenz() -> str | None:
    for name in ("LICENSE.txt", "LICENSE_PYTHON.txt", "LICENSE"):
        datei = Path(sys.base_prefix) / name
        if datei.is_file():
            return datei.read_text(encoding="utf-8", errors="replace").strip()
    return None


def text_erzeugen() -> str:
    teile = [
        "Bilderkatalog – Lizenzhinweise für mitgelieferte Software und Daten",
        "",
        "Der Bilderkatalog selbst steht unter der MIT-Lizenz (Datei LICENSE).",
        "Das Programmpaket enthält die folgenden Bestandteile Dritter. Ihre Lizenzen",
        "gelten jeweils für den betreffenden Bestandteil.",
        "",
        _TRENNER, GEONAMES, "", _TRENNER, LGPL_HINWEIS, "", _TRENNER, PYINSTALLER, "",
        _TRENNER, WEBVIEW2, "",
    ]
    python = _python_lizenz()
    teile += [_TRENNER, f"Python {sys.version.split()[0]} – Python Software Foundation License",
              "https://docs.python.org/3/license.html", ""]
    if python:
        teile += [python, ""]

    for dist in abhaengigkeiten():
        meta = dist.metadata
        teile += [_TRENNER, f"{meta['Name']} {dist.version}", f"Lizenz: {_lizenzname(dist)}"]
        if _projektseite(dist):
            teile.append(f"Projekt: {_projektseite(dist)}")
        texte = _lizenztexte(dist)
        if meta["Name"].lower() == "reverse_geocoder":
            lgpl = _lgpl_volltext()
            texte = texte or ([("GNU LGPL 2.1", lgpl)] if lgpl else [])
        if not texte and "MIT" in _lizenzname(dist).upper():
            autor = meta.get("Author") or meta.get("Author-email") or "die Autoren des Projekts"
            texte = [("MIT-Lizenz (Text nicht im Paket enthalten)", _MIT.format(autor=autor))]
        if not texte:
            teile.append("(Kein Lizenztext im Paket enthalten, siehe Projektseite.)")
        for herkunft, inhalt in texte:
            teile += ["", f"--- {herkunft} ---", inhalt]
        teile.append("")
    return "\n".join(teile) + "\n"


_MIT = """Copyright (c) {autor}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def gebuendelt_oder_erzeugen() -> str:
    """Zur Laufzeit: die beim Build erzeugte Datei, im Quellcode-Betrieb frisch erzeugt."""
    datei = Path(__file__).parent / "static" / DATEINAME
    if datei.is_file():
        return datei.read_text(encoding="utf-8")
    try:
        return text_erzeugen()
    except ImportError:  # "packaging" fehlt – nur im Quellcode-Betrieb denkbar
        return "Die Lizenzhinweise werden beim Bauen des Installers erzeugt (siehe README_INSTALLER.md)."


if __name__ == "__main__":
    ziel = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DATEINAME)
    ziel.write_text(text_erzeugen(), encoding="utf-8")
    print(f"{ziel} geschrieben ({ziel.stat().st_size // 1024} KB)")
