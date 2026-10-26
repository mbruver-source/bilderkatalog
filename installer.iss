; Inno Setup Script für den Bilderkatalog
; =======================================
;
; Baut einen Windows-Installer, der auch UPDATES kann: Läuft der Installer einer neueren
; Version über eine bestehende Installation, erkennt Inno Setup das an der festen AppId
; und ersetzt die alte Installation, statt eine zweite daneben anzulegen.
;
; NIEMALS ändern:
;   - #AppId (feste GUID): Inno Setup erkennt eine bestehende Installation nur darüber.
;
; Die Versionsnummer wird beim Kompilieren übergeben (siehe build_installer.bat bzw.
; .github/workflows/build-installer.yml):
;
;     ISCC installer.iss /DMyAppVersion=1.0.1
;
; DEINE DATEN SIND VON UPDATES UND DER DEINSTALLATION NICHT BETROFFEN:
; Datenbank (Alben, Katalog), Vorschaubilder und Einstellungen liegen nicht im
; Programmordner, sondern unter %LOCALAPPDATA%\bilderkatalog (siehe config.data_dir).
; Setup und Deinstallation berühren ausschließlich {app}.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "Bilderkatalog"
#define MyAppPublisher "Marco"
#define MyAppURL "https://github.com/mbruver-source/bilderkatalog"
#define MyAppExeName "Bilderkatalog.exe"
; Feste, für immer gleichbleibende Kennung dieser Anwendung – NIEMALS ändern!
#define AppId "{{9AE21F3E-EA70-4C8C-84CF-9D94F5C54DF5}"

[Setup]
AppId={#AppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=Output
OutputBaseFilename=Bilderkatalog-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=bilderkatalog\static\icon.ico
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Kein Admin-Zwang: ohne Adminrechte landet das Programm im Benutzerordner
; (autopf löst je nach Rechten passend auf), mit Adminrechten unter "Programme".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Läuft das Programm noch, bittet Setup darum, es zu schließen (Restart Manager),
; und startet es danach wieder.
CloseApplications=yes
RestartApplications=yes
; Erkennt ein laufendes Programm auch unabhängig vom Restart Manager. Namen = Laufkennung
; in bilderkatalog/__main__.py (_window): lokal und global (andere Sitzung).
AppMutex=Bilderkatalog-Laufend,Global\Bilderkatalog-Laufend

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung anlegen"; GroupDescription: "Zusätzliche Symbole:"

[InstallDelete]
; Programmordner vor dem Kopieren leeren: Eine neue Version bringt evtl. andere
; Bibliotheken mit; Reste der alten dürfen nicht liegen bleiben.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Lizenzen gut sichtbar im Programmordner (eigene MIT-Lizenz und Fremdsoftware)
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "build\THIRD_PARTY_LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} deinstallieren"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} jetzt starten"; Flags: nowait postinstall skipifsilent

; Hinweis zur Deinstallation: Entfernt nur die Programmdateien unter {app}. Der Katalog
; (%LOCALAPPDATA%\bilderkatalog) bleibt erhalten, damit Alben und Vorschaubilder nach einer
; Neuinstallation sofort wieder da sind. Bewusst KEIN [UninstallDelete] für diesen Ordner.
