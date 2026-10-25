# Bilderkatalog für OneDrive

Ein lokales Programm, das deine Bilder und Videos in OneDrive über ihre **Metadaten** kennt, ohne sie zu verschieben, umzubenennen oder zu kopieren.

- **Sortieren und Filtern** nach Datum, Jahr, Monat, Ort (Land/Stadt), Ordner, Kamera und Dateiname
- **Virtuelle Ordner (Alben):** Bilder frei zusammenstellen. Ein Bild kann in mehreren Alben liegen.
- **Ereignisse:** Bilder, die zeitlich und örtlich zusammengehören, werden automatisch erkannt (z. B. ein Urlaubstag) und lassen sich als Album übernehmen.
- **ZIP-Export** einer Auswahl, eines Albums oder der aktuellen Ansicht, wahlweise flach, nach Jahr/Monat oder wie in OneDrive gegliedert
- **Prüflisten:** Bilder ohne Aufnahmedatum, ohne GPS, nur Videos

> **In OneDrive wird nichts verändert.** Das Programm fordert nur die Berechtigung `Files.Read` an (reine Leserechte), und der Graph-Client im Code kennt ausschließlich `GET`-Anfragen. Alben, Gruppen und Sortierungen existieren nur in einer lokalen SQLite-Datenbank.

*English summary: A local, read-only photo catalogue for OneDrive. It indexes metadata (date, GPS, camera) via Microsoft Graph, resolves places offline, and lets you filter, group into virtual albums and export ZIPs. Nothing in OneDrive is ever modified. UI language: German.*

## So funktioniert es

```
Browser (localhost) ─► Python-Server (FastAPI) ─► SQLite-Datenbank (lokal)
                              │
                              ├─► Microsoft Graph API (nur lesend: Metadaten, Vorschaubilder, Download für ZIP)
                              └─► Offline-Ortsauflösung (GeoNames-Datensatz, keine Online-Abfrage)
```

- Metadaten (Aufnahmedatum, Kamera, GPS) liest OneDrive selbst aus den Dateien aus, das Programm fragt sie über die Graph API ab. Dateien werden dafür **nicht heruntergeladen**, auch nicht solche, die „nur online“ verfügbar sind.
- Fehlt ein EXIF-Datum, wird es aus dem Dateinamen gelesen (z. B. `IMG-20190312-WA0001.jpg`) und erst danach aus dem Datei-Zeitstempel. Die Quelle wird jeweils angezeigt.
- GPS-Koordinaten werden **offline** in Stadt, Region und Land umgewandelt (`reverse_geocoder`).
- Spätere Aktualisierungen holen per Delta-Abfrage nur noch die Änderungen.

## Voraussetzungen

- Python 3.10 oder neuer
- Ein privates Microsoft-Konto mit OneDrive
- Eine eigene, kostenlose App-Registrierung bei Microsoft (einmalig, siehe unten)

## Installation

```bash
git clone <URL dieses Repositorys>
cd <Repository-Ordner>
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install .
```

## Einrichtung: eigene App-Registrierung (einmalig, ca. 5 Minuten)

Microsoft verlangt für den Zugriff auf OneDrive eine registrierte Anwendung. Jede Person nutzt dafür **ihre eigene** Registrierung, in diesem Repository steht keine Client-ID.

1. Öffne <https://entra.microsoft.com> (oder das Azure-Portal) und melde dich mit deinem Microsoft-Konto an. Falls Microsoft dazu auffordert, lege das kostenlose Verzeichnis bzw. Azure-Konto an.
2. **Anwendungen → App-Registrierungen → Neue Registrierung**
   - Name: beliebig, z. B. `Bilderkatalog`
   - Unterstützte Kontotypen: **Nur persönliche Microsoft-Konten**
   - Umleitungs-URI: Plattform **Öffentlicher Client/nativ (mobil und Desktop)**, Wert `http://localhost`
   - Auf **Registrieren** klicken.
3. Unter **Authentifizierung** den Punkt **Öffentliche Clientflows zulassen** auf **Ja** stellen und speichern.
4. Unter **API-Berechtigungen → Berechtigung hinzufügen → Microsoft Graph → Delegierte Berechtigungen** die Berechtigung **`Files.Read`** auswählen. Schreibberechtigungen wie `Files.ReadWrite` werden nicht benötigt.
5. Auf der Übersichtsseite die **Anwendungs-ID (Client-ID)** kopieren.

Die Client-ID trägst du entweder beim ersten Start in der Weboberfläche ein oder legst sie in einer Datei `.env` ab (Vorlage: `.env.example`):

```
BILDERKATALOG_CLIENT_ID=<deine Client-ID>
```

## Start

```bash
bilderkatalog
```

Der Browser öffnet `http://127.0.0.1:8765/`. Danach:

1. **Mit Microsoft anmelden.** Es öffnet sich das Microsoft-Anmeldefenster. Du bestätigst dort die Leseberechtigung.
2. **Ordner wählen:** einzelne OneDrive-Ordner oder „Ganzes OneDrive“
3. **Aktualisieren:** Die Metadaten werden eingelesen. Bei vielen tausend Bildern dauert der erste Lauf einige Minuten, spätere Läufe gehen schnell.

Weitere Befehle:

| Befehl | Wirkung |
|---|---|
| `bilderkatalog serve --port 9000 --no-browser` | Server auf anderem Port, ohne Browser |
| `bilderkatalog login` | Anmeldung über die Kommandozeile |
| `bilderkatalog sync` | Gewählte Ordner einlesen, ohne Oberfläche |
| `bilderkatalog info` | Zeigt das Datenverzeichnis |

### Bedienung

- **Klick auf ein Bild** öffnet die Großansicht mit allen Metadaten und einem Link „In OneDrive öffnen“. Mit den Pfeiltasten blätterst du weiter.
- **Klick auf den Kreis** oben links wählt ein Bild aus. Mit **Shift-Klick** wählst du einen ganzen Bereich. Sobald etwas ausgewählt ist, wählt jeder Klick aus.
- Über die Auswahlleiste geht es **Zu Album …** oder **Als ZIP …**.
- **Ansicht → Album / Ansicht → ZIP** übernimmt alle Bilder des aktuellen Filters, etwa „Juli 2023“ oder „Paris“.
- In einem Album entfernt **Aus Album entfernen** die Bilder nur aus dem Album.

## Datenschutz

- Alle persönlichen Daten liegen **nur lokal** in deinem Benutzer-Datenverzeichnis, nicht im Programmordner:
  - Windows: `%LOCALAPPDATA%\bilderkatalog\`
  - macOS: `~/Library/Application Support/bilderkatalog/`
  - Linux: `~/.local/share/bilderkatalog/`

  Dort liegen die Datenbank (`katalog.db`), der Anmelde-Token (`token_cache.bin`), die Einstellungen (`settings.json`) und die Vorschaubilder (`thumbs/`). Mit der Umgebungsvariable `BILDERKATALOG_DATA_DIR` lässt sich der Ort ändern.
- Der Server lauscht nur auf `127.0.0.1` und lehnt Anfragen von fremden Webseiten ab.
- GPS-Koordinaten werden nicht an externe Dienste geschickt.
- Zum Zurücksetzen löschst du einfach das Datenverzeichnis. Die Berechtigung der App widerrufst du unter <https://account.live.com/consent/Manage>.

## Entwicklung

```bash
pip install -e ".[dev]"
pytest
```

Die Tests verwenden ausschließlich erfundene Graph-Antworten (`tests/fixtures/`). Ein Test stellt sicher, dass der Code keine schreibenden HTTP-Methoden gegen Graph verwendet.

## Lizenz

[MIT](LICENSE)
