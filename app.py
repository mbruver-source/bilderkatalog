"""Startskript für die gebündelte Windows-Anwendung (PyInstaller, siehe build.spec).

    Bilderkatalog.exe                  → Programmfenster
    Bilderkatalog.exe --selbsttest [protokoll]
                                       → Selbsttest ohne Fenster (CI und Fehlersuche)

Für die Entwicklung genügt `python -m bilderkatalog` bzw. `bilderkatalog`.
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def _ausgabe_umleiten(ziel: str) -> None:
    """Die EXE hat keine Konsole (sys.stdout/stderr sind None). Ausgaben und Fehler
    landen deshalb in einer Logdatei, statt beim ersten print() abzustürzen."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        datei = open(ziel, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 – lebt so lange wie das Programm
    except OSError:
        datei = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    sys.stdout = sys.stdout or datei
    sys.stderr = sys.stderr or datei


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--selbsttest"]:
        _ausgabe_umleiten(os.devnull)
        from bilderkatalog.selbsttest import fuehre_selbsttest_aus

        return fuehre_selbsttest_aus(argv[1] if len(argv) > 1 else None)

    from bilderkatalog import config

    _ausgabe_umleiten(str(config.data_dir() / "bilderkatalog.log"))
    from bilderkatalog.__main__ import main as cli

    return cli(argv or ["app"])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
