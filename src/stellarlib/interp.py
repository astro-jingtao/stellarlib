"""Interpolation across grid nodes, and wavelength resampling.

The corner-weight machinery is the N-dimensional generalization of the
rectangular interpolation the downstream applications hand-computed:
corners are enumerated in C order over the axes in their declared order
(for (teff, log g, [M/H]): ``c = 4*dt + 2*dg + d*m``; for (teff, log g):
``c = 2*dt + d*g``), flat node indices ravel the axis product in C order,
and each row's weights sum to 1.

Voronoi interpolation is the hole-tolerant alternative: a query is
answered with the single nearest published node, nearest measured in
units of the grid's own step per axis (each axis is scaled by the median
spacing of its published values, so a 250 K Teff offset and a 0.25 dex
log g offset are comparable). It never invents data between nodes, which
is exactly why it is safe where a hole makes rectangular interpolation
impossible.

Resampling moves a spectrum onto a new wavelength grid by linear
interpolation in wavelength and records the operation in provenance —
the result is a derived product and never masquerades as native.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import dataclasses

import numpy as np
from scipy.spatial import cKDTree

from stellarlib.exceptions import UnsafeInterpolationError
from stellarlib.native import NativeSpectrum
from stellarlib.spectrum import Spectrum

__all__ = [
    "InterpolationRecord",
    "corner_weights",
    "interp_methods",
    "resample_wavelengths",
    "voronoi_nearest",
]

_METHODS = ("multilinear", "voronoi")


def interp_methods() -> tuple[str, ...]:
    """The query methods the record vocabulary knows."""
    return _METHODS


@dataclasses.dataclass(frozen=True)
class InterpolationRecord:
    """Everything needed to audit one interpolation.

    ``method`` is ``"exact-node"`` (no interpolation), ``"multilinear"``
    (rectangular corner interpolation; ``node_indices`` carries the 2^N
    corners per query), ``"voronoi"`` (one nearest node per query), or
    ``"resample"`` (wavelength resampling; ``node_indices`` is empty and
    the transformation lives in ``note``).
    """

    method: str
    axes: tuple[str, ...]
    requested: tuple[Mapping[str, float], ...]
    node_indices: tuple[tuple[int, ...], ...]
    node_values: tuple[tuple[Mapping[str, float], ...], ...]
    weights: tuple[tuple[float, ...], ...]
    clamped: tuple[Mapping[str, bool], ...]
    note: str | None = None

    def to_dict(self) -> dict:
        """The record as a JSON-ready mapping."""
        return {
            "method": self.method,
            "axes": list(self.axes),
            "requested": [dict(r) for r in self.requested],
            "node_indices": [list(ix) for ix in self.node_indices],
            "node_values": [[dict(v) for v in q] for q in self.node_values],
            "weights": [list(w) for w in self.weights],
            "clamped": [dict(c) for c in self.clamped],
            "note": self.note,
        }


# -- rectangular corner machinery ---------------------------------------------


def _bracket(axis: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if axis.size == 1:
        # a degenerate (single-value) axis has no cell: both corner digits
        # land on the one value with fraction 0
        return np.zeros(x.shape, dtype=np.int64), np.zeros(x.shape)
    i = np.clip(np.searchsorted(axis, x) - 1, 0, axis.size - 2)
    w = np.clip((x - axis[i]) / (axis[i + 1] - axis[i]), 0.0, 1.0)
    return i, w


def _validated_axes(
    axes: Mapping[str, Sequence[float]],
    queries: Mapping[str, float | Sequence[float]],
) -> tuple[tuple[str, ...], dict[str, np.ndarray]]:
    """Axis names (declaration order) with their validated float64
    arrays: non-empty, 1-D, strictly increasing unless degenerate."""
    names = tuple(axes)
    if set(names) != set(queries):
        raise ValueError(
            f"queries must name exactly the axes {names}; got "
            f"{sorted(queries)}"
        )
    arrays = {}
    for name in names:
        axis = np.asarray(axes[name], dtype=np.float64)
        if axis.ndim != 1 or axis.size < 1:
            raise ValueError(f"axis {name!r} must be a non-empty 1-D array")
        if axis.size > 1 and not np.all(np.diff(axis) > 0):
            raise ValueError(
                f"axis {name!r} must be strictly increasing"
            )
        arrays[name] = axis
    return names, arrays


def _query_arrays_of(
    names: tuple[str, ...],
    queries: Mapping[str, float | Sequence[float]],
) -> dict[str, np.ndarray]:
    """Queries as 1-D float64 arrays, all sharing one length."""
    qs = {
        name: np.atleast_1d(np.asarray(queries[name], dtype=np.float64))
        for name in names
    }
    n = None
    for name, q in qs.items():
        if q.ndim != 1:
            raise ValueError(f"query for axis {name!r} must be scalar or 1-D")
        if n is None:
            n = q.size
        elif q.size != n:
            raise ValueError("query arrays must share one length")
    return qs


def _fill_corners(
    names: tuple[str, ...],
    arrays: dict[str, np.ndarray],
    qs: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """The ``(n, 2^N)`` flat corner indices and multilinear weights, one
    row per query point.

    Flat indices address the C-order ravel of the axis product (for
    (teff, log g, [M/H]) this reproduces the downstream
    ``c = 4*dt + 2*dg + d*m`` contract exactly); the C-order corner
    enumeration makes the first axis most significant.
    """
    n = qs[names[0]].size
    sizes = [arrays[name].size for name in names]
    n_corners = 2 ** len(names)
    idx = np.empty((n, n_corners), dtype=np.int64)
    w = np.empty((n, n_corners), dtype=np.float64)
    # strides of the C-order ravel over the axis product
    strides = np.concatenate(
        [np.cumprod(sizes[::-1])[::-1][1:], [1]]
    )
    fractions = {}
    cell_index = {}
    cell_step = {}
    for name in names:
        i, fw = _bracket(arrays[name], qs[name])
        cell_index[name] = i
        fractions[name] = fw
        # a degenerate axis pins both corner digits on its single value
        cell_step[name] = 1 if arrays[name].size > 1 else 0
    for corner, digits in enumerate(np.ndindex(*(2,) * len(names))):
        w_col = np.ones(n)
        flat = np.zeros(n, dtype=np.int64)
        for pos, name in enumerate(names):
            digit = digits[pos]
            w_col *= digit * fractions[name] + (1 - digit) * (1 - fractions[name])
            flat += (cell_index[name] + digit * cell_step[name]) * strides[pos]
        idx[:, corner] = flat
        w[:, corner] = w_col
    return idx, w


def corner_weights(
    axes: Mapping[str, Sequence[float]], **queries: float | Sequence[float]
) -> tuple[np.ndarray, np.ndarray, tuple[np.ndarray, ...]]:
    """Rectangular cell corners and weights for query points.

    Parameters
    ----------
    axes
        Ordered mapping of axis name to its strictly increasing values
        (a single-value axis is allowed: it is degenerate, both corner
        digits land on its one value with zero weight on one side). The
        declaration order fixes the corner enumeration (C order over the
        axes) and the flat index raveling — for (teff, log g, [M/H]) this
        reproduces the downstream ``c = 4*dt + 2*dg + d*m`` contract
        exactly.
    **queries
        Query values per axis (scalar or 1-D array; all broadcast to one
        length).

    Returns
    -------
    ``(idx, w, clamped)``
        ``idx`` and ``w`` of shape ``(n, 2^N)``: flat indices into the
        C-order ravel of the axis product and the matching multilinear
        weights (each row sums to 1). ``clamped`` is one boolean mask per
        axis (in declaration order) marking queries outside the axis
        range; this function clamps such queries to the edge cell and
        reports — the policy decision belongs to the caller.
    """
    names, arrays = _validated_axes(axes, queries)
    qs = _query_arrays_of(names, queries)
    idx, w = _fill_corners(names, arrays, qs)
    clamped = tuple(
        (qs[name] < arrays[name][0]) | (qs[name] > arrays[name][-1])
        for name in names
    )
    return idx, w, clamped


# -- Voronoi (nearest-node) machinery -----------------------------------------


def voronoi_nearest(
    axes: Mapping[str, Sequence[float]],
    available_keys: Sequence[Sequence[float]],
    queries: Sequence[Sequence[float]],
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """Nearest available node per query (Voronoi cell assignment).

    Distances are euclidean after scaling each axis by the median spacing
    of its published values, so ``nearest`` is measured in grid steps, not
    raw physical units (a 250 K Teff step and a 0.25 dex log g step count
    as comparable). Returns ``(indices, distances, scales)`` — indices
    into ``available_keys``.
    """
    names = tuple(axes)
    axis_arrays = {name: np.asarray(axes[name], dtype=float) for name in names}
    scales: dict[str, float] = {}
    for name, axis in axis_arrays.items():
        diffs = np.diff(np.unique(axis))
        scales[name] = float(np.median(diffs)) if diffs.size else 1.0

    scale_vector = np.array([scales[name] for name in names])
    tree_points = np.asarray(available_keys, dtype=np.float64) / scale_vector
    query_points = np.atleast_2d(np.asarray(queries, dtype=np.float64)) / scale_vector
    if tree_points.ndim != 2 or tree_points.shape[1] != len(names):
        raise ValueError(
            f"available keys must have one value per axis {names}"
        )
    tree = cKDTree(tree_points)
    distances, indices = tree.query(query_points, k=1)
    return np.asarray(indices), np.asarray(distances, dtype=np.float64), scales


# -- wavelength resampling -----------------------------------------------------


def resample_wavelengths(
    spectrum: Spectrum,
    wavelength_out: Iterable[float],
    method: str = "linear",
) -> Spectrum:
    """Resample a spectrum's arrays onto a new wavelength grid.

    Linear interpolation in wavelength (second-order effects are negligible
    on the libraries' log-uniform grids); no extrapolation — requests
    outside the spectrum's range raise :class:`UnsafeInterpolationError`.
    The returned spectrum is a derived product: its provenance carries the
    transformation and it never masquerades as native data.
    """

    if not isinstance(spectrum, Spectrum):
        raise TypeError(f"expected a Spectrum, got {type(spectrum).__name__}")
    if method != "linear":
        raise ValueError(f"method must be 'linear', got {method!r}")
    native = spectrum.native
    if native.wavelength is None:
        raise UnsafeInterpolationError(
            "cannot resample a spectrum with no wavelength grid attached"
        )
    wave = np.asarray(native.wavelength, dtype=np.float64)
    if wave.size < 2 or not np.all(np.diff(wave) > 0):
        raise UnsafeInterpolationError(
            "the spectrum's wavelength grid must be strictly increasing"
        )
    new_wave = np.asarray(list(wavelength_out), dtype=np.float64)
    if new_wave.size < 1 or not np.all(np.diff(new_wave) > 0):
        raise UnsafeInterpolationError(
            "the target wavelength grid must be 1-D and strictly increasing"
        )
    outside = (new_wave < wave[0]) | (new_wave > wave[-1])
    if np.any(outside):
        bad = new_wave[outside][:5]
        raise UnsafeInterpolationError(
            f"target wavelengths {bad} fall outside the spectrum range "
            f"[{wave[0]}, {wave[-1]}]; extrapolation is not performed"
        )

    new_flux = np.interp(new_wave, wave, native.flux)
    new_continuum = (
        np.interp(new_wave, wave, native.continuum)
        if native.continuum is not None
        else None
    )

    transformation = (
        f"resample linear in wavelength: {wave.size} -> {new_wave.size} points "
        f"over [{new_wave[0]}, {new_wave[-1]}]"
    )
    provenance = spectrum.provenance
    new_provenance = dataclasses.replace(
        provenance,
        transformations=(*provenance.transformations, transformation),
    )

    new_native = NativeSpectrum(
        wavelength=new_wave,
        flux=new_flux,
        continuum=new_continuum,
        header=native.header,
        metadata=dict(native.metadata),
        provenance_keys=dict(native.provenance_keys),
    )
    return Spectrum(
        native=new_native,
        identity=spectrum.identity,
        parameters=spectrum.parameters,
        field_specs=dict(spectrum.field_specs),
        provenance=new_provenance,
        selection=dict(spectrum.selection),
        unified_view=spectrum.unified.with_native(new_native),
    )
