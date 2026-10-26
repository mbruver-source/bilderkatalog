# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-Spec für den Bilderkatalog (Windows).

Baut einen Programmordner dist/Bilderkatalog/ mit Bilderkatalog.exe (ohne Konsole).
Anders als das SHS-Prüfungsprogramm (eine einzelne --onefile-EXE) ist das hier bewusst
ein Ordner: Python, numpy/scipy (Ortsdatensatz), Pillow, pywebview und .NET-Anbindung
sind zusammen groß – eine Einzeldatei müsste sich bei JEDEM Start erst in einen
Temp-Ordner entpacken (mehrere Sekunden). Der Installer (installer.iss) ersetzt beim
Update den kompletten Ordner, damit keine Reste alter Versionen liegen bleiben.

Verwendung (Python mit `pip install . pyinstaller`):

    pyinstaller --noconfirm --clean build.spec

Die Versionsnummer wird nicht von Hand gepflegt: bump_version.py schreibt sie in
version_info.txt (wird hier eingebettet) – siehe README_INSTALLER.md.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

NAME = "Bilderkatalog"

# Anaconda/Miniforge als Basis-Python (auch hinter einer venv): _sqlite3 und _ctypes
# brauchen sqlite3.dll bzw. ffi*.dll aus <basis>\Library\bin, die PyInstaller dort
# nicht sucht. Ohne diese DLLs startet die EXE, scheitert aber beim ersten Zugriff auf
# die Datenbank (vom Selbsttest erkannt). Mit python.org-Python (CI) entfällt das.
binaries = []
_conda_bin = Path(sys.base_prefix) / "Library" / "bin"
if _conda_bin.is_dir():
    for muster in ("sqlite3.dll", "ffi*.dll", "libffi*.dll"):
        binaries += [(str(dll), ".") for dll in _conda_bin.glob(muster)]

# Lizenzhinweise der mitgelieferten Fremdsoftware, bei jedem Build neu aus den
# tatsächlich installierten Paketen erzeugt (siehe bilderkatalog/lizenzen.py)
sys.path.insert(0, SPECPATH)
from bilderkatalog import lizenzen  # noqa: E402

_lizenzdatei = Path(SPECPATH) / "build" / lizenzen.DATEINAME
_lizenzdatei.parent.mkdir(exist_ok=True)
_lizenzdatei.write_text(lizenzen.text_erzeugen(), encoding="utf-8")

datas = [
    # Weboberfläche und Programmsymbol (bilderkatalog/static/*)
    ("bilderkatalog/static", "bilderkatalog/static"),
    (str(_lizenzdatei), "bilderkatalog/static"),
    ("LICENSE", "."),
]
# Ortsdatensatz (GeoNames, rg_cities1000.csv) für die Offline-Ortsauflösung
datas += collect_data_files("reverse_geocoder")
# WebView2-Bibliotheken für das Programmfenster
datas += collect_data_files("webview")

hiddenimports = [
    # uvicorn lädt Protokoll-/Loop-Implementierungen dynamisch
    *collect_submodules("uvicorn"),
    # pywin32: Aufnahmedatum aus OneDrive-Platzhaltern (winfiles.placeholder_date_taken)
    "win32com.propsys",
    "win32com.propsys.pscon",
    "pythoncom",
    # pywebview-Backend für Windows (wird zur Laufzeit per Name gewählt)
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nicht benötigte Schwergewichte, die über optionale Importe hereinkämen
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
    # LGPL: reverse_geocoder als austauschbarer Quellcode im Programmordner statt im Archiv
    module_collection_mode={"reverse_geocoder": "py"},
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # reine Fensteranwendung, Ausgaben gehen in bilderkatalog.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="bilderkatalog/static/icon.ico",
    version="version_info.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
