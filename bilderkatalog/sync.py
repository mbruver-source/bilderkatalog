"""Indexierung: liest Metadaten per Graph-Delta-Abfrage in die lokale Datenbank."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import unquote

from .graph_client import GraphClient, GraphError

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif",
    ".dng", ".cr2", ".nef", ".arw", ".mp4", ".mov", ".m4v", ".avi", ".3gp", ".mkv",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".3gp", ".mkv"}

# z. B. IMG_20190312_101500.jpg, IMG-20190312-WA0001.jpg, 2019-03-12 10.15.00.jpg
_FILENAME_DATE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[-_ T.]?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d))?(?!\d)"
)

Progress = Callable[[str, int], None]


# -- Bereiche (vom Nutzer gewählte Ordner) ----------------------------------

def item_path(item: dict) -> str:
    """Vollständiger Pfad eines Items aus einer normalen (Nicht-Delta-)Abfrage."""
    if "root" in item:
        return "/"
    parent = unquote(item.get("parentReference", {}).get("path", ""))
    parent = parent.split(":", 1)[1] if ":" in parent else ""
    return f"{parent.rstrip('/')}/{item['name']}"


def add_scope(conn: sqlite3.Connection, client: GraphClient, item_id: str) -> dict:
    item = client.get_item(item_id)
    if "folder" not in item and "root" not in item:
        raise ValueError("Nur Ordner können als Bereich gewählt werden.")
    path = item_path(item)
    conn.execute(
        "INSERT OR IGNORE INTO scope(item_id, path) VALUES (?, ?)", (item["id"], path)
    )
    _upsert_folder(conn, item["id"], item.get("name", ""), None, path=path)
    conn.commit()
    return {"item_id": item["id"], "path": path}


def remove_scope(conn: sqlite3.Connection, item_id: str) -> None:
    """Entfernt einen Bereich samt Bildern, die von keinem anderen Bereich abgedeckt sind."""
    row = conn.execute("SELECT path, kind FROM scope WHERE item_id = ?", (item_id,)).fetchone()
    if not row:
        return
    conn.execute("DELETE FROM scope WHERE item_id = ?", (item_id,))
    if row["kind"] == "local":
        conn.execute("DELETE FROM photo WHERE source = 'local' AND scope_id = ?", (item_id,))
        conn.commit()
        return
    remaining = [r["path"] for r in conn.execute("SELECT path FROM scope WHERE kind = 'graph'")]
    photos = conn.execute(
        "SELECT id, parent_path FROM photo WHERE source = 'graph' AND "
        "(parent_path = ? OR parent_path LIKE ? ESCAPE '\\')",
        (row["path"], _like_prefix(row["path"])),
    ).fetchall()
    orphaned = [p["id"] for p in photos if not any(_under(p["parent_path"], s) for s in remaining)]
    conn.executemany("DELETE FROM photo WHERE id = ?", [(i,) for i in orphaned])
    conn.commit()


def _under(path: str | None, scope_path: str) -> bool:
    if path is None:
        return False
    return scope_path == "/" or path == scope_path or path.startswith(scope_path.rstrip("/") + "/")


def _like_prefix(path: str) -> str:
    escaped = path.rstrip("/").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "/%"


# -- Synchronisation --------------------------------------------------------

def sync_all(conn: sqlite3.Connection, client: GraphClient, progress: Progress | None = None) -> int:
    total = 0
    for scope in conn.execute("SELECT item_id, path, delta_link FROM scope WHERE kind = 'graph'").fetchall():
        total += sync_scope(conn, client, scope["item_id"], scope["delta_link"], progress)
    return total


def sync_scope(
    conn: sqlite3.Connection,
    client: GraphClient,
    item_id: str,
    delta_link: str | None = None,
    progress: Progress | None = None,
) -> int:
    """Gleicht einen Bereich ab. Gibt die Zahl verarbeiteter Medien-Items zurück."""
    try:
        return _run_delta(conn, client, item_id, delta_link, progress)
    except GraphError as err:
        if err.status == 410 and delta_link:  # Delta-Token abgelaufen → vollständig neu einlesen
            return _run_delta(conn, client, item_id, None, progress)
        raise


def _run_delta(conn, client, item_id, delta_link, progress) -> int:
    count = 0
    new_link = None
    for items, link in client.delta(item_id, delta_link):
        for item in items:
            if apply_item(conn, item):
                count += 1
        conn.commit()
        new_link = link or new_link
        if progress:
            progress(item_id, count)
    resolve_paths(conn)
    if new_link:
        conn.execute(
            "UPDATE scope SET delta_link = ?, last_sync = ? WHERE item_id = ?",
            (new_link, datetime.now(timezone.utc).isoformat(timespec="seconds"), item_id),
        )
    conn.commit()
    return count


def apply_item(conn: sqlite3.Connection, item: dict) -> bool:
    """Übernimmt ein Delta-Item. True, wenn es ein Bild/Video war."""
    parent_id = item.get("parentReference", {}).get("id")
    if "deleted" in item:
        _mark_deleted(conn, item["id"])
        return False
    if "folder" in item or "root" in item:
        _upsert_folder(conn, item["id"], item.get("name", ""), parent_id, is_root="root" in item)
        return False
    if not is_media(item):
        return False
    conn.execute(
        """
        INSERT INTO photo (id, name, parent_id, size, mime, is_video, taken_at, date_source, year, month,
                           camera_make, camera_model, width, height, lat, lon, web_url, etag, deleted,
                           geocoded, city, region, country_code)
        VALUES (:id, :name, :parent_id, :size, :mime, :is_video, :taken_at, :date_source, :year, :month,
                :camera_make, :camera_model, :width, :height, :lat, :lon, :web_url, :etag, 0,
                0, NULL, NULL, NULL)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name, parent_id = excluded.parent_id, size = excluded.size,
            mime = excluded.mime, is_video = excluded.is_video, taken_at = excluded.taken_at,
            date_source = excluded.date_source, year = excluded.year, month = excluded.month,
            camera_make = excluded.camera_make, camera_model = excluded.camera_model,
            width = excluded.width, height = excluded.height, web_url = excluded.web_url,
            etag = excluded.etag, deleted = 0,
            geocoded = CASE WHEN photo.lat IS excluded.lat AND photo.lon IS excluded.lon
                            THEN photo.geocoded ELSE 0 END,
            lat = excluded.lat, lon = excluded.lon
        """,
        photo_row(item, parent_id),
    )
    return True


def is_media(item: dict) -> bool:
    if "file" not in item:
        return False
    if any(k in item for k in ("photo", "image", "video")):
        return True
    mime = item["file"].get("mimeType", "")
    return mime.startswith(("image/", "video/")) or _ext(item["name"]) in MEDIA_EXTENSIONS


def photo_row(item: dict, parent_id: str | None) -> dict:
    photo = item.get("photo", {})
    location = item.get("location", {})
    dims = item.get("image") or item.get("video") or {}
    taken_at, source = taken_date(item)
    mime = item.get("file", {}).get("mimeType")
    is_video = "video" in item or (mime or "").startswith("video/") or _ext(item["name"]) in VIDEO_EXTENSIONS
    return {
        "id": item["id"],
        "name": item["name"],
        "parent_id": parent_id,
        "size": item.get("size"),
        "mime": mime,
        "is_video": int(is_video),
        "taken_at": taken_at,
        "date_source": source,
        "year": int(taken_at[:4]) if taken_at else None,
        "month": int(taken_at[5:7]) if taken_at else None,
        "camera_make": photo.get("cameraMake"),
        "camera_model": photo.get("cameraModel"),
        "width": dims.get("width"),
        "height": dims.get("height"),
        "lat": location.get("latitude"),
        "lon": location.get("longitude"),
        "web_url": item.get("webUrl"),
        "etag": item.get("eTag"),
    }


def taken_date(item: dict) -> tuple[str | None, str | None]:
    """Aufnahmezeit: EXIF → Dateiname → Dateisystem-Zeitstempel."""
    exif = item.get("photo", {}).get("takenDateTime")
    if exif:
        return _iso(exif), "exif"
    from_name = date_from_filename(item.get("name", ""))
    if from_name:
        return from_name, "filename"
    fs = item.get("fileSystemInfo", {})
    stamp = fs.get("createdDateTime") or fs.get("lastModifiedDateTime") or item.get("createdDateTime")
    return (_iso(stamp), "file") if stamp else (None, None)


def date_from_filename(name: str) -> str | None:
    match = _FILENAME_DATE.search(name)
    if not match:
        return None
    y, mo, d, h, mi, s = match.groups()
    try:
        value = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None
    if value > datetime.now():
        return None
    return value.isoformat(timespec="seconds")


def _iso(stamp: str) -> str:
    # Graph liefert z. B. "2019-03-12T10:15:00Z"; EXIF-Zeiten sind lokale Zeit mit "Z"-Suffix.
    return stamp.rstrip("Z").split(".")[0][:19]


def _ext(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot >= 0 else ""


# -- Ordner und Pfade -------------------------------------------------------

def _upsert_folder(conn, folder_id, name, parent_id, path=None, is_root=False) -> None:
    if is_root:
        path = "/"
    if path is not None:
        conn.execute(
            """INSERT INTO folder(id, name, parent_id, path, fixed_path) VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(id) DO UPDATE SET path = excluded.path, fixed_path = 1""",
            (folder_id, name, parent_id, path),
        )
    else:
        conn.execute(
            """INSERT INTO folder(id, name, parent_id) VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name = excluded.name, parent_id = excluded.parent_id""",
            (folder_id, name, parent_id),
        )


def resolve_paths(conn: sqlite3.Connection) -> None:
    """Berechnet Ordnerpfade aus der Eltern-Kette (Delta-Antworten enthalten keine Pfade)."""
    folders = {r["id"]: dict(r) for r in conn.execute("SELECT id, name, parent_id, path, fixed_path FROM folder")}
    resolved: dict[str, str | None] = {}

    def path_of(folder_id: str, depth: int = 0) -> str | None:
        if folder_id in resolved:
            return resolved[folder_id]
        folder = folders.get(folder_id)
        if folder is None or depth > 200:
            return None
        if folder["fixed_path"]:
            result = folder["path"]
        else:
            parent = path_of(folder["parent_id"], depth + 1) if folder["parent_id"] else None
            result = None if parent is None else f"{parent.rstrip('/')}/{folder['name']}"
        resolved[folder_id] = result
        return result

    updates = [(path_of(fid), fid) for fid, f in folders.items() if not f["fixed_path"]]
    conn.executemany("UPDATE folder SET path = ? WHERE id = ?", updates)
    conn.execute(
        """UPDATE photo SET parent_path = (SELECT path FROM folder WHERE folder.id = photo.parent_id)
           WHERE parent_id IN (SELECT id FROM folder)"""
    )


def _mark_deleted(conn: sqlite3.Connection, item_id: str) -> None:
    """Gelöschtes Item: Bild markieren bzw. Ordner samt Inhalt."""
    conn.execute("UPDATE photo SET deleted = 1 WHERE id = ?", (item_id,))
    ids = [item_id]
    while ids:
        current = ids.pop()
        conn.execute("UPDATE photo SET deleted = 1 WHERE parent_id = ?", (current,))
        ids += [r["id"] for r in conn.execute("SELECT id FROM folder WHERE parent_id = ?", (current,))]
        conn.execute("DELETE FROM folder WHERE id = ? AND fixed_path = 0", (current,))
