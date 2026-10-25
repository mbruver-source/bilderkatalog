from fastapi.testclient import TestClient

from bilderkatalog import config
from bilderkatalog.api import State, create_app

HDR = {"X-Bilderkatalog": "1"}


def make_client(tmp_path):
    state = State(tmp_path / "api.db")
    conn = state.connect()
    conn.executemany(
        "INSERT INTO photo (id, name, parent_path, taken_at, year, month) VALUES (?, ?, ?, ?, ?, ?)",
        [("A", "a.jpg", "/X", "2021-01-01T10:00:00", 2021, 1), ("B", "b.jpg", "/X", "2022-01-01T10:00:00", 2022, 1)],
    )
    conn.commit()
    conn.close()
    return TestClient(create_app(state))


def test_status_without_setup(tmp_path):
    client = make_client(tmp_path)
    status = client.get("/api/status").json()
    assert status["online_configured"] is False and status["photos"] == 2


def test_setup_validates_and_stores_outside_repo(tmp_path):
    client = make_client(tmp_path)
    assert client.post("/api/setup", json={"client_id": "kein-guid"}, headers=HDR).status_code == 400
    guid = "00000000-0000-0000-0000-000000000000"
    assert client.post("/api/setup", json={"client_id": guid}, headers=HDR).status_code == 200
    assert config.client_id() == guid
    assert (config.data_dir() / "settings.json").exists()


def test_post_without_header_is_rejected(tmp_path):
    client = make_client(tmp_path)
    assert client.post("/api/albums", json={"name": "x"}).status_code == 403


def test_foreign_host_is_rejected(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 403


def test_albums_and_photos_flow(tmp_path):
    client = make_client(tmp_path)
    album = client.post("/api/albums", json={"name": "Winter"}, headers=HDR).json()
    added = client.post(f"/api/albums/{album['id']}/photos", json={"filter": {"year": 2022}}, headers=HDR).json()
    assert added == {"added": 1}
    photos = client.get(f"/api/photos?album={album['id']}").json()
    assert [p["id"] for p in photos["items"]] == ["B"]
    assert client.get("/api/photos?year=2021").json()["total"] == 1
    assert client.get("/api/photos/A").json()["albums"] == []
    assert client.get("/").status_code == 200


def test_static_files_are_versioned_and_revalidated(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/").text
    assert '/static/app.js?v=' in html and '/static/style.css?v=' in html
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
