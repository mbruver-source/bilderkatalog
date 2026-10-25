"""ZIP-Export: lädt Originale aus OneDrive (nur lesend) und packt sie lokal."""

from __future__ import annotations

import re
import sqlite3
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .graph_client import GraphClient, GraphError

STRUCTURES = ("flat", "year_month", "folder")

Progress = Callable[[int, int, str], None]


def safe_filename(name: str, fallback: str = "Export") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:120] or fallback


def export_zip(
    conn: sqlite3.Connection,
    client: GraphClient,
    photo_ids: list[str],
    target_dir: Path,
    name: str,
    structure: str = "flat",
    progress: Progress | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    """Schreibt eine ZIP-Datei und gibt Pfad sowie Fehlerliste zurück."""
    if structure not in STRUCTURES:
        raise ValueError(f"Unbekannte Struktur: {structure}")
    if not photo_ids:
        raise ValueError("Keine Bilder ausgewählt.")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target = target_dir / f"{safe_filename(name)}_{stamp}.zip"
    partial = target.with_name(target.name + ".part")

    rows = {r["id"]: r for r in _fetch_rows(conn, photo_ids)}
    used: set[str] = set()
    errors: list[str] = []
    written = 0
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for index, pid in enumerate(photo_ids, start=1):
                if cancelled():
                    raise InterruptedError("Export abgebrochen")
                row = rows.get(pid)
                if row is None:
                    continue
                arcname = _unique(_arcname(row, structure), used)
                if progress:
                    progress(index, len(photo_ids), row["name"])
                info = zipfile.ZipInfo(arcname, date_time=_zip_time(row["taken_at"]))
                info.compress_type = zipfile.ZIP_STORED
                try:
                    with client.content(pid) as chunks, zf.open(info, "w", force_zip64=True) as dest:
                        for chunk in chunks:
                            dest.write(chunk)
                    written += 1
                except GraphError as err:
                    errors.append(f"{row['name']}: {err}")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return {"path": str(target), "written": written, "errors": errors}


def _fetch_rows(conn: sqlite3.Connection, ids: list[str]) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows += conn.execute(
            f"SELECT id, name, parent_path, taken_at, year, month FROM photo "
            f"WHERE deleted = 0 AND id IN ({','.join('?' * len(chunk))})",
            chunk,
        ).fetchall()
    return rows


def _arcname(row: sqlite3.Row, structure: str) -> str:
    name = safe_filename(row["name"], "bild")
    if structure == "year_month":
        if row["year"]:
            return f"{row['year']}/{row['month']:02d}/{name}"
        return f"ohne Datum/{name}"
    if structure == "folder" and row["parent_path"]:
        parts = [safe_filename(p, "_") for p in row["parent_path"].strip("/").split("/") if p]
        return "/".join([*parts, name])
    return name


def _unique(arcname: str, used: set[str]) -> str:
    candidate, counter = arcname, 2
    stem, dot, ext = arcname.rpartition(".")
    if not dot:
        stem, ext = arcname, ""
    while candidate.lower() in used:
        candidate = f"{stem} ({counter}).{ext}" if ext else f"{stem} ({counter})"
        counter += 1
    used.add(candidate.lower())
    return candidate


def _zip_time(taken_at: str | None) -> tuple[int, int, int, int, int, int]:
    try:
        value = datetime.fromisoformat(taken_at) if taken_at else None
    except ValueError:
        value = None
    if value is None or value.year < 1980:
        value = datetime(1980, 1, 1)
    return (value.year, value.month, value.day, value.hour, value.minute, value.second)
