"""BaSeL backend: product knowledge around the raw parser.

BaSeL (Lejeune, Cuisinier & Buser 1998; Westera et al. 2002) is a
semi-empirical library: theoretical SEDs (Kurucz ATLAS9 and others)
recalibrated onto empirical color-temperature relations, published as
one ASCII file per metallicity holding every spectrum of that
metallicity. One :class:`~stellarlib.spectrum.SpectrumSet` holds one
published version — 2.2 or 3.1 — at one calibration variant (3.1
publishes two: ``WLBC99`` and ``PADOVA2000``, genuinely different
recalibrations at the same node sets; the variant is a read-side
selection, never a fetch-side subset, and never an unstated fact: a
flat 3.1 file set must say which calibration its bytes carry).

The flux unit question the author himself flags ("you might have to
multiply or divide by 4pi") is settled by the bolometric closure of the
published numbers themselves: integrating any block over frequency
reproduces ``sigma T^4 / 4 pi`` to 0.1-0.5% across Teff 2000-50000 K in
both versions, so the files hold the Eddington flux per steradian
(erg/s/cm^2/Hz/sr) and the unified layer's ``surface_flux`` is
``4 pi H``. The recalibration conserves each model's bolometric flux.

Node coordinates: Teff and log g from each block's parameter line; the
metallicity coordinate is the FILE's metallicity — the library's own
one-file-per-metallicity partition — and blocks whose [Fe/H] column
disagrees with their file (29 blocks in 2.2) are recorded per node and
in the set notes, never silently relabeled.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import dataclasses
import os
from pathlib import Path
import tarfile

import numpy as np

from stellarlib.core import ModelIdentity, ParameterSpec
from stellarlib.exceptions import (
    AmbiguousConventionError,
    DataIntegrityError,
    MetadataConflictError,
    UnrecognizedFormatError,
)
from stellarlib.models.basel.reader import (
    BLOCK_LINES,
    HEADER_LINES,
    CorFile,
    parse_cor,
    parse_cor_index,
    parse_filename,
    recognize_basel,
)
from stellarlib.native import NativeSpectrum
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

__all__ = [
    "BASEL_REFERENCES",
    "VARIANTS_31",
    "basel_identity",
    "read_basel",
    "recognize_basel",
]

_SELECTABLE = ("teff", "log_surface_gravity", "m_h")
VARIANTS_31 = ("WLBC99", "PADOVA2000")
_PAGE = "http://professor.ufabc.edu.br/~pieter.westera/BaSeL.html"
_TAR_URLS = {
    "2.2": "http://professor.ufabc.edu.br/~pieter.westera/BaSeL2_2.tar.gz",
    "3.1": "http://professor.ufabc.edu.br/~pieter.westera/BaSeL3_1.tar.gz",
}
BASEL_REFERENCES = references("basel")


@dataclasses.dataclass(frozen=True)
class _Source:
    """Where one ``*.cor`` file's bytes live: on disk or inside a tar."""

    path: Path  # the file, or the tar holding the member
    member: str | None  # tar member name, None for a plain file
    name: str  # the cor filename ("wlbc99_p00.cor")

    def parent_dir(self) -> str:
        return Path(self.member or self.path).parent.name

    def text(self) -> str:
        if self.member is None:
            return self.path.read_text(encoding="utf-8")
        with tarfile.open(self.path, "r") as tf:
            payload = tf.extractfile(self.member)
            if payload is None:
                raise DataIntegrityError(
                    f"{self.member}: not a regular member of "
                    f"{self.path.name}"
                )
            return payload.read().decode("utf-8")


def basel_identity(release: str, variant: str | None) -> ModelIdentity:
    """The identity of one BaSeL version at one calibration variant."""
    return ModelIdentity(
        family="basel",
        release=release,
        product="semiempirical_seds",
        physics_configuration=f"calibration {variant}" if variant else None,
        description=(
            f"BaSeL {release} semi-empirical SEDs"
            + (f", {variant} calibration" if variant else "")
            + " (theoretical spectra recalibrated onto empirical colors)"
        ),
    )


_TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz")


def _is_tar(p: Path) -> bool:
    return p.name.endswith(_TAR_SUFFIXES)


def _enumerate_paths(
    sources: str | os.PathLike | Iterable[str | os.PathLike],
) -> list[Path]:
    """Flatten the ``sources`` argument: a path (directory or file), or
    an iterable of them. A directory contributes its cor files and any
    tarball (a database product directory holds the published tar);
    unrelated files (a MANIFEST) ride along unmentioned."""
    paths: list[Path] = []

    def _add(p: Path) -> None:
        if p.is_dir():
            paths.extend(
                sorted(
                    q
                    for q in p.rglob("*")
                    if q.is_file() and (recognize_basel(q) or _is_tar(q))
                )
            )
        else:
            paths.append(p)

    if isinstance(sources, (str, os.PathLike)):
        _add(Path(sources))
    else:
        for s in sources:
            _add(Path(s))
    if not paths:
        raise UnrecognizedFormatError(f"no BaSeL cor files found among {sources!r}")
    return paths


def _collect_sources(
    sources: str | os.PathLike | Iterable[str | os.PathLike],
) -> tuple[list[_Source], Path | None]:
    """Every ``*.cor`` source named by ``sources``, plus the tar if any.

    A tar is the upstream publishing unit of both versions and holds
    every file; a directory or file list enumerates the same way.
    """
    tar_path: Path | None = None
    files: list[Path] = []
    for p in _enumerate_paths(sources):
        if _is_tar(p):
            if tar_path is not None:
                raise ValueError(
                    "one set holds one published tarball; pass the 2.2 or "
                    "the 3.1 tar, not both"
                )
            tar_path = p
        elif recognize_basel(p):
            files.append(p)
        else:
            raise UnrecognizedFormatError(
                f"{p}: not a BaSeL cor file or a published tarball"
            )

    if tar_path is not None:
        out = _tar_sources(tar_path)
    else:
        out = [_Source(path=p, member=None, name=p.name) for p in files]
    if not out:
        raise UnrecognizedFormatError(
            f"{tar_path if tar_path is not None else files[0]}: no BaSeL "
            "cor files found"
        )
    return out, tar_path


def _tar_sources(tar_path: Path) -> list[_Source]:
    """The cor members of a published tarball, in member order."""
    with tarfile.open(tar_path, "r") as tf:
        return [
            _Source(path=tar_path, member=m.name, name=Path(m.name).name)
            for m in tf.getmembers()
            if m.isfile() and recognize_basel(m.name)
        ]


def _resolve_variant(
    cor_sources: list[_Source], release: str, variant: str | None
) -> tuple[str | None, list[_Source]]:
    """Pin the calibration variant, filtering the sources if needed.

    3.1 publishes the two variants as parallel directories of identical
    filenames; whichever way the variant becomes known — the directory
    structure or the argument — it ends stated on the set. 2.2 has no
    variants.
    """
    if variant is not None and variant not in VARIANTS_31:
        raise ValueError(
            f"variant must be one of {VARIANTS_31} or None, got {variant!r}"
        )
    present = tuple(v for v in VARIANTS_31 if v in {s.parent_dir() for s in cor_sources})
    if release != "3.1":
        if variant is not None:
            raise ValueError(
                f"BaSeL {release} publishes no calibration variants; "
                "leave variant unset"
            )
        return None, cor_sources
    if len(present) > 1:
        if variant is None:
            raise AmbiguousConventionError(
                "these sources hold both calibration variants of BaSeL 3.1 "
                f"({list(present)}) — the same nodes with different "
                "recalibrations; pass variant='WLBC99' or "
                "variant='PADOVA2000'"
            )
        return variant, [
            s for s in cor_sources if s.parent_dir() == variant
        ]
    if len(present) == 1:
        if variant is not None and variant != present[0]:
            raise MetadataConflictError(
                f"variant {variant!r} requested but the sources hold "
                f"{present[0]!r}"
            )
        return present[0], cor_sources
    # flat file set: the files do not state their calibration — the
    # caller must
    if variant is None:
        raise AmbiguousConventionError(
            "a flat BaSeL 3.1 file set does not state which calibration "
            "its bytes carry; pass variant='WLBC99' or variant='PADOVA2000'"
        )
    return variant, cor_sources


def _index_file(
    source: _Source, m_h: float, release: str, variant: str | None
) -> tuple[np.ndarray, list[GridNode], list[dict]]:
    """The index pass over one file: its wavelength grid, one node per
    block, and every [Fe/H]-column mismatch recorded (never fixed)."""
    file_wave, blocks = parse_cor_index(source.text(), source.name)
    nodes = []
    mismatches = []
    seen_tg: dict[tuple[float, float], int] = {}
    for block in blocks:
        tg_key = (block.teff, block.logg)
        if tg_key in seen_tg:
            raise DataIntegrityError(
                f"{source.name}: blocks {seen_tg[tg_key]} and "
                f"{block.spectrum_number} both sit at Teff {block.teff}, "
                f"log g {block.logg}"
            )
        seen_tg[tg_key] = block.spectrum_number
        matches = abs(block.feh_column - m_h) < 1e-9
        if not matches:
            mismatches.append(
                {
                    "file": source.name,
                    "spectrum_number": block.spectrum_number,
                    "teff": block.teff,
                    "logg": block.logg,
                    "feh_column": block.feh_column,
                    "file_m_h": m_h,
                }
            )
        nodes.append(
            GridNode(
                coordinates={
                    "teff": block.teff,
                    "log_surface_gravity": block.logg,
                    "m_h": m_h,
                },
                path=source.path,
                facts={
                    "release": release,
                    "variant": variant,
                    "file": source.name,
                    "member": source.member,
                    "spectrum_number": block.spectrum_number,
                    "feh_column": block.feh_column,
                    "feh_matches_file": matches,
                    "vturb_column": block.vturb_column,
                    "h_fraction_column": block.h_fraction_column,
                    "param_line": block.param_line,
                },
            )
        )
    return file_wave, nodes, mismatches


def _provenance_for(
    tar_path: Path | None, cor_sources: list[_Source], release: str
) -> Provenance:
    """Provenance assets: the whole tarball when the set streams from
    one, one record per held file otherwise."""
    if tar_path is not None:
        return Provenance(
            assets=(
                DataAsset(
                    filename=tar_path.name,
                    size_bytes=tar_path.stat().st_size,
                    source=SourceRecord(
                        source_class=SourceClass.OFFICIAL_ARCHIVE,
                        url=_TAR_URLS.get(release, _PAGE),
                        status=SourceStatus.NOT_CHECKED,
                        note=(
                            "whole published tarball from the author's "
                            "page; every file of the version is held in "
                            "it — no upstream checksums are published"
                        ),
                    ),
                ),
            ),
            scientific_references=BASEL_REFERENCES,
        )
    return Provenance(
        assets=tuple(
            DataAsset(
                filename=source.name,
                size_bytes=source.path.stat().st_size,
                source=SourceRecord(
                    source_class=SourceClass.OFFICIAL_ARCHIVE,
                    url=_TAR_URLS.get(release, _PAGE),
                    status=SourceStatus.NOT_CHECKED,
                    note=(
                        "distributed inside the version's tarball at the "
                        "author's page; no per-file URLs are published"
                    ),
                ),
            )
            for source in cor_sources
        ),
        scientific_references=BASEL_REFERENCES,
    )


class _NodeLoader:
    """Loads nodes on demand, parsing each source file once (a small
    cache; corner queries sweep files in flat-index order)."""

    def __init__(
        self,
        identity: ModelIdentity,
        parameters: tuple[ParameterSpec, ...],
        field_specs: Mapping[str, object],
        provenance: Provenance,
    ) -> None:
        self._identity = identity
        self._parameters = parameters
        self._field_specs = field_specs
        self._provenance = provenance
        self._cache: dict[tuple[str, str | None], CorFile] = {}

    def _parsed(self, node: GridNode) -> CorFile:
        key = (str(node.path), node.facts["member"])
        parsed = self._cache.get(key)
        if parsed is None:
            parsed = parse_cor(
                _Source(
                    path=node.path,
                    member=node.facts["member"],
                    name=node.facts["file"],
                ).text(),
                node.facts["file"],
            )
            if len(self._cache) >= 4:
                self._cache.clear()
            self._cache[key] = parsed
        return parsed

    def __call__(self, node: GridNode) -> Spectrum:
        parsed = self._parsed(node)
        index = (node.facts["param_line"] - HEADER_LINES) // BLOCK_LINES
        block = parsed.blocks[index]
        native = NativeSpectrum(
            wavelength=parsed.wavelength,
            flux=parsed.fluxes[index],
            metadata={
                "release": node.facts["release"],
                "variant": node.facts["variant"],
                "file": node.facts["file"],
                "spectrum_number": block.spectrum_number,
                "teff": block.teff,
                "logg": block.logg,
                "feh_column": block.feh_column,
                "m_h": node.coordinates["m_h"],
                "vturb_column": block.vturb_column,
                "h_fraction_column": block.h_fraction_column,
            },
            provenance_keys={
                "file": node.facts["file"],
                "member": node.facts["member"],
            },
            wavelength_unit="nm",
            flux_unit="erg / (s cm2 Hz sr)",
        )
        return Spectrum(
            native=native,
            identity=self._identity,
            parameters=self._parameters,
            field_specs=dict(self._field_specs),
            provenance=self._provenance,
            selection=dict(node.coordinates),
            unified_view=UnifiedView(native, dict(self._field_specs)),
        )


def read_basel(
    sources: str | os.PathLike | Iterable[str | os.PathLike],
    variant: str | None = None,
) -> SpectrumSet:
    """Read BaSeL semi-empirical SEDs into a set: the published tarball
    (the upstream unit — spectra stream from its members), an extracted
    tree, or a list of ``*.cor`` files.

    ``variant`` states the calibration of a 3.1 set; it is required for
    a flat 3.1 file set and for sources holding both variants, inferred
    from a one-variant tree, and rejected for 2.2.
    """
    cor_sources, tar_path = _collect_sources(sources)

    releases = {parse_filename(s.name)["release"] for s in cor_sources}
    if len(releases) != 1:
        raise MetadataConflictError(
            f"one set holds one BaSeL version; found files of "
            f"{sorted(releases)}"
        )
    release = releases.pop()
    variant, cor_sources = _resolve_variant(cor_sources, release, variant)

    wavelength: np.ndarray | None = None
    nodes: list[GridNode] = []
    mismatches: list[dict] = []
    file_blocks: dict[str, int] = {}
    for source in cor_sources:
        facts = parse_filename(source.name)
        file_wave, file_nodes, file_mismatches = _index_file(
            source, facts["m_h"], release, variant
        )
        if wavelength is None:
            wavelength = file_wave
        elif not np.array_equal(wavelength, file_wave):
            raise MetadataConflictError(
                f"{source.name}: wavelength grid differs from the set's "
                "other files — one set holds the library's one shared grid"
            )
        file_blocks[source.name] = len(file_nodes)
        nodes.extend(file_nodes)
        mismatches.extend(file_mismatches)
    assert wavelength is not None  # cor_sources is non-empty

    identity = basel_identity(release, variant)
    parameters = resolve_parameters(
        "basel",
        release,
        {
            "teff": tuple(sorted({n.coordinates["teff"] for n in nodes})),
            "logg": tuple(
                sorted({n.coordinates["log_surface_gravity"] for n in nodes})
            ),
            "m_h": tuple(sorted({n.coordinates["m_h"] for n in nodes})),
        },
    )
    field_specs = field_spec_dict("basel", release)
    provenance = _provenance_for(tar_path, cor_sources, release)
    loader = _NodeLoader(identity, parameters, dict(field_specs), provenance)

    notes = {
        "variant": variant,
        "files": file_blocks,
        "wavelength_grid": (
            f"{len(wavelength)} points, nm, {wavelength[0]}-{wavelength[-1]} "
            "(one grid shared by every file of both versions)"
        ),
        "backend_notes": list(backend_notes("basel")),
    }
    if mismatches:
        notes["feh_column_mismatches"] = mismatches
    return SpectrumSet(
        nodes=tuple(nodes),
        identity=identity,
        loader=loader,
        selectable=_SELECTABLE,
        parameters=parameters,
        provenance=provenance,
        notes=notes,
    )
