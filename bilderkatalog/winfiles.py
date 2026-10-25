"""Windows-spezifische Dateifunktionen für OneDrive-Platzhalter ("nur online").

Auf anderen Systemen liefern die Funktionen neutrale Ergebnisse, sodass der
lokale Modus dort mit normalen Ordnern funktioniert.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

FILE_ATTRIBUTE_PINNED = 0x00080000             # "Immer auf diesem Gerät behalten"
FILE_ATTRIBUTE_UNPINNED = 0x00100000           # "Speicherplatz freigeben"
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_ONLINE_ONLY = FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | FILE_ATTRIBUTE_RECALL_ON_OPEN


def onedrive_roots() -> list[Path]:
    """Lokale OneDrive-Ordner des angemeldeten Windows-Benutzers."""
    roots: list[Path] = []
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(var)
        if value and Path(value).is_dir() and Path(value) not in roots:
            roots.append(Path(value))
    return roots


def is_online_only(st: os.stat_result) -> bool:
    """True, wenn die Datei nur als Platzhalter vorliegt (Lesen löst Download aus)."""
    return bool(getattr(st, "st_file_attributes", 0) & _ONLINE_ONLY)


def free_up_space(path: Path) -> bool:
    """Entspricht "Speicherplatz freigeben" im Explorer: OneDrive entfernt die
    lokale Kopie wieder, die Datei in der Cloud bleibt unverändert."""
    if not IS_WINDOWS:
        return False
    import ctypes

    attrs = os.stat(path).st_file_attributes
    new = (attrs & ~FILE_ATTRIBUTE_PINNED) | FILE_ATTRIBUTE_UNPINNED
    return bool(ctypes.windll.kernel32.SetFileAttributesW(str(path), new))


def placeholder_date_taken(path: Path) -> datetime | None:
    """Aufnahmedatum aus dem Windows-Eigenschaftsspeicher.

    Funktioniert auch für "nur online"-Dateien, ohne sie herunterzuladen,
    weil OneDrive diese Eigenschaft im Platzhalter hinterlegt.
    Rückgabe in lokaler Zeit (wie EXIF), ohne Zeitzone.
    """
    if not IS_WINDOWS:
        return None
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
    except ImportError:
        return None
    pythoncom.CoInitialize()
    try:
        store = propsys.SHGetPropertyStoreFromParsingName(str(path), None, 0)
        value = store.GetValue(pscon.PKEY_Photo_DateTaken).GetValue()
    except Exception:
        return None
    if value is None:
        return None
    # Windows liefert UTC; EXIF-Zeiten sind lokale Zeit → umrechnen.
    local = value.astimezone() if value.tzinfo else value
    return datetime(local.year, local.month, local.day, local.hour, local.minute, local.second)


def reveal(path: Path) -> None:
    """Datei im Dateimanager anzeigen."""
    import subprocess

    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
