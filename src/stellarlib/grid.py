"""SpectrumGrid and open_grid: queryable collections of library nodes.

A grid holds the lightweight node index of one product (one directory of
files of the same model, release, and physics configuration). Spectra are
loaded on demand and cached. Selection supports ``exact`` (fail loudly
when absent) and ``nearest`` (warn, record requested vs returned values
and distances). The grid reports the truth about its coverage: ``axes``
lists the values that actually exist, and ``holes`` lists the cells of
the axis product that are missing — never silently filled.
"""

from __future__ import annotations

from collections.abc import Iterable
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
)
from stellarlib.spectrum import GridNode, Spectrum, SpectrumSet
from stellarlib.warnings import NearestGridPointWarning

__all__ = ["SpectrumGrid", "open_grid"]


class SpectrumGrid:
    """All nodes of one library product, queryable by grid coordinates.

    The selectable coordinates come from the sets' backend-pinned order
    (BOSZ: teff, log_surface_gravity, m_h; TLUSTY: teff,
    log_surface_gravity). Node spectra are loaded lazily through the
    owning set's loader and cached on the grid
    (``load_cache_size()``/``clear_cache()``).
    """

    def __init__(self, sets: Iterable[SpectrumSet]):
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

    # -- construction -------------------------------------------------------

    @classmethod
    def from_paths(
        cls, model: str, paths: Iterable[str | os.PathLike], **reader_options
    ) -> SpectrumGrid:
        from stellarlib.models import get_backend

        backend = get_backend(model)
        path_list = [Path(p) for p in paths]
        if not path_list:
            raise ValueError("paths must not be empty")
        return cls([backend.read(path_list, **reader_options)])

    @classmethod
    def from_directory(
        cls, model: str, directory: str | os.PathLike, **reader_options
    ) -> SpectrumGrid:
        from stellarlib.models import get_backend

        backend = get_backend(model)
        root = Path(directory)
        if not root.is_dir():
            raise NotADirectoryError(root)
        return cls([backend.read(root, **reader_options)])

    # -- introspection ------------------------------------------------------

    @property
    def sets(self) -> tuple[SpectrumSet, ...]:
        return self._sets

    @property
    def selectable(self) -> tuple[str, ...]:
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
        return len(self._node_of_key) != int(
            np.prod([len(v) for v in self.axes.values()])
        )

    def load_cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
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
    def provenance(self):
        from stellarlib.provenance import Provenance

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

    # -- selection ----------------------------------------------------------

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
        """
        if method not in ("exact", "nearest"):
            raise ValueError(f"method must be 'exact' or 'nearest', got {method!r}")

        requested = {k: v for k, v in coordinates.items()}
        unknown = set(requested) - set(self._selectable)
        if unknown:
            raise ValueError(
                f"unknown selection coordinates {sorted(unknown)}; "
                f"selectable: {list(self._selectable)}"
            )

        if method == "exact":
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

        # nearest: snap each requested coordinate independently
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
    """
    from stellarlib.models import parse_model_spec

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

    from stellarlib.models import BACKENDS

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
