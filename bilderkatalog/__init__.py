"""Bilderkatalog: Metadaten-Katalog für Bilder in OneDrive und lokalen Ordnern."""

try:
    # version.py wird von bump_version.py erzeugt (Quelle: version.txt)
    from .version import VERSION as __version__
except ImportError:  # frische Arbeitskopie vor dem ersten "bump_version.py --sync"
    __version__ = "dev"
