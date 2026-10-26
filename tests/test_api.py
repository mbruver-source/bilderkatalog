from bilderkatalog import config
from bilderkatalog.api import State, create_app

from .conftest import client_for

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
    return client_for(create_app(state))


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


def test_start_does_not_wait_for_write_lock(tmp_path):
    """Regression: Hielt ein Hintergrund-Nachladen eine Schreibsperre, brach jeder Neustart
    nach 30 s mit "database is locked" ab. Ein aktueller Katalog wird beim Start nur gelesen."""
    import time

    from bilderkatalog import db

    State(tmp_path / "lock.db")
    blocker = db.connect(tmp_path / "lock.db")
    blocker.execute("BEGIN IMMEDIATE")  # wie ein Schreibvorgang eines anderen Prozesses
    try:
        conn = db.connect(tmp_path / "lock.db")
        conn.execute("PRAGMA busy_timeout = 500")
        started = time.monotonic()
        db.init(conn)
        assert time.monotonic() - started < 0.5
        conn.close()
    finally:
        blocker.rollback()
        blocker.close()


def test_shutdown_cancels_running_jobs(tmp_path):
    import threading

    state = State(tmp_path / "jobs.db")
    begonnen = threading.Event()

    def lange_aufgabe(job):
        begonnen.set()
        while not job.cancelled():
            threading.Event().wait(0.02)
        raise InterruptedError("abgebrochen")

    assert state.enrich_job.start(lange_aufgabe)
    begonnen.wait(2)
    state.shutdown(timeout=5)
    assert state.enrich_job.state == "cancelled"


def test_session_token_required(tmp_path):
    """Andere Programme auf demselben PC kennen den Sitzungsschlüssel nicht und bekommen nichts."""
    from fastapi.testclient import TestClient

    app = create_app(State(tmp_path / "api.db"))
    fremd = TestClient(app)
    assert fremd.get("/api/status").status_code == 401
    assert fremd.get("/api/photos").status_code == 401
    assert fremd.get("/api/thumb/x").status_code == 401
    assert fremd.post("/api/delete", json={"ids": ["x"], "confirm_count": 1}, headers=HDR).status_code == 401
    assert fremd.get("/api/status", headers={"X-Bilderkatalog-Token": "falsch"}).status_code == 401
    assert fremd.get("/").status_code == 403
    assert fremd.get("/static/app.js").status_code == 200  # Programmcode ist öffentlich

    token = app.state.token
    assert len(token) >= 40 and create_app(State(tmp_path / "b.db")).state.token != token
    seite = fremd.get("/", params={"t": token})
    assert seite.status_code == 200 and f'content="{token}"' in seite.text
    assert seite.headers["referrer-policy"] == "no-referrer"
    assert fremd.get("/api/status", headers={"X-Bilderkatalog-Token": token}).status_code == 200
    assert fremd.get("/api/thumb/x", params={"t": token}).status_code == 404  # Vorschau per <img src>
    # Schlüssel im URL-Parameter nur für GET, ändernde Anfragen brauchen den Header
    assert fremd.post("/api/albums", params={"t": token}, json={"name": "x"}, headers=HDR).status_code == 401
