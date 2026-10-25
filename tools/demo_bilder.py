"""Erzeugt erfundene Demo-Fotos zum Ausprobieren und für Screenshots (Handbuch, Webseite).

Gezeichnete Landschaften (Strand, Berge, Stadt, Wald) mit echten EXIF-Daten:
Aufnahmedatum, Kamera und GPS-Position an bekannten Orten. Es sind keine echten Fotos
und keine persönlichen Daten enthalten.

    python tools/demo_bilder.py ZIELORDNER
"""

from __future__ import annotations

import math
import random
import sys
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BREITE, HOEHE = 1200, 800

# (Ordner, Ort, Breite, Länge, Motiv, Startzeit, Anzahl, Kamera)
REISEN = [
    ("Urlaub/2025 Bretagne", "Bretagne", 48.6493, -2.0257, "strand", datetime(2025, 7, 14, 10, 5), 14, ("Samsung", "Galaxy S23")),
    ("Urlaub/2025 Bretagne", "Mont-Saint-Michel", 48.6361, -1.5115, "stadt", datetime(2025, 7, 17, 15, 20), 6, ("Samsung", "Galaxy S23")),
    ("Urlaub/2024 Lissabon", "Lissabon", 38.7223, -9.1393, "stadt", datetime(2024, 9, 3, 11, 0), 10, ("Apple", "iPhone 15")),
    ("Urlaub/2024 Lissabon", "Cascais", 38.6979, -9.4215, "strand", datetime(2024, 9, 5, 16, 30), 8, ("Apple", "iPhone 15")),
    ("Wandern/2024 Allgäu", "Oberstdorf", 47.4099, 10.2797, "berge", datetime(2024, 6, 8, 9, 15), 12, ("Canon", "EOS R6")),
    ("Wandern/2023 Taunus", "Königstein", 50.1797, 8.4689, "wald", datetime(2023, 10, 21, 13, 0), 9, ("Canon", "EOS R6")),
    ("Familie/2023", "Frankfurt am Main", 50.1109, 8.6821, "stadt", datetime(2023, 12, 24, 17, 45), 7, ("Samsung", "Galaxy S21")),
    ("Familie/2025", "Rom", 41.9028, 12.4964, "stadt", datetime(2025, 4, 18, 10, 30), 9, ("Samsung", "Galaxy S23")),
]


def _dms(wert: float) -> tuple[Fraction, Fraction, Fraction]:
    wert = abs(wert)
    grad = int(wert)
    minuten = int((wert - grad) * 60)
    sekunden = round((wert - grad - minuten / 60) * 3600, 2)
    return Fraction(grad), Fraction(minuten), Fraction(sekunden).limit_denominator(100)


def _verlauf(draw: ImageDraw.ImageDraw, oben, unten, y0: int, y1: int) -> None:
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0)
        draw.line([(0, y), (BREITE, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(oben, unten)))


def _himmel(draw, rnd, abend=False):
    horizont = int(HOEHE * rnd.uniform(0.42, 0.55))
    if abend:
        _verlauf(draw, (70, 90, 160), (250, 170, 110), 0, horizont)
    else:
        _verlauf(draw, (40, 110, 200), (170, 210, 245), 0, horizont)
    sx, sy, r = rnd.randint(100, BREITE - 100), rnd.randint(60, horizont // 2), rnd.randint(30, 55)
    draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(255, 230, 150) if abend else (255, 248, 210))
    for _ in range(rnd.randint(2, 5)):  # Wolken
        cx, cy = rnd.randint(0, BREITE), rnd.randint(30, horizont - 80)
        for _ in range(5):
            dx, dy, rr = rnd.randint(-60, 60), rnd.randint(-15, 15), rnd.randint(25, 45)
            draw.ellipse([cx + dx - rr, cy + dy - rr, cx + dx + rr, cy + dy + rr], fill=(250, 250, 252))
    return horizont


def motiv(art: str, rnd: random.Random) -> Image.Image:
    bild = Image.new("RGB", (BREITE, HOEHE))
    draw = ImageDraw.Draw(bild)
    abend = rnd.random() < 0.25
    horizont = _himmel(draw, rnd, abend)
    if art == "strand":
        strand = horizont + rnd.randint(90, 160)
        _verlauf(draw, (30, 105, 150), (60, 150, 180), horizont, strand)
        for _ in range(40):  # Wellen
            x, y = rnd.randint(0, BREITE), rnd.randint(horizont + 10, strand - 5)
            draw.line([(x, y), (x + rnd.randint(20, 60), y)], fill=(200, 230, 240), width=2)
        _verlauf(draw, (235, 215, 170), (215, 190, 140), strand, HOEHE)
        if rnd.random() < 0.6:  # Sonnenschirm
            x = rnd.randint(150, BREITE - 150)
            draw.line([(x, strand + 40), (x, HOEHE - 120)], fill=(90, 70, 50), width=6)
            draw.pieslice([x - 110, strand - 20, x + 110, strand + 100], 180, 360, fill=rnd.choice([(220, 60, 60), (240, 170, 40), (40, 130, 200)]))
    elif art == "berge":
        for ebene, farbe in enumerate([(120, 140, 170), (90, 115, 120), (70, 100, 80)]):
            punkte = [(0, HOEHE)]
            x = 0
            while x <= BREITE:
                punkte.append((x, horizont - rnd.randint(40, 220) + ebene * 70))
                x += rnd.randint(120, 260)
            punkte += [(BREITE, horizont + ebene * 60), (BREITE, HOEHE)]
            draw.polygon(punkte, fill=farbe)
        _verlauf(draw, (95, 140, 70), (70, 115, 55), horizont + 150, HOEHE)
    elif art == "wald":
        _verlauf(draw, (90, 130, 70), (60, 95, 50), horizont, HOEHE)
        for _ in range(28):
            x, h = rnd.randint(-40, BREITE + 40), rnd.randint(180, 380)
            basis = horizont + rnd.randint(40, 200)
            herbst = rnd.choice([(40, 95, 50), (180, 110, 40), (200, 150, 50), (35, 80, 45)])
            draw.rectangle([x - 6, basis - 30, x + 6, basis + 20], fill=(80, 60, 40))
            draw.polygon([(x, basis - h), (x - h // 3, basis), (x + h // 3, basis)], fill=herbst)
    else:  # stadt
        _verlauf(draw, (160, 160, 160), (110, 110, 115), horizont + 120, HOEHE)
        x = -20
        while x < BREITE:
            b, h = rnd.randint(70, 160), rnd.randint(150, 380)
            farbe = rnd.choice([(230, 200, 160), (200, 120, 90), (240, 230, 210), (180, 190, 200), (250, 220, 120)])
            draw.rectangle([x, horizont + 120 - h, x + b, horizont + 140], fill=farbe)
            for fy in range(horizont + 140 - h, horizont + 110, 40):
                for fx in range(x + 12, x + b - 20, 30):
                    draw.rectangle([fx, fy, fx + 14, fy + 20], fill=(70, 90, 120) if not abend else (255, 220, 120))
            draw.polygon([(x - 5, horizont + 120 - h), (x + b // 2, horizont + 70 - h), (x + b + 5, horizont + 120 - h)], fill=(160, 70, 50))
            x += b + rnd.randint(0, 12)
    return bild.filter(ImageFilter.GaussianBlur(0.6))


def erzeugen(ziel: Path) -> int:
    rnd = random.Random(2026)
    anzahl = 0
    for ordner, ort, lat, lon, art, start, n, (marke, modell) in REISEN:
        ordnerpfad = ziel / ordner
        ordnerpfad.mkdir(parents=True, exist_ok=True)
        zeit = start
        for i in range(n):
            zeit += timedelta(minutes=rnd.randint(3, 50))
            exif = Image.Exif()
            exif[0x010F], exif[0x0110] = marke, modell
            exif.get_ifd(0x8769)[0x9003] = zeit.strftime("%Y:%m:%d %H:%M:%S")
            b = lat + rnd.uniform(-0.01, 0.01)
            l = lon + rnd.uniform(-0.01, 0.01)
            exif.get_ifd(0x8825).update({1: "N" if b >= 0 else "S", 2: _dms(b), 3: "E" if l >= 0 else "W", 4: _dms(l)})
            name = f"IMG_{zeit:%Y%m%d_%H%M%S}.jpg"
            motiv(art, rnd).save(ordnerpfad / name, "JPEG", quality=85, exif=exif)
            anzahl += 1
    # Ein paar Bilder ohne GPS (z. B. aus Messengern), Datum nur im Dateinamen
    sonstiges = ziel / "Messenger"
    sonstiges.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        tag = datetime(2025, 2, 1) + timedelta(days=i * 9)
        motiv(rnd.choice(["strand", "wald", "berge"]), rnd).resize((800, 533)).save(
            sonstiges / f"IMG-{tag:%Y%m%d}-WA{i:04d}.jpg", "JPEG", quality=80)
        anzahl += 1
    return anzahl


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(f"{erzeugen(Path(sys.argv[1]))} Demo-Bilder erzeugt.")
