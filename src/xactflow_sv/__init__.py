__version__ = "0.1.0"

from pathlib import Path

import ipxact
from xactflow import Importer


class SVImporter(Importer):
    name = "sv"

    def import_(self, source_path: Path, **options: object) -> object:
        raise NotImplementedError


__all__ = ["__version__", "SVImporter"]
