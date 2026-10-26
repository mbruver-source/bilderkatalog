"""Filtern, Sortieren und Gruppieren des Katalogs."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import datetime

SORTS = {
    "date_desc": "taken_at IS NULL, taken_at DESC, name",
    "date_asc": "taken_at IS NULL, taken_at ASC, name",
    "name": "name COLLATE NOCASE, taken_at",
    "place": "country_code IS NULL, country_code, city, taken_at",
    "size": "size DESC, name",
}
DUP_ORDER = "p.dup_group, p.dup_rank"  # Kopien direkt nebeneinander

BOOL_FILTERS = ("no_date", "no_gps", "partial", "favorite", "dups", "unique", "date_manual")

# Kameraname ohne doppelten Hersteller ("Canon" + "Canon EOS 600D" → "Canon EOS 600D")
CAMERA = (
    "CASE WHEN {p}camera_model LIKE {p}camera_make || '%' THEN {p}camera_model "
    "ELSE TRIM(COALESCE({p}camera_make, '') || ' ' || COALESCE({p}camera_model, '')) END"
)

PHOTO_COLUMNS = (
    "id, source, online_only, meta_level, local_path, name, parent_path, size, mime, is_video, taken_at, date_source, year, month, "
    "camera_make, camera_model, width, height, lat, lon, city, region, country_code, web_url, "
    "favorite, dup_group, dup_rank"
)


@dataclass
class Filter:
    year: int | None = None
    month: int | None = None
    date_from: str | None = None   # ISO-Datum, inklusiv
    date_to: str | None = None     # ISO-Datum, inklusiv
    country: str | None = None     # ISO-Ländercode
    city: str | None = None
    folder: str | None = None      # Pfad, inklusive Unterordner
    album: int | None = None
    camera: str | None = None
    q: str | None = None           # Teil des Dateinamens
    media: str | None = None       # 'image' | 'video'
    no_date: bool = False          # nur Bilder ohne EXIF-Aufnahmedatum
    no_gps: bool = False
    partial: bool = False          # nur Datum bekannt (Details noch nicht nachgeladen)
    favorite: bool = False
    dups: bool = False             # nur Dateien, die mehrfach vorhanden sind
    unique: bool = False           # Kopien ausblenden (nur Hauptexemplare)
    date_manual: bool = False      # Datum von Hand gesetzt

    @classmethod
    def from_dict(cls, data: dict) -> "Filter":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names and v not in (None, "")})

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, False, "")}

    def where(self) -> tuple[str, list]:
        clauses, params = ["p.deleted = 0"], []
        if self.year is not None:
            clauses.append("p.year = ?")
            params.append(int(self.year))
        if self.month is not None:
            clauses.append("p.month = ?")
            params.append(int(self.month))
        if self.date_from:  # "2019-03-12" oder "2019-03-12T10:15:00"
            clauses.append("p.taken_at >= ?")
            params.append(self.date_from)
        if self.date_to:
            clauses.append("p.taken_at <= ?")
            # Reines Datum: ganzer Tag inklusive
            params.append(self.date_to + "T99" if len(self.date_to) <= 10 else self.date_to)
        if self.country:
            clauses.append("p.country_code = ?")
            params.append(self.country)
        if self.city:
            clauses.append("p.city = ?")
            params.append(self.city)
        if self.folder:
            if self.folder == "/":
                clauses.append("p.parent_path IS NOT NULL")
            else:
                clauses.append("(p.parent_path = ? OR p.parent_path LIKE ? ESCAPE '\\')")
                params += [self.folder, _escape_like(self.folder.rstrip("/")) + "/%"]
        if self.album is not None:
            clauses.append("p.id IN (SELECT photo_id FROM album_photo WHERE album_id = ?)")
            params.append(int(self.album))
        if self.camera:
            clauses.append(f"{CAMERA.format(p='p.')} = ?")
            params.append(self.camera)
        if self.q:
            clauses.append("p.name LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(self.q)}%")
        if self.media == "video":
            clauses.append("p.is_video = 1")
        elif self.media == "image":
            clauses.append("p.is_video = 0")
        if self.no_date:
            clauses.append("(p.date_source IS NULL OR p.date_source NOT IN ('exif', 'manual'))")
        if self.no_gps:
            clauses.append("p.lat IS NULL")
        if self.partial:
            clauses.append("p.meta_level = 'partial'")
        if self.favorite:
            clauses.append("p.favorite = 1")
        if self.dups:
            clauses.append("p.dup_group IS NOT NULL")
        if self.unique:
            clauses.append("(p.dup_rank IS NULL OR p.dup_rank = 0)")
        if self.date_manual:
            clauses.append("p.date_source = 'manual'")
        return " AND ".join(clauses), params


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_photos(
    conn: sqlite3.Connection, flt: Filter, sort: str = "date_desc", limit: int = 200, offset: int = 0
) -> dict:
    where, params = flt.where()
    order = DUP_ORDER if flt.dups else SORTS.get(sort, SORTS["date_desc"])
    total, partial = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(p.meta_level = 'partial'), 0) FROM photo p WHERE {where}", params
    ).fetchone()
    rows = conn.execute(
        f"SELECT {PHOTO_COLUMNS} FROM photo p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, int(limit), int(offset)],
    ).fetchall()
    return {"total": total, "partial": partial, "items": [dict(r) for r in rows]}


def photo_ids(conn: sqlite3.Connection, flt: Filter) -> list[str]:
    where, params = flt.where()
    return [r[0] for r in conn.execute(f"SELECT p.id FROM photo p WHERE {where} ORDER BY p.taken_at", params)]


def get_photo(conn: sqlite3.Connection, photo_id: str) -> dict | None:
    row = conn.execute(f"SELECT {PHOTO_COLUMNS} FROM photo WHERE id = ?", (photo_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["albums"] = [
        dict(r) for r in conn.execute(
            "SELECT a.id, a.name FROM album a JOIN album_photo ap ON ap.album_id = a.id "
            "WHERE ap.photo_id = ? ORDER BY a.name", (photo_id,)
        )
    ]
    return result


def facets(conn: sqlite3.Connection) -> dict:
    """Gruppen mit Anzahl für die Seitenleiste."""
    q = lambda sql: [dict(r) for r in conn.execute(sql)]  # noqa: E731
    return {
        "total": conn.execute("SELECT COUNT(*) FROM photo WHERE deleted = 0").fetchone()[0],
        "years": q(
            "SELECT year, month, COUNT(*) AS n FROM photo WHERE deleted = 0 AND year IS NOT NULL "
            "GROUP BY year, month ORDER BY year DESC, month DESC"
        ),
        "places": q(
            "SELECT country_code, city, COUNT(*) AS n FROM photo WHERE deleted = 0 AND country_code IS NOT NULL "
            "GROUP BY country_code, city ORDER BY country_code, n DESC"
        ),
        "folders": q(
            "SELECT parent_path AS path, COUNT(*) AS n FROM photo WHERE deleted = 0 AND parent_path IS NOT NULL "
            "GROUP BY parent_path ORDER BY parent_path COLLATE NOCASE"
        ),
        "cameras": q(
            f"SELECT {CAMERA.format(p='')} AS camera, COUNT(*) AS n "
            "FROM photo WHERE deleted = 0 AND (camera_make IS NOT NULL OR camera_model IS NOT NULL) "
            "GROUP BY camera ORDER BY n DESC"
        ),
        "special": dict(conn.execute(
            "SELECT COALESCE(SUM(date_source IS NULL OR date_source NOT IN ('exif', 'manual')), 0) AS no_date, "
            "COALESCE(SUM(lat IS NULL), 0) AS no_gps, COALESCE(SUM(is_video), 0) AS videos, "
            "COALESCE(SUM(meta_level = 'partial'), 0) AS partial, "
            "COALESCE(SUM(favorite), 0) AS favorites, "
            "COALESCE(SUM(date_source = 'manual'), 0) AS date_manual, "
            "COALESCE(SUM(dup_group IS NOT NULL), 0) AS dup_files, "
            "COALESCE(SUM(dup_rank > 0), 0) AS dup_copies, "
            "COUNT(DISTINCT dup_group) AS dup_groups "
            "FROM photo WHERE deleted = 0"
        ).fetchone()),
    }


def events(
    conn: sqlite3.Connection, flt: Filter | None = None, gap_hours: float = 6, min_size: int = 5
) -> list[dict]:
    """Erkennt Ereignisse: zeitlich dicht beieinanderliegende Bilder am selben Ort.

    Eine neue Gruppe beginnt, wenn zwischen zwei Bildern mehr als `gap_hours`
    liegen oder beide einen bekannten, aber unterschiedlichen Ort haben.
    """
    flt = flt or Filter()
    where, params = flt.where()
    rows = conn.execute(
        f"SELECT p.id, p.taken_at, p.city, p.country_code FROM photo p "
        f"WHERE {where} AND p.taken_at IS NOT NULL AND p.date_source NOT IN ('file', 'manual') ORDER BY p.taken_at",
        params,
    ).fetchall()

    groups: list[list[sqlite3.Row]] = []
    last_time: datetime | None = None
    last_city: str | None = None
    for row in rows:
        when = datetime.fromisoformat(row["taken_at"])
        city = row["city"]
        new_group = (
            last_time is None
            or (when - last_time).total_seconds() > gap_hours * 3600
            or (city and last_city and city != last_city)
        )
        if new_group:
            groups.append([])
        groups[-1].append(row)
        last_time = when
        last_city = city or last_city

    result = []
    for group in groups:
        if len(group) < min_size:
            continue
        places = Counter((r["city"], r["country_code"]) for r in group if r["city"])
        city, country = places.most_common(1)[0][0] if places else (None, None)
        step = max(1, len(group) // 4)
        result.append({
            "start": group[0]["taken_at"],
            "end": group[-1]["taken_at"],
            "count": len(group),
            "city": city,
            "country_code": country,
            "samples": [r["id"] for r in group[::step][:4]],
        })
    result.reverse()  # neueste zuerst
    return result
