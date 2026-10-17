"""stellarlib: faithful and unified access to theoretical stellar spectral libraries.

stellarlib is a provenance-first, model-agnostic Python interface for reading,
preserving, standardizing, and querying theoretical stellar spectra published
on (Teff, log g, [M/H], ...) parameter grids.
"""

__version__ = "0.1.0"

from stellarlib.core import FieldSpec, ModelIdentity, ParameterRole, ParameterSpec
from stellarlib.exceptions import StellarLibError
from stellarlib.native import NativeSpectrum
from stellarlib.provenance import (
    DataAsset,
    Provenance,
    Reference,
    SourceChain,
    SourceClass,
    SourceRecord,
)
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
    "UnifiedView",
    # provenance
    "Provenance",
    "SourceRecord",
    "SourceClass",
    "SourceChain",
    "DataAsset",
    "Reference",
    # errors
    "StellarLibError",
    # unified vocabulary
    "TRANSFORMATIONS",
]
