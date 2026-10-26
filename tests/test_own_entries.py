"""Duplikate, Favoriten, Datumskorrektur und Sicherung (erzeugte Testbilder, keine echten Fotos)."""

import json
import os
import shutil

import pytest

from bilderkatalog import albums, dates, duplicates, local_source, queries, sicherung, winfiles
from bilderkatalog.queries import Filter

from .test_local import make_jpeg, rows, scan


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Fotos"
    make_jpeg(root / "Urlaub" / "IMG_0001.jpg", "2021:07:14 09:30:00")
    make_jpeg(root / "Urlaub" / "IMG_0002.jpg", "2021:07:14 11:00:00", size=(80, 60))
    make_jpeg(root / "Scan" / "scan_001.jpg")  # ohne EXIF, Datum aus Dateizeit
    # dieselbe Datei zweimal kopiert, einmal umbenannt
    (root / "WhatsApp").mkdir()
    shutil.copy2(root / "Urlaub" / "IMG_0001.jpg", root / "WhatsApp" / "IMG_0001.jpg")
    shutil.copy2(root / "Urlaub" / "IMG_0001.jpg", root / "Urlaub" / "IMG_0001 (1).jpg")
    return root


# -- Duplikate ------------------------------------------------------------------

def test_duplicates_grouped_with_best_copy_first(conn, library):
    scan(conn, library)
    assert duplicates.refresh(conn) == {"groups": 1, "copies": 2}
    group = conn.execute("SELECT parent_path, name, dup_rank FROM photo WHERE dup_group IS NOT NULL "
                         "ORDER BY dup_rank").fetchall()
    # gleicher Name + Größe (WhatsApp) bzw. gleiche Größe + EXIF-Zeit (umbenannt) → eine Gruppe
    assert [(r["parent_path"], r["name"]) for r in group] == [
        ("/Fotos/Urlaub", "IMG_0001.jpg"), ("/Fotos/Urlaub", "IMG_0001 (1).jpg"), ("/Fotos/WhatsApp", "IMG_0001.jpg")]
    assert queries.list_photos(conn, Filter(unique=True))["total"] == 3
    assert queries.list_photos(conn, Filter(dups=True))["total"] == 3
    assert queries.facets(conn)["special"]["dup_copies"] == 2


def test_favorite_becomes_main_copy(conn, library):
    scan(conn, library)
    whatsapp = conn.execute("SELECT id FROM photo WHERE parent_path = '/Fotos/WhatsApp'").fetchone()["id"]
    albums.set_favorite(conn, [whatsapp], True)
    duplicates.refresh(conn)
    assert conn.execute("SELECT dup_rank FROM photo WHERE id = ?", (whatsapp,)).fetchone()[0] == 0


# -- Datum von Hand ---------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("1998", ("1998-01-01T12:00:00", "year")),
    ("7.1998", ("1998-07-01T12:00:00", "month")),
    ("1998-07", ("1998-07-01T12:00:00", "month")),
    ("14.07.1998", ("1998-07-14T12:00:00", "day")),
    ("14.7.1998 9:05", ("1998-07-14T09:05:00", "time")),
    ("1998-07-14T09:05:30", ("1998-07-14T09:05:30", "time")),
])
def test_parse_date(text, expected):
    assert dates.parse(text) == expected


@pytest.mark.parametrize("text", ["", "gestern", "31.02.1998", "1700", "13.1998"])
def test_parse_date_rejects(text):
    with pytest.raises(ValueError):
        dates.parse(text)


def test_manual_date_survives_rescan_and_can_be_reset(conn, library):
    scan(conn, library)
    scan_id = rows(conn)["scan_001.jpg"]["id"]
    auto = rows(conn)["scan_001.jpg"]["taken_at"]
    assert dates.set_date(conn, [scan_id], "07.1998") == 1

    # Datei geändert → wird neu gelesen; das manuelle Datum muss bleiben
    path = library / "Scan" / "scan_001.jpg"
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 60))
    scan(conn, library)
    photo = rows(conn)["scan_001.jpg"]
    assert (photo["taken_at"], photo["date_source"], photo["year"], photo["month"]) == (
        "1998-07-01T12:00:00", "manual", 1998, 7)
    assert queries.list_photos(conn, Filter(date_manual=True))["total"] == 1
    assert queries.list_photos(conn, Filter(no_date=True, folder="/Fotos/Scan"))["total"] == 0

    assert dates.reset(conn, [scan_id]) == 1
    photo = rows(conn)["scan_001.jpg"]
    assert photo["date_source"] == "file" and photo["taken_at"] != "1998-07-01T12:00:00"
    assert photo["taken_at"][:4] == auto[:4] or photo["taken_at"] > auto  # neue Dateizeit


def test_manual_date_survives_enrich(conn, library, monkeypatch):
    monkeypatch.setattr(winfiles, "is_online_only", lambda st: True)
    monkeypatch.setattr(winfiles, "placeholder_date_taken", lambda p: None)
    monkeypatch.setattr(winfiles, "free_up_space", lambda p: True)
    scan(conn, library)
    photo_id = rows(conn)["IMG_0002.jpg"]["id"]
    dates.set_date(conn, [photo_id], "1.1.2000")
    local_source.enrich(conn, ids=[photo_id], workers=1)
    photo = rows(conn)["IMG_0002.jpg"]
    assert photo["meta_level"] == "full" and photo["taken_at"] == "2000-01-01T12:00:00"
    auto = conn.execute("SELECT auto_taken_at, auto_date_source FROM date_override").fetchone()
    assert tuple(auto) == ("2021-07-14T11:00:00", "exif")  # EXIF-Datum fürs Zurücksetzen gemerkt


def test_replaced_file_keeps_favorite_date_and_album(conn, library):
    scan(conn, library)
    old_id = rows(conn)["scan_001.jpg"]["id"]
    album = albums.create_album(conn, "Scans")
    albums.add_photos(conn, album["id"], [old_id])
    albums.set_favorite(conn, [old_id], True)
    dates.set_date(conn, [old_id], "1985")

    path = library / "Scan" / "scan_001.jpg"
    keep = library / "keep.jpg"
    path.rename(keep)
    make_jpeg(path, size=(70, 50))  # neue Datei am selben Ort → neue Datei-ID
    keep.unlink()
    scan(conn, library)

    photo = rows(conn)["scan_001.jpg"]
    assert photo["id"] != old_id
    assert photo["favorite"] == 1 and photo["taken_at"] == "1985-01-01T12:00:00"
    assert albums.list_albums(conn)[0]["n"] == 1


# -- Sicherung --------------------------------------------------------------------

def _own_entries(conn):
    by_name = {r["name"] + r["parent_path"]: r["id"] for r in conn.execute("SELECT * FROM photo")}
    urlaub = by_name["IMG_0002.jpg/Fotos/Urlaub"]
    scan_id = by_name["scan_001.jpg/Fotos/Scan"]
    album = albums.create_album(conn, "Bretagne", "Sommer")
    albums.add_photos(conn, album["id"], [urlaub, scan_id])
    albums.set_favorite(conn, [urlaub], True)
    dates.set_date(conn, [scan_id], "14.07.1998")


def test_backup_roundtrip_on_other_pc(tmp_path, library):
    from bilderkatalog import db

    first = db.connect(tmp_path / "a.db")
    db.init(first)
    scan(first, library)
    _own_entries(first)
    data = json.loads(json.dumps(sicherung.collect(first)))
    first.close()
    assert sicherung.counts(data) == {"albums": 1, "favorites": 1, "dates": 1}

    # anderer PC: andere Datei-IDs → Zuordnung über Ordner und Name
    for ref in data["photos"]:
        old = ref["id"]
        ref["id"] = "L:fremd:" + old
        for album in data["albums"]:
            album["photos"] = [ref["id"] if p == old else p for p in album["photos"]]
        data["favorites"] = [ref["id"] if p == old else p for p in data["favorites"]]
        for entry in data["dates"]:
            entry["photo"] = ref["id"] if entry["photo"] == old else entry["photo"]

    second = db.connect(tmp_path / "b.db")
    db.init(second)
    scan(second, library)
    result = sicherung.restore(second, data)
    assert result == {"albums_new": 1, "assigned": 2, "favorites": 1, "dates": 1, "not_found": 0}
    photos = rows(second)
    assert photos["IMG_0002.jpg"]["favorite"] == 1
    assert photos["scan_001.jpg"]["taken_at"] == "1998-07-14T12:00:00"
    assert dates.precision_of(second, photos["scan_001.jpg"]["id"]) == "day"
    # erneutes Einspielen verdoppelt nichts
    assert sicherung.restore(second, data)["albums_new"] == 0
    assert albums.list_albums(second)[0]["n"] == 2
    second.close()


def test_restore_rejects_other_files(conn):
    with pytest.raises(ValueError):
        sicherung.restore(conn, {"format": "etwas anderes"})


def test_auto_backup_only_when_changed(conn, library):
    scan(conn, library)
    assert sicherung.auto_backup(conn) is None  # nichts Eigenes → keine Datei
    _own_entries(conn)
    assert sicherung.auto_backup(conn) is not None
    assert sicherung.auto_backup(conn) is None  # unverändert
    assert len(sicherung.list_auto()) == 1


# -- API ----------------------------------------------------------------------------

def test_api_favorites_dates_backup(tmp_path, library):
    from fastapi.testclient import TestClient

    from bilderkatalog.api import State, create_app

    state = State(tmp_path / "api.db")
    conn = state.connect()
    scan(conn, library)
    ids = {r["name"] + r["parent_path"]: r["id"] for r in conn.execute("SELECT * FROM photo")}
    conn.close()
    client = TestClient(create_app(state))
    hdr = {"X-Bilderkatalog": "1"}
    scan_id = ids["scan_001.jpg/Fotos/Scan"]

    assert client.post("/api/favorites", json={"ids": [scan_id]}, headers=hdr).json() == {"changed": 1}
    assert client.get("/api/photos", params={"favorite": "1"}).json()["total"] == 1
    assert client.post("/api/dates", json={"ids": [scan_id], "date": "unsinn"}, headers=hdr).status_code == 400
    assert client.post("/api/dates", json={"ids": [scan_id], "date": "1990"}, headers=hdr).json() == {"changed": 1}
    detail = client.get(f"/api/photos/{scan_id}").json()
    assert detail["date_source"] == "manual" and detail["date_precision"] == "year" and detail["favorite"] == 1

    client.post("/api/duplicates/refresh", headers=hdr)
    original = ids["IMG_0001.jpg/Fotos/Urlaub"]
    assert len(client.get(f"/api/photos/{original}").json()["copies"]) == 3

    saved = client.post("/api/backup", json={"target_dir": str(tmp_path / "out")}, headers=hdr).json()
    assert saved["favorites"] == 1 and os.path.exists(saved["path"])
    data = json.loads(open(saved["path"], encoding="utf-8").read())
    assert client.post("/api/restore", json={"data": data}, headers=hdr).json()["not_found"] == 0
    assert client.post("/api/restore", json={"auto": "../../etc/passwd"}, headers=hdr).status_code == 400
