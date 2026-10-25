import httpx

from bilderkatalog import geocode, sync
from tests.conftest import load_fixture

SCOPE_ITEM = {
    "id": "FOLDER-A",
    "name": "Urlaub",
    "folder": {"childCount": 3},
    "parentReference": {"id": "ROOT", "path": "/drive/root:/Bilder%20Archiv"},
}


def setup_scope(conn, graph):
    graph.route("/me/drive/items/FOLDER-A", SCOPE_ITEM)
    graph.route("/me/drive/items/FOLDER-A/delta", load_fixture("delta_page1.json"))
    graph.route("/me/drive/items/FOLDER-A/delta?token=page2", load_fixture("delta_page2.json"))
    client = graph.client()
    scope = sync.add_scope(conn, client, "FOLDER-A")
    return client, scope


def test_full_sync_reads_media_with_paths(conn, graph):
    client, scope = setup_scope(conn, graph)
    assert scope["path"] == "/Bilder Archiv/Urlaub"

    count = sync.sync_all(conn, client)

    assert count == 4  # Textdatei wird ignoriert
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM photo")}
    assert set(rows) == {"IMG-1", "IMG-2", "IMG-3", "VID-1"}
    assert rows["IMG-1"]["parent_path"] == "/Bilder Archiv/Urlaub/Strand"
    assert rows["IMG-3"]["parent_path"] == "/Bilder Archiv/Urlaub"
    assert rows["IMG-1"]["taken_at"] == "2021-07-14T09:30:00"
    assert rows["IMG-1"]["date_source"] == "exif"
    assert (rows["IMG-1"]["year"], rows["IMG-1"]["month"]) == (2021, 7)
    assert rows["IMG-3"]["taken_at"] == "2019-03-12T00:00:00"
    assert rows["IMG-3"]["date_source"] == "filename"
    assert rows["VID-1"]["is_video"] == 1
    assert rows["VID-1"]["date_source"] == "file"
    link = conn.execute("SELECT delta_link FROM scope").fetchone()[0]
    assert link.endswith("token=next")


def test_incremental_sync_marks_deleted(conn, graph):
    client, _ = setup_scope(conn, graph)
    sync.sync_all(conn, client)
    graph.route("/me/drive/items/FOLDER-A/delta?token=next", {
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/drive/items/FOLDER-A/delta?token=later",
        "value": [
            {"id": "IMG-2", "name": "IMG_0002.jpg", "deleted": {}, "parentReference": {"id": "FOLDER-B"}},
            {"id": "FOLDER-B", "name": "Strand", "deleted": {}, "folder": {}, "parentReference": {"id": "FOLDER-A"}},
        ],
    })
    sync.sync_all(conn, client)
    deleted = {r[0] for r in conn.execute("SELECT id FROM photo WHERE deleted = 1")}
    assert deleted == {"IMG-1", "IMG-2"}  # IMG-1 lag im gelöschten Ordner


def test_expired_delta_link_triggers_full_resync(conn, graph):
    client, _ = setup_scope(conn, graph)
    sync.sync_all(conn, client)
    graph.route("/me/drive/items/FOLDER-A/delta?token=next",
                lambda _req: httpx.Response(410, json={"error": {"message": "resync required"}}))
    assert sync.sync_all(conn, client) == 4


def test_remove_scope_keeps_photos_of_other_scopes(conn, graph):
    client, _ = setup_scope(conn, graph)
    sync.sync_all(conn, client)
    conn.execute("INSERT INTO scope(item_id, path) VALUES ('OTHER', '/Bilder Archiv/Urlaub/Strand')")
    sync.remove_scope(conn, "FOLDER-A")
    remaining = {r[0] for r in conn.execute("SELECT id FROM photo")}
    assert remaining == {"IMG-1", "IMG-2"}


def test_geocoding_uses_lookup_only_for_coordinates(conn, graph):
    client, _ = setup_scope(conn, graph)
    sync.sync_all(conn, client)
    calls = []

    def fake_lookup(coords):
        calls.append(coords)
        return [{"name": "Paris", "admin1": "Ile-de-France", "cc": "FR"} for _ in coords]

    assert geocode.geocode_pending(conn, fake_lookup) == 2
    assert len(calls) == 1
    row = conn.execute("SELECT city, country_code FROM photo WHERE id = 'IMG-1'").fetchone()
    assert tuple(row) == ("Paris", "FR")
    assert geocode.geocode_pending(conn, fake_lookup) == 0  # nichts doppelt


def test_filename_dates():
    assert sync.date_from_filename("IMG_20190312_101500.jpg") == "2019-03-12T10:15:00"
    assert sync.date_from_filename("2020-01-05 18.30.12.png") == "2020-01-05T18:30:12"
    assert sync.date_from_filename("Screenshot_20211301.png") is None  # Monat 13
    assert sync.date_from_filename("DSC01234.JPG") is None
