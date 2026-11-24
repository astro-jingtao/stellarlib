"""Grids and their queries: the node index and the interpolation machinery.

A grid is one product's published parameter nodes held as a lightweight
index (``grid``); the queries over it — node selection, multilinear and
Voronoi point queries, wavelength resampling — and their audit records
live in ``interp``.
"""

from stellarlib.grids.grid import SpectrumGrid, open_grid
from stellarlib.grids.interp import (
    InterpolationRecord,
    corner_weights,
    interp_methods,
    resample_wavelengths,
    voronoi_nearest,
)

__all__ = [
    "InterpolationRecord",
    "SpectrumGrid",
    "corner_weights",
    "interp_methods",
    "open_grid",
    "resample_wavelengths",
    "voronoi_nearest",
]
