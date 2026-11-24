"""Spectrum classes, SpectrumSet, and GridNode: the per-node and per-product objects.

The spectrum class family separates how a spectrum was reached:
a :class:`NodeSpectrum` is one published library node — one file, one
parameter point — exposing both the native arrays it was parsed into and
the unified view mapped onto it; an :class:`InterpSpectrum` is one grid
query's answer, traceable to the exact nodes and weights that produced it
through its :attr:`~InterpSpectrum.record`. Both share the :class:`Spectrum`
base, so every layer access (``native``, ``unified``, ``to_table``) reads
identically. A :class:`GridNode` is the lightweight index entry for one
node (coordinates, path, facts); a :class:`SpectrumSet` is the nodes of one
coherent product plus a loader that materializes nodes on demand, so a grid
of hundreds of gzip files stays cheap until spectra are actually asked for.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from astropy.table import Table

from stellarlib.provenance import Provenance
from stellarlib.spectra.core import FieldSpec, ModelIdentity, ParameterSpec
from stellarlib.spectra.native import NativeSpectrum
from stellarlib.spectra.unified import UnifiedView

if TYPE_CHECKING:
    from stellarlib.grids.interp import InterpolationRecord

__all__ = [
    "GridNode",
    "InterpSpectrum",
    "NodeSpectrum",
    "Spectrum",
    "SpectrumSet",
]


@dataclasses.dataclass(frozen=True)
class GridNode:
    """One grid point's lightweight index entry.

    ``coordinates`` are the node's values on the selectable axes (for
    example ``{"teff": 4000.0, "log_surface_gravity": 1.5, "m_h": 0.0}``);
    ``facts`` carries everything else the filename or header states (the
    BOSZ atmosphere family token, composition values, resolution). The
    spectrum data itself is loaded on demand through the owning set's
    loader, never at index time.
    """

    coordinates: Mapping[str, Any]
    path: Path
    facts: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    checksum: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinates", dict(self.coordinates))
        object.__setattr__(self, "facts", dict(self.facts))

    @property
    def filename(self) -> str:
        """The node's file name (the tar member name for archive sets)."""
        return self.path.name

    def key(self, selectable: Iterable[str]) -> tuple:
        """The node's coordinate tuple in the given selectable order."""
        return tuple(self.coordinates[name] for name in selectable)


class Spectrum:
    """Base of the spectrum class family: layers plus metadata.

    Do not construct this directly: backends construct
    :class:`NodeSpectrum` (one published node) and the grid query paths
    construct :class:`InterpSpectrum` (one query's answer). The class of
    a spectrum states how it was reached — published node or query
    result — and everything else (the ``native``/``unified`` layers, the
    identity, parameters, field specs, provenance) is shared behavior.
    """

    def __init__(
        self,
        native: NativeSpectrum,
        *,
        identity: ModelIdentity,
        parameters: tuple[ParameterSpec, ...] = (),
        field_specs: Mapping[str, FieldSpec] | None = None,
        provenance: Provenance | None = None,
        selection: Mapping[str, Any] | None = None,
        unified_view: Any = None,
    ) -> None:
        """Hold the layers and metadata; built by backends, not by users."""
        self._native = native
        self.identity = identity
        self.parameters = tuple(parameters)
        self.field_specs: Mapping[str, FieldSpec] = dict(field_specs or {})
        self.provenance = provenance if provenance is not None else Provenance()
        self.selection: Mapping[str, Any] = dict(selection or {})
        self.selection_info: Mapping[str, Any] | None = None
        self._unified_view = unified_view

    def _clone_kwargs(self) -> dict:
        """Constructor keyword arguments reproducing this spectrum.

        Subclasses append their own state (the interpolation record);
        derived-product helpers (``resample_wavelengths``, selection
        annotation) rebuild through this so the class of a derived
        spectrum matches its source.
        """
        return {
            "native": self._native,
            "identity": self.identity,
            "parameters": self.parameters,
            "field_specs": dict(self.field_specs),
            "provenance": self.provenance,
            "selection": dict(self.selection),
            "unified_view": self._unified_view,
        }

    def with_selection_info(self, info: Mapping[str, Any]) -> Spectrum:
        """A shallow copy carrying grid-selection metadata.

        The native spectrum, identity, parameters, and unified view are
        shared with this object (no data is copied); only the selection
        metadata is new, so annotating one selection never contaminates
        another consumer of the same cached spectrum. The clone keeps
        this spectrum's class.
        """
        clone = type(self)(**self._clone_kwargs())
        clone.selection_info = dict(info)
        return clone

    # -- layers ------------------------------------------------------------

    @property
    def native(self) -> NativeSpectrum:
        """The verbatim parsed arrays (the native layer)."""
        return self._native

    @property
    def unified(self) -> UnifiedView:
        """The unified view over the native arrays."""
        if self._unified_view is None:
            raise AttributeError(
                "no unified view is attached; the backend that produced this "
                "spectrum did not build one"
            )
        return self._unified_view

    # -- parameter access ----------------------------------------------------

    def parameter(self, name: str) -> ParameterSpec:
        """One parameter's spec by name."""
        for spec in self.parameters:
            if spec.name == name:
                return spec
        raise KeyError(f"no parameter named {name!r}")

    # -- unified field access -------------------------------------------------

    def get(self, name: str) -> Any:
        """Unified field values (same as ``self.unified[name]``)."""
        return self.unified[name]

    def has_field(self, name: str) -> bool:
        """Whether the product maps a unified field of this name."""
        return name in self.field_specs

    def field_info(self, name: str) -> FieldSpec:
        """The FieldSpec tracing unified field ``name`` back to native data."""
        try:
            return self.field_specs[name]
        except KeyError:
            raise KeyError(
                f"no unified field {name!r}; available: {sorted(self.field_specs)}"
            ) from None

    def __getattr__(self, name: str) -> Any:
        # called only when normal attribute lookup fails; expose unified
        # fields as attributes (spec.wavelength, spec.surface_flux, ...)
        specs = object.__getattribute__(self, "field_specs")
        if name in specs:
            return object.__getattribute__(self, "unified")[name]
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    def parameter_value(self, name: str) -> Any:
        """The scalar value of a parameter for this spectrum.

        Only meaningful for parameters that are constant across the set
        (fixed/implicit roles, or an axis parameter pinned by the
        selection); axis parameters without a pinned value raise.
        """
        spec = self.parameter(name)
        if spec.role.value == "axis":
            pinned = self.selection.get(name)
            if pinned is None:
                raise KeyError(
                    f"parameter {name!r} is a grid axis; its value for this "
                    "spectrum lives in the selection metadata, which does "
                    "not pin it"
                )
            return pinned
        if spec.value is not None:
            return spec.value
        if spec.status is not None:
            raise KeyError(f"parameter {name!r} status: {spec.status}")
        raise KeyError(f"parameter {name!r} carries no value")

    # -- export ------------------------------------------------------------

    def to_table(self, layer: str = "unified") -> Table:
        """Return the spectrum as a table in the chosen layer.

        ``layer="native"`` gives the verbatim arrays (astropy Table with
        units); ``layer="unified"`` gives the unified view's table.
        """
        if layer == "native":
            return self._native.to_table()
        if layer == "unified":
            return self.unified.to_table()
        raise ValueError(f"layer must be 'native' or 'unified', got {layer!r}")

    def __len__(self) -> int:
        return len(self._native)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({self.identity.label}, {len(self)} points, "
            f"{len(self.parameters)} parameters)"
        )


class NodeSpectrum(Spectrum):
    """One published library node, verbatim.

    This is what a backend's loader materializes and what
    ``grid.node(...)`` returns: the spectrum exactly as published at one
    parameter point. It carries no query record because no query
    transformed it — asking for one is a category error the type system
    answers (``InterpSpectrum`` is the class that has one).
    """


class InterpSpectrum(Spectrum):
    """One grid query's answer, traceable to the nodes that produced it.

    Constructed by the grid query paths (``grid.spectrum(...)``), never
    by backends. ``record`` is the full
    :class:`~stellarlib.grids.interp.InterpolationRecord` — the method,
    the corner nodes with their parameter values, the weights, the clamp
    state — and the ``corner_nodes``/``corner_weights`` properties are
    its single-query projection: for an exact-node hit or a Voronoi
    answer they name the one node returned with weight 1.0, for a
    multilinear answer they name the bracket cell's corners. The
    underlying data is shared with the node spectra when the answer IS a
    node (exact hit, Voronoi) and computed when it is not (multilinear).
    """

    def __init__(self, *args: Any, record: InterpolationRecord, **kwargs: Any) -> None:
        """Hold the layers plus the interpolation record that produced them."""
        super().__init__(*args, **kwargs)
        if record is None:
            raise ValueError("an InterpSpectrum requires a non-None record")
        self.record = record

    def _clone_kwargs(self) -> dict:
        kwargs = super()._clone_kwargs()
        kwargs["record"] = self.record
        return kwargs

    @classmethod
    def from_node(cls, node: Spectrum, record: InterpolationRecord) -> InterpSpectrum:
        """A query answer that IS one node's data, with the query record.

        The exact-node and Voronoi paths return a published node's
        spectrum unchanged; this wraps that node's layers in the
        InterpSpectrum class so the returned type states "this came from
        a query" while the arrays stay the node's own (no copy).
        """
        return cls(**node._clone_kwargs(), record=record)

    @property
    def corner_nodes(self) -> tuple[Mapping[str, float], ...]:
        """The nodes this answer was built from, with their coordinates."""
        return self.record.node_values[0]

    @property
    def corner_weights(self) -> tuple[float, ...]:
        """The weight of each corner node (they sum to 1)."""
        return self.record.weights[0]


class SpectrumSet:
    """All nodes of one coherent library product.

    The set is a lightweight index: ``nodes`` are :class:`GridNode` entries
    (coordinates, paths, filename facts) and ``loader`` materializes a node
    into a :class:`Spectrum` on demand — reading every gzip file up front
    is never imposed. ``selectable`` is the backend-pinned order of the
    coordinates the grid exposes (BOSZ: teff, log_surface_gravity, m_h;
    TLUSTY: teff, log_surface_gravity).
    """

    def __init__(
        self,
        nodes: Iterable[GridNode],
        *,
        identity: ModelIdentity,
        loader: Callable[[GridNode], Spectrum],
        selectable: tuple[str, ...],
        parameters: tuple[ParameterSpec, ...] = (),
        provenance: Provenance | None = None,
        notes: Mapping[str, Any] | None = None,
    ) -> None:
        """Index the product's nodes and keep its loader for lazy use."""
        self._nodes = tuple(nodes)
        self.identity = identity
        self.loader = loader
        self.selectable = tuple(selectable)
        self.parameters = tuple(parameters)
        self.provenance = provenance if provenance is not None else Provenance()
        self.notes: Mapping[str, Any] = dict(notes or {})

    @property
    def nodes(self) -> tuple[GridNode, ...]:
        """The product's index entries, in declaration order."""
        return self._nodes

    def load(self, node: GridNode) -> Spectrum:
        """Materialize one node's spectrum (the grid caches, the set does not)."""
        return self.loader(node)

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self) -> Any:
        """Iterate the set's nodes (index entries, not spectra)."""
        return iter(self._nodes)

    def __getitem__(self, idx: int | slice) -> GridNode:
        """The node at the given index."""
        return self._nodes[idx]

    def parameter(self, name: str) -> ParameterSpec:
        """One parameter's spec by name."""
        for spec in self.parameters:
            if spec.name == name:
                return spec
        raise KeyError(f"no parameter named {name!r}")

    def __repr__(self) -> str:
        return (
            f"SpectrumSet({self.identity.label}, {len(self)} nodes, "
            f"axes {list(self.selectable)})"
        )
