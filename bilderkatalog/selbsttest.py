"""Selbsttest der fertigen Anwendung: `Bilderkatalog.exe --selbsttest [protokoll]`.

Prüft ohne Fenster in einem temporären Ordner, ob alle gebündelten Teile der
ausgelieferten EXE funktionieren – genau das, was die Python-Tests nicht abdecken:
fehlt in build.spec ein Modul oder eine Datendatei (Ortsdatensatz, Weboberfläche,
Symbol), sind die Tests grün, die EXE scheitert aber erst beim Nutzer.

Die CI (build-installer.yml) startet damit die frisch gebaute und die per Installer
still installierte EXE. Echte Daten werden nicht berührt: alles passiert in einem
Temp-Ordner, der danach gelöscht wird.

Ergebnis: Exit-Code 0 (alles in Ordnung) bzw. 1, dazu eine Protokolldatei (die EXE hat
keine Konsole) – Standard: %TEMP%\\bilderkatalog_selbsttest.log.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path

ZEITGRENZE_S = 300


def fuehre_selbsttest_aus(protokoll_pfad: str | None = None) -> int:
    protokoll = Path(protokoll_pfad or Path(tempfile.gettempdir()) / "bilderkatalog_selbsttest.log")
    zeilen: list[str] = []
    fehler = 0

    def schritt(name, funktion):
        nonlocal fehler
        start = time.perf_counter()
        try:
            info = funktion()
            zeilen.append(f"OK     {name} ({time.perf_counter() - start:.1f}s){': ' + str(info) if info else ''}")
        except Exception:
            fehler += 1
            zeilen.append(f"FEHLER {name}\n{traceback.format_exc()}")

    # Watchdog: ein hängender Schritt darf die CI nicht stundenlang blockieren
    def notbremse():
        protokoll.write_text("\n".join(zeilen + [f"FEHLER Zeitgrenze {ZEITGRENZE_S}s überschritten"]), encoding="utf-8")
        os._exit(1)

    wachhund = threading.Timer(ZEITGRENZE_S, notbremse)
    wachhund.daemon = True
    wachhund.start()

    arbeitsordner = Path(tempfile.mkdtemp(prefix="bilderkatalog_selbsttest_"))
    os.environ["BILDERKATALOG_DATA_DIR"] = str(arbeitsordner / "daten")
    bilder = arbeitsordner / "Bilder" / "Urlaub"
    try:
        from . import __version__

        zeilen.append(f"Bilderkatalog {__version__}, Python {sys.version.split()[0]}, "
                      f"{'gebündelt' if getattr(sys, 'frozen', False) else 'Quellcode'}")
        kontext: dict = {}

        schritt("Testbild mit EXIF und GPS erzeugen", lambda: _testbild(bilder / "IMG_0001.jpg"))
        schritt("Ordner einlesen", lambda: _einlesen(bilder.parent, kontext))
        schritt("Ortsauflösung (Ortsdatensatz gebündelt)", lambda: _orte(kontext))
        schritt("Vorschaubild", lambda: _vorschau(kontext))
        schritt("ZIP-Export", lambda: _zip(kontext, arbeitsordner / "export"))
        schritt("Duplikate, Favoriten, Datum, Ort, Sicherung", lambda: _eigene_eintraege(kontext))
        schritt("Weboberfläche und API", _server)
        schritt("Lizenzhinweise", _lizenzen)
        if sys.platform == "win32":
            schritt("Windows-Eigenschaftsspeicher (pywin32)", _pywin32)
            schritt("Windows-Vorschau (Videos, Nur-online-Dateien)", lambda: _windows_vorschau(kontext))
            schritt("Programmfenster-Bibliothek (pywebview/WebView2)", _webview)
    finally:
        wachhund.cancel()
        shutil.rmtree(arbeitsordner, ignore_errors=True)

    zeilen.append("ERGEBNIS: " + ("alles in Ordnung" if fehler == 0 else f"{fehler} Fehler"))
    protokoll.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return 0 if fehler == 0 else 1


def _testbild(path: Path) -> str:
    from fractions import Fraction

    from PIL import Image

    path.parent.mkdir(parents=True)
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "Selbsttest", "Kamera"
    exif.get_ifd(0x8769)[0x9003] = "2024:07:14 09:30:00"
    dms = lambda v: (Fraction(int(v)), Fraction(int(v * 60 % 60)), Fraction(round(v * 3600 % 60)))  # noqa: E731
    exif.get_ifd(0x8825).update({1: "N", 2: dms(48.8566), 3: "E", 4: dms(2.3522)})  # Paris
    Image.new("RGB", (640, 480), (40, 120, 200)).save(path, "JPEG", exif=exif)
    return path.name


def _einlesen(ordner: Path, kontext: dict) -> str:
    from . import db, local_source

    conn = db.connect()
    db.init(conn)
    local_source.add_local_scope(conn, str(ordner))
    anzahl = local_source.scan_scope(conn, str(ordner.resolve()), workers=1)
    row = conn.execute("SELECT * FROM photo").fetchone()
    assert anzahl == 1 and row["taken_at"] == "2024-07-14T09:30:00", dict(row)
    assert row["lat"] is not None and row["camera_model"] == "Kamera", dict(row)
    kontext.update(conn=conn, id=row["id"], pfad=row["local_path"])
    return f"{anzahl} Bild, Aufnahme {row['taken_at']}"


def _orte(kontext: dict) -> str:
    from . import geocode

    conn = kontext["conn"]
    geocode.geocode_pending(conn)
    city, country = conn.execute("SELECT city, country_code FROM photo").fetchone()
    assert country == "FR", (city, country)
    return f"{city}, {country}"


def _vorschau(kontext: dict) -> str:
    from . import local_source

    data = local_source.thumbnail_bytes(kontext["pfad"], "medium")
    assert data and data[:2] == b"\xff\xd8", "keine JPEG-Vorschau"
    return f"{len(data)} Bytes"


def _eigene_eintraege(kontext: dict) -> str:
    import shutil as sh

    from . import albums, dates, duplicates, local_source, places, sicherung

    conn, pfad = kontext["conn"], Path(kontext["pfad"])
    kopie = pfad.with_name("IMG_0001 (1).jpg")
    sh.copy2(pfad, kopie)
    local_source.scan_scope(conn, str(pfad.parent.parent.resolve()), workers=1)
    assert duplicates.refresh(conn) == {"groups": 1, "copies": 1}
    albums.set_favorite(conn, [kontext["id"]], True)
    dates.set_date(conn, [kontext["id"]], "07.1998")
    assert conn.execute("SELECT year FROM photo WHERE id = ?", (kontext["id"],)).fetchone()[0] == 1998
    treffer = places.search(conn, "Lissabon")  # Ortsverzeichnis gebündelt?
    assert treffer and treffer[0]["country_code"] == "PT", treffer[:1]
    places.set_place(conn, [kontext["id"]], treffer[0])
    daten = sicherung.collect(conn)
    assert sicherung.counts(daten) == {"albums": 0, "favorites": 1, "dates": 1, "places": 1}, sicherung.counts(daten)
    dates.reset(conn, [kontext["id"]])
    places.reset(conn, [kontext["id"]])
    kopie.unlink()
    local_source.scan_scope(conn, str(pfad.parent.parent.resolve()), workers=1)
    duplicates.refresh(conn)
    return "1 Kopie erkannt, Ort gefunden, Sicherung vollständig"


def _windows_vorschau(kontext: dict) -> str:
    """Nur Hinweis: Auf Servern ohne Vorschau-Dienst (CI) liefert Windows evtl. nichts."""
    from . import winfiles

    data = winfiles.shell_thumbnail(Path(kontext["pfad"]), 160, timeout=10)
    return f"{len(data)} Bytes" if data else "nicht verfügbar (ohne Bedeutung auf Servern)"


def _zip(kontext: dict, ziel: Path) -> str:
    import zipfile

    from . import export

    result = export.export_zip(kontext["conn"], None, [kontext["id"]], ziel, "Selbsttest", "year_month")
    with zipfile.ZipFile(result["path"]) as zf:
        assert zf.namelist() == ["2024/07/IMG_0001.jpg"], zf.namelist()
    return Path(result["path"]).name


def _server() -> str:
    import socket

    import uvicorn

    from .api import create_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        basis = f"http://127.0.0.1:{port}"
        seite = urllib.request.urlopen(basis + "/", timeout=10).read().decode("utf-8")
        assert "Bilderkatalog" in seite and "/static/app.js?v=" in seite
        for pfad in ("/static/app.js", "/static/style.css", "/static/icon.ico", "/static/icon.png", "/api/status"):
            assert urllib.request.urlopen(basis + pfad, timeout=10).status == 200, pfad
        return f"Port {port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _lizenzen() -> str:
    from . import lizenzen

    text = lizenzen.gebuendelt_oder_erzeugen()
    for pflicht in ("GeoNames", "GNU LESSER GENERAL PUBLIC LICENSE", "reverse_geocoder", "pywebview", "numpy"):
        assert pflicht in text, f"{pflicht} fehlt in den Lizenzhinweisen"
    if getattr(sys, "frozen", False):
        import reverse_geocoder

        assert reverse_geocoder.__file__.endswith(".py"), "reverse_geocoder nicht als austauschbarer Quellcode gebündelt"
    return f"{len(text) // 1024} KB"


def _pywin32() -> str:
    from win32com.propsys import propsys, pscon  # noqa: F401

    return "propsys verfügbar"


def _webview() -> str:
    import webview  # noqa: F401
    from webview.platforms import winforms  # noqa: F401  (lädt pythonnet und WebView2-DLLs)

    return "winforms/WebView2 geladen"
