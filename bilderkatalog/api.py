"""HTTP-API und Auslieferung der Weboberfläche (nur für localhost gedacht)."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    __version__, albums, config, dates, db, duplicates, export, geocode, local_source, places, queries, sicherung,
    sync, updates, winfiles,
)
from .auth import Auth, AuthError
from .graph_client import GraphClient, GraphError, NotLoggedIn
from .jobs import Job

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
CSRF_HEADER = "x-bilderkatalog"


class State:
    """Gemeinsamer Zustand: optionale Anmeldung, Graph-Client und Hintergrundaufgaben."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.db_path()
        self._lock = threading.Lock()
        self._auth: Auth | None = None
        self._client: GraphClient | None = None
        self.sync_job = Job("sync")
        self.enrich_job = Job("enrich")
        self.export_job = Job("export")
        self.login_job = Job("login")
        conn = db.connect(self.db_path)
        try:
            vorher = conn.execute("PRAGMA user_version").fetchone()[0]
            if db.init(conn):  # neu angelegt oder migriert
                duplicates.refresh(conn)
                if 0 < vorher < 5:
                    self._ortsnamen_umstellen(conn)
        finally:
            conn.close()

    def _ortsnamen_umstellen(self, conn: sqlite3.Connection) -> None:
        """Ältere Kataloge: Orte auf Deutsch umstellen. Läuft im Hintergrund mit Fortschrittsanzeige."""
        if not places.eindeutschen_katalog(conn):
            return

        def run(job: Job):
            job.update("Ortsnamen werden auf Deutsch umgestellt (einmalig) …")
            umstellung = self.connect()
            try:
                geocode.geocode_pending(umstellung)
            finally:
                umstellung.close()
            job.update("Ortsnamen sind jetzt deutsch.")

        self.sync_job.start(run)

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
        """Graph-Client; nur für den optionalen Online-Zugang."""
        auth = self.auth
        if auth is None:
            raise HTTPException(409, "Online-Zugang ist nicht eingerichtet.")
        with self._lock:
            if self._client is None:
                self._client = GraphClient(auth.token)
            return self._client

    def optional_client(self) -> GraphClient | None:
        return self.client if self.auth and self.auth.account_name() else None

    def connect(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    def shutdown(self, timeout: float = 15.0) -> None:
        """Beim Schließen des Fensters: laufende Aufgaben abbrechen und kurz auf sie warten,
        damit angefangene Downloads noch wieder freigegeben werden."""
        jobs = (self.sync_job, self.enrich_job, self.export_job)
        for job in jobs:
            job.cancel()
        deadline = time.monotonic() + timeout
        while any(job.running for job in jobs) and time.monotonic() < deadline:
            time.sleep(0.1)

    def busy(self) -> str | None:
        for job in (self.sync_job, self.enrich_job):
            if job.running:
                return job.kind
        return None


# -- Eingabemodelle -----------------------------------------------------------

class SetupIn(BaseModel):
    client_id: str


class ScopeIn(BaseModel):
    kind: str = "local"         # 'local' | 'graph'
    path: str | None = None     # lokal: Ordnerpfad
    item_id: str | None = None  # graph: OneDrive-Item-ID oder "root"


class AlbumIn(BaseModel):
    name: str | None = None
    description: str | None = None


class SelectionIn(BaseModel):
    ids: list[str] | None = None
    filter: dict | None = None  # alternativ: alle Bilder der aktuellen Ansicht


class FavoriteIn(SelectionIn):
    value: bool = True


class DateIn(SelectionIn):
    date: str = ""


class PlaceIn(SelectionIn):
    place: dict | None = None    # lat, lon, city, region, country_code; None = Ort entfernen


class BackupIn(BaseModel):
    target_dir: str | None = None


class RestoreIn(BaseModel):
    data: dict | None = None
    auto: str | None = None


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
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            # Nach Updates nie veralteten Code verwenden (Browser fragt per ETag nach → meist 304)
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(NotLoggedIn)
    async def not_logged_in(_request, _err):
        return JSONResponse({"detail": "Nicht bei Microsoft angemeldet."}, status_code=401)

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

    # -- Status und optionaler Online-Zugang -----------------------------------

    @app.get("/api/status")
    def status(conn=Conn):
        auth = state.auth
        partial = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM photo "
            "WHERE source = 'local' AND meta_level = 'partial' AND deleted = 0"
        ).fetchone()
        return {
            "version": __version__,
            "data_dir": str(config.data_dir()),
            "online_configured": auth is not None,
            "account": auth.account_name() if auth else None,
            "photos": conn.execute("SELECT COUNT(*) FROM photo WHERE deleted = 0").fetchone()[0],
            "partial": {"count": partial[0], "bytes": partial[1]},
            "scopes": [dict(r) for r in conn.execute(
                "SELECT item_id, kind, path, last_sync FROM scope ORDER BY kind, path")],
            "onedrive_roots": [str(p) for p in winfiles.onedrive_roots()],
            "jobs": {j.kind: j.to_dict() for j in (state.sync_job, state.enrich_job, state.export_job, state.login_job)},
            "export_dir": str(config.default_export_dir()),
        }

    @app.get("/api/update")
    def update_check():
        return updates.pruefen()

    @app.get("/api/licenses")
    def licenses():
        from . import lizenzen

        return Response(lizenzen.gebuendelt_oder_erzeugen(), media_type="text/plain; charset=utf-8")

    @app.post("/api/open-releases")
    def open_releases():
        """Release-Seite im Standardbrowser öffnen (aus dem Programmfenster heraus)."""
        import webbrowser

        webbrowser.open(config.RELEASES_URL)
        return {"ok": True}

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

    # -- Ordnerauswahl ---------------------------------------------------------

    @app.get("/api/local/folders")
    def local_folders(path: str | None = None):
        return local_source.list_subfolders(path)

    @app.get("/api/folders")
    def graph_folders(parent: str = "root"):
        items = state.client.child_folders(parent)
        return [
            {"id": f["id"], "name": f["name"], "children": f.get("folder", {}).get("childCount", 0)}
            for f in sorted(items, key=lambda f: f["name"].lower())
        ]

    @app.post("/api/scopes")
    def add_scope(body: ScopeIn, conn=Conn):
        if state.busy():
            raise HTTPException(409, "Bitte warten, bis die laufende Aufgabe beendet ist.")
        if body.kind == "local":
            if not body.path:
                raise HTTPException(400, "Ordnerpfad fehlt.")
            return local_source.add_local_scope(conn, body.path)
        if body.kind == "graph":
            return sync.add_scope(conn, state.client, body.item_id or "root")
        raise HTTPException(400, "Unbekannte Art.")

    @app.delete("/api/scopes")
    def remove_scope(item_id: str, conn=Conn):
        if state.busy():
            raise HTTPException(409, "Bitte warten, bis die laufende Aufgabe beendet ist.")
        sync.remove_scope(conn, item_id)
        duplicates.refresh(conn)
        return {"ok": True}

    # -- Hintergrundaufgaben ---------------------------------------------------

    @app.post("/api/sync")
    def start_sync():
        if state.enrich_job.running:
            raise HTTPException(409, "Während des Nachladens nicht möglich.")

        def run(job: Job):
            conn = state.connect()
            try:
                scopes = conn.execute("SELECT item_id, kind, path, delta_link FROM scope").fetchall()
                if not scopes:
                    raise ValueError("Bitte zuerst Ordner auswählen.")
                total, skipped = 0, []
                for scope in scopes:
                    if job.cancelled():
                        raise InterruptedError("Einlesen abgebrochen")
                    if scope["kind"] == "local":
                        total += local_source.scan_scope(
                            conn, scope["item_id"],
                            progress=lambda text, done, n: job.update(text, done, n),
                            cancelled=job.cancelled,
                        )
                        continue
                    client = state.optional_client()
                    if client is None:
                        skipped.append(scope["path"])
                        continue
                    path = scope["path"]

                    def progress(_item_id, count, path=path):
                        job.update(f"Lese online {path} …", count, 0)
                        if job.cancelled():
                            raise InterruptedError("Einlesen abgebrochen")

                    total += sync.sync_scope(conn, client, scope["item_id"], scope["delta_link"], progress)
                job.update("Orte werden bestimmt …", 0, 0)
                geocode.geocode_pending(conn)
                job.update("Suche doppelte Dateien …", 0, 0)
                duplicates.refresh(conn)
                message = f"Fertig: {total} neue oder geänderte Dateien."
                if skipped:
                    message += f" Übersprungen (nicht angemeldet): {', '.join(skipped)}"
                job.update(message)
                return {"processed": total}
            finally:
                conn.close()

        if not state.sync_job.start(run):
            raise HTTPException(409, "Einlesen läuft bereits.")
        return state.sync_job.to_dict()

    @app.post("/api/enrich")
    def start_enrich(body: SelectionIn | None = None, conn=Conn):
        """Ohne Angabe alle Nur-online-Bilder, sonst nur Auswahl (ids) bzw. Ansicht (filter)."""
        if state.sync_job.running:
            raise HTTPException(409, "Während des Einlesens nicht möglich.")
        ids = _selection(conn, body) if body and (body.ids is not None or body.filter is not None) else None

        def run(job: Job):
            conn = state.connect()
            try:
                result = local_source.enrich(
                    conn,
                    progress=lambda text, done, n: job.update(text, done, n),
                    cancelled=job.cancelled,
                    ids=ids,
                )
                job.update("Orte werden bestimmt …")
                geocode.geocode_pending(conn)
                duplicates.refresh(conn)
                job.update(f"Fertig: Details für {result['processed']} Bilder geladen, Speicher wieder freigegeben.")
                return result
            finally:
                conn.close()

        if not state.enrich_job.start(run):
            raise HTTPException(409, "Nachladen läuft bereits.")
        return state.enrich_job.to_dict()

    @app.post("/api/jobs/{kind}/cancel")
    def cancel_job(kind: str):
        job = {"sync": state.sync_job, "enrich": state.enrich_job, "export": state.export_job}.get(kind)
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
        result["copies"] = duplicates.copies_of(conn, photo_id) if result["dup_group"] else []
        result["date_precision"] = dates.precision_of(conn, photo_id)
        result["place_manual"] = places.is_manual(conn, photo_id)
        return result

    @app.get("/api/places/search")
    def search_places(q: str = "", conn=Conn):
        return places.search(conn, q)

    @app.post("/api/places")
    def set_places(body: PlaceIn, conn=Conn):
        return {"changed": places.set_place(conn, _selection(conn, body), body.place)}

    @app.post("/api/places/reset")
    def reset_places(body: SelectionIn, conn=Conn):
        changed = places.reset(conn, _selection(conn, body))
        geocode.geocode_pending(conn)
        return {"changed": changed}

    @app.post("/api/favorites")
    def favorites(body: FavoriteIn, conn=Conn):
        ids = _selection(conn, body)
        changed = albums.set_favorite(conn, ids, body.value)
        # Ein Favorit wird bevorzugt Hauptexemplar seiner Duplikatgruppe
        if changed and (len(ids) > 500 or conn.execute(
            f"SELECT 1 FROM photo WHERE dup_group IS NOT NULL AND id IN ({','.join('?' * len(ids))}) LIMIT 1", ids
        ).fetchone()):
            duplicates.refresh(conn)
        return {"changed": changed}

    @app.post("/api/dates")
    def set_dates(body: DateIn, conn=Conn):
        return {"changed": dates.set_date(conn, _selection(conn, body), body.date)}

    @app.post("/api/dates/reset")
    def reset_dates(body: SelectionIn, conn=Conn):
        return {"changed": dates.reset(conn, _selection(conn, body))}

    @app.post("/api/duplicates/refresh")
    def refresh_duplicates(conn=Conn):
        return duplicates.refresh(conn)

    @app.post("/api/photos/{photo_id}/enrich")
    def enrich_photo(photo_id: str, conn=Conn):
        local_source.enrich_one(conn, photo_id)
        geocode.geocode_pending(conn)
        return queries.get_photo(conn, photo_id)

    @app.post("/api/photos/{photo_id}/reveal")
    def reveal(photo_id: str, conn=Conn):
        row = conn.execute("SELECT local_path FROM photo WHERE id = ?", (photo_id,)).fetchone()
        if not row or not row["local_path"] or not os.path.exists(row["local_path"]):
            raise HTTPException(404, "Datei nicht gefunden.")
        winfiles.reveal(Path(row["local_path"]))
        return {"ok": True}

    @app.get("/api/facets")
    def facets(conn=Conn):
        return queries.facets(conn)

    @app.get("/api/events")
    def events(request: Request, gap_hours: float = 6, min_size: int = 5, conn=Conn):
        flt = queries.Filter.from_dict(_filter_params(request))
        return queries.events(conn, flt, gap_hours, min_size)

    @app.get("/api/thumb/{photo_id}")
    def thumb(photo_id: str, size: str = "medium", conn=Conn):
        if size not in local_source.THUMB_PX:
            raise HTTPException(400)
        row = conn.execute("SELECT source, local_path, is_video FROM photo WHERE id = ?", (photo_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        cache = local_source.thumb_cache_file(photo_id, size)
        if cache.exists():
            return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})
        if row["source"] == "local":
            data = _local_thumb(row["local_path"], size, bool(row["is_video"]))
        else:
            try:
                data = state.client.thumbnail(photo_id, size)
            except (GraphError, HTTPException):
                data = None
        if data is None:
            return Response(status_code=404)
        if size != "large":  # große Vorschauen nicht dauerhaft speichern
            tmp = cache.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            tmp.replace(cache)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})

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

    # -- Sicherung der eigenen Einträge -----------------------------------------

    @app.get("/api/backups")
    def backups():
        return {"auto": sicherung.list_auto(), "auto_dir": str(sicherung.auto_dir())}

    @app.post("/api/backup")
    def backup(body: BackupIn, conn=Conn):
        target = Path(body.target_dir).expanduser() if body.target_dir else config.default_export_dir()
        return sicherung.write(conn, target)

    @app.post("/api/restore")
    def restore(body: RestoreIn, conn=Conn):
        if body.auto:
            data = sicherung.read_auto(body.auto)
        elif body.data is not None:
            data = body.data
        else:
            raise HTTPException(400, "Keine Sicherung angegeben.")
        result = sicherung.restore(conn, data)
        duplicates.refresh(conn)  # Favoriten/Alben beeinflussen das Hauptexemplar
        return result

    # -- Export -----------------------------------------------------------------

    @app.post("/api/export")
    def start_export(body: ExportIn, conn=Conn):
        ids = _selection(conn, body)
        if not ids:
            raise HTTPException(400, "Keine Bilder ausgewählt.")
        if body.structure not in export.STRUCTURES:
            raise HTTPException(400, "Unbekannte Struktur.")
        target = Path(body.target_dir).expanduser() if body.target_dir else config.default_export_dir()
        client = state.optional_client()

        def run(job: Job):
            job.update(f"Exportiere {len(ids)} Dateien …", 0, len(ids))
            export_conn = state.connect()
            try:
                result = export.export_zip(
                    export_conn, client, ids, target, body.name, body.structure,
                    progress=lambda i, n, name: job.update(f"Kopiere {name}", i, n),
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
        # Versionskennung an Skript/Stylesheet hängen, damit Browser-Caches sie neu laden
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "style.css"):
            version = int((STATIC / name).stat().st_mtime)
            html = html.replace(f'/static/{name}"', f'/static/{name}?v={version}"')
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _local_thumb(path: str | None, size: str, is_video: bool) -> bytes | None:
    """Vorschau einer lokalen Datei. "Nur online"-Dateien und Videos über Windows –
    OneDrive liefert deren Vorschau aus der Cloud, ohne die Datei herunterzuladen."""
    if not path:
        return None
    try:
        online = winfiles.is_online_only(os.stat(path))
    except OSError:
        return None
    data = None
    if not online and not is_video:
        data = local_source.thumbnail_bytes(path, size)  # schnell und in voller Qualität
    return data or winfiles.shell_thumbnail(Path(path), local_source.THUMB_PX[size])


def _filter_params(request: Request) -> dict:
    params = dict(request.query_params)
    for key in queries.BOOL_FILTERS:
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
