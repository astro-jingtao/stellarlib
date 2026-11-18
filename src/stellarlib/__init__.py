"""stellarlib: faithful and unified access to published stellar spectral libraries.

stellarlib is a provenance-first, model-agnostic Python interface for reading,
preserving, standardizing, and querying stellar spectra — model-atmosphere
flux grids on (Teff, log g, [M/H], ...) parameter nodes and empirical
libraries of observed stars.
"""

__version__ = "0.1.2"

from stellarlib.core import FieldSpec, ModelIdentity, ParameterRole, ParameterSpec
from stellarlib.database.build import (
    ProductVerification,
    VerifyReport,
    build_database,
    register_local_product,
    verify_database,
)
from stellarlib.database.core import Database, open_database
from stellarlib.database.transfer.export import (
    ExportResult,
    export_database,
    rsync_command,
    run_rsync,
    verify_transfer,
)
from stellarlib.database.transfer.merge import (
    MergeConflict,
    MergeReport,
    merge_database,
)
from stellarlib.exceptions import StellarLibError
from stellarlib.facade import (
    BoszSpectra,
    MilesSpectra,
    StelibSpectra,
    TlustySpectra,
    UnifiedSpectra,
    construct,
)
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
    "MilesSpectra",
    "MergeConflict",
    "MergeReport",
    "merge_database",
    "ExportResult",
    "export_database",
    "verify_transfer",
    "rsync_command",
    "run_rsync",
    "StelibSpectra",
    "build_database",
    "verify_database",
    "VerifyReport",
    "ProductVerification",
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
