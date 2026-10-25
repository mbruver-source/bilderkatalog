"""Konfiguration: Datenverzeichnis, Client-ID und Berechtigungen.

Persönliche Daten (Datenbank, Token, Vorschaubilder) liegen ausschließlich im
Datenverzeichnis des Benutzers – nie im Programmverzeichnis.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from platformdirs import user_data_dir, user_downloads_dir

load_dotenv()

APP_NAME = "bilderkatalog"
GRAPH_URL = "https://graph.microsoft.com/v1.0"

# Releases (Versionsprüfung im Programm, Download neuer Versionen)
GITHUB_REPO = "mbruver-source/bilderkatalog"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Nur Leserechte. "offline_access" ergänzt MSAL automatisch.
# Diese Liste darf niemals eine Schreibberechtigung enthalten.
SCOPES = ["Files.Read"]

_GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def data_dir() -> Path:
    path = Path(os.environ.get("BILDERKATALOG_DATA_DIR") or user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "katalog.db"


def token_cache_path() -> Path:
    return data_dir() / "token_cache.bin"


def thumbs_dir() -> Path:
    path = data_dir() / "thumbs"
    path.mkdir(exist_ok=True)
    return path


def default_export_dir() -> Path:
    return Path(user_downloads_dir())


def authority() -> str:
    return os.environ.get("BILDERKATALOG_AUTHORITY", "https://login.microsoftonline.com/consumers")


def _settings_file() -> Path:
    return data_dir() / "settings.json"


def _load_settings() -> dict:
    try:
        return json.loads(_settings_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def client_id() -> str | None:
    """Client-ID aus der Umgebung (.env) oder aus den gespeicherten Einstellungen."""
    value = os.environ.get("BILDERKATALOG_CLIENT_ID") or _load_settings().get("client_id")
    return value.strip() if value and value.strip() else None


def is_valid_client_id(value: str) -> bool:
    return bool(_GUID.match(value.strip()))


def save_client_id(value: str) -> None:
    if not is_valid_client_id(value):
        raise ValueError("Die Client-ID muss eine GUID sein (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).")
    settings = _load_settings()
    settings["client_id"] = value.strip()
    _settings_file().write_text(json.dumps(settings, indent=2), encoding="utf-8")
