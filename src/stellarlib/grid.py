"""SpectrumGrid and open_grid: queryable collections of library nodes.

A grid holds the lightweight node index of one product (one directory of
files of the same model, release, and physics configuration). Spectra are
loaded on demand and cached. The grid reports the truth about its
coverage: ``axes`` lists the values that actually exist, and ``holes``
lists the cells of the axis product that are missing — never silently
filled.

Queries split by intent. ``node(...)`` is the strict published-node
interface: the coordinates must land exactly on a node, and the answer is
that node's :class:`~stellarlib.spectrum.NodeSpectrum`. ``spectrum(...)``
is the convenience query interface: it interpolates between nodes (or
snaps to the nearest one) and always answers an
:class:`~stellarlib.spectrum.InterpSpectrum` whose record traces the
answer back to the exact nodes and weights — including the degenerate
cases, where an exact-node hit or a Voronoi nearest-node answer carries
weight 1.0 on the single node it returns.
"""

from __future__ import annotations

from collections.abc import Iterable
import dataclasses
import itertools
import os
from pathlib import Path
from typing import Any
import warnings

import numpy as np

from stellarlib.exceptions import (
    AmbiguousFormatError,
    GridHoleError,
    GridPointNotFoundError,
    UnrecognizedFormatError,
    UnsafeInterpolationError,
)
from stellarlib.interp import (
    InterpolationRecord,
    corner_weights,
    voronoi_nearest,
)
from stellarlib.models import BACKENDS, get_backend, parse_model_spec
from stellarlib.native import NativeSpectrum
from stellarlib.provenance import Provenance
from stellarlib.spectrum import (
    GridNode,
    InterpSpectrum,
    NodeSpectrum,
    Spectrum,
    SpectrumSet,
)
from stellarlib.warnings import (
    ClampedInterpolationWarning,
    NearestGridPointWarning,
    VoronoiInterpolationWarning,
)

__all__ = ["SpectrumGrid", "open_grid"]


class SpectrumGrid:
    """All nodes of one library product, queryable by grid coordinates.

    The selectable coordinates come from the sets' backend-pinned order
    (BOSZ: teff, log_surface_gravity, m_h; TLUSTY: teff,
    log_surface_gravity). Node spectra are loaded lazily through the
    owning set's loader and cached on the grid
    (``load_cache_size()``/``clear_cache()``). Point queries leave their
    :class:`~stellarlib.interp.InterpolationRecord` on ``last_record``.
    """

    def __init__(self, sets: Iterable[SpectrumSet]) -> None:
        """Index the sets' nodes; duplicates at one point are an error."""
        self._sets = tuple(sets)
        if not self._sets:
            raise ValueError("a SpectrumGrid needs at least one SpectrumSet")
        identities = {s.identity for s in self._sets}
        if len(identities) > 1:
            raise ValueError(
                "cannot mix library products in one grid: "
                + "; ".join(i.label for i in identities)
            )
        selectable_orders = {s.selectable for s in self._sets}
        if len(selectable_orders) > 1:
            raise ValueError(
                "cannot mix selectable-coordinate orders in one grid: "
                + "; ".join(str(list(o)) for o in selectable_orders)
            )
        self.identity = self._sets[0].identity
        self._selectable = self._sets[0].selectable
        self._node_of_key: dict[tuple, tuple[SpectrumSet, GridNode]] = {}
        for s in self._sets:
            for node in s:
                key = node.key(self._selectable)
                if key in self._node_of_key:
                    raise ValueError(f"duplicate spectrum at grid point {key}")
                self._node_of_key[key] = (s, node)
        self._cache: dict[tuple, Spectrum] = {}
        self.last_record: InterpolationRecord | None = None

    # -- construction -------------------------------------------------------

    @classmethod
    def from_paths(
        cls, model: str, paths: Iterable[str | os.PathLike], **reader_options
    ) -> SpectrumGrid:
        """Read the given files through the family's backend."""
        backend = get_backend(model)
        path_list = [Path(p) for p in paths]
        if not path_list:
            raise ValueError("paths must not be empty")
        return cls([backend.read(path_list, **reader_options)])

    @classmethod
    def from_directory(
        cls, model: str, directory: str | os.PathLike, **reader_options
    ) -> SpectrumGrid:
        """Read one product directory through the family's backend."""
        backend = get_backend(model)
        root = Path(directory)
        if not root.is_dir():
            raise NotADirectoryError(root)
        return cls([backend.read(root, **reader_options)])

    # -- introspection ------------------------------------------------------

    @property
    def sets(self) -> tuple[SpectrumSet, ...]:
        """The grid's spectrum sets (one coherent product each)."""
        return self._sets

    @property
    def selectable(self) -> tuple[str, ...]:
        """The backend-pinned coordinate names queries address."""
        return self._selectable

    def __len__(self) -> int:
        return len(self._node_of_key)

    @property
    def axes(self) -> dict[str, tuple]:
        """Available values per selectable coordinate, sorted, unique.

        This is the truth about what exists — not a declared rectangle.
        Compare with ``holes`` for the missing cells of the axis product.
        """
        axes: dict[str, tuple] = {}
        for pos, name in enumerate(self._selectable):
            axes[name] = tuple(sorted({key[pos] for key in self._node_of_key}))
        return axes

    @property
    def nodes(self) -> tuple[GridNode, ...]:
        """All exact nodes: lightweight index entries, declaration order.

        Each entry's ``coordinates`` are the node's values on the
        selectable axes — the full inventory of points ``node()`` accepts
        and ``spectrum()`` interpolates between.
        """
        return tuple(entry[1] for entry in self._node_of_key.values())

    @property
    def holes(self) -> tuple[tuple, ...]:
        """Cells of the axis product with no published node.

        Each entry is a coordinate tuple in selectable order. An empty
        tuple means the grid is a complete rectangle — the precondition of
        multilinear interpolation everywhere on it.
        """
        product = itertools.product(*self.axes.values())
        return tuple(
            cell for cell in product if cell not in self._node_of_key
        )

    def has_holes(self) -> bool:
        """Whether any cell of the axis product lacks a published node."""
        return len(self._node_of_key) != int(
            np.prod([len(v) for v in self.axes.values()])
        )

    def load_cache_size(self) -> int:
        """How many node spectra are currently materialized."""
        return len(self._cache)

    def clear_cache(self) -> None:
        """Drop all materialized spectra (the index stays)."""
        self._cache.clear()

    def _flat_axes(self) -> dict[str, np.ndarray]:
        return {name: np.asarray(values) for name, values in self.axes.items()}

    def _coordinates_of_flat(self, flat_index: int) -> tuple:
        """Inverse of the C-order ravel over the axis product."""
        values = [list(v) for v in self.axes.values()]
        sizes = [len(v) for v in values]
        strides = np.concatenate([np.cumprod(sizes[::-1])[::-1][1:], [1]])
        return tuple(
            values[pos][int(flat_index // strides[pos]) % sizes[pos]]
            for pos in range(len(values))
        )

    def load_node(self, key: tuple) -> Spectrum:
        """Materialize (and cache) the spectrum at one coordinate tuple."""
        if key in self._cache:
            return self._cache[key]
        try:
            set_, node = self._node_of_key[key]
        except KeyError:
            raise GridPointNotFoundError(
                f"no spectrum at {dict(zip(self._selectable, key))}; "
                f"available axes: {self.axes}"
            ) from None
        spectrum = set_.load(node)
        self._cache[key] = spectrum
        return spectrum

    @property
    def provenance(self) -> Provenance:
        """The merged provenance of every set the grid holds."""
        return Provenance(
            data_sources=tuple(
                src for s in self._sets for src in s.provenance.data_sources
            ),
            assets=tuple(a for s in self._sets for a in s.provenance.assets),
            source_chains=tuple(
                c for s in self._sets for c in s.provenance.source_chains
            ),
            scientific_references=self._sets[0].provenance.scientific_references,
            software_references=self._sets[0].provenance.software_references,
            transformations=tuple(
                t for s in self._sets for t in s.provenance.transformations
            ),
        )

    def describe(self) -> str:
        """A human-readable summary: identity, axes, holes, cache size."""
        lines = [
            f"{type(self).__name__}: {self.identity.label}",
            f"product: {self.identity.product}",
            f"nodes: {len(self)}",
        ]
        for name, values in self.axes.items():
            if values:
                shown = (
                    values
                    if len(values) <= 8
                    else f"{values[0]}..{values[-1]} ({len(values)} values)"
                )
                lines.append(f"axis {name}: {shown}")
        lines.append(f"holes in axis product: {len(self.holes)}")
        lines.append(f"loaded spectra: {self.load_cache_size()}")
        return "\n".join(lines)

    # -- node selection -----------------------------------------------------

    def get_spectrum(self, method: str = "exact", **coordinates) -> Spectrum:
        """Select one node's spectrum by grid coordinates.

        Parameters
        ----------
        method
            ``"exact"``: every requested coordinate must match an available
            grid point (``GridPointNotFoundError`` otherwise). ``"nearest"``:
            each coordinate snaps to the nearest available value
            (``NearestGridPointWarning`` when any snapping occurred).
        **coordinates
            Any of the selectable coordinates; unspecified coordinates are
            unconstrained only when exactly one node remains — otherwise
            the missing axes must be pinned.

        The answer is the published node's data — a
        :class:`~stellarlib.spectrum.NodeSpectrum` when the loader builds
        one — annotated with ``selection_info``; unlike ``spectrum()`` this
        never interpolates and leaves ``last_record`` untouched.
        """
        if method not in ("exact", "nearest"):
            raise ValueError(f"method must be 'exact' or 'nearest', got {method!r}")

        requested = dict(coordinates)
        unknown = set(requested) - set(self._selectable)
        if unknown:
            raise ValueError(
                f"unknown selection coordinates {sorted(unknown)}; "
                f"selectable: {list(self._selectable)}"
            )
        if method == "exact":
            return self._select_exact(requested)
        return self._select_nearest(requested)

    def _select_exact(self, requested: dict) -> Spectrum:
        """The exact path: pin degenerate axes, demand the node."""
        filled = dict(requested)
        for name in self._selectable:
            if name in filled:
                continue
            values = self.axes[name]
            if len(values) == 1:
                filled[name] = values[0]
        still_missing = [
            k
            for k in self._selectable
            if k not in filled and len(self.axes[k]) > 1
        ]
        if still_missing:
            raise GridPointNotFoundError(
                f"coordinates {still_missing} have several values in this "
                f"grid and were not pinned; available axes: {self.axes}"
            )
        key = tuple(filled.get(k) for k in self._selectable)
        if key not in self._node_of_key:
            if key in self.holes:
                hole = dict(zip(self._selectable, key))
                raise GridHoleError(
                    f"no published node at {hole} (a hole in the axis "
                    f"rectangle); the grid's axes list what exists — "
                    "see grid.holes"
                )
            raise GridPointNotFoundError(
                f"no spectrum at {filled}; available axes: {self.axes}"
            )
        spectrum = self.load_node(key)
        return spectrum.with_selection_info(
            {"method": "exact", "requested": dict(requested), "returned": filled}
        )

    def _select_nearest(self, requested: dict) -> Spectrum:
        """The nearest path: snap each coordinate, demand one candidate."""
        snapped = {}
        distances = {}
        for name, value in requested.items():
            available = np.asarray(self.axes[name], dtype=float)
            if len(available) == 0:
                raise GridPointNotFoundError(f"axis {name!r} has no values")
            i = int(np.argmin(np.abs(available - float(value))))
            snapped[name] = (
                available[i].item() if available.dtype.kind == "f" else available[i]
            )
            distances[name] = float(abs(available[i] - float(value)))

        candidates = [
            key
            for key in self._node_of_key
            if all(
                key[self._selectable.index(name)] == value
                for name, value in snapped.items()
            )
        ]
        if len(candidates) == 1:
            key = candidates[0]
        elif not candidates:
            raise GridPointNotFoundError(
                f"no spectrum at snapped coordinates {snapped} "
                f"(requested {requested}); available axes: {self.axes}"
            )
        else:
            unpinned = [k for k in self._selectable if k not in requested]
            raise GridPointNotFoundError(
                f"snapped coordinates {snapped} match {len(candidates)} "
                f"spectra; pin one of {unpinned}"
            )

        spectrum = self.load_node(key)
        info: dict[str, Any] = {
            "method": "nearest",
            "requested": dict(requested),
            "returned": dict(zip(self._selectable, key)),
            "distances": distances,
        }
        if any(d > 0 for d in distances.values()):
            warnings.warn(
                f"nearest grid point used: requested {requested}, "
                f"returned {info['returned']}, distances {distances}",
                NearestGridPointWarning,
                stacklevel=2,
            )
        return spectrum.with_selection_info(info)

    # -- point queries --------------------------------------------------------

    @property
    def _empirical(self) -> bool:
        """Whether the grid's family is an empirical library.

        Empirical families (observed stars, arbitrary per-star
        normalizations) refuse cross-node interpolation; the family's
        backend entry states it (model knowledge lives in the registry,
        not in ``if family == ...`` branches here).
        """
        backend = BACKENDS.get(self.identity.family)
        return backend is not None and bool(getattr(backend, "empirical", False))

    def node(self, **coordinates) -> NodeSpectrum:
        """The published node exactly at the given coordinates (strict).

        Every requested coordinate must land exactly on a published node
        value of its axis (single-valued axes may be omitted, as in
        ``spectrum``). A coordinate between node values raises
        :class:`GridPointNotFoundError` naming the axis; a combination
        whose values all exist but whose node is unpublished raises
        :class:`GridHoleError`. The answer is the node's own spectrum —
        a :class:`~stellarlib.spectrum.NodeSpectrum` — with no
        interpolation, no record, and no warning; it is the strict
        counterpart of :meth:`spectrum`, which interpolates and always
        answers an :class:`~stellarlib.spectrum.InterpSpectrum`.
        """
        requested = self._validated_coordinates(coordinates)
        for name in self._selectable:
            if requested[name] not in self.axes[name]:
                raise GridPointNotFoundError(
                    f"{name}={requested[name]!r} is not a published node "
                    f"value of this grid (available: {self.axes[name]}); "
                    "node() answers exact nodes only — spectrum() "
                    "interpolates between them"
                )
        key = tuple(requested[name] for name in self._selectable)
        if key not in self._node_of_key:
            hole = dict(zip(self._selectable, key))
            raise GridHoleError(
                f"no published node at {hole} (a hole in the axis "
                f"rectangle); the grid's axes list what exists — "
                "see grid.holes"
            )
        spectrum = self.load_node(key)
        if not isinstance(spectrum, NodeSpectrum):
            raise TypeError(
                f"the set's loader returned {type(spectrum).__name__}; a "
                "NodeSpectrum is required for the strict node interface"
            )
        return spectrum

    def spectrum(
        self,
        interp: str | None = None,
        range_policy: str = "strict",
        **coordinates,
    ) -> InterpSpectrum:
        """One spectrum at the given grid coordinates (interpolating).

        Parameters
        ----------
        interp
            ``"multilinear"`` (the default for model grids): rectangular
            corner interpolation of the bracket cell (linear in every
            axis, matching the libraries' own axis labeling).
            ``"voronoi"`` (the default for empirical libraries): the
            nearest published node (step-normalized distance) — the
            hole-tolerant path; the result is one node's data, flagged by
            ``VoronoiInterpolationWarning`` and in the record. Empirical
            libraries refuse ``"multilinear"`` outright.
        range_policy
            ``"strict"``: coordinates outside an axis range raise
            ``UnsafeInterpolationError`` (no extrapolation).
            ``"clamp"``: they clamp to the edge cell with
            ``ClampedInterpolationWarning`` and a record entry.
        **coordinates
            One scalar per selectable axis (single-valued axes may be
            omitted).

        The answer is always an
        :class:`~stellarlib.spectrum.InterpSpectrum` — including the
        degenerate cases. An exact node hit returns that node's spectrum
        unchanged with ``method="exact-node"`` and weight 1.0 on the
        node (no interpolation runs); a Voronoi answer returns the
        nearest node's data with the request and distance on the record.
        The record — the full audit trail of corner nodes, weights, and
        clamp state — rides on the returned object (``spec.record``,
        ``spec.corner_nodes``, ``spec.corner_weights``) and on
        ``self.last_record``.
        """
        if interp is None:
            interp = "voronoi" if self._empirical else "multilinear"
        if interp == "multilinear" and self._empirical:
            backend = BACKENDS[self.identity.family]
            raise ValueError(
                f"interp='multilinear' is not defined for {self.identity.family}: "
                f"{backend.empirical_note}; use interp='voronoi' (the "
                "nearest parameterized star)"
            )
        if interp not in ("multilinear", "voronoi"):
            raise ValueError(
                f"interp must be 'multilinear' or 'voronoi', got {interp!r}"
            )

        requested = self._validated_coordinates(coordinates)
        axes_names = self._selectable

        # exact node hit: no interpolation at all
        try_key = tuple(
            requested.get(name, self.axes[name][0]) for name in axes_names
        )
        if try_key in self._node_of_key:
            record = self._record(
                method="exact-node",
                axes=axes_names,
                requested=(dict(zip(axes_names, try_key)),),
                node_indices=((-1,),),
                node_values=(
                    tuple(dict(zip(axes_names, try_key)) for _ in [try_key]),
                ),
                weights=((1.0,),),
                clamped=(dict.fromkeys(axes_names, False),),
            )
            answer = InterpSpectrum.from_node(self.load_node(try_key), record)
            answer.selection_info = {
                "method": "exact-node",
                "requested": dict(zip(axes_names, try_key)),
                "returned": dict(zip(axes_names, try_key)),
            }
            return answer

        if interp == "voronoi":
            return self._voronoinate(requested)
        return self._multilinear(requested, range_policy)

    def get_flux(
        self,
        field: str = "surface_flux",
        interp: str | None = None,
        range_policy: str = "strict",
        **coordinates,
    ) -> np.ndarray:
        """Unified flux field at the given coordinates (default F_lambda)."""
        spectrum = self.spectrum(
            interp=interp, range_policy=range_policy, **coordinates
        )
        return spectrum.unified[field]

    # -- raw corner mechanism --------------------------------------------------

    def corner_weights(self, **queries) -> tuple[np.ndarray, np.ndarray, tuple]:
        """The rectangular corner mechanism over this grid's axes.

        Returns ``(idx, w, clamped)`` with ``idx``/``w`` of shape
        ``(n, 2^N)``: flat indices into the C-order ravel of the axis
        product and multilinear weights (rows sum to 1), plus one boolean
        clamp mask per axis. Queries whose bracket cell contains a hole
        raise :class:`GridHoleError` (use ``interp="voronoi"`` there). The
        indices pair with :meth:`corner_spectrum`.
        """
        self._validated_coordinates(
            dict.fromkeys(queries, 0.0), require_all=False
        )
        idx, w, clamped = corner_weights(self.axes, **queries)
        per_query = np.atleast_1d(np.asarray(next(iter(queries.values())), dtype=float))
        n = per_query.size
        for q in range(n):
            corners = idx[q]
            missing = [
                self._coordinates_of_flat(int(flat))
                for flat in corners
                if self._coordinates_of_flat(int(flat)) not in self._node_of_key
            ]
            if missing:
                raise GridHoleError(
                    f"the bracket cell of query {q} contains "
                    f"{len(missing)} missing node(s) "
                    f"{[dict(zip(self._selectable, m)) for m in missing]}; "
                    "rectangular interpolation is impossible there — use "
                    "interp='voronoi' or select a hole-free grid"
                )
        return idx, w, clamped

    def corner_spectrum(self, flat_indices: Iterable[int]) -> np.ndarray:
        """Flux rows of nodes by flat index into the axis product."""
        rows = []
        for flat in flat_indices:
            key = self._coordinates_of_flat(int(flat))
            if key not in self._node_of_key:
                raise GridHoleError(
                    f"flat index {flat} names the missing node "
                    f"{dict(zip(self._selectable, key))}"
                )
            rows.append(self.load_node(key).native.flux)
        return np.asarray(rows)

    # -- query internals ---------------------------------------------------------

    def _validated_coordinates(
        self, coordinates: dict, require_all: bool = True
    ) -> dict:
        unknown = set(coordinates) - set(self._selectable)
        if unknown:
            raise ValueError(
                f"unknown coordinates {sorted(unknown)}; "
                f"selectable: {list(self._selectable)}"
            )
        filled = {}
        for name in self._selectable:
            if name in coordinates:
                value = coordinates[name]
                if not np.isscalar(value):
                    raise ValueError(
                        f"coordinate {name!r} must be scalar for point "
                        "queries; use corner_weights for vector queries"
                    )
                filled[name] = float(value)
            elif len(self.axes[name]) == 1:
                filled[name] = float(self.axes[name][0])
            elif require_all:
                raise ValueError(
                    f"coordinate {name!r} is required (it has several values "
                    f"in this grid: {self.axes[name]})"
                )
        return filled

    def _record(self, **kwargs) -> InterpolationRecord:
        record = InterpolationRecord(**kwargs)
        self.last_record = record
        return record

    def _voronoinate(self, requested: dict) -> InterpSpectrum:
        axes = self.axes
        keys = list(self._node_of_key)
        query = [tuple(requested[name] for name in self._selectable)]
        indices, distances, scales = voronoi_nearest(
            {name: axes[name] for name in self._selectable}, keys, query
        )
        node_key = keys[int(indices[0])]
        node_spectrum = self.load_node(node_key)
        warnings.warn(
            f"Voronoi interpolation: returning the nearest published node "
            f"{dict(zip(self._selectable, node_key))} for request "
            f"{dict(requested)} (grid-step-normalized distance "
            f"{float(distances[0]):.3g}); the result is one node's data, "
            "not an interpolation",
            VoronoiInterpolationWarning,
            stacklevel=3,
        )
        record = self._record(
            method="voronoi",
            axes=self._selectable,
            requested=(dict(requested),),
            node_indices=((int(indices[0]),),),
            node_values=((dict(zip(self._selectable, node_key)),),),
            weights=((1.0,),),
            clamped=(dict.fromkeys(self._selectable, False),),
            note=(
                "nearest published node; distances normalized per axis by "
                f"the median grid step {scales}"
            ),
        )
        answer = InterpSpectrum.from_node(node_spectrum, record)
        answer.selection_info = {
            "method": "voronoi",
            "requested": dict(requested),
            "returned": dict(zip(self._selectable, node_key)),
            "distance_grid_steps": float(distances[0]),
        }
        return answer

    def _multilinear(
        self, requested: dict, range_policy: str
    ) -> InterpSpectrum:
        if range_policy not in ("strict", "clamp"):
            raise ValueError(
                f"range_policy must be 'strict' or 'clamp', got {range_policy!r}"
            )
        axes_arrays = {
            name: np.asarray(values) for name, values in self.axes.items()
        }
        idx, w, clamped = corner_weights(
            axes_arrays,
            **{name: requested[name] for name in self._selectable},
        )
        any_clamped = {name: bool(mask[0]) for name, mask in zip(self._selectable, clamped)}
        if any(any_clamped.values()):
            if range_policy == "strict":
                bad = {k: v for k, v in requested.items() if any_clamped[k]}
                ranges = {
                    k: (axes_arrays[k][0].item(), axes_arrays[k][-1].item())
                    for k in bad
                }
                raise UnsafeInterpolationError(
                    f"coordinates {bad} fall outside the grid ranges {ranges}; "
                    "extrapolation is not performed — use "
                    "range_policy='clamp' to clamp to the edge cell"
                )
            warnings.warn(
                f"coordinates clamped to the grid's edge cell: "
                f"{ {k: requested[k] for k, v in any_clamped.items() if v} }",
                ClampedInterpolationWarning,
                stacklevel=3,
            )

        corner_keys = [
            self._coordinates_of_flat(int(flat)) for flat in idx[0]
        ]
        missing = [k for k in corner_keys if k not in self._node_of_key]
        if missing:
            raise GridHoleError(
                f"the bracket cell of {dict(requested)} contains missing "
                f"node(s) {[dict(zip(self._selectable, m)) for m in missing]}; "
                "rectangular interpolation is impossible there — use "
                "interp='voronoi' or select a hole-free grid"
            )

        corners = [self.load_node(k) for k in corner_keys]
        first = corners[0]
        wavelength = first.native.wavelength
        if wavelength is not None:
            for corner in corners[1:]:
                other = corner.native.wavelength
                if other is None or not np.array_equal(wavelength, other):
                    raise UnsafeInterpolationError(
                        "corner spectra do not share one wavelength grid; "
                        "refusing to interpolate across mismatched grids"
                    )
        flux_stack = np.asarray([c.native.flux for c in corners])
        weights = w[0]
        out_flux = weights @ flux_stack
        has_continuum = [c.native.continuum is not None for c in corners]
        if all(has_continuum):
            cont_stack = np.asarray([c.native.continuum for c in corners])
            out_continuum = weights @ cont_stack
        elif any(has_continuum):
            raise UnsafeInterpolationError(
                "corner spectra disagree on continuum presence; refusing "
                "to interpolate"
            )
        else:
            out_continuum = None

        merged = Provenance(
            data_sources=tuple(
                dict.fromkeys(
                    src for c in corners for src in c.provenance.data_sources
                )
            ),
            assets=tuple(
                dict.fromkeys(
                    a for c in corners for a in c.provenance.assets
                )
            ),
            source_chains=first.provenance.source_chains,
            scientific_references=first.provenance.scientific_references,
            software_references=first.provenance.software_references,
            transformations=first.provenance.transformations,
        )
        node_descriptions = ", ".join(
            str(dict(zip(self._selectable, k))) for k in corner_keys
        )
        transformation = (
            f"multilinear node interpolation at {dict(requested)} over "
            f"bracket-cell corners [{node_descriptions}]"
        )
        native = NativeSpectrum(
            wavelength=None if wavelength is None else np.asarray(wavelength),
            flux=out_flux,
            continuum=out_continuum,
            header=(),
            metadata={
                "derived": True,
                "derivation": transformation,
                "nodes": node_descriptions,
            },
            provenance_keys={
                "source_files": [c.native.provenance_keys.get("file") for c in corners]
            },
        )
        merged = dataclasses.replace(
            merged, transformations=(*merged.transformations, transformation)
        )
        record = self._record(
            method="multilinear",
            axes=self._selectable,
            requested=(dict(requested),),
            node_indices=(tuple(int(i) for i in idx[0]),),
            node_values=(tuple(dict(zip(self._selectable, k)) for k in corner_keys),),
            weights=(tuple(float(x) for x in weights),),
            clamped=(any_clamped,),
            note=f"corners: {node_descriptions}",
        )
        return InterpSpectrum(
            native=native,
            identity=first.identity,
            parameters=first.parameters,
            field_specs=dict(first.field_specs),
            provenance=merged,
            selection=dict(requested),
            unified_view=first.unified.with_native(native),
            record=record,
        )


def open_grid(
    path: str | os.PathLike,
    model: str | None = None,
    **reader_options,
) -> SpectrumGrid:
    """Open a local grid from a file or directory of files.

    With ``model`` given (family only, ``"bosz"``, or version-qualified,
    ``"bosz/2024"``, ``"tlusty/OSTAR2002"``), the corresponding backend
    reads every file it recognizes under ``path`` (``reader_options`` pass
    through, for example ``wave_path`` for BOSZ wavelength sidecars); a
    stated release is validated against the files' own identity. Without
    ``model``, every registered backend sniffs the sources; exactly one
    must claim them (``AmbiguousFormatError`` otherwise,
    ``UnrecognizedFormatError`` if none).

    The returned grid carries the full query interface — ``node()`` for
    strict published-node access, ``spectrum()`` for interpolating
    queries — not just the node index.
    """

    p = Path(path)
    if model is not None:
        family, release = parse_model_spec(model)
        grid = (
            SpectrumGrid.from_directory(family, p, **reader_options)
            if p.is_dir()
            else SpectrumGrid.from_paths(family, [p], **reader_options)
        )
        if release is not None and grid.identity.release != release:
            raise ValueError(
                f"model {model!r} does not match the data: the files state "
                f"release {grid.identity.release!r}"
            )
        return grid

    paths = (
        sorted(q for q in p.rglob("*") if q.is_file() and not q.name.startswith("."))
        if p.is_dir()
        else [p]
    )
    claiming = [
        name
        for name, backend in BACKENDS.items()
        if any(backend.recognize(q) for q in paths)
    ]
    if len(claiming) == 0:
        raise UnrecognizedFormatError(f"no backend recognizes {path}")
    if len(claiming) > 1:
        raise AmbiguousFormatError(
            f"backends {claiming} all claim {path}; pass model= explicitly"
        )
    return (
        SpectrumGrid.from_directory(claiming[0], p, **reader_options)
        if p.is_dir()
        else SpectrumGrid.from_paths(claiming[0], paths, **reader_options)
    )
