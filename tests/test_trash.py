"""Löschen in den Papierkorb. Der echte Papierkorb wird nie benutzt (Attrappe)."""

import os
import time

import pytest
from bilderkatalog import albums, trash, winfiles
from bilderkatalog.api import State, create_app

from .test_local import make_jpeg, rows, scan
from .conftest import client_for

HDR = {"X-Bilderkatalog": "1"}


@pytest.fixture
def papierkorb(monkeypatch):
    """Attrappe: "verschiebt" Dateien, indem sie sie entfernt; gesperrte Dateien bleiben liegen."""
    erhalten: list[str] = []
    gesperrt: set[str] = set()

    def move(paths):
        for p in paths:
            erhalten.append(p.name)
            if p.name not in gesperrt:
                os.remove(p)

    monkeypatch.setattr(winfiles, "move_to_recycle_bin", move)
    return erhalten, gesperrt


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Fotos"
    for i in range(3):
        make_jpeg(root / f"IMG_000{i}.jpg", f"2021:07:14 09:3{i}:00", size=(64 + i, 48))
    return root


def test_summary_and_delete(conn, library, papierkorb):
    erhalten, gesperrt = papierkorb
    scan(conn, library)
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM photo")}
    albums.set_favorite(conn, [ids["IMG_0000.jpg"]], True)
    conn.execute("INSERT INTO photo (id, source, name, size) VALUES ('online1', 'graph', 'cloud.jpg', 5)")
    conn.commit()

    info = trash.summary(conn, [ids["IMG_0000.jpg"], ids["IMG_0001.jpg"], "online1"])
    assert (info["count"], info["favorites"], info["not_local"]) == (2, 1, 1)  # Graph-Bilder nie löschen

    gesperrt.add("IMG_0001.jpg")  # z. B. in einem anderen Programm geöffnet
    result = trash.delete(conn, [ids["IMG_0000.jpg"], ids["IMG_0001.jpg"], "online1"])
    assert result == {"deleted": 1, "failed": ["IMG_0001.jpg"], "failed_count": 1}
    assert sorted(erhalten) == ["IMG_0000.jpg", "IMG_0001.jpg"]
    photos = rows(conn)
    assert photos["IMG_0000.jpg"]["deleted"] == 1 and photos["IMG_0001.jpg"]["deleted"] == 0
    assert photos["cloud.jpg"]["deleted"] == 0
    assert (library / "IMG_0001.jpg").exists() and (library / "IMG_0002.jpg").exists()


def test_restored_from_recycle_bin_comes_back_with_album(conn, library, papierkorb):
    scan(conn, library)
    photo_id = rows(conn)["IMG_0002.jpg"]["id"]
    album = albums.create_album(conn, "Urlaub")
    albums.add_photos(conn, album["id"], [photo_id])
    kopie = (library / "IMG_0002.jpg").read_bytes()
    trash.delete(conn, [photo_id])
    assert albums.list_albums(conn)[0]["n"] == 0  # gelöscht → nicht mehr im Album sichtbar
    (library / "IMG_0002.jpg").write_bytes(kopie)  # "Wiederherstellen" (neue Datei am selben Ort)
    scan(conn, library)
    assert albums.list_albums(conn)[0]["n"] == 1


def test_api_requires_ids_and_matching_confirmation(tmp_path, library, papierkorb):
    state = State(tmp_path / "api.db")
    conn = state.connect()
    scan(conn, library)
    ids = [r["id"] for r in conn.execute("SELECT id FROM photo ORDER BY name")]
    conn.close()
    client = client_for(create_app(state))

    preview = client.post("/api/delete/preview", json={"ids": ids[:2]}, headers=HDR).json()
    assert preview["count"] == 2 and len(preview["examples"]) == 2
    # ohne Sicherheits-Header, mit Filter statt IDs oder mit falscher Anzahl: abgelehnt
    assert client.post("/api/delete", json={"ids": ids[:2], "confirm_count": 2}).status_code == 403
    assert client.post("/api/delete", json={"filter": {}, "confirm_count": 3}, headers=HDR).status_code == 422
    assert client.post("/api/delete", json={"ids": ids[:2], "confirm_count": 3}, headers=HDR).status_code == 409
    assert client.post("/api/delete", json={"ids": [], "confirm_count": 0}, headers=HDR).status_code == 409

    assert client.post("/api/delete", json={"ids": ids[:2], "confirm_count": 2}, headers=HDR).status_code == 200
    for _ in range(50):
        if not state.delete_job.running:
            break
        time.sleep(0.05)
    assert state.delete_job.result["deleted"] == 2
    assert client.get("/api/photos").json()["total"] == 1
    assert sorted(p.name for p in library.iterdir()) == ["IMG_0002.jpg"]


def test_unchanged_file_back_from_recycle_bin_is_visible_again(conn, library, tmp_path, monkeypatch):
    """Regression: Eine unverändert zurückgeholte Datei (gleiche Größe, Zeit und Datei-ID)
    galt beim Einlesen als "unverändert" und blieb als gelöscht markiert."""
    ablage = tmp_path / "Papierkorb"
    ablage.mkdir()
    monkeypatch.setattr(winfiles, "move_to_recycle_bin", lambda paths: [p.rename(ablage / p.name) for p in paths])
    scan(conn, library)
    photo_id = rows(conn)["IMG_0001.jpg"]["id"]
    trash.delete(conn, [photo_id])
    assert rows(conn)["IMG_0001.jpg"]["deleted"] == 1
    (ablage / "IMG_0001.jpg").rename(library / "IMG_0001.jpg")  # wiederherstellen
    scan(conn, library)
    photo = rows(conn)["IMG_0001.jpg"]
    assert (photo["id"], photo["deleted"]) == (photo_id, 0)
