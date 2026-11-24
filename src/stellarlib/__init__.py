"""stellarlib: faithful and unified access to published stellar spectral libraries.

stellarlib is a provenance-first, model-agnostic Python interface for reading,
preserving, standardizing, and querying stellar spectra — model-atmosphere
flux grids on (Teff, log g, [M/H], ...) parameter nodes and empirical
libraries of observed stars.
"""

__version__ = "0.1.2"

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
    BaselSpectra,
    BoszComposition,
    BoszSpectra,
    MilesSpectra,
    StelibSpectra,
    TlustySpectra,
    UnifiedSpectra,
    construct,
)
from stellarlib.grids.grid import SpectrumGrid, open_grid
from stellarlib.grids.interp import (
    InterpolationRecord,
    corner_weights,
    resample_wavelengths,
    voronoi_nearest,
)
from stellarlib.provenance import (
    DataAsset,
    Provenance,
    Reference,
    SourceChain,
    SourceClass,
    SourceRecord,
)
from stellarlib.spectra.core import FieldSpec, ModelIdentity, ParameterRole, ParameterSpec
from stellarlib.spectra.native import NativeSpectrum
from stellarlib.spectra.spectrum import (
    GridNode,
    InterpSpectrum,
    NodeSpectrum,
    Spectrum,
    SpectrumSet,
)
from stellarlib.spectra.unified import TRANSFORMATIONS, UnifiedView

__all__ = [
    "TRANSFORMATIONS",
    "BaselSpectra",
    "BoszComposition",
    "BoszSpectra",
    "DataAsset",
    "Database",
    "ExportResult",
    "FieldSpec",
    "GridNode",
    "InterpSpectrum",
    "InterpolationRecord",
    "MergeConflict",
    "MergeReport",
    "MilesSpectra",
    "ModelIdentity",
    "NativeSpectrum",
    "NodeSpectrum",
    "ParameterRole",
    "ParameterSpec",
    "ProductVerification",
    "Provenance",
    "Reference",
    "SourceChain",
    "SourceClass",
    "SourceRecord",
    "Spectrum",
    "SpectrumGrid",
    "SpectrumSet",
    "StelibSpectra",
    "StellarLibError",
    "TlustySpectra",
    "UnifiedSpectra",
    "UnifiedView",
    "VerifyReport",
    "__version__",
    "build_database",
    "construct",
    "corner_weights",
    "export_database",
    "merge_database",
    "open_database",
    "open_grid",
    "register_local_product",
    "resample_wavelengths",
    "rsync_command",
    "run_rsync",
    "verify_database",
    "verify_transfer",
    "voronoi_nearest",
]
