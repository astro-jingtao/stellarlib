"""Warning categories for stellarlib.

Warnings (unlike exceptions) cover situations where execution continues but
the result differs from what was literally asked for. Every warning category
here corresponds to a condition that is also recorded in provenance or in
selection/interpolation records attached to the returned objects.
"""

__all__ = [
    "CatalogSizeMismatchWarning",
    "ChecksumUnavailableWarning",
    "ClampedInterpolationWarning",
    "DataQuirkWarning",
    "DerivedQuantityWarning",
    "IncompleteMetadataWarning",
    "NearestGridPointWarning",
    "StellarLibWarning",
    "ThirdPartySourceWarning",
    "VoronoiInterpolationWarning",
]


class StellarLibWarning(UserWarning):
    """Base class for all stellarlib warnings."""


class NearestGridPointWarning(StellarLibWarning):
    """A nearest grid point was returned instead of the requested exact one."""


class VoronoiInterpolationWarning(StellarLibWarning):
    """Voronoi (nearest-node) interpolation was applied — the returned
    spectrum is a single published node's data, not an interpolation."""


class ClampedInterpolationWarning(StellarLibWarning):
    """Out-of-range query coordinates were clamped to the grid's edge cell
    before interpolation (range_policy="clamp")."""


class DataQuirkWarning(StellarLibWarning):
    """A known quirk of the published upstream data was encountered.

    The quirk is recorded faithfully (per node and in the set's notes)
    and never fixed in code; the warning says it is there and where the
    verified facts live. Current user: BaSeL 2.2 grids."""


class DerivedQuantityWarning(StellarLibWarning):
    """A quantity was derived from other quantities rather than read natively."""


class IncompleteMetadataWarning(StellarLibWarning):
    """Metadata expected from the upstream product is missing or partial."""


class ThirdPartySourceWarning(StellarLibWarning):
    """An asset was (or would be) obtained from a non-official source."""


class ChecksumUnavailableWarning(StellarLibWarning):
    """No checksum is available to verify an asset's integrity."""


class CatalogSizeMismatchWarning(StellarLibWarning):
    """A downloaded file's size differs from the size recorded in the release
    catalog at verification time (the upstream file may have been replaced)."""
