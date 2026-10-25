import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


@pytest.fixture
def bump(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bump_version", ROOT / "bump_version.py")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    (tmp_path / "bilderkatalog").mkdir()
    monkeypatch.setattr(modul, "VERSION_DATEI", tmp_path / "version.txt")
    monkeypatch.setattr(modul, "VERSION_INFO_DATEI", tmp_path / "version_info.txt")
    monkeypatch.setattr(modul, "VERSION_PY_DATEI", tmp_path / "bilderkatalog" / "version.py")
    monkeypatch.setattr(modul, "HANDBUCH_DATEI", tmp_path / "HANDBUCH.md")
    return modul


@pytest.mark.parametrize("alt, neu", [
    ((1, 0, 0), (1, 0, 1)),
    ((1, 0, 98), (1, 0, 99)),
    ((1, 0, 99), (1, 1, 0)),
    ((1, 99, 99), (2, 0, 0)),
])
def test_kilometerzaehler(bump, alt, neu):
    assert bump.naechste_version(alt) == neu


def test_main_schreibt_alle_dateien(bump, tmp_path):
    (tmp_path / "version.txt").write_text("1.0.7\n", encoding="utf-8")
    assert bump.main([]) == "1.0.8"
    assert (tmp_path / "version.txt").read_text(encoding="utf-8").strip() == "1.0.8"
    assert 'VERSION = "1.0.8"' in (tmp_path / "bilderkatalog" / "version.py").read_text(encoding="utf-8")
    info = (tmp_path / "version_info.txt").read_text(encoding="utf-8")
    assert "filevers=(1, 0, 8, 0)" in info and '"ProductVersion", "1.0.8.0"' in info


def test_handbuch_stand_wird_nachgezogen(bump, tmp_path):
    (tmp_path / "version.txt").write_text("1.0.0\n", encoding="utf-8")
    (tmp_path / "HANDBUCH.md").write_bytes(b"# Handbuch\r\n\r\nStand: Version 1.0.0.\r\n")
    bump.main([])
    assert (tmp_path / "HANDBUCH.md").read_bytes() == b"# Handbuch\r\n\r\nStand: Version 1.0.1.\r\n"


def test_sync_erhoeht_nicht(bump, tmp_path):
    (tmp_path / "version.txt").write_text("2.3.4\n", encoding="utf-8")
    assert bump.main(["--sync"]) == "2.3.4"
    assert (tmp_path / "version.txt").read_text(encoding="utf-8").strip() == "2.3.4"


def test_ungueltige_version(bump, tmp_path):
    (tmp_path / "version.txt").write_text("1.0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bump.main([])


def test_eingecheckte_versionen_passen_zusammen():
    """version.txt, version.py und version_info.txt müssen dieselbe Nummer tragen."""
    from bilderkatalog import __version__

    text = (ROOT / "version.txt").read_text(encoding="utf-8").strip()
    assert __version__ == text
    assert f'"ProductVersion", "{text}.0"' in (ROOT / "version_info.txt").read_text(encoding="utf-8")
    assert f"Stand: Version {text}." in (ROOT / "docs" / "HANDBUCH.md").read_text(encoding="utf-8")


def test_update_vergleich(monkeypatch):
    from bilderkatalog import updates

    assert updates.version_tupel("v1.2.3") == (1, 2, 3)
    assert updates.version_tupel("1.2") is None
    monkeypatch.setattr(updates, "__version__", "1.0.5")
    monkeypatch.setattr(updates, "neueste_version", lambda: ("1.0.10", None))
    assert updates.pruefen()["newer"] is True
    monkeypatch.setattr(updates, "neueste_version", lambda: (None, "Keine Verbindung"))
    result = updates.pruefen()
    assert result["newer"] is False and result["error"] == "Keine Verbindung"
