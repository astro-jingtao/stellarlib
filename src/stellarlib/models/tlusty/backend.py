"""TLUSTY backend: product knowledge around the raw parser.

Builds the :class:`ModelIdentity`, the set-level :class:`ParameterSpec`
tuple, and the lazily-loading :class:`SpectrumSet` for a vispec tar
archive (the upstream publishing unit) or a directory/list of TLUSTY
optical spectrum files. One set holds one release (OSTAR2002 or
BSTAR2006), one composition, one microturbulence. Kind 7 (detailed
spectrum) and kind 17 (continuum) are one model's two data and are held
together: every archive carries both, the kind-17 sibling of each node
is recorded in its facts and the set notes, and nothing is skipped or
warned about — their presence is the expected state. The two kinds are
not row-aligned (kind 17 samples its own coarser wavelength grid), so
the continuum is never fused into a per-row array; it stays addressable
by its own member name.
"""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import tarfile

from stellarlib.core import ModelIdentity
from stellarlib.exceptions import (
    AmbiguousConventionError,
    MetadataConflictError,
    UnrecognizedFormatError,
)
from stellarlib.models.tlusty.design import TLUSTY_TARS
from stellarlib.models.tlusty.reader import (
    FILENAME_RE,
    parse_filename,
    read_vis,
    read_vis_payload,
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

__all__ = ["TLUSTY_REFERENCES", "read_tlusty", "recognize_tlusty", "tlusty_identity"]

_SELECTABLE = ("teff", "log_surface_gravity")
_OSTAR_SPAN = (27500, 55000)
_BSTAR_SPAN = (15000, 30000)
_DISTRIBUTION = "https://tlusty.oca.eu/"
_ARCHIVE_BASE = "https://tlusty.oca.eu/tlusty/Tlusty2002/database"
TLUSTY_REFERENCES = references("tlusty")


def tlusty_identity(release: str, composition: str) -> ModelIdentity:
    """The identity of one TLUSTY release at one composition."""
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


def _collect_files(
    sources: str | os.PathLike | Iterable[str | os.PathLike],
) -> list[Path]:
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


def read_tlusty(
    sources: str | os.PathLike | Iterable[str | os.PathLike],
    release: str | None = None,
) -> SpectrumSet:
    """Read TLUSTY optical spectra into a set: a vispec tar archive (the
    upstream publishing unit — spectra stream from its members, kind 7
    and kind 17 together) or a directory/file list of extracted members."""
    files = _collect_files(sources)
    tars = [f for f in files if f.name.endswith(".tar")]
    if tars:
        # one set holds one tar; unrecognized files in the directory
        # (a MANIFEST, dotfiles) ride along and are ignored
        strays = [
            f for f in files
            if f not in tars and (recognize_tlusty(f) or f.name.endswith(".tar"))
        ]
        if len(tars) > 1 or strays:
            raise ValueError(
                "one set holds one vispec tar; found "
                f"{[f.name for f in [*tars, *strays]]} — "
                "split the sources per product"
            )
        return _read_tar(tars[0], release)
    candidates = [f for f in files if recognize_tlusty(f)]
    if not candidates:
        raise UnrecognizedFormatError(
            f"no TLUSTY vis files found among {files[0].parent}"
        )

    # kind-17 continuum files ride along in every archive — one model's
    # two data; they are recorded (each node names its sibling), never
    # skipped or warned about
    kind7, kind17 = [], []
    for f in candidates:
        name = f.name if FILENAME_RE.match(f.name) else f.name + ".gz"
        facts = parse_filename(name)
        (kind7 if facts["kind"] == "7" else kind17).append((f, facts))
    if not kind7:
        raise UnrecognizedFormatError(
            f"only kind-17 continuum files found among {files[0].parent}; "
            "a spectrum product needs the kind-7 detailed spectra"
        )
    siblings = {
        _model_key(facts): f for f, facts in kind17
    }

    composition, vmicro, teffs, loggs, release_name = _set_facts(kind7, release)
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
                note="member of a per-composition vispec tar archive at "
                "tlusty.oca.eu; no per-file URLs and no checksums are "
                "published",
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
                **{key: facts[key]
                   for key in ("composition", "teff", "logg", "vmicro", "kind")},
                "continuum_member": (
                    siblings[_model_key(facts)].name
                    if _model_key(facts) in siblings else None
                ),
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
            "kind17_files": [f.name for f, _ in kind17],
            "backend_notes": list(backend_notes("tlusty")),
        },
    )


def _model_key(facts: dict) -> tuple:
    return (
        facts["composition"], int(facts["teff"]),
        float(facts["logg"]), float(facts["vmicro"]),
    )


def _set_facts(kind7: list, release: str | None) -> tuple:
    def _single(key: str, label: str) -> object:
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
    return composition, vmicro, teffs, loggs, release_name


def _read_tar(tar_path: Path, release: str | None) -> SpectrumSet:
    """Read one vispec tar archive: the whole model, both kinds."""

    with tarfile.open(tar_path, "r") as tf:
        member_names = [
            m.name.rsplit("/", 1)[-1]
            for m in tf.getmembers()
            if m.isfile() and recognize_tlusty(m.name.rsplit("/", 1)[-1])
        ]
    kind7 = [n for n in member_names if parse_filename(n)["kind"] == "7"]
    kind17 = [n for n in member_names if parse_filename(n)["kind"] == "17"]
    if not kind7:
        raise UnrecognizedFormatError(
            f"{tar_path.name}: the archive holds no kind-7 detailed spectra"
        )
    siblings = {_model_key(parse_filename(n)): n for n in kind17}

    parsed7 = [(n, parse_filename(n)) for n in sorted(kind7)]
    composition, vmicro, teffs, loggs, release_name = _set_facts(
        parsed7, release
    )
    product_key = "ostar2002" if release_name == "OSTAR2002" else "bstar2006"
    parameters = resolve_parameters("tlusty", product_key, {
        "teff": tuple(float(t) for t in teffs),
        "logg": loggs,
        "composition": composition,
        "vmicro": vmicro,
    })
    field_specs = field_spec_dict("tlusty", product_key)
    identity = tlusty_identity(release_name, composition)

    official = any(t["archive"] == tar_path.name for t in TLUSTY_TARS.values())
    provenance = Provenance(
        assets=(
            DataAsset(
                filename=tar_path.name,
                size_bytes=tar_path.stat().st_size,
                source=SourceRecord(
                    source_class=SourceClass.OFFICIAL_ARCHIVE,
                    url=f"{_ARCHIVE_BASE}/{tar_path.name}" if official else None,
                    status=SourceStatus.NOT_CHECKED,
                    note="whole per-composition vispec tar archive; every "
                    "member (kind 7 spectrum + kind 17 continuum) is held "
                    "in it — no upstream checksums are published",
                ),
            ),
        ),
        scientific_references=TLUSTY_REFERENCES,
    )

    nodes = tuple(
        GridNode(
            coordinates={
                "teff": float(facts["teff"]),
                "log_surface_gravity": float(facts["logg"]),
            },
            path=tar_path,
            facts={
                **{key: facts[key]
                   for key in ("composition", "teff", "logg", "vmicro", "kind")},
                "member": name,
                "continuum_member": siblings.get(_model_key(facts)),
            },
        )
        for name, facts in parsed7
    )

    def loader(node: GridNode) -> Spectrum:
        with tarfile.open(node.path, "r") as tf:
            payload = tf.extractfile(node.facts["member"]).read()
        native = read_vis_payload(payload, node.facts["member"])
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
            "tar": tar_path.name,
            "members": {"kind7": len(kind7), "kind17": len(kind17)},
            "kind17_files": sorted(kind17),
            "backend_notes": list(backend_notes("tlusty")),
        },
    )
