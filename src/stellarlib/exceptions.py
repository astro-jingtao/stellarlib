"""Exception hierarchy for stellarlib.

All errors raised by stellarlib derive from :class:`StellarLibError`, so
callers can catch the library's failures as a group. Errors are raised loudly
rather than silently worked around; fallbacks that do exist (for example,
nearest-grid-point selection or clamped interpolation) emit warnings and
record the event in interpolation/selection records instead of raising.
"""

__all__ = [
    "StellarLibError",
    "UnsupportedModelError",
    "UnsupportedReleaseError",
    "UnrecognizedFormatError",
    "AmbiguousFormatError",
    "MissingParameterError",
    "ParameterNotReportedError",
    "GridPointNotFoundError",
    "GridBoundaryError",
    "GridHoleError",
    "UnsafeInterpolationError",
    "AmbiguousConventionError",
    "MetadataConflictError",
    "DataIntegrityError",
    "SourceUnavailableError",
    "LicenseUnavailableError",
    "WavelengthGridError",
]


class StellarLibError(Exception):
    """Base class for all stellarlib errors."""


class UnsupportedModelError(StellarLibError):
    """The requested library family has no registered backend."""


class UnsupportedReleaseError(StellarLibError):
    """The backend does not support the requested release of a library."""


class UnrecognizedFormatError(StellarLibError):
    """No backend recognizes a data source as its native format."""


class AmbiguousFormatError(StellarLibError):
    """More than one backend claims a data source; the caller must choose."""


class MissingParameterError(StellarLibError):
    """A parameter required to identify or select a spectrum is absent."""


class ParameterNotReportedError(StellarLibError):
    """A parameter value was requested that the upstream data does not report."""


class GridPointNotFoundError(StellarLibError):
    """No spectrum exists at the requested grid coordinates (exact selection)."""


class GridBoundaryError(StellarLibError):
    """The requested coordinates lie outside the grid's covered range."""


class GridHoleError(StellarLibError):
    """The parameter rectangle covering the request contains missing nodes
    (a grid hole), so rectangular interpolation is impossible."""


class UnsafeInterpolationError(StellarLibError):
    """An interpolation was requested that stellarlib refuses to perform
    silently (extrapolation, non-monotonic coordinate, wavelength mismatch)."""


class AmbiguousConventionError(StellarLibError):
    """A quantity or parameter is requested under a convention the data does
    not unambiguously support (for example vacuum vs air wavelengths)."""


class MetadataConflictError(StellarLibError):
    """Independent metadata sources disagree (file header vs filename vs
    user assertion)."""


class DataIntegrityError(StellarLibError):
    """A downloaded or on-disk asset failed checksum or structural validation."""


class SourceUnavailableError(StellarLibError):
    """All eligible sources for an asset failed; nothing was fetched."""


class LicenseUnavailableError(StellarLibError):
    """The redistribution license of an asset could not be established."""


class WavelengthGridError(StellarLibError):
    """A wavelength grid is missing, mismatched, or ambiguous where one is
    required (for example pairing a resampled flux file with its sidecar
    wavelength grid)."""
