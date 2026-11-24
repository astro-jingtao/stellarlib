"""stellarlib: faithful and unified access to published stellar spectral libraries.

stellarlib is a provenance-first, model-agnostic Python interface for reading,
preserving, standardizing, and querying stellar spectra — model-atmosphere
flux grids on (Teff, log g, [M/H], ...) parameter nodes and empirical
libraries of observed stars.

The package root exports only the four whole-library entry verbs; every
other public name is imported from the concrete module that defines it
(``stellarlib.grids.grid``, ``stellarlib.facade``, ``stellarlib.database``,
``stellarlib.spectra``, ...), so a caller always sees where a name lives.
"""

__version__ = "0.1.2"

from stellarlib.database.build import build_database
from stellarlib.database.core import open_database
from stellarlib.facade import construct
from stellarlib.grids.grid import open_grid

__all__ = [
    "__version__",
    "build_database",
    "construct",
    "open_database",
    "open_grid",
]
