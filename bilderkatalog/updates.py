"""Versionsprüfung: gibt es auf GitHub eine neuere veröffentlichte Version?

Wird nur auf Knopfdruck im Versionsfenster ausgeführt (eine einzige GET-Anfrage an die
GitHub-API, ohne persönliche Daten). Jeder Fehler wird zu einem verständlichen Text,
das Programm stürzt an dieser Stelle nie ab.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

from . import __version__, config


def version_tupel(text: str) -> tuple[int, int, int] | None:
    """'1.2.3' bzw. 'v1.2.3' → (1, 2, 3); None bei anderem Format."""
    teile = text.strip().lstrip("vV").split(".")
    if len(teile) != 3:
        return None
    try:
        a, b, c = (int(t) for t in teile)
    except ValueError:
        return None
    return (a, b, c)


def neueste_version() -> tuple[str | None, str | None]:
    """(Versionsnummer, None) bei Erfolg, sonst (None, Fehlertext)."""
    anfrage = urllib.request.Request(config.LATEST_RELEASE_API_URL, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=5) as antwort:
            daten = json.loads(antwort.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "Keine öffentliche Version gefunden (noch kein Release oder das Repository ist privat)."
        return None, f"GitHub antwortete mit Fehler {exc.code}."
    except (urllib.error.URLError, OSError) as exc:
        if isinstance(exc, ssl.SSLError) or isinstance(getattr(exc, "reason", None), ssl.SSLError):
            return None, "Die sichere Verbindung zu GitHub ließ sich nicht prüfen (Zertifikatsfehler)."
        return None, "Keine Verbindung zu GitHub möglich (kein Internetzugang?)."
    except ValueError:
        return None, "Die Antwort von GitHub konnte nicht gelesen werden."
    tag = str(daten.get("tag_name", "")).strip() if isinstance(daten, dict) else ""
    if not tag:
        return None, "GitHub hat keine Versionsnummer geliefert."
    return tag.lstrip("vV"), None


def pruefen() -> dict:
    neueste, fehler = neueste_version()
    aktuell = version_tupel(__version__)
    neuer = bool(neueste and aktuell and (version_tupel(neueste) or (0, 0, 0)) > aktuell)
    return {"current": __version__, "latest": neueste, "newer": neuer, "error": fehler, "url": config.RELEASES_URL}
