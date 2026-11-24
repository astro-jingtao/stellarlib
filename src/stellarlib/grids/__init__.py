"""Grids and their queries: the node index and the interpolation machinery.

A grid is one product's published parameter nodes held as a lightweight
index (``grid``); the queries over it — node selection, multilinear and
Voronoi point queries, wavelength resampling — and their audit records
live in ``interp``. Import each name from its concrete module.
"""
