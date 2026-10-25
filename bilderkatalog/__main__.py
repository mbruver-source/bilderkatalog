"""Einstiegspunkt: `bilderkatalog` bzw. `python -m bilderkatalog`."""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from . import __version__, config, db, geocode, sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bilderkatalog", description="Nur lesender Bilderkatalog für OneDrive")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Weboberfläche starten (Standard)")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch öffnen")

    sub.add_parser("login", help="Optional: am Microsoft-Konto anmelden (Online-Zugang)")
    sub.add_parser("sync", help="Alle gewählten Ordner einlesen")
    sub.add_parser("enrich", help="Details der Nur-online-Bilder nachladen (Speicher wird wieder freigegeben)")
    sub.add_parser("info", help="Datenverzeichnis und Status anzeigen")

    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "info":
        print(f"Datenverzeichnis: {config.data_dir()}")
        print(f"Online-Zugang eingerichtet: {'ja' if config.client_id() else 'nein (optional)'}")
        return 0

    if command == "serve":
        return _serve(getattr(args, "port", 8765), getattr(args, "no_browser", False))

    if command == "login":
        from .auth import Auth

        if not config.client_id():
            print("Keine Client-ID eingerichtet. Siehe README, Abschnitt 'Optional: Online-Zugang'.", file=sys.stderr)
            return 1
        print(f"Angemeldet als {Auth(config.client_id()).login_interactive()}")
        return 0

    from . import local_source

    conn = db.connect()
    db.init(conn)
    show = lambda text, done, total: print(f"\r{text} {done}/{total or '?'}", end="", flush=True)  # noqa: E731

    if command == "enrich":
        result = local_source.enrich(conn, progress=show)
        print(f"\nDetails für {result['processed']} Bilder geladen, {geocode.geocode_pending(conn)} verortet.")
        for error in result["errors"]:
            print("  Fehler:", error)
        return 0

    if command == "sync":
        scopes = conn.execute("SELECT item_id, kind FROM scope").fetchall()
        if not scopes:
            print("Noch keine Ordner gewählt – bitte in der Weboberfläche auswählen.", file=sys.stderr)
            return 1
        for scope in scopes:
            if scope["kind"] == "local":
                local_source.scan_scope(conn, scope["item_id"], progress=show)
        if any(s["kind"] == "graph" for s in scopes) and config.client_id():
            from .auth import Auth
            from .graph_client import GraphClient

            client = GraphClient(Auth(config.client_id()).token)
            sync.sync_all(conn, client, lambda _id, n: print(f"\r{n} Medien online …", end="", flush=True))
        print(f"\nFertig, {geocode.geocode_pending(conn)} Bilder verortet.")
        return 0
    return 0


def _serve(port: int, no_browser: bool) -> int:
    import uvicorn

    from .api import create_app

    url = f"http://127.0.0.1:{port}/"
    print(f"Bilderkatalog läuft auf {url}  (Beenden mit Strg+C)")
    print(f"Daten liegen in {config.data_dir()}")
    if not no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
