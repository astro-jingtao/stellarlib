"""TLUSTY backend: product knowledge around the raw parser.

Builds the :class:`ModelIdentity`, the set-level :class:`ParameterSpec`
tuple, and the lazily-loading :class:`SpectrumSet` for a directory or
list of TLUSTY optical spectra. One set holds one release (OSTAR2002 or
BSTAR2006), one composition, one microturbulence, and only kind-7
detailed spectra — kind-17 continuum files are a separate published
product and are skipped with a warning and a record, never mixed in.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from stellarlib.core import ModelIdentity
from stellarlib.exceptions import (
    AmbiguousConventionError,
    MetadataConflictError,
    UnrecognizedFormatError,
)
from stellarlib.models.tlusty.reader import (
    FILENAME_RE,
    parse_filename,
    read_vis,
    recognize_tlusty,
)
from stellarlib.provenance import (
    DataAsset,
    Provenance,
    SourceClass,
    SourceRecord,
    SourceStatus,
)
from stellarlib.registry import (
    backend_notes,
    field_spec_dict,
    references,
    resolve_parameters,
)
from stellarlib.spectrum import GridNode, Spectrum, SpectrumSet
from stellarlib.unified import UnifiedView
from stellarlib.warnings import IncompleteMetadataWarning

__all__ = ["read_tlusty", "recognize_tlusty", "tlusty_identity", "TLUSTY_REFERENCES"]

_SELECTABLE = ("teff", "log_surface_gravity")
_OSTAR_SPAN = (27500, 55000)
_BSTAR_SPAN = (15000, 30000)
_DISTRIBUTION = "https://tlusty.oca.eu/"
TLUSTY_REFERENCES = references("tlusty")


def tlusty_identity(release: str, composition: str) -> ModelIdentity:
    product = "ostar2002" if release == "OSTAR2002" else "bstar2006"
    return ModelIdentity(
        family="tlusty",
        release=release,
        product=product,
        physics_configuration=f"composition {composition}",
        description=(
            f"TLUSTY {release} optical spectra (kind 7, SYNSPEC visible "
            "range)"
        ),
    )


def _infer_release(teffs: tuple[int, ...], release: str | None) -> str:
    if release is not None:
        release = release.upper()
        if release not in ("OSTAR2002", "BSTAR2006"):
            raise ValueError(
                f"release must be 'OSTAR2002' or 'BSTAR2006', got {release!r}"
            )
        low, high = _OSTAR_SPAN if release == "OSTAR2002" else _BSTAR_SPAN
        bad = [t for t in teffs if not low <= t <= high]
        if bad:
            raise AmbiguousConventionError(
                f"release {release} spans Teff {low}-{high} K, but the "
                f"sources include {bad}"
            )
        return release
    if any(t < _OSTAR_SPAN[0] for t in teffs):
        return "BSTAR2006"
    if any(t > _BSTAR_SPAN[1] for t in teffs):
        return "OSTAR2002"
    raise AmbiguousConventionError(
        f"all sources sit in the OSTAR2002/BSTAR2006 overlap band "
        f"{_OSTAR_SPAN[0]}-{_BSTAR_SPAN[1]} K; pass release= explicitly"
    )


def _collect_files(sources) -> list[Path]:
    if isinstance(sources, (str, os.PathLike)):
        p = Path(sources)
        if p.is_dir():
            return sorted(
                q for q in p.rglob("*") if q.is_file() and not q.name.startswith(".")
            )
        return [p]
    files: list[Path] = []
    for s in sources:
        p = Path(s)
        if p.is_dir():
            files.extend(
                sorted(
                    q
                    for q in p.rglob("*")
                    if q.is_file() and not q.name.startswith(".")
                )
            )
        else:
            files.append(p)
    if not files:
        raise ValueError("sources must not be empty")
    return files


def read_tlusty(sources, release: str | None = None) -> SpectrumSet:
    """Read TLUSTY optical spectra (directory or file list) into a set."""
    files = _collect_files(sources)
    candidates = [f for f in files if recognize_tlusty(f)]
    if not candidates:
        raise UnrecognizedFormatError(
            f"no TLUSTY vis files found among {files[0].parent}"
        )

    # kind-17 continuum files ride along in the same archives; they are a
    # separate product and are skipped with a record, never mixed in
    kind7, skipped_kind17 = [], []
    for f in candidates:
        name = f.name if FILENAME_RE.match(f.name) else f.name + ".gz"
        facts = parse_filename(name)
        (kind7 if facts["kind"] == "7" else skipped_kind17).append((f, facts))
    if not kind7:
        raise UnrecognizedFormatError(
            f"only kind-17 continuum files found among {files[0].parent}; "
            "stellarlib consumes kind-7 detailed spectra"
        )
    if skipped_kind17:
        names = [f.name for f, _ in skipped_kind17]
        warnings.warn(
            f"skipping {len(names)} kind-17 continuum file(s) (a separate "
            f"published product): {names[:5]}{'...' if len(names) > 5 else ''}",
            IncompleteMetadataWarning,
            stacklevel=2,
        )

    def _single(key: str, label: str):
        values = sorted({facts[key] for _, facts in kind7})
        if len(values) != 1:
            raise MetadataConflictError(
                f"one set holds one {label}; found {values} — split the "
                "sources per product"
            )
        return values[0]

    composition = _single("composition", "composition")
    vmicro = _single("vmicro", "microturbulence")
    teffs = tuple(sorted({int(facts["teff"]) for _, facts in kind7}))
    loggs = tuple(sorted({float(facts["logg"]) for _, facts in kind7}))
    release_name = _infer_release(teffs, release)
    product_key = "ostar2002" if release_name == "OSTAR2002" else "bstar2006"

    parameters = resolve_parameters("tlusty", product_key, {
        "teff": tuple(float(t) for t in teffs),
        "logg": loggs,
        "composition": composition,
        "vmicro": vmicro,
    })
    field_specs = field_spec_dict("tlusty", product_key)
    identity = tlusty_identity(release_name, composition)

    assets = tuple(
        DataAsset(
            filename=f.name,
            size_bytes=f.stat().st_size,
            source=SourceRecord(
                source_class=SourceClass.OFFICIAL_ORIGIN,
                url=_DISTRIBUTION,
                status=SourceStatus.NOT_CHECKED,
                note="per-composition vispec tar archives at tlusty.oca.eu; "
                "no per-file URLs and no checksums are published",
            ),
        )
        for f, _ in kind7
    )
    provenance = Provenance(
        assets=assets,
        scientific_references=TLUSTY_REFERENCES,
    )

    nodes = tuple(
        GridNode(
            coordinates={
                "teff": float(facts["teff"]),
                "log_surface_gravity": float(facts["logg"]),
            },
            path=f,
            facts={
                key: facts[key]
                for key in ("composition", "teff", "logg", "vmicro", "kind")
            },
        )
        for f, facts in kind7
    )

    def loader(node: GridNode) -> Spectrum:
        native = read_vis(node.path)
        return Spectrum(
            native=native,
            identity=identity,
            parameters=parameters,
            field_specs=dict(field_specs),
            provenance=provenance,
            selection=dict(node.coordinates),
            unified_view=UnifiedView(native, dict(field_specs)),
        )

    return SpectrumSet(
        nodes=nodes,
        identity=identity,
        loader=loader,
        selectable=_SELECTABLE,
        parameters=parameters,
        provenance=provenance,
        notes={
            "skipped_kind17": [f.name for f, _ in skipped_kind17],
            "backend_notes": list(backend_notes("tlusty")),
        },
    )
