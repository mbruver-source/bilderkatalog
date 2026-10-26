"""Lokaler Modus mit erzeugten Testbildern (keine echten Fotos)."""

import os
import sqlite3
import zipfile
from datetime import datetime
from fractions import Fraction

import pytest
from PIL import Image

from bilderkatalog import albums, db, export, local_source, winfiles

from .conftest import client_for


def make_jpeg(path, taken=None, gps=None, camera=("Beispiel", "Cam 1"), size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (120, 80, 40))
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = camera
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
    if gps:
        lat, lon = gps
        to_dms = lambda v: tuple(Fraction(x).limit_denominator(10000) for x in (int(abs(v)), int(abs(v) * 60 % 60), abs(v) * 3600 % 60))  # noqa: E731
        exif.get_ifd(0x8825).update({1: "N" if lat >= 0 else "S", 2: to_dms(lat), 3: "E" if lon >= 0 else "W", 4: to_dms(lon)})
    image.save(path, "JPEG", exif=exif)
    return path


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Fotos"
    make_jpeg(root / "Urlaub" / "IMG_0001.jpg", "2021:07:14 09:30:00", (48.8566, 2.3522))
    make_jpeg(root / "Urlaub" / "IMG_0002.jpg", "2021:07:14 11:00:00", (-33.8688, 151.2093))
    make_jpeg(root / "IMG-20190312-WA0001.jpg")  # ohne EXIF → Datum aus Dateiname
    (root / "notizen.txt").write_text("kein Bild")
    (root / "clip.mp4").write_bytes(b"\x00" * 10)
    return root


def scan(conn, root):
    local_source.add_local_scope(conn, str(root)) if not conn.execute("SELECT 1 FROM scope").fetchone() else None
    return local_source.scan_scope(conn, str(root.resolve()), workers=2)


def rows(conn):
    return {r["name"]: r for r in conn.execute("SELECT * FROM photo")}


def test_scan_reads_exif_gps_and_dates(conn, library):
    assert scan(conn, library) == 4
    photos = rows(conn)
    assert set(photos) == {"IMG_0001.jpg", "IMG_0002.jpg", "IMG-20190312-WA0001.jpg", "clip.mp4"}
    first = photos["IMG_0001.jpg"]
    assert first["taken_at"] == "2021-07-14T09:30:00" and first["date_source"] == "exif"
    assert first["lat"] == pytest.approx(48.8566, abs=1e-3) and first["lon"] == pytest.approx(2.3522, abs=1e-3)
    assert (first["camera_make"], first["camera_model"]) == ("Beispiel", "Cam 1")
    assert first["parent_path"] == "/Fotos/Urlaub" and first["meta_level"] == "full"
    south = photos["IMG_0002.jpg"]
    assert south["lat"] < 0 and south["lon"] > 0
    assert photos["IMG-20190312-WA0001.jpg"]["date_source"] == "filename"
    assert photos["clip.mp4"]["is_video"] == 1 and photos["clip.mp4"]["date_source"] == "file"


def test_rescan_is_incremental_and_tracks_changes(conn, library):
    scan(conn, library)
    assert scan(conn, library) == 0
    album = albums.create_album(conn, "A")
    albums.add_photos(conn, album["id"], [rows(conn)["IMG_0001.jpg"]["id"]])

    (library / "Urlaub" / "IMG_0002.jpg").unlink()
    moved = library / "Neu" / "IMG_0001.jpg"
    moved.parent.mkdir()
    os.replace(library / "Urlaub" / "IMG_0001.jpg", moved)  # Verschieben behält die Datei-ID
    scan(conn, library)

    photos = rows(conn)
    assert photos["IMG_0002.jpg"]["deleted"] == 1
    assert photos["IMG_0001.jpg"]["parent_path"] == "/Fotos/Neu"
    assert conn.execute("SELECT COUNT(*) FROM album_photo").fetchone()[0] == 1  # Album bleibt erhalten


def test_overlapping_scopes(conn, library):
    local_source.add_local_scope(conn, str(library / "Urlaub"))
    with pytest.raises(ValueError):
        local_source.add_local_scope(conn, str(library / "Urlaub"))
    local_source.add_local_scope(conn, str(library))  # übernimmt den Unterordner
    assert [r[0] for r in conn.execute("SELECT item_id FROM scope")] == [str(library.resolve())]


def test_online_only_files_are_not_opened(conn, library, monkeypatch):
    monkeypatch.setattr(winfiles, "is_online_only", lambda st: True)
    monkeypatch.setattr(winfiles, "placeholder_date_taken", lambda p: datetime(2020, 1, 2, 3, 4, 5))
    monkeypatch.setattr(local_source, "read_exif", lambda p: pytest.fail("darf nicht geöffnet werden"))
    scan(conn, library)
    first = rows(conn)["IMG_0001.jpg"]
    assert first["meta_level"] == "partial" and first["online_only"] == 1
    assert first["taken_at"] == "2020-01-02T03:04:05" and first["lat"] is None
    assert rows(conn)["clip.mp4"]["meta_level"] == "full"  # Videos werden nie nachgeladen


def test_enrich_reads_details_and_frees_space(conn, library, monkeypatch):
    monkeypatch.setattr(winfiles, "is_online_only", lambda st: True)
    monkeypatch.setattr(winfiles, "placeholder_date_taken", lambda p: None)
    scan(conn, library)
    freed = []
    monkeypatch.setattr(winfiles, "free_up_space", lambda p: freed.append(p.name) or True)

    result = local_source.enrich(conn, workers=1)

    assert result["processed"] == 3 and not result["errors"]
    assert sorted(freed) == ["IMG-20190312-WA0001.jpg", "IMG_0001.jpg", "IMG_0002.jpg"]
    first = rows(conn)["IMG_0001.jpg"]
    assert first["meta_level"] == "full" and first["date_source"] == "exif"
    assert first["lat"] == pytest.approx(48.8566, abs=1e-3)
    assert local_source.thumb_cache_file(first["id"], "medium").exists()
    assert conn.execute("SELECT COUNT(*) FROM photo WHERE meta_level = 'partial'").fetchone()[0] == 0


def test_enrich_stops_on_low_disk_space(conn, library, monkeypatch):
    monkeypatch.setattr(winfiles, "is_online_only", lambda st: True)
    scan(conn, library)
    monkeypatch.setattr(local_source, "MIN_FREE_BYTES", 10**18)
    with pytest.raises(local_source.LowDiskSpace):
        local_source.enrich(conn, workers=1)


def test_thumbnail_respects_orientation(tmp_path):
    path = make_jpeg(tmp_path / "a.jpg", size=(400, 200))
    data = local_source.thumbnail_bytes(str(path), "small")
    from io import BytesIO

    assert Image.open(BytesIO(data)).size == (160, 80)


def test_export_local_files(conn, library, tmp_path):
    scan(conn, library)
    ids = [r["id"] for r in conn.execute("SELECT id FROM photo WHERE name GLOB 'IMG_*' ORDER BY name")]
    result = export.export_zip(conn, None, ids, tmp_path / "out", "Urlaub", "folder")
    with zipfile.ZipFile(result["path"]) as zf:
        assert sorted(zf.namelist()) == ["Fotos/Urlaub/IMG_0001.jpg", "Fotos/Urlaub/IMG_0002.jpg"]
        assert zf.read("Fotos/Urlaub/IMG_0001.jpg") == (library / "Urlaub" / "IMG_0001.jpg").read_bytes()


def test_migration_from_schema_v1(tmp_path):
    path = tmp_path / "alt.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE photo (id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT, parent_path TEXT,
            size INTEGER, mime TEXT, is_video INTEGER NOT NULL DEFAULT 0, taken_at TEXT, date_source TEXT,
            year INTEGER, month INTEGER, camera_make TEXT, camera_model TEXT, width INTEGER, height INTEGER,
            lat REAL, lon REAL, city TEXT, region TEXT, country_code TEXT, geocoded INTEGER NOT NULL DEFAULT 0,
            web_url TEXT, etag TEXT, deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE scope (item_id TEXT PRIMARY KEY, path TEXT NOT NULL, delta_link TEXT, last_sync TEXT);
        INSERT INTO photo (id, name) VALUES ('X', 'alt.jpg');
        PRAGMA user_version = 1;
    """)
    old.close()
    conn = db.connect(path)
    db.init(conn)
    row = conn.execute("SELECT source, meta_level FROM photo WHERE id = 'X'").fetchone()
    assert tuple(row) == ("graph", "full")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    conn.close()


def test_enrich_only_selected_ids(conn, library, monkeypatch):
    monkeypatch.setattr(winfiles, "is_online_only", lambda st: True)
    monkeypatch.setattr(winfiles, "placeholder_date_taken", lambda p: None)
    monkeypatch.setattr(winfiles, "free_up_space", lambda p: True)
    scan(conn, library)
    target = rows(conn)["IMG_0002.jpg"]["id"]

    result = local_source.enrich(conn, workers=1, ids=[target])

    assert result["processed"] == 1
    levels = {name: r["meta_level"] for name, r in rows(conn).items()}
    assert levels["IMG_0002.jpg"] == "full" and levels["IMG_0001.jpg"] == "partial"


def test_enrich_one_via_api(tmp_path, library, monkeypatch):
    from bilderkatalog.api import State, create_app

    monkeypatch.setattr(winfiles, "is_online_only", lambda st: True)
    monkeypatch.setattr(winfiles, "placeholder_date_taken", lambda p: None)
    monkeypatch.setattr(winfiles, "free_up_space", lambda p: True)
    state = State(tmp_path / "api.db")
    conn = state.connect()
    scan(conn, library)
    photo_id = rows(conn)["IMG_0001.jpg"]["id"]
    conn.close()
    client = client_for(create_app(state))
    hdr = {"X-Bilderkatalog": "1"}

    view = client.get("/api/photos", params={"folder": "/Fotos/Urlaub"}).json()
    assert view["partial"] == 2
    # nur online → nie aus der Datei (würde herunterladen), sondern über die Windows-Vorschau
    shell_calls = []
    monkeypatch.setattr(winfiles, "shell_thumbnail", lambda path, px: shell_calls.append(px))
    assert client.get(f"/api/thumb/{photo_id}").status_code == 404
    assert shell_calls == [320]
    updated = client.post(f"/api/photos/{photo_id}/enrich", headers=hdr).json()
    assert updated["meta_level"] == "full" and updated["camera_model"] == "Cam 1"
    assert client.get(f"/api/thumb/{photo_id}").status_code == 200


def test_huge_and_broken_images_never_abort(conn, library, monkeypatch):
    """Riesenbilder (Panoramen) und defekte Dateien dürfen keinen Lauf abbrechen."""
    from PIL import Image as PILImage

    (library / "kaputt.jpg").write_bytes(b"kein jpeg")
    make_jpeg(library / "panorama.jpg", "2020:05:01 12:00:00", size=(400, 100))
    assert PILImage.MAX_IMAGE_PIXELS is None  # Pillow-Sperre ist aufgehoben

    monkeypatch.setattr(local_source, "MAX_DECODE_PIXELS", 10)  # simuliert "zu groß zum Dekodieren"
    scan(conn, library)
    photos = rows(conn)
    assert photos["panorama.jpg"]["taken_at"] == "2020-05-01T12:00:00"  # Metadaten trotzdem gelesen
    assert photos["kaputt.jpg"]["date_source"] == "file"
    assert local_source.thumbnail_bytes(str(library / "panorama.jpg"), "medium") is None
    assert local_source.thumbnail_bytes(str(library / "kaputt.jpg"), "medium") is None


def test_enrich_skips_unreadable_and_continues(conn, library, monkeypatch):
    monkeypatch.setattr(winfiles, "is_online_only", lambda st: True)
    monkeypatch.setattr(winfiles, "placeholder_date_taken", lambda p: None)
    monkeypatch.setattr(winfiles, "free_up_space", lambda p: True)
    scan(conn, library)
    real = local_source.read_exif

    def flaky(path):
        if path.endswith("IMG_0001.jpg"):
            raise OSError(380, "Der Clouddateianbieter wird nicht ausgeführt")
        return real(path)

    monkeypatch.setattr(local_source, "read_exif", flaky)
    result = local_source.enrich(conn, workers=1)
    assert result["processed"] == 3 and len(result["errors"]) == 1
    levels = {name: r["meta_level"] for name, r in rows(conn).items()}
    assert levels["IMG_0001.jpg"] == "partial"  # wird beim nächsten Mal erneut versucht
    assert levels["IMG_0002.jpg"] == "full"
