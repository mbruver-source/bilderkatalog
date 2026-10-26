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


# Vorschaubilder über Windows (IShellItemImageFactory). Liefert auch für Videos und
# "nur online"-Dateien ein Bild: OneDrive stellt die Vorschau aus der Cloud bereit,
# ohne die Datei herunterzuladen.
_SIIGBF_THUMBNAILONLY = 0x08
_WTS_E_EXTRACTIONPENDING = 0x8004B205
_shell_api = None


def _shell():
    global _shell_api
    if _shell_api is None:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16),
                        ("d", ctypes.c_ubyte * 8)]

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                        ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
                        ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                        ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

        class BITMAP(ctypes.Structure):
            _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                        ("bmWidthBytes", ctypes.c_long), ("bmPlanes", ctypes.c_uint16),
                        ("bmBitsPixel", ctypes.c_uint16), ("bmBits", ctypes.c_void_p)]

        ole32 = ctypes.OleDLL("ole32")
        iid = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p("{bcc18b79-ba16-442f-80c4-8a59c30c463b}"), ctypes.byref(iid))
        gdi32 = ctypes.WinDLL("gdi32")
        gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.GetObjectW.argtypes = [wintypes.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
        _shell_api = {
            "ctypes": ctypes, "wintypes": wintypes, "ole32": ole32, "shell32": ctypes.OleDLL("shell32"),
            "gdi32": gdi32, "iid": iid, "SIZE": SIZE, "BIH": BITMAPINFOHEADER, "BITMAP": BITMAP,
            "GetImage": ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, SIZE, ctypes.c_int,
                                           ctypes.POINTER(wintypes.HBITMAP)),
            "Release": ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p),
        }
    return _shell_api


def shell_thumbnail(path: Path, px: int, timeout: float = 15.0) -> bytes | None:
    """JPEG-Vorschau (längste Seite höchstens `px`) über Windows, sonst None. Lädt nichts herunter."""
    if not IS_WINDOWS:
        return None
    import time

    deadline = time.monotonic() + timeout
    while True:
        try:
            return _shell_thumbnail_once(str(path), px)
        except OSError as err:
            pending = (getattr(err, "winerror", 0) or 0) & 0xFFFFFFFF == _WTS_E_EXTRACTIONPENDING
            if not pending or time.monotonic() >= deadline:
                return None
            time.sleep(0.25)  # Windows erzeugt die Vorschau gerade im Hintergrund
        except Exception:
            return None


def _shell_thumbnail_once(path: str, px: int) -> bytes | None:
    import io

    from PIL import Image

    api = _shell()
    ctypes, wintypes = api["ctypes"], api["wintypes"]
    try:
        api["ole32"].CoInitializeEx(None, 0x2)  # STA; S_FALSE, wenn schon geschehen
    except OSError:
        pass  # Thread ist bereits anders initialisiert – das genügt
    factory = ctypes.c_void_p()
    api["shell32"].SHCreateItemFromParsingName(ctypes.c_wchar_p(path), None, ctypes.byref(api["iid"]),
                                               ctypes.byref(factory))
    vtable = ctypes.cast(ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    bitmap = wintypes.HBITMAP()
    try:
        api["GetImage"](vtable[3])(factory, api["SIZE"](px, px), _SIIGBF_THUMBNAILONLY, ctypes.byref(bitmap))
    finally:
        api["Release"](vtable[2])(factory)
    gdi32 = api["gdi32"]
    try:
        info = api["BITMAP"]()
        gdi32.GetObjectW(bitmap, ctypes.sizeof(info), ctypes.byref(info))
        width, height = info.bmWidth, abs(info.bmHeight)
        if not width or not height:
            return None
        header = api["BIH"](biSize=ctypes.sizeof(api["BIH"]), biWidth=width, biHeight=-height,  # von oben
                            biPlanes=1, biBitCount=32, biCompression=0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        dc = gdi32.CreateCompatibleDC(None)
        try:
            lines = gdi32.GetDIBits(dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
        finally:
            gdi32.DeleteDC(dc)
        if lines != height:
            return None
    finally:
        gdi32.DeleteObject(bitmap)
    image = Image.frombuffer("RGB", (width, height), buffer.raw, "raw", "BGRX", 0, 1)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=82)
    return out.getvalue()


def reveal(path: Path) -> None:
    """Datei im Dateimanager anzeigen."""
    import subprocess

    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
