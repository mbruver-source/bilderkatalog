# Bilderkatalog – Build & Release (Windows-Installer)

Diese Anleitung beschreibt, wie aus dem Quellcode die Installationsdatei
`Bilderkatalog-Setup-X.Y.Z.exe` entsteht und wie ein neues Release veröffentlicht wird.
Aufbau und Ablauf entsprechen dem SHS-Prüfungsprogramm.

## Bausteine

| Datei | Aufgabe |
|---|---|
| `version.txt` | **einzige Quelle** der Versionsnummer (z. B. `1.0.0`) |
| `bump_version.py` | erhöht die Nummer und schreibt `version_info.txt` und `bilderkatalog/version.py` |
| `bilderkatalog/version.py` | zur Laufzeit gelesen: Anzeige im Programm (Knopf `v1.0.0` oben rechts), `bilderkatalog --version`, Paketversion |
| `version_info.txt` | Windows-Versionsinfo, wird in die EXE eingebettet (Eigenschaften → Details) |
| `app.py` | Startskript der gebündelten EXE (`--selbsttest` für Prüfungen) |
| `build.spec` | PyInstaller: baut `dist\Bilderkatalog\` (Programmordner mit `Bilderkatalog.exe`) |
| `installer.iss` | Inno Setup: baut `Output\Bilderkatalog-Setup-X.Y.Z.exe` |
| `build_installer.bat` | alles in einem Schritt (lokal) |
| `.github/workflows/build-installer.yml` | dasselbe auf GitHub, veröffentlicht bei einem Versions-Tag das Release |
| `RELEASE_NOTES.md` | „Was ist neu“ – steht oben im GitHub-Release |

## Versionsschema

Wie im SHS-Prüfungsprogramm: Bei jedem Release erhöht `bump_version.py` die 3. Stelle um 1.
Nach `.99` beginnt sie wieder bei 0 und die 2. Stelle wird erhöht (`1.0.99` → `1.1.0`),
sinngemäß auch für die 1. Stelle.

```
python bump_version.py          # erhöhen, gibt die neue Nummer aus
python bump_version.py --sync   # nur abgeleitete Dateien neu schreiben (ohne Erhöhung)
```

Die Tests prüfen, dass `version.txt`, `version.py` und `version_info.txt` übereinstimmen.

## Voraussetzungen (einmalig, Windows)

1. Python 3.10 oder neuer und die Projektumgebung `.venv` (siehe README).
2. [Inno Setup 6](https://jrsoftware.org/isinfo.php) (stellt `ISCC.exe` bereit).

Mit einem Anaconda-/Miniforge-Python als Basis packt `build.spec` die nötigen
`sqlite3.dll`/`ffi*.dll` automatisch mit ein.

## Lokal bauen

```
build_installer.bat
```

Das Skript installiert PyInstaller, **erhöht die Versionsnummer**, baut das Programm,
führt den Selbsttest der EXE aus und baut den Installer. Ergebnis:
`Output\Bilderkatalog-Setup-X.Y.Z.exe`.

Nur bauen, ohne die Nummer zu erhöhen:

```
.venv\Scripts\python -m PyInstaller --noconfirm --clean build.spec
dist\Bilderkatalog\Bilderkatalog.exe --selbsttest %TEMP%\selbsttest.log
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss /DMyAppVersion=1.0.0
```

## Selbsttest

`Bilderkatalog.exe --selbsttest [protokoll]` prüft ohne Fenster in einem Temp-Ordner:
EXIF/GPS lesen, Ordner einlesen, Ortsauflösung (gebündelter Ortsdatensatz), Vorschaubild,
ZIP-Export, Weboberfläche/API, pywin32 und pywebview/WebView2. Exit-Code 0 = in Ordnung.
Standardprotokoll: `%TEMP%\bilderkatalog_selbsttest.log`. Echte Daten werden nicht berührt.

## Release veröffentlichen

1. `python bump_version.py`
2. `RELEASE_NOTES.md` für die neue Version schreiben
3. Committen und pushen:
   ```
   git add version.txt version_info.txt bilderkatalog/version.py RELEASE_NOTES.md
   git commit -m "Version X.Y.Z"
   git push
   ```
4. Tag setzen – das startet den Build auf GitHub:
   ```
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

Der Workflow prüft, dass der Tag zu `version.txt` passt, führt die Tests aus, baut die EXE,
macht den Selbsttest, baut den Installer, installiert ihn still, testet die installierte
EXE, deinstalliert sie wieder und veröffentlicht erst dann das Release mit der Setup-Datei.

Über **Actions → Installer bauen → Run workflow** lässt sich der Build auch ohne Release
starten; die Setup-Datei gibt es dann als Artefakt zum Herunterladen.

## Updates beim Anwender

- Die neue Setup-Datei einfach über die alte Version installieren. Die feste `AppId` in
  `installer.iss` sorgt dafür, dass ersetzt statt daneben installiert wird – **niemals ändern**.
- Läuft das Programm noch, bittet das Setup darum, es zu schließen (Laufkennung `AppMutex`).
- Katalog, Alben und Vorschaubilder liegen in `%LOCALAPPDATA%\bilderkatalog` und bleiben bei
  Update und Deinstallation erhalten.
- Im Programm prüft **v1.0.0 → Nach Updates suchen**, ob es auf GitHub eine neuere Version gibt.
  Das funktioniert erst, wenn das Repository öffentlich ist.

## Code-Signatur

Die Setup-Datei ist nicht signiert. Windows SmartScreen zeigt beim ersten Start daher
„Der Computer wurde durch Windows geschützt“ → **Weitere Informationen → Trotzdem ausführen**.
