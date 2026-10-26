# Mitwirken am Bilderkatalog

Danke, dass du das Programm verbessern möchtest! Die größte Hilfe sind gute Rückmeldungen
aus der Praxis: Was funktioniert nicht, was fehlt, was ist umständlich?

## So kannst du beitragen: über Issues

Alle Beiträge laufen über [Issues](https://github.com/mbruver-source/bilderkatalog/issues/new/choose).
Dort gibt es zwei Vorlagen:

- **„Fehler melden“** – etwas funktioniert nicht wie erwartet.
- **„Idee / Wunsch“** – ein Vorschlag für eine neue Funktion oder eine Verbesserung.

Damit eine Meldung schnell bearbeitet werden kann:

1. Vorher kurz ins [Benutzerhandbuch](../docs/HANDBUCH.md) schauen – vielleicht ist das
   Verhalten dort schon erklärt (Kapitel „Fragen und Probleme“).
2. Die **Programmversion** angeben (steht oben rechts im Programm, z. B. `v1.0.0`).
3. Die **Schritte** beschreiben, die zum Problem führen, und was du stattdessen erwartet
   hättest. Die Protokolldatei `bilderkatalog.log` aus `%LOCALAPPDATA%\bilderkatalog` hilft oft.
4. **Keine privaten Fotos** anhängen und in Screenshots keine persönlichen Ordner- oder
   Dateinamen zeigen. Zum Nachstellen eignen sich die Demo-Bilder (`tools/demo_bilder.py`).

Bitte auf Deutsch schreiben.

## Pull Requests

Der Code wird vom Maintainer selbst gepflegt. **Pull Requests von Dritten werden nicht
übernommen** – auch nicht für kleine Korrekturen. Unaufgefordert geöffnete Pull Requests
werden mit der Bitte geschlossen, das Anliegen als Issue zu melden. So bleibt der Code aus
einer Hand, und jede Änderung wird zusammen mit den Tests sauber nachgezogen.

## Sicherheitslücken

Sicherheitslücken bitte **nicht** als öffentliches Issue melden, sondern wie in
[SECURITY.md](SECURITY.md) beschrieben.

## Selbst am Code ausprobieren

Das Programm steht unter der [MIT-Lizenz](../LICENSE). Du darfst es jederzeit forken,
anpassen und weitergeben. Zum Einstieg:

- Installation aus dem Quellcode und Tests: [README](../README.md#installation-für-entwickler-alle-systeme)
- Installer bauen und Release: [README_INSTALLER.md](../README_INSTALLER.md)

```
pip install -e ".[dev]"
python -m pytest
python app.py --selbsttest
```

Bitte beachte dabei die [Verhaltensregeln](CODE_OF_CONDUCT.md).
