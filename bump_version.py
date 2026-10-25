"""Erhöht die Versionsnummer für ein neues Release (Schema wie im SHS-Prüfungsprogramm).

Die 3. Stelle (Patch) wird bei jedem Release um 1 erhöht, bis sie 99 erreicht hat –
danach wird die 2. Stelle (Minor) um 1 erhöht und die 3. beginnt wieder bei 0. Erreicht
auch die 2. Stelle 99, wird nach demselben Muster die 1. Stelle (Major) erhöht.

`version.txt` ist die einzige Quelle der Wahrheit. Dieses Skript schreibt dieselbe Nummer
zusätzlich in
  - `version_info.txt`  (Windows-Versionsinfo, die PyInstaller in die .exe einbettet,
                         sichtbar unter "Eigenschaften → Details", siehe build.spec)
  - `bilderkatalog/version.py` (zur Laufzeit importiert: Versionsanzeige im Programm,
                         `bilderkatalog --version`, Paketversion in pyproject.toml)

Wird von build_installer.bat vor dem PyInstaller-Build aufgerufen und gibt die neue
Versionsnummer auf stdout aus, damit das Skript sie an Inno Setup weitergeben kann.

    python bump_version.py          # erhöht und schreibt alle Dateien
    python bump_version.py --sync   # schreibt nur die abgeleiteten Dateien neu (ohne Erhöhung)
"""

from __future__ import annotations

import pathlib
import sys

HIER = pathlib.Path(__file__).resolve().parent
VERSION_DATEI = HIER / "version.txt"
VERSION_INFO_DATEI = HIER / "version_info.txt"
VERSION_PY_DATEI = HIER / "bilderkatalog" / "version.py"


def version_lesen() -> tuple[int, int, int]:
    """Aktuelle Version aus version.txt; fehlt die Datei, beginnt die Zählung bei 1.0.0."""
    if not VERSION_DATEI.exists():
        return (1, 0, 0)
    text = VERSION_DATEI.read_text(encoding="utf-8").strip()
    teile = text.split(".")
    if len(teile) != 3 or not all(t.isdigit() for t in teile):
        raise ValueError(f"Ungültige Versionsnummer in {VERSION_DATEI}: {text!r} (erwartet: X.Y.Z)")
    major, minor, patch = (int(t) for t in teile)
    return (major, minor, patch)


def naechste_version(aktuell: tuple[int, int, int]) -> tuple[int, int, int]:
    """Kilometerzähler-Logik: 3. Stelle erhöhen; über 99 → 0 und 2. Stelle erhöhen usw."""
    major, minor, patch = aktuell
    patch += 1
    if patch > 99:
        patch = 0
        minor += 1
    if minor > 99:
        minor = 0
        major += 1
    return (major, minor, patch)


def version_info_schreiben(version: tuple[int, int, int]) -> None:
    major, minor, patch = version
    inhalt = f"""# UTF-8
#
# Windows-Versionsinformationen für PyInstaller (siehe build.spec, Parameter
# `version="version_info.txt"`). Wird in die .exe eingebettet und erscheint im
# Explorer unter "Eigenschaften → Details".
#
# WIRD AUTOMATISCH von bump_version.py erzeugt – bitte nicht von Hand editieren.
# Die gültige Versionsnummer steht in version.txt.

VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,        # VOS_NT_WINDOWS32
    fileType=0x1,      # VFT_APP
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          "040704B0",  # 0407 = Deutsch, 04B0 = Unicode
          [
            StringStruct("CompanyName", "Marco"),
            StringStruct("FileDescription", "Bilderkatalog - Fotos sortieren, gruppieren und als ZIP exportieren"),
            StringStruct("FileVersion", "{major}.{minor}.{patch}.0"),
            StringStruct("InternalName", "Bilderkatalog"),
            StringStruct("LegalCopyright", "Copyright (c) 2026 Marco, MIT-Lizenz"),
            StringStruct("OriginalFilename", "Bilderkatalog.exe"),
            StringStruct("ProductName", "Bilderkatalog"),
            StringStruct("ProductVersion", "{major}.{minor}.{patch}.0"),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct("Translation", [1031, 1200])]),  # 1031 = Deutsch, 1200 = Unicode
  ]
)
"""
    VERSION_INFO_DATEI.write_text(inhalt, encoding="utf-8")


def version_py_schreiben(version_text: str) -> None:
    inhalt = (
        '"""WIRD AUTOMATISCH von bump_version.py erzeugt - bitte nicht von Hand editieren."""\n'
        "\n"
        f'VERSION = "{version_text}"\n'
    )
    VERSION_PY_DATEI.write_text(inhalt, encoding="utf-8")


def abgeleitete_dateien_schreiben(version: tuple[int, int, int]) -> str:
    text = ".".join(str(n) for n in version)
    version_info_schreiben(version)
    version_py_schreiben(text)
    return text


def main(argv: list[str] | None = None) -> str:
    argv = sys.argv[1:] if argv is None else argv
    aktuell = version_lesen()
    if "--sync" in argv:
        return abgeleitete_dateien_schreiben(aktuell)
    neu = naechste_version(aktuell)
    VERSION_DATEI.write_text(".".join(str(n) for n in neu) + "\n", encoding="utf-8")
    return abgeleitete_dateien_schreiben(neu)


if __name__ == "__main__":
    print(main())
