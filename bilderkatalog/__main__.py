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

    sub.add_parser("login", help="Am Microsoft-Konto anmelden")
    sub.add_parser("sync", help="Alle gewählten Ordner neu einlesen")
    sub.add_parser("info", help="Datenverzeichnis und Status anzeigen")

    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "info":
        print(f"Datenverzeichnis: {config.data_dir()}")
        print(f"Client-ID eingerichtet: {'ja' if config.client_id() else 'nein'}")
        return 0

    if command == "serve":
        return _serve(getattr(args, "port", 8765), getattr(args, "no_browser", False))

    from .auth import Auth

    if not config.client_id():
        print("Keine Client-ID eingerichtet. Siehe README, Abschnitt 'Einrichtung'.", file=sys.stderr)
        return 1
    auth = Auth(config.client_id())

    if command == "login":
        print(f"Angemeldet als {auth.login_interactive()}")
        return 0

    if command == "sync":
        from .graph_client import GraphClient

        conn = db.connect()
        db.init(conn)
        if not conn.execute("SELECT 1 FROM scope").fetchone():
            print("Noch keine Ordner gewählt – bitte in der Weboberfläche auswählen.", file=sys.stderr)
            return 1
        client = GraphClient(auth.token)
        count = sync.sync_all(conn, client, lambda _id, n: print(f"\r{n} Medien …", end="", flush=True))
        print(f"\n{count} Bilder/Videos verarbeitet, {geocode.geocode_pending(conn)} verortet.")
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
