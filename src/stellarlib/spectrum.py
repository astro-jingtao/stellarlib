"""Spectrum, SpectrumSet, and GridNode: the per-node and per-product objects.

A :class:`Spectrum` is one library node — one published file, one parameter
point — exposing both the native arrays it was parsed into and the unified
view mapped onto it. A :class:`GridNode` is the lightweight index entry for
one node (coordinates, path, facts); a :class:`SpectrumSet` is the nodes of
one coherent product plus a loader that materializes nodes on demand, so a
grid of hundreds of gzip files stays cheap until spectra are actually
asked for.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import dataclasses
from pathlib import Path
from typing import Any, Callable

from astropy.table import Table

from stellarlib.core import FieldSpec, ModelIdentity, ParameterSpec
from stellarlib.native import NativeSpectrum
from stellarlib.provenance import Provenance
from stellarlib.unified import UnifiedView

__all__ = ["GridNode", "Spectrum", "SpectrumSet"]


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
    """One library node: a native spectrum plus its unified view.

    Instances are constructed by backends (through the owning set's
    loader), not directly by users. The ``unified`` accessor and
    convenience attributes come from the unified machinery
    (``stellarlib.unified``); this class holds the pieces and the metadata
    that travels with them.
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
        """Hold one node's layers; built by backends, not by users."""
        self._native = native
        self.identity = identity
        self.parameters = tuple(parameters)
        self.field_specs: Mapping[str, FieldSpec] = dict(field_specs or {})
        self.provenance = provenance if provenance is not None else Provenance()
        self.selection: Mapping[str, Any] = dict(selection or {})
        self.selection_info: Mapping[str, Any] | None = None
        self._unified_view = unified_view

    def with_selection_info(self, info: Mapping[str, Any]) -> Spectrum:
        """A shallow copy carrying grid-selection metadata.

        The native spectrum, identity, parameters, and unified view are
        shared with this object (no data is copied); only the selection
        metadata is new, so annotating one selection never contaminates
        another consumer of the same cached spectrum.
        """
        clone = Spectrum(
            native=self._native,
            identity=self.identity,
            parameters=self.parameters,
            field_specs=dict(self.field_specs),
            provenance=self.provenance,
            selection=dict(self.selection),
            unified_view=self._unified_view,
        )
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
            f"Spectrum({self.identity.label}, {len(self)} points, "
            f"{len(self.parameters)} parameters)"
        )


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
