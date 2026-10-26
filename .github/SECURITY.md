# Sicherheitsrichtlinie

## Unterstützte Versionen

Sicherheitskorrekturen gibt es nur für die **jeweils aktuelle Version**
([Releases](https://github.com/mbruver-source/bilderkatalog/releases/latest)). Bitte vor einer
Meldung prüfen, ob das Problem dort noch auftritt. Im Programm: Versionsnummer oben rechts →
„Nach Updates suchen“.

## Sicherheitslücke melden

Bitte Sicherheitslücken **nicht als öffentliches Issue** melden, damit sie nicht ausgenutzt
werden können, bevor eine Korrektur verfügbar ist.

1. **Bevorzugt:** über GitHub im Reiter
   [**Security → „Report a vulnerability“**](https://github.com/mbruver-source/bilderkatalog/security/advisories/new).
   Die Meldung ist nur für dich und den Maintainer sichtbar.
2. **Alternativ:** per E-Mail an die Adresse im
   [Impressum](https://mbruver-source.github.io/bilderkatalog/impressum.html) mit dem Betreff
   „Bilderkatalog Sicherheit“.

Hilfreich sind:

- die betroffene Programmversion,
- die Schritte, mit denen sich das Problem nachvollziehen lässt,
- deine Einschätzung, was ein Angreifer damit erreichen könnte.

**Bitte keine privaten Fotos mitschicken** – die Demo-Bilder aus `tools/demo_bilder.py` reichen.

## Was danach passiert

Das Projekt wird in der Freizeit gepflegt, feste Fristen gibt es daher nicht. In der Regel:

- bekommst du **innerhalb einer Woche** eine Eingangsbestätigung,
- wird die Lücke in einer neuen Version behoben,
- wirst du auf Wunsch in den Release-Notizen als Finder genannt.

Eine Belohnung (Bug-Bounty) gibt es nicht.

## Geltungsbereich

- das Programm und der Windows-Installer,
- der eingebaute lokale Webserver (er lauscht nur auf `127.0.0.1`, prüft den Host-Header und
  verlangt für ändernde Anfragen einen eigenen Header gegen Cross-Site-Anfragen),
- der optionale Online-Zugang über Microsoft Graph (nur Leserechte, `Files.Read`).

Das Programm ist für die Nutzung **auf dem eigenen Rechner** gedacht und nicht dafür, im Netzwerk
oder aus dem Internet erreichbar zu sein.
