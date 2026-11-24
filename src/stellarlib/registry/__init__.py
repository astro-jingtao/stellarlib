"""Declarative unified-parameter registry.

The registry is split by ownership. The JSON file next to this module
(``registry/unified_parameters.json``) is stellarlib's own definitions:
the unified fields and the physical parameters every data source maps
into. Each library family's characteristics — its products' field
mappings and static parameter knowledge, plus the literature to cite —
live in the family's data file ``models/<family>/unified_parameters.json``
next to its ``catalog.json``. This module loads the definitions and every
family file together, validates them as one registry, and serves the
merged structure; the human-readable document
``docs/unified-parameters.md`` is generated from it by
``tools/generate_parameter_docs.py`` — edit the JSON files, regenerate
the doc, never the other way round.

Backends read their field specs, parameter templates, and literature
references from here; only values that come from the data files
themselves are injected at read time through ``resolve_parameters``.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from stellarlib.provenance import Reference
from stellarlib.spectra.core import (
    FieldOrigin,
    FieldSpec,
    ParameterRole,
    ParameterSource,
    ParameterSpec,
)
from stellarlib.spectra.unified import TRANSFORMATIONS

_REGISTRY_FILE = Path(__file__).with_name("unified_parameters.json")
_MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

__all__ = [
    "REGISTRY_FILE",
    "backend_data_file",
    "backend_names",
    "backend_notes",
    "field_spec_dict",
    "field_specs",
    "load_registry",
    "product_names",
    "product_notes",
    "references",
    "resolve_parameters",
    "unified_field_names",
]

REGISTRY_FILE = _REGISTRY_FILE


def backend_data_file(family: str) -> Path:
    """The family's own data file (``models/<family>/unified_parameters.json``)."""
    return _MODELS_DIR / family.lower() / "unified_parameters.json"


@lru_cache(maxsize=1)
def _registry() -> dict:
    with open(_REGISTRY_FILE, encoding="utf-8") as fh:
        reg = json.load(fh)
    reg["backends"] = {}
    for family_dir in sorted(p for p in _MODELS_DIR.iterdir() if p.is_dir()):
        data_file = backend_data_file(family_dir.name)
        if not data_file.exists():
            continue
        with open(data_file, encoding="utf-8") as fh:
            backend = json.load(fh)
        if backend.get("schema_version") != reg["schema_version"]:
            raise ValueError(
                f"{data_file} declares schema_version "
                f"{backend.get('schema_version')!r}, expected "
                f"{reg['schema_version']!r} (the definitions file's version)"
            )
        declared = backend.get("family")
        if declared != family_dir.name:
            raise ValueError(
                f"{data_file} declares family {declared!r}; the directory "
                f"name is {family_dir.name!r}"
            )
        reg["backends"][declared] = backend
    if not reg["backends"]:
        raise ValueError(
            f"no library family data files (unified_parameters.json) found "
            f"under {_MODELS_DIR}"
        )
    _validate(reg)
    return reg


def load_registry() -> dict:
    """The parsed registry (read-only by convention; callers must not mutate)."""
    return _registry()


def _validate(reg: dict) -> None:
    fields = {f["name"] for f in reg["unified_fields"]}
    if len(fields) != len(reg["unified_fields"]):
        raise ValueError("unified_fields contains duplicate names")
    known_params = {p["name"] for p in reg["physical_parameters"]}
    for name, backend in reg["backends"].items():
        for product_key, product in backend["products"].items():
            for mapping in product["field_mappings"]:
                if mapping["unified"] not in fields:
                    raise ValueError(
                        f"backend {name!r} product {product_key!r} maps unknown "
                        f"unified field {mapping['unified']!r}"
                    )
                origin = FieldOrigin(mapping["origin"])
                if (
                    origin is FieldOrigin.TRANSFORMED_NATIVE
                    and mapping.get("transformation") not in TRANSFORMATIONS
                ):
                    raise ValueError(
                            f"backend {name!r} product {product_key!r} field "
                            f"{mapping['unified']!r} uses transformation "
                            f"{mapping.get('transformation')!r}, which the "
                            "unified view does not implement"
                        )
                if origin in (
                    FieldOrigin.NATIVE,
                    FieldOrigin.TRANSFORMED_NATIVE,
                ) and mapping.get("native") not in (
                    "wavelength",
                    "flux",
                    "continuum",
                ):
                    raise ValueError(
                        f"backend {name!r} product {product_key!r} field "
                        f"{mapping['unified']!r} names native source "
                        f"{mapping.get('native')!r}; a native spectrum has "
                        "only wavelength, flux, and continuum"
                    )
            for entry in product["parameters"]:
                if entry["name"] not in known_params:
                    raise ValueError(
                        f"backend {name!r} product {product_key!r} declares "
                        f"unknown physical parameter {entry['name']!r}"
                    )


def _backend(name: str) -> dict:
    try:
        return _registry()["backends"][name.lower()]
    except KeyError:
        known = sorted(_registry()["backends"])
        raise ValueError(
            f"no backend {name!r} in the unified registry; known: {known}"
        ) from None


def _product(backend: str, product: str) -> dict:
    be = _backend(backend)
    products = be["products"]
    key = product.lower()
    try:
        return products[key]
    except KeyError:
        raise ValueError(
            f"backend {backend!r} has no product {product!r}; "
            f"known: {sorted(products)}"
        ) from None


def backend_names() -> tuple[str, ...]:
    """The families that declare registry data."""
    return tuple(_registry()["backends"])


def product_names(backend: str) -> tuple[str, ...]:
    """One family's product keys."""
    return tuple(_backend(backend)["products"])


def unified_field_names() -> tuple[str, ...]:
    """The unified vocabulary's field names."""
    return tuple(f["name"] for f in _registry()["unified_fields"])


# -- field mappings -----------------------------------------------------------


def field_specs(backend: str, product: str) -> tuple[FieldSpec, ...]:
    """The product's unified field specs, built from the registry.

    Units and representation come from the unified vocabulary (one place);
    a mapping adds the native array, origin, transformation, and any
    product-specific description.
    """
    be = _backend(backend)
    prod = _product(backend, product)
    vocab = {f["name"]: f for f in _registry()["unified_fields"]}
    specs = []
    for mapping in prod["field_mappings"]:
        if mapping["origin"] == "missing":
            specs.append(
                FieldSpec(
                    name=mapping["unified"],
                    unit=None,
                    representation="linear",
                    origin=FieldOrigin.MISSING,
                    transformation=None,
                    description=mapping.get("description"),
                    backend=backend.lower(),
                    mapping_version=be["mapping_version"],
                )
            )
            continue
        v = vocab[mapping["unified"]]
        native_unit = mapping.get("native_unit", v["unit"])
        specs.append(
            FieldSpec(
                name=mapping["unified"],
                native_name=mapping["native"],
                unit=v["unit"],
                native_unit=native_unit,
                representation=v["representation"],
                origin=FieldOrigin(mapping["origin"]),
                transformation=mapping.get("transformation"),
                description=mapping.get("description", v.get("description")),
                backend=backend.lower(),
                mapping_version=be["mapping_version"],
            )
        )
    return tuple(specs)


def field_spec_dict(backend: str, product: str) -> dict[str, FieldSpec]:
    """The product's field specs keyed by field name."""
    return {spec.name: spec for spec in field_specs(backend, product)}


# -- physical parameters ------------------------------------------------------


def _when_passes(when: Mapping[str, str], dynamic: Mapping[str, Any]) -> bool:
    if "available" in when:
        return when["available"] in dynamic
    if "missing" in when:
        return when["missing"] not in dynamic
    raise ValueError(f"unknown when condition {dict(when)!r}")


def _choose(entry: dict, dynamic: Mapping[str, Any]) -> dict | None:
    """Resolve an entry's variants against the reader-supplied values.

    ``when`` on the entry itself gates the whole entry; ``alternatives``
    are tried in order, the first whose ``when`` passes wins, and an
    alternative without ``when`` always passes (put it last as fallback).
    """
    when = entry.get("when")
    if when is not None and not _when_passes(when, dynamic):
        return None
    if "alternatives" in entry:
        for alt in entry["alternatives"]:
            alt_when = alt.get("when")
            if alt_when is None or _when_passes(alt_when, dynamic):
                return alt
        return None
    return entry


def _base_kwargs(chosen: Mapping[str, Any], vocab: dict) -> dict[str, Any]:
    """The copyable scalar attributes (unit, convention, description,
    status) — the entry's value overrides the registry vocabulary's."""
    kwargs: dict[str, Any] = {}
    for key in ("unit", "convention", "description", "status"):
        value = chosen.get(key, vocab.get(key))
        if value is not None:
            kwargs[key] = value
    return kwargs


def _entry_value(
    name: str,
    chosen: Mapping[str, Any],
    dynamic: Mapping[str, Any],
    singular: str,
) -> Any:
    """Resolve one entry's value form (``value``/``value_from``) or
    values form (``values``/``values_from``) against the reader-supplied
    dynamic mapping; None when the entry states neither form."""
    from_key = f"{singular}_from"
    if from_key in chosen:
        key = chosen[from_key]
        if key not in dynamic:
            raise KeyError(
                f"parameter {name!r} wants {from_key} {key!r}, which the "
                "reader did not supply"
            )
        value = dynamic[key]
    elif singular in chosen:
        value = chosen[singular]
    else:
        return None
    return tuple(value) if singular == "values" else value


def _spec_from_entry(
    name: str, chosen: Mapping[str, Any], dynamic: Mapping[str, Any]
) -> ParameterSpec:
    vocab = {p["name"]: p for p in _registry()["physical_parameters"]}[name]
    kwargs = _base_kwargs(chosen, vocab)
    if "source" in chosen:
        kwargs["source"] = ParameterSource(chosen["source"])
    if "depends_on" in chosen:
        kwargs["depends_on"] = tuple(chosen["depends_on"])
    if "relation" in chosen:
        kwargs["relation"] = chosen["relation"]
    if "relation_parameters" in chosen:
        kwargs["relation_parameters"] = dict(chosen["relation_parameters"])
    value = _entry_value(name, chosen, dynamic, "value")
    if value is not None:
        kwargs["value"] = value
    values = _entry_value(name, chosen, dynamic, "values")
    if values is not None:
        kwargs["values"] = values
    return ParameterSpec(name=name, role=ParameterRole(chosen["role"]), **kwargs)


def resolve_parameters(
    backend: str, product: str, dynamic: Mapping[str, Any]
) -> tuple[ParameterSpec, ...]:
    """Build the product's ParameterSpecs from registry templates.

    ``dynamic`` carries the values only the data files know, keyed by
    parameter name (e.g. ``{"teff": (3500.0, 4000.0), "m_h": 0.0}``);
    everything static (roles, relations, implicit parameters) comes from
    the registry. Omitting a key selects the registry's fallback variant
    for parameters that declare one, and is an error for parameters whose
    value must come from the file.
    """
    prod = _product(backend, product)
    specs = []
    for entry in prod["parameters"]:
        chosen = _choose(entry, dynamic)
        if chosen is None:
            continue
        specs.append(_spec_from_entry(entry["name"], chosen, dynamic))
    return tuple(specs)


# -- references and notes -----------------------------------------------------


def references(backend: str) -> tuple:
    """The backend family's scientific references (what to cite)."""

    be = _backend(backend)
    return tuple(Reference(**ref) for ref in be["references"])


def backend_notes(backend: str) -> tuple[str, ...]:
    """Documented caveats shared by the backend's products."""
    return tuple(_backend(backend)["notes"])


def product_notes(backend: str, product: str) -> tuple[str, ...]:
    """Documented caveats of one product's mapping."""
    return tuple(_product(backend, product).get("notes", ()))
