"""Lokaler Modus: liest Bilder direkt aus dem OneDrive-Ordner (oder jedem anderen Ordner).

Ohne Anmeldung. "Nur online"-Dateien werden beim Einlesen nicht geöffnet –
von ihnen wird nur das Aufnahmedatum aus dem Windows-Platzhalter gelesen
(meta_level = 'partial'). `enrich` lädt sie auf Wunsch einzeln herunter,
liest GPS/Kamera aus, erzeugt ein Vorschaubild und gibt den Speicher sofort
wieder frei. Am Inhalt in OneDrive ändert sich dabei nichts.
"""

from __future__ import annotations

import io
import math
import mimetypes
import os
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import config, winfiles
from .sync import MEDIA_EXTENSIONS, VIDEO_EXTENSIONS, date_from_filename

THUMB_PX = {"small": 160, "medium": 320, "large": 1600}
MIN_FREE_BYTES = 2 * 1024**3  # Nachladen pausiert, wenn weniger frei ist
BATCH = 200

Progress = Callable[[str, int, int], None]  # (Text, erledigt, gesamt)

try:  # HEIC/HEIF-Unterstützung, falls installiert
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


class LowDiskSpace(RuntimeError):
    pass


# -- Bereiche ---------------------------------------------------------------

def display_path(folder: Path, scope_root: Path | None = None) -> str:
    """Anzeigepfad: relativ zum OneDrive-Ordner, sonst ab dem gewählten Ordner."""
    for root in winfiles.onedrive_roots():
        try:
            rel = folder.relative_to(root)
        except ValueError:
            continue
        return "/" + rel.as_posix() if rel.parts else "/"
    base = scope_root or folder
    try:
        return "/" + (Path(base.name) / folder.relative_to(base)).as_posix().rstrip("/.")
    except ValueError:
        return "/" + folder.name


def add_local_scope(conn: sqlite3.Connection, folder: str) -> dict:
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Ordner nicht gefunden: {folder}")
    for row in conn.execute("SELECT item_id, path FROM scope WHERE kind = 'local'").fetchall():
        existing = Path(row["item_id"])
        if path == existing or existing in path.parents:
            raise ValueError(f"Der Ordner ist bereits erfasst (in „{row['path']}“).")
    # Bereits erfasste Unterordner gehen im neuen, größeren Bereich auf.
    for row in conn.execute("SELECT item_id FROM scope WHERE kind = 'local'").fetchall():
        if path in Path(row["item_id"]).parents:
            conn.execute("UPDATE photo SET scope_id = ? WHERE scope_id = ?", (str(path), row["item_id"]))
            conn.execute("DELETE FROM scope WHERE item_id = ?", (row["item_id"],))
    shown = display_path(path)
    conn.execute("INSERT INTO scope(item_id, kind, path) VALUES (?, 'local', ?)", (str(path), shown))
    conn.commit()
    return {"item_id": str(path), "kind": "local", "path": shown}


def list_subfolders(folder: str | None) -> list[dict]:
    """Ordner für die Auswahl. Ohne Angabe: die OneDrive-Ordner des Benutzers."""
    if not folder:
        return [{"path": str(r), "name": r.name, "has_children": True} for r in winfiles.onedrive_roots()]
    result = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except OSError as err:
        raise ValueError(str(err)) from err
    for entry in entries:
        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith((".", "$")):
            result.append({"path": entry.path, "name": entry.name, "has_children": True})
    return result


# -- Einlesen ---------------------------------------------------------------

def scan_scope(
    conn: sqlite3.Connection,
    scope_id: str,
    progress: Progress | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    workers: int = 8,
) -> int:
    """Gleicht einen lokalen Ordner mit der Datenbank ab. Gibt neue/geänderte Dateien zurück."""
    root = Path(scope_id)
    if not root.is_dir():
        raise ValueError(f"Ordner nicht erreichbar: {root}")
    known = {
        r["local_path"]: r
        for r in conn.execute(
            "SELECT id, local_path, size, mtime, online_only, meta_level FROM photo "
            "WHERE source = 'local' AND scope_id = ?", (scope_id,)
        )
    }
    seen: set[str] = set()
    todo: list[tuple[str, os.stat_result]] = []
    found = 0
    for path, st in _walk_media(root):
        if cancelled():
            raise InterruptedError("Einlesen abgebrochen")
        found += 1
        seen.add(path)
        prev = known.get(path)
        online = winfiles.is_online_only(st)
        unchanged = prev is not None and prev["size"] == st.st_size and prev["mtime"] == st.st_mtime
        # Inzwischen lokal vorhandene Dateien mit nur teilweisen Metadaten erneut lesen
        if unchanged and not (prev["meta_level"] == "partial" and not online):
            if prev["online_only"] != int(online):
                conn.execute("UPDATE photo SET online_only = ? WHERE id = ?", (int(online), prev["id"]))
            continue
        todo.append((path, st))
        if progress and found % 500 == 0:
            progress(f"Durchsuche {display_path(root)} …", found, 0)

    done = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="meta") as pool:
        for start in range(0, len(todo), BATCH):
            if cancelled():
                raise InterruptedError("Einlesen abgebrochen")
            chunk = todo[start:start + BATCH]
            rows = list(pool.map(lambda item: read_file(item[0], item[1], scope_id), chunk))
            for row in rows:
                _upsert(conn, row, known.get(row["local_path"]))
            conn.commit()
            done += len(rows)
            if progress:
                progress(f"Lese Metadaten {display_path(root)} …", done, len(todo))

    gone = [r["id"] for p, r in known.items() if p not in seen]
    conn.executemany("UPDATE photo SET deleted = 1 WHERE id = ?", [(i,) for i in gone])
    conn.execute(
        "UPDATE scope SET last_sync = ? WHERE item_id = ?",
        (datetime.now().isoformat(timespec="seconds"), scope_id),
    )
    conn.commit()
    return len(todo)


def _walk_media(root: Path) -> Iterator[tuple[str, os.stat_result]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$"))]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS:
                path = os.path.join(dirpath, name)
                try:
                    yield path, os.stat(path)  # stat lädt keine Platzhalter herunter
                except OSError:
                    continue


def file_id(path: str, st: os.stat_result) -> str:
    """Stabile ID: Datei-Index des Dateisystems (bleibt beim Umbenennen/Verschieben gleich)."""
    if st.st_ino:
        return f"L:{st.st_dev:x}:{st.st_ino:x}"
    import hashlib

    return "L:p:" + hashlib.sha1(path.encode("utf-8")).hexdigest()


def read_file(path: str, st: os.stat_result, scope_id: str) -> dict:
    """Metadaten einer Datei. Öffnet "nur online"-Dateien nicht."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    is_video = ext in VIDEO_EXTENSIONS
    online = winfiles.is_online_only(st)
    meta: dict = {}
    level = "full"
    if is_video:
        pass  # Videos: Datum aus Dateiname/Dateizeit; Nachladen lohnt nicht
    elif online:
        taken = winfiles.placeholder_date_taken(Path(path))
        if taken:
            meta["taken_at"] = taken.isoformat(timespec="seconds")
        level = "partial"
    else:
        meta = read_exif(path)

    taken_at, source = meta.get("taken_at"), "exif" if meta.get("taken_at") else None
    if not taken_at:
        taken_at = date_from_filename(name)
        source = "filename" if taken_at else None
    if not taken_at:
        taken_at = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
        source = "file"
    return {
        "id": file_id(path, st),
        "local_path": path,
        "scope_id": scope_id,
        "mtime": st.st_mtime,
        "online_only": int(online),
        "meta_level": level,
        "name": name,
        "parent_path": display_path(Path(path).parent, Path(scope_id)),
        "size": st.st_size,
        "mime": mimetypes.guess_type(name)[0],
        "is_video": int(is_video),
        "taken_at": taken_at,
        "date_source": source,
        "year": int(taken_at[:4]),
        "month": int(taken_at[5:7]),
        "camera_make": meta.get("camera_make"),
        "camera_model": meta.get("camera_model"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
    }


def _upsert(conn: sqlite3.Connection, row: dict, previous: sqlite3.Row | None) -> None:
    # Wurde die Datei ersetzt (neue Datei-ID am selben Pfad), Albumzuordnung übernehmen.
    if previous is not None and previous["id"] != row["id"]:
        conn.execute("INSERT OR IGNORE INTO photo(id, name) VALUES (?, ?)", (row["id"], row["name"]))
        conn.execute(
            "INSERT OR IGNORE INTO album_photo(album_id, photo_id, added_at) "
            "SELECT album_id, ?, added_at FROM album_photo WHERE photo_id = ?",
            (row["id"], previous["id"]),
        )
        conn.execute("DELETE FROM photo WHERE id = ?", (previous["id"],))
    conn.execute(
        """
        INSERT INTO photo (id, source, local_path, scope_id, mtime, online_only, meta_level, name,
                           parent_path, size, mime, is_video, taken_at, date_source, year, month,
                           camera_make, camera_model, width, height, lat, lon, deleted, geocoded)
        VALUES (:id, 'local', :local_path, :scope_id, :mtime, :online_only, :meta_level, :name,
                :parent_path, :size, :mime, :is_video, :taken_at, :date_source, :year, :month,
                :camera_make, :camera_model, :width, :height, :lat, :lon, 0, 0)
        ON CONFLICT(id) DO UPDATE SET
            source = 'local', local_path = excluded.local_path, scope_id = excluded.scope_id,
            mtime = excluded.mtime, online_only = excluded.online_only, meta_level = excluded.meta_level,
            name = excluded.name, parent_path = excluded.parent_path, size = excluded.size,
            mime = excluded.mime, is_video = excluded.is_video, taken_at = excluded.taken_at,
            date_source = excluded.date_source, year = excluded.year, month = excluded.month,
            camera_make = excluded.camera_make, camera_model = excluded.camera_model,
            width = excluded.width, height = excluded.height, deleted = 0,
            geocoded = CASE WHEN photo.lat IS excluded.lat AND photo.lon IS excluded.lon
                            THEN photo.geocoded ELSE 0 END,
            lat = excluded.lat, lon = excluded.lon
        """,
        row,
    )


# -- EXIF ---------------------------------------------------------------------

def read_exif(path: str) -> dict:
    """Aufnahmedatum, Kamera, Abmessungen und GPS aus der Datei (Pillow)."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as im:
            width, height = im.size
            exif = im.getexif()
            sub = exif.get_ifd(0x8769)
            gps = exif.get_ifd(0x8825)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return {}
    if exif.get(0x0112) in (5, 6, 7, 8):  # Hochformat laut Orientierung
        width, height = height, width
    lat = _dms(gps.get(2), gps.get(1))
    lon = _dms(gps.get(4), gps.get(3))
    if lat == 0 and lon == 0:
        lat = lon = None
    return {
        "taken_at": _exif_date(sub.get(0x9003) or sub.get(0x9004) or exif.get(0x0132)),
        "camera_make": _text(exif.get(0x010F)),
        "camera_model": _text(exif.get(0x0110)),
        "width": width,
        "height": height,
        "lat": lat,
        "lon": lon,
    }


def _exif_date(value) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text[:19], "%Y:%m:%d %H:%M:%S").isoformat(timespec="seconds")
    except ValueError:
        return None


def _text(value) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    if not isinstance(value, str):
        return None
    value = value.strip("\x00 ").strip()
    return value or None


def _dms(value, ref) -> float | None:
    try:
        degrees, minutes, seconds = (float(v) for v in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    result = degrees + minutes / 60 + seconds / 3600
    if math.isnan(result) or not -180 <= result <= 180:
        return None
    if _text(ref) in ("S", "W"):
        result = -result
    return round(result, 6)


# -- Vorschaubilder -----------------------------------------------------------

def thumbnail_bytes(path: str, size: str) -> bytes | None:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(path) as im:
            im.draft("RGB", (THUMB_PX[size] * 2, THUMB_PX[size] * 2))  # JPEG schneller dekodieren
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB_PX[size], THUMB_PX[size]))
            buffer = io.BytesIO()
            im.convert("RGB").save(buffer, "JPEG", quality=82)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None


def thumb_cache_file(photo_id: str, size: str) -> Path:
    import re

    return config.thumbs_dir() / f"{re.sub(r'[^A-Za-z0-9!_-]', '_', photo_id)}_{size}.jpg"


# -- Details nachladen ---------------------------------------------------------

def enrich(
    conn: sqlite3.Connection,
    progress: Progress | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    workers: int = 4,
) -> dict:
    """Lädt "nur online"-Bilder einzeln, liest Metadaten, gibt Speicher wieder frei."""
    rows = conn.execute(
        "SELECT id, local_path, size FROM photo WHERE source = 'local' AND meta_level = 'partial' "
        "AND deleted = 0 ORDER BY taken_at DESC"
    ).fetchall()
    total, done, errors = len(rows), 0, []

    def work(row: sqlite3.Row) -> tuple[str, dict | None, str | None]:
        if cancelled():
            return row["id"], None, None
        path = row["local_path"]
        try:
            if shutil.disk_usage(os.path.dirname(path)).free < MIN_FREE_BYTES + (row["size"] or 0):
                raise LowDiskSpace("Zu wenig freier Speicher – Nachladen angehalten.")
            was_online = winfiles.is_online_only(os.stat(path))
            try:
                meta = read_exif(path)  # löst den Download aus
                data = thumbnail_bytes(path, "medium")
                if data:
                    thumb_cache_file(row["id"], "medium").write_bytes(data)
            finally:
                if was_online:
                    winfiles.free_up_space(Path(path))
            return row["id"], meta, None
        except LowDiskSpace:
            raise
        except OSError as err:
            return row["id"], None, f"{os.path.basename(path)}: {err.strerror or err}"

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="enrich") as pool:
        for start in range(0, total, 30):
            if cancelled():
                raise InterruptedError("Nachladen abgebrochen")
            for photo_id, meta, error in pool.map(work, rows[start:start + 30]):
                if error:
                    errors.append(error)
                elif meta is not None:
                    _apply_enriched(conn, photo_id, meta)
                done += 1
            conn.commit()
            if progress:
                progress("Lade Details (Speicher wird danach wieder freigegeben) …", done, total)
    return {"processed": done, "errors": errors}


def _apply_enriched(conn: sqlite3.Connection, photo_id: str, meta: dict) -> None:
    conn.execute(
        """
        UPDATE photo SET
            meta_level = 'full',
            taken_at = COALESCE(:taken_at, taken_at),
            date_source = CASE WHEN :taken_at IS NOT NULL THEN 'exif' ELSE date_source END,
            year = COALESCE(CAST(substr(:taken_at, 1, 4) AS INTEGER), year),
            month = COALESCE(CAST(substr(:taken_at, 6, 2) AS INTEGER), month),
            camera_make = :camera_make, camera_model = :camera_model,
            width = COALESCE(:width, width), height = COALESCE(:height, height),
            geocoded = CASE WHEN lat IS :lat AND lon IS :lon THEN geocoded ELSE 0 END,
            lat = :lat, lon = :lon
        WHERE id = :id
        """,
        {
            "id": photo_id,
            **{k: meta.get(k) for k in ("taken_at", "camera_make", "camera_model", "width", "height", "lat", "lon")},
        },
    )
