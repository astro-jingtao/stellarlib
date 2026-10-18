"""stellarlib: faithful and unified access to theoretical stellar spectral libraries.

stellarlib is a provenance-first, model-agnostic Python interface for reading,
preserving, standardizing, and querying theoretical stellar spectra published
on (Teff, log g, [M/H], ...) parameter grids.
"""

__version__ = "0.1.2"

from stellarlib.core import FieldSpec, ModelIdentity, ParameterRole, ParameterSpec
from stellarlib.database.build import build_database, register_local_product
from stellarlib.database.core import Database, open_database
from stellarlib.exceptions import StellarLibError
from stellarlib.facade import BoszSpectra, TlustySpectra, UnifiedSpectra, construct
from stellarlib.grid import SpectrumGrid, open_grid
from stellarlib.interp import (
    InterpolationRecord,
    corner_weights,
    resample_wavelengths,
    voronoi_nearest,
)
from stellarlib.native import NativeSpectrum
from stellarlib.provenance import (
    DataAsset,
    Provenance,
    Reference,
    SourceChain,
    SourceClass,
    SourceRecord,
)
from stellarlib.spectrum import GridNode, Spectrum, SpectrumSet
from stellarlib.unified import TRANSFORMATIONS, UnifiedView

__all__ = [
    "__version__",
    # core schema
    "FieldSpec",
    "ModelIdentity",
    "ParameterRole",
    "ParameterSpec",
    # objects
    "NativeSpectrum",
    "Spectrum",
    "SpectrumSet",
    "GridNode",
    "SpectrumGrid",
    "UnifiedView",
    # provenance
    "Provenance",
    "SourceRecord",
    "SourceClass",
    "SourceChain",
    "DataAsset",
    "Reference",
    # entry points
    "open_grid",
    "construct",
    "UnifiedSpectra",
    "BoszSpectra",
    "TlustySpectra",
    "build_database",
    "open_database",
    "Database",
    "register_local_product",
    # interpolation
    "InterpolationRecord",
    "corner_weights",
    "voronoi_nearest",
    "resample_wavelengths",
    # errors
    "StellarLibError",
    # unified vocabulary
    "TRANSFORMATIONS",
]
