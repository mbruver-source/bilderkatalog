"""HTTP-API und Auslieferung der Weboberfläche (nur für localhost gedacht)."""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import albums, config, db, export, geocode, queries, sync
from .auth import Auth, AuthError
from .graph_client import GraphClient, GraphError, NotLoggedIn
from .jobs import Job

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
CSRF_HEADER = "x-bilderkatalog"


class State:
    """Gemeinsamer Zustand: Anmeldung, Graph-Client und Hintergrundaufgaben."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.db_path()
        self._lock = threading.Lock()
        self._auth: Auth | None = None
        self._client: GraphClient | None = None
        self.sync_job = Job("sync")
        self.export_job = Job("export")
        self.login_job = Job("login")
        conn = db.connect(self.db_path)
        try:
            db.init(conn)
        finally:
            conn.close()

    @property
    def auth(self) -> Auth | None:
        with self._lock:
            if self._auth is None and config.client_id():
                self._auth = Auth(config.client_id())
            return self._auth

    def reset_auth(self) -> None:
        with self._lock:
            self._auth, self._client = None, None

    @property
    def client(self) -> GraphClient:
        auth = self.auth
        if auth is None:
            raise HTTPException(409, "Keine Client-ID eingerichtet.")
        with self._lock:
            if self._client is None:
                self._client = GraphClient(auth.token)
            return self._client

    def connect(self) -> sqlite3.Connection:
        return db.connect(self.db_path)


# -- Eingabemodelle -----------------------------------------------------------

class SetupIn(BaseModel):
    client_id: str


class ScopeIn(BaseModel):
    item_id: str


class AlbumIn(BaseModel):
    name: str | None = None
    description: str | None = None


class SelectionIn(BaseModel):
    ids: list[str] | None = None
    filter: dict | None = None  # alternativ: alle Bilder der aktuellen Ansicht


class ExportIn(SelectionIn):
    name: str = "Export"
    structure: str = "flat"
    target_dir: str | None = None


def create_app(state: State | None = None) -> FastAPI:
    state = state or State()
    app = FastAPI(title="Bilderkatalog", docs_url=None, redoc_url=None)
    app.state.bk = state

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Schutz vor DNS-Rebinding und Cross-Site-Anfragen anderer Webseiten.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS and host != "testserver":
            return JSONResponse({"detail": "Nur lokaler Zugriff erlaubt."}, status_code=403)
        if request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": "Fehlender Sicherheits-Header."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(NotLoggedIn)
    async def not_logged_in(_request, _err):
        return JSONResponse({"detail": "Nicht angemeldet."}, status_code=401)

    @app.exception_handler(GraphError)
    async def graph_error(_request, err: GraphError):
        return JSONResponse({"detail": str(err)}, status_code=502)

    @app.exception_handler(ValueError)
    async def value_error(_request, err: ValueError):
        return JSONResponse({"detail": str(err)}, status_code=400)

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = state.connect()
        try:
            yield conn
        finally:
            conn.close()

    Conn = Depends(get_conn)

    # -- Status, Einrichtung, Anmeldung -------------------------------------

    @app.get("/api/status")
    def status(conn=Conn):
        auth = state.auth
        return {
            "configured": auth is not None,
            "account": auth.account_name() if auth else None,
            "photos": conn.execute("SELECT COUNT(*) FROM photo WHERE deleted = 0").fetchone()[0],
            "scopes": [dict(r) for r in conn.execute("SELECT item_id, path, last_sync FROM scope ORDER BY path")],
            "jobs": {j.kind: j.to_dict() for j in (state.sync_job, state.export_job, state.login_job)},
            "export_dir": str(config.default_export_dir()),
        }

    @app.post("/api/setup")
    def setup(body: SetupIn):
        config.save_client_id(body.client_id)
        state.reset_auth()
        return {"ok": True}

    @app.post("/api/login")
    def login():
        auth = state.auth
        if auth is None:
            raise HTTPException(409, "Keine Client-ID eingerichtet.")

        def run(job: Job):
            job.update("Anmeldefenster im Browser geöffnet …")
            try:
                return auth.login_interactive()
            except AuthError as err:
                raise RuntimeError(str(err)) from err

        state.login_job.start(run)
        return state.login_job.to_dict()

    @app.post("/api/logout")
    def logout():
        if state.auth:
            state.auth.logout()
        return {"ok": True}

    # -- Ordnerauswahl und Indexierung --------------------------------------

    @app.get("/api/folders")
    def folders(parent: str = "root"):
        items = state.client.child_folders(parent)
        return [
            {"id": f["id"], "name": f["name"], "children": f.get("folder", {}).get("childCount", 0)}
            for f in sorted(items, key=lambda f: f["name"].lower())
        ]

    @app.post("/api/scopes")
    def add_scope(body: ScopeIn, conn=Conn):
        return sync.add_scope(conn, state.client, body.item_id)

    @app.delete("/api/scopes/{item_id}")
    def remove_scope(item_id: str, conn=Conn):
        if state.sync_job.running:
            raise HTTPException(409, "Während der Indexierung nicht möglich.")
        sync.remove_scope(conn, item_id)
        return {"ok": True}

    @app.post("/api/sync")
    def start_sync():
        client = state.client

        def run(job: Job):
            conn = state.connect()
            try:
                scopes = conn.execute("SELECT item_id, path, delta_link FROM scope").fetchall()
                if not scopes:
                    raise ValueError("Bitte zuerst Ordner auswählen.")
                total = 0
                for scope in scopes:
                    if job.cancelled():
                        raise InterruptedError("Indexierung abgebrochen")
                    path = scope["path"]

                    def progress(_item_id, count, path=path, base=total):
                        job.update(f"Lese {path} …", done=base + count)
                        if job.cancelled():
                            raise InterruptedError("Indexierung abgebrochen")

                    total += sync.sync_scope(conn, client, scope["item_id"], scope["delta_link"], progress)
                job.update("Orte werden bestimmt …")
                geocode.geocode_pending(conn)
                job.update(f"Fertig: {total} Bilder/Videos verarbeitet.", done=total)
                return {"processed": total}
            finally:
                conn.close()

        if not state.sync_job.start(run):
            raise HTTPException(409, "Indexierung läuft bereits.")
        return state.sync_job.to_dict()

    @app.post("/api/jobs/{kind}/cancel")
    def cancel_job(kind: str):
        job = {"sync": state.sync_job, "export": state.export_job}.get(kind)
        if job is None:
            raise HTTPException(404)
        job.cancel()
        return job.to_dict()

    # -- Katalog --------------------------------------------------------------

    @app.get("/api/photos")
    def photos(request: Request, sort: str = "date_desc", limit: int = 200, offset: int = 0, conn=Conn):
        flt = queries.Filter.from_dict(_filter_params(request))
        return queries.list_photos(conn, flt, sort, min(limit, 1000), max(offset, 0))

    @app.get("/api/photos/{photo_id}")
    def photo(photo_id: str, conn=Conn):
        result = queries.get_photo(conn, photo_id)
        if not result:
            raise HTTPException(404)
        return result

    @app.get("/api/facets")
    def facets(conn=Conn):
        return queries.facets(conn)

    @app.get("/api/events")
    def events(request: Request, gap_hours: float = 6, min_size: int = 5, conn=Conn):
        flt = queries.Filter.from_dict(_filter_params(request))
        return queries.events(conn, flt, gap_hours, min_size)

    @app.get("/api/thumb/{photo_id}")
    def thumb(photo_id: str, size: str = "medium", conn=Conn):
        if size not in ("small", "medium", "large"):
            raise HTTPException(400)
        if not conn.execute("SELECT 1 FROM photo WHERE id = ?", (photo_id,)).fetchone():
            raise HTTPException(404)
        cache = config.thumbs_dir() / f"{re.sub(r'[^A-Za-z0-9!_-]', '_', photo_id)}_{size}.jpg"
        if not cache.exists():
            try:
                data = state.client.thumbnail(photo_id, size)
            except GraphError as err:
                if err.status == 404:  # z. B. Formate ohne Vorschau
                    return Response(status_code=404)
                raise
            tmp = cache.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            tmp.replace(cache)
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})

    # -- Alben ----------------------------------------------------------------

    @app.get("/api/albums")
    def list_albums(conn=Conn):
        return albums.list_albums(conn)

    @app.post("/api/albums")
    def create_album(body: AlbumIn, conn=Conn):
        return albums.create_album(conn, body.name or "", body.description or "")

    @app.patch("/api/albums/{album_id}")
    def update_album(album_id: int, body: AlbumIn, conn=Conn):
        result = albums.update_album(conn, album_id, body.name, body.description)
        if not result:
            raise HTTPException(404)
        return result

    @app.delete("/api/albums/{album_id}")
    def delete_album(album_id: int, conn=Conn):
        albums.delete_album(conn, album_id)
        return {"ok": True}

    @app.post("/api/albums/{album_id}/photos")
    def add_to_album(album_id: int, body: SelectionIn, conn=Conn):
        _require_album(conn, album_id)
        return {"added": albums.add_photos(conn, album_id, _selection(conn, body))}

    @app.post("/api/albums/{album_id}/photos/remove")
    def remove_from_album(album_id: int, body: SelectionIn, conn=Conn):
        _require_album(conn, album_id)
        return {"removed": albums.remove_photos(conn, album_id, _selection(conn, body))}

    # -- Export -----------------------------------------------------------------

    @app.post("/api/export")
    def start_export(body: ExportIn, conn=Conn):
        ids = _selection(conn, body)
        if not ids:
            raise HTTPException(400, "Keine Bilder ausgewählt.")
        if body.structure not in export.STRUCTURES:
            raise HTTPException(400, "Unbekannte Struktur.")
        target = Path(body.target_dir).expanduser() if body.target_dir else config.default_export_dir()
        client = state.client

        def run(job: Job):
            job.update(f"Exportiere {len(ids)} Dateien …", 0, len(ids))
            export_conn = state.connect()
            try:
                result = export.export_zip(
                    export_conn, client, ids, target, body.name, body.structure,
                    progress=lambda i, n, name: job.update(f"Lade {name}", i, n),
                    cancelled=job.cancelled,
                )
            finally:
                export_conn.close()
            job.update(f"Gespeichert: {result['path']}")
            return result

        if not state.export_job.start(run):
            raise HTTPException(409, "Ein Export läuft bereits.")
        return state.export_job.to_dict()

    # -- Oberfläche -------------------------------------------------------------

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _filter_params(request: Request) -> dict:
    params = dict(request.query_params)
    for key in ("no_date", "no_gps"):
        if key in params:
            params[key] = params[key] in ("1", "true", "on")
    return params


def _selection(conn: sqlite3.Connection, body: SelectionIn) -> list[str]:
    if body.ids is not None:
        return list(dict.fromkeys(body.ids))
    if body.filter is not None:
        return queries.photo_ids(conn, queries.Filter.from_dict(body.filter))
    raise HTTPException(400, "Entweder 'ids' oder 'filter' angeben.")


def _require_album(conn: sqlite3.Connection, album_id: int) -> None:
    if not albums.get_album(conn, album_id):
        raise HTTPException(404, "Album nicht gefunden.")
