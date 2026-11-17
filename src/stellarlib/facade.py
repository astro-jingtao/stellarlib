"""The unified facade: family-specific grid objects and ``construct()``.

``UnifiedSpectra`` is the abstract face of a loaded library product: point
queries ``spectrum(teff=..., log_surface_gravity=..., m_h=...)`` that
combine exact-node selection, multilinear interpolation over the bracket
cell's corners, or Voronoi nearest-node assignment for grids with holes —
every path leaving an :class:`~stellarlib.interp.InterpolationRecord` on
``last_record``. ``corner_weights``/``corner_spectrum`` expose the raw
rectangular mechanism for sparse-matrix consumers. ``construct()`` builds
the right subclass from local paths or from a built database.
"""

from __future__ import annotations

import dataclasses
import warnings
from abc import ABC
from pathlib import Path
from typing import Iterable

import numpy as np

from stellarlib.exceptions import GridHoleError, UnsafeInterpolationError
from stellarlib.grid import SpectrumGrid
from stellarlib.interp import InterpolationRecord, corner_weights, voronoi_nearest
from stellarlib.native import NativeSpectrum
from stellarlib.provenance import Provenance
from stellarlib.spectrum import Spectrum, SpectrumSet
from stellarlib.warnings import (
    ClampedInterpolationWarning,
    VoronoiInterpolationWarning,
)

__all__ = [
    "UnifiedSpectra",
    "BoszSpectra",
    "TlustySpectra",
    "MilesSpectra",
    "construct",
]


class UnifiedSpectra(SpectrumGrid, ABC):
    """Abstract unified interface over one library's spectral product.

    Concrete subclasses declare ``model_name`` and inherit the generic
    implementation; the per-family behavior lives in the backend the grid
    was built from.
    """

    model_name: str = ""

    def __init__(self, sets: Iterable[SpectrumSet]):
        super().__init__(sets)
        self.last_record: InterpolationRecord | None = None

    # -- point queries --------------------------------------------------------

    def spectrum(
        self,
        interp: str = "multilinear",
        range_policy: str = "strict",
        **coordinates,
    ) -> Spectrum:
        """One spectrum at the given grid coordinates.

        Parameters
        ----------
        interp
            ``"multilinear"``: rectangular corner interpolation of the
            bracket cell (linear in every axis, matching the libraries'
            own axis labeling). ``"voronoi"``: the nearest published node
            (step-normalized distance) — the hole-tolerant path; the
            result is one node's data, flagged by
            ``VoronoiInterpolationWarning`` and in the record.
        range_policy
            ``"strict"``: coordinates outside an axis range raise
            ``UnsafeInterpolationError`` (no extrapolation).
            ``"clamp"``: they clamp to the edge cell with
            ``ClampedInterpolationWarning`` and a record entry.
        **coordinates
            One scalar per selectable axis (single-valued axes may be
            omitted). An exact node hit returns that node's spectrum
            unchanged (``method="exact-node"``).
        """
        requested = self._validated_coordinates(coordinates)
        axes_names = self._selectable

        # exact node hit: no interpolation at all
        try_key = tuple(
            requested.get(name, self.axes[name][0]) for name in axes_names
        )
        if try_key in self._node_of_key:
            record = InterpolationRecord(
                method="exact-node",
                axes=axes_names,
                requested=(dict(zip(axes_names, try_key)),),
                node_indices=((-1,),),
                node_values=(
                    tuple(dict(zip(axes_names, try_key)) for _ in [try_key]),
                ),
                weights=((1.0,),),
                clamped=({name: False for name in axes_names},),
            )
            self.last_record = record
            return self.load_node(try_key).with_selection_info(
                {
                    "method": "exact-node",
                    "requested": dict(zip(axes_names, try_key)),
                    "returned": dict(zip(axes_names, try_key)),
                }
            )

        if interp == "voronoi":
            return self._voronoinate(requested)
        if interp != "multilinear":
            raise ValueError(
                f"interp must be 'multilinear' or 'voronoi', got {interp!r}"
            )
        return self._multilinear(requested, range_policy)

    def get_flux(
        self,
        field: str = "surface_flux",
        interp: str = "multilinear",
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
            {k: 0.0 for k in queries}, require_all=False
        )
        idx, w, clamped = corner_weights(self.axes, **queries)
        n_axes = len(self._selectable)
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

    # -- internals --------------------------------------------------------------

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

    def _voronoinate(self, requested: dict) -> Spectrum:
        axes = self.axes
        keys = list(self._node_of_key)
        query = [tuple(requested[name] for name in self._selectable)]
        indices, distances, scales = voronoi_nearest(
            {name: axes[name] for name in self._selectable}, keys, query
        )
        node_key = keys[int(indices[0])]
        spectrum = self.load_node(node_key)
        warnings.warn(
            f"Voronoi interpolation: returning the nearest published node "
            f"{dict(zip(self._selectable, node_key))} for request "
            f"{dict(requested)} (grid-step-normalized distance "
            f"{float(distances[0]):.3g}); the result is one node's data, "
            "not an interpolation",
            VoronoiInterpolationWarning,
            stacklevel=3,
        )
        self._record(
            method="voronoi",
            axes=self._selectable,
            requested=(dict(requested),),
            node_indices=((int(indices[0]),),),
            node_values=((dict(zip(self._selectable, node_key)),),),
            weights=((1.0,),),
            clamped=({name: False for name in self._selectable},),
            note=(
                "nearest published node; distances normalized per axis by "
                f"the median grid step {scales}"
            ),
        )
        return spectrum.with_selection_info(
            {
                "method": "voronoi",
                "requested": dict(requested),
                "returned": dict(zip(self._selectable, node_key)),
                "distance_grid_steps": float(distances[0]),
            }
        )

    def _multilinear(self, requested: dict, range_policy: str) -> Spectrum:
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
            merged, transformations=merged.transformations + (transformation,)
        )
        self._record(
            method="multilinear",
            axes=self._selectable,
            requested=(dict(requested),),
            node_indices=(tuple(int(i) for i in idx[0]),),
            node_values=(tuple(dict(zip(self._selectable, k)) for k in corner_keys),),
            weights=(tuple(float(x) for x in weights),),
            clamped=(any_clamped,),
            note=f"corners: {node_descriptions}",
        )
        return Spectrum(
            native=native,
            identity=first.identity,
            parameters=first.parameters,
            field_specs=dict(first.field_specs),
            provenance=merged,
            selection=dict(requested),
            unified_view=first.unified.with_native(native),
        )

    # -- introspection ---------------------------------------------------------

    def describe(self) -> str:
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


class BoszSpectra(UnifiedSpectra):
    """BOSZ 2024 resampled spectra under the unified interface."""

    model_name = "bosz"

    @classmethod
    def construct(
        cls,
        paths=None,
        wave_path=None,
        database=None,
        label=None,
        collection=None,
        am=0.0,
        cm=0.0,
        vmicro=None,
    ) -> "BoszSpectra":
        """Load BOSZ spectra from local files/directories or a database.

        ``paths`` may be a directory (a mirror is read by selecting one
        composition: ``am``/``cm`` default 0, ``vmicro`` the family
        policy; the wavelength sidecar is discovered per the known
        layouts, or pinned with ``wave_path``), files, or both.
        ``database``/``label`` load a product from a database built with
        ``stellarlib.build_database`` — a mirror product needs its
        ``collection`` (e.g. ``"r500"``) when the metallicity label alone
        is ambiguous. ``wave_path`` pins the wavelength sidecar on
        either branch; left unset on the database branch, the sidecar is
        discovered across the database's products (mirror products hold
        no wave file of their own — the sidecars mirror as their own
        product).
        """
        if database is not None:
            selection = dict(am=am, cm=cm, vmicro=vmicro)
            # only a stated pin rides along: a None would occupy the
            # option and turn the database's cross-product sidecar
            # discovery off (grid treats a present key as a pin)
            if wave_path is not None:
                selection["wave_path"] = wave_path
            return cls(
                _database_grid(
                    "bosz/2024",
                    database,
                    label,
                    collection=collection,
                    **selection,
                ).sets
            )
        if paths is None:
            raise ValueError(
                "construct('bosz/2024') requires paths= of local files or "
                "database= of a built database"
            )
        grid = SpectrumGrid.from_paths(
            "bosz",
            _as_path_list(paths),
            wave_path=wave_path,
            am=am,
            cm=cm,
            vmicro=vmicro,
        )
        return cls(grid.sets)


class TlustySpectra(UnifiedSpectra):
    """TLUSTY OSTAR2002/BSTAR2006 optical spectra under the unified interface."""

    model_name = "tlusty"

    @classmethod
    def construct(
        cls,
        paths=None,
        release=None,
        database=None,
        label=None,
    ) -> "TlustySpectra":
        """Load TLUSTY spectra from local files/directories or a database.

        ``release`` (``"OSTAR2002"`` or ``"BSTAR2006"``) is inferred from
        the files' Teff span when omitted; a stated release is validated.
        """
        if database is not None:
            model = "tlusty" if release is None else f"tlusty/{release}"
            return cls(_database_grid(model, database, label).sets)
        if paths is None:
            raise ValueError(
                "construct('tlusty/<release>') requires paths= of local "
                "files or database= of a built database"
            )
        options = {} if release is None else {"release": release}
        grid = SpectrumGrid.from_paths("tlusty", _as_path_list(paths), **options)
        return cls(grid.sets)


class MilesSpectra(UnifiedSpectra):
    """MILES empirical stellar spectra under the unified interface.

    An empirical library is an observed scatter, not a model grid: each
    star's spectrum carries an arbitrary per-star normalization, so a
    weighted mix of two stars' fluxes is not a spectrum of anything.
    Point queries are therefore nearest-star only — the default
    ``interp="voronoi"`` returns the nearest parameterized star, warned
    and recorded; ``interp="multilinear"`` is refused outright.
    """

    model_name = "miles"

    def spectrum(
        self,
        interp: str = "voronoi",
        range_policy: str = "strict",
        **coordinates,
    ) -> Spectrum:
        if interp == "multilinear":
            raise ValueError(
                "interp='multilinear' is not defined for MILES: the stars "
                "are an empirical scatter whose spectra carry arbitrary "
                "per-star normalizations — a weighted mix is not a "
                "spectrum of anything; use interp='voronoi' (the nearest "
                "parameterized star)"
            )
        return super().spectrum(
            interp=interp, range_policy=range_policy, **coordinates
        )

    @classmethod
    def construct(
        cls,
        paths=None,
        params_path=None,
        database=None,
        label=None,
    ) -> "MilesSpectra":
        """Load MILES spectra from the library tarball, an extracted
        directory or file list (``params_path=`` pins the parameter
        table; it is discovered among the sources when omitted), or from
        a database built with ``stellarlib.build_database`` (the library
        is one product; ``label`` is accepted for uniformity and is
        always ``"all"``)."""
        if database is not None:
            return cls(_database_grid("miles/2011", database, label).sets)
        if paths is None:
            raise ValueError(
                "construct('miles/2011') requires paths= of the library "
                "tarball, extracted files, or database= of a built database"
            )
        options = {} if params_path is None else {"params_path": params_path}
        grid = SpectrumGrid.from_paths(
            "miles", _as_path_list(paths), **options
        )
        return cls(grid.sets)


def _database_grid(
    model: str, database, label, collection=None, **selection
) -> SpectrumGrid:
    from stellarlib.database.core import open_database

    db = database if not isinstance(database, (str, Path)) else open_database(database)
    if "/" in model:
        return db.grid(model, label=label, collection=collection, **selection)
    # an unqualified family ("tlusty", release left to inference): the
    # database states the release itself — the label must pin one
    # product across the family's releases, the same pinning
    # Database.grid applies within one release
    family = model
    releases = sorted(
        p.release for p in db.products_for(family) if label is None or p.label == label
    )
    if len(set(releases)) != 1:
        raise ValueError(
            f"model {model!r} is not version-qualified and label {label!r} "
            f"does not pin one release (products: {releases or 'none'}); "
            f"state the release, e.g. {family}/<release>"
        )
    return db.grid(
        f"{family}/{releases[0]}", label=label, collection=collection, **selection
    )


def _as_path_list(paths) -> list:
    if isinstance(paths, (str, Path)):
        return [paths]
    return list(paths)


_REGISTRY = {"bosz": BoszSpectra, "tlusty": TlustySpectra, "miles": MilesSpectra}


def construct(
    model: str,
    paths=None,
    database=None,
    label: str | None = None,
    **family_options,
) -> UnifiedSpectra:
    """Build the unified point-query facade for one library product.

    ``model`` is version-qualified: ``"bosz/2024"``,
    ``"tlusty/OSTAR2002"``. The release part is validated against what
    the files state about themselves — a mismatch fails loudly.

    Data comes either from ``paths`` (files or directories you hold;
    ``family_options`` pass through, e.g. ``wave_path`` for BOSZ) or from
    a ``database`` built with ``stellarlib.build_database`` (use
    ``label`` to pick one product when a release publishes several);
    never from the network.
    """
    from stellarlib.exceptions import UnsupportedModelError
    from stellarlib.models import parse_model_spec

    family, release = parse_model_spec(model)
    if release is None:
        raise ValueError(
            f"model must be version-qualified, e.g. 'bosz/2024'; got {model!r}"
        )
    try:
        cls = _REGISTRY[family]
    except KeyError:
        raise UnsupportedModelError(
            f"no unified class registered for model {model!r}; "
            f"registered families: {sorted(_REGISTRY)}"
        ) from None

    obj = cls.construct(paths=paths, database=database, label=label, **family_options)
    if obj.identity.release != release:
        raise ValueError(
            f"model {model!r} does not match the data: the files state "
            f"release {obj.identity.release!r}"
        )
    return obj
