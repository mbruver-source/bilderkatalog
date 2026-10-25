@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  Bilderkatalog - Installer-Build
echo ============================================================
echo.

rem --- Python finden (bevorzugt die Projektumgebung .venv) --------------------
set "PY="
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "PY=python"
    ) else (
        where py >nul 2>nul
        if not errorlevel 1 set "PY=py"
    )
)
if "%PY%"=="" (
    echo Python wurde nicht gefunden. Bitte Python von https://python.org installieren
    echo und dabei "Add python.exe to PATH" aktivieren.
    goto :fehler
)
echo Verwende Python: %PY%
%PY% --version
echo.

echo [1/5] Python-Pakete installieren ^(Programm und PyInstaller^)...
%PY% -m pip install --quiet -e . pyinstaller==6.22.3
if errorlevel 1 goto :fehler
echo.

echo [2/5] Versionsnummer erhoehen ^(bump_version.py^)...
rem Erhoeht die 3. Stelle (bis 99), danach die 2. Stelle - siehe bump_version.py.
rem Schreibt version.txt, version_info.txt und bilderkatalog\version.py neu.
set "VERSION="
for /f "delims=" %%V in ('%PY% bump_version.py') do set "VERSION=%%V"
if "%VERSION%"=="" (
    echo Versionsnummer konnte nicht ermittelt werden ^(bump_version.py fehlgeschlagen^).
    goto :fehler
)
echo   -^> Neue Version: %VERSION%
echo.

echo [3/5] Programm bauen ^(PyInstaller^)...
rem --clean: alle Module frisch einpacken, nie alte Zwischenstaende verwenden.
%PY% -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 goto :fehler
echo.

echo [4/5] Selbsttest der gebauten EXE...
set "PROTOKOLL=%TEMP%\bilderkatalog_selbsttest.log"
start "" /wait "dist\Bilderkatalog\Bilderkatalog.exe" --selbsttest "%PROTOKOLL%"
set "TESTCODE=%errorlevel%"
type "%PROTOKOLL%"
if not "%TESTCODE%"=="0" (
    echo Selbsttest fehlgeschlagen ^(Exit-Code %TESTCODE%^).
    goto :fehler
)
echo.

echo [5/5] Installer bauen ^(Inno Setup^)...
set "ISCC="
where ISCC >nul 2>nul
if not errorlevel 1 (
    set "ISCC=ISCC"
) else (
    for %%C in (
        "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
        "%ProgramFiles%\Inno Setup 6\ISCC.exe"
        "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
    ) do (
        if exist %%C if "!ISCC!"=="" set "ISCC=%%~C"
    )
)
if "%ISCC%"=="" (
    echo Inno Setup ^(ISCC.exe^) nicht gefunden: https://jrsoftware.org/isinfo.php
    goto :fehler
)
echo Verwende Inno Setup Compiler: %ISCC%
"%ISCC%" /Q installer.iss /DMyAppVersion=%VERSION%
if errorlevel 1 goto :fehler

echo.
echo ============================================================
echo  FERTIG!  Installer: %~dp0Output\Bilderkatalog-Setup-%VERSION%.exe
echo.
echo  Fuer ein Release: version.txt, version_info.txt und
echo  bilderkatalog\version.py committen und pushen, dann
echo     git tag v%VERSION% ^&^& git push origin v%VERSION%
echo ============================================================
if not defined NOPAUSE pause
exit /b 0

:fehler
echo.
echo ------------------------------------------------------------
echo  Beim Build ist ein Fehler aufgetreten ^(siehe Meldungen oben^).
echo ------------------------------------------------------------
if not defined NOPAUSE pause
exit /b 1
