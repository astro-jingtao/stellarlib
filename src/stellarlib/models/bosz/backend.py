"""BOSZ backend: product knowledge around the raw parser.

Builds the :class:`ModelIdentity`, the set-level :class:`ParameterSpec`
tuple, and the lazily-loading :class:`SpectrumSet` for a directory or list
of BOSZ 2024 resampled spectra. Wavelength grids pair by explicit decision
or by the two known on-disk layouts (flat, or a ``wavelength_grids/``
subdirectory) — ambiguity is loud, never a guess. All values stated by the
filenames are taken from them; values stated only in official documentation
cite their source.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from stellarlib.core import ModelIdentity
from stellarlib.exceptions import (
    MetadataConflictError,
    UnrecognizedFormatError,
    WavelengthGridError,
)
from stellarlib.models.bosz.reader import (
    FILENAME_RE,
    WAVE_FILENAME_RE,
    mast_url,
    parse_filename,
    read_resam,
    read_wave_grid,
    recognize_bosz,
    recognize_wave_grid,
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

__all__ = ["BOSZ_REFERENCES", "bosz_identity", "read_bosz", "recognize_bosz"]

_FIELD_SPECS = field_spec_dict("bosz", "resampled_spectra")
BOSZ_REFERENCES = references("bosz")
_SELECTABLE = ("teff", "log_surface_gravity", "m_h")


def bosz_identity(facts: dict) -> ModelIdentity:
    return ModelIdentity(
        family="bosz",
        release="2024",
        product="resampled_spectra",
        physics_configuration=f"r{facts['resolution']}",
        description="BOSZ 2024 resampled spectra (two-column resam files)",
    )


def _collect_sources(sources) -> tuple[list[Path], Path | None]:
    """Split the input into spectrum files and an optional in-band wave file."""
    if isinstance(sources, (str, os.PathLike)):
        p = Path(sources)
        if p.is_dir():
            files = sorted(
                q for q in p.rglob("*") if q.is_file() and not q.name.startswith(".")
            )
            return files, p
        return [p], p.parent
    paths: list[Path] = []
    for s in sources:
        p = Path(s)
        if p.is_dir():
            paths.extend(
                sorted(
                    q
                    for q in p.rglob("*")
                    if q.is_file() and not q.name.startswith(".")
                )
            )
        else:
            paths.append(p)
    if not paths:
        raise ValueError("sources must not be empty")
    spectra = [p for p in paths if recognize_bosz(p)]
    others = [p for p in paths if not recognize_bosz(p) and p.is_file()]
    base = spectra[0].parent if spectra else paths[0].parent
    return spectra + others, base


def _discover_wave_path(
    resolution: int, files: list[Path], base: Path, wave_path: Path | None
) -> Path:
    if wave_path is not None:
        if not wave_path.is_file():
            raise WavelengthGridError(f"wave_path {wave_path} does not exist")
        return wave_path
    expected = f"bosz2024_wave_r{resolution}.txt"
    candidates: list[Path] = []
    for candidate in (
        *(f for f in files if recognize_wave_grid(f)),
        base / expected,
        base / "wavelength_grids" / expected,
    ):
        if candidate.is_file() and candidate not in candidates:
            candidates.append(candidate)
    matching = [c for c in candidates if c.name == expected]
    if not matching:
        raise WavelengthGridError(
            f"no wavelength grid for r{resolution} found next to the spectra "
            f"({base}); pass wave_path= explicitly (official file: "
            f"https://archive.stsci.edu/hlsps/bosz/bosz2024/wavelength_grids/"
            f"{expected})"
        )
    if len({str(c.resolve()) for c in matching}) > 1:
        listed = "\n  ".join(str(c) for c in matching)
        raise WavelengthGridError(
            f"multiple candidate wavelength grids for r{resolution}:\n  {listed}\n"
            "pass wave_path= to choose one"
        )
    return matching[0]


def read_bosz(
    sources,
    wave_path: str | os.PathLike | None = None,
    am: float = 0.0,
    cm: float = 0.0,
    vmicro: int | None = None,
) -> SpectrumSet:
    """Read BOSZ 2024 resam spectra (directory or file list) into a set.

    One set holds one (resolution, [a/M], [C/M]) combination. A mirror
    directory interleaves every composition and vmicro variant of every
    node, so reading one is a selection, not an error: ``am``/``cm``
    (defaults 0, solar-scaled) pick the composition, and ``vmicro``
    (default ``None``: the atmosphere-family policy — mp 1, ms/ap 2
    km/s) picks each node's variant. The selection is recorded in the
    set notes (total files, selected files, policy). Explicitly curated
    sources that hold exactly one composition read identically with the
    defaults. With ``vmicro`` given, every node uses that value (a node
    whose file is absent is a hole, reported by the grid).
    """
    files, base = _collect_sources(sources)
    spectrum_files = [f for f in files if recognize_bosz(f)]
    if not spectrum_files:
        raise UnrecognizedFormatError(
            f"no BOSZ 2024 resam files found among {base}"
        )
    from stellarlib.models.bosz.design import family_for

    parsed = []
    for f in spectrum_files:
        facts = parse_filename(f.name)
        facts["_path"] = f
        parsed.append(facts)

    total = len(parsed)
    am = round(float(am), 2)
    cm = round(float(cm), 2)
    selected = [f for f in parsed if f["am"] == am and f["cm"] == cm]
    if not selected:
        available_am = sorted({f["am"] for f in parsed})
        available_cm = sorted({f["cm"] for f in parsed})
        raise ValueError(
            f"no spectra at [a/M] = {am:+.2f}, [C/M] = {cm:+.2f} among the "
            f"{total} files of {base}; available [a/M] {available_am}, "
            f"[C/M] {available_cm} — pass am=/cm= to choose a composition"
        )
    if vmicro is None:
        kept = [
            f for f in selected
            if f["vmicro"] == family_for(int(f["teff"]), float(f["logg"]))[1]
        ]
    else:
        kept = [f for f in selected if f["vmicro"] == int(vmicro)]
    if not kept:
        raise ValueError(
            f"no spectra at [a/M] = {am:+.2f}, [C/M] = {cm:+.2f} with the "
            f"requested vmicro selection among the {total} files of {base}"
        )
    all_facts = kept
    vmicro_policy = "family" if vmicro is None else int(vmicro)

    def _single(key: str) -> object:
        values = sorted({facts[key] for facts in all_facts})
        if len(values) != 1:
            raise MetadataConflictError(
                f"one set holds one {key}; found {values} — split the "
                "sources per product"
            )
        return values[0]

    resolution = _single("resolution")
    vmicro_values = sorted({facts["vmicro"] for facts in all_facts})
    vmicro = vmicro_values[0] if len(vmicro_values) == 1 else None

    wave_file = _discover_wave_path(resolution, files, base, Path(wave_path) if wave_path else None)
    wave = read_wave_grid(wave_file)

    teffs = tuple(sorted({float(facts["teff"]) for facts in all_facts}))
    loggs = tuple(sorted({float(facts["logg"]) for facts in all_facts}))
    mhs = tuple(sorted({float(facts["mh"]) for facts in all_facts}))
    dynamic = {
        "teff": teffs,
        "logg": loggs,
        "mh": mhs,
        "am": am,
        "cm": cm,
        "resolution": resolution,
    }
    if vmicro is not None:
        dynamic["vmicro"] = vmicro
    parameters = resolve_parameters("bosz", "resampled_spectra", dynamic)

    identity = bosz_identity({"resolution": resolution})
    assets = tuple(
        DataAsset(
            filename=facts["_path"].name,
            size_bytes=facts["_path"].stat().st_size,
            source=SourceRecord(
                source_class=SourceClass.OFFICIAL_ARCHIVE,
                url=mast_url(facts),
                status=SourceStatus.NOT_CHECKED,
                note="MAST HLSP tree layout verified 2026-10-17; no "
                "upstream checksums — sha256 recorded at database build",
            ),
        )
        for facts in all_facts
    )
    wave_asset = DataAsset(
        filename=wave_file.name,
        size_bytes=wave_file.stat().st_size,
        source=SourceRecord(
            source_class=SourceClass.OFFICIAL_ARCHIVE,
            url=(
                "https://archive.stsci.edu/hlsps/bosz/bosz2024/"
                f"wavelength_grids/{wave_file.name}"
            ),
            status=SourceStatus.NOT_CHECKED,
        ),
    )
    provenance = Provenance(
        assets=(wave_asset,) + assets,
        scientific_references=BOSZ_REFERENCES,
    )

    nodes = tuple(
        GridNode(
            coordinates={
                "teff": float(facts["teff"]),
                "log_surface_gravity": float(facts["logg"]),
                "m_h": float(facts["mh"]),
            },
            path=facts["_path"],
            facts={
                key: facts[key]
                for key in ("family", "teff", "logg", "mh", "am", "cm", "vmicro", "resolution")
            },
        )
        for facts in all_facts
    )

    def loader(node: GridNode) -> Spectrum:
        native = read_resam(node.path)
        if native.flux.size != wave.size:
            raise WavelengthGridError(
                f"spectrum rows ({native.flux.size}) and wavelength grid "
                f"rows ({wave.size}) disagree -- wrong wave file for "
                f"{node.path}?"
            )
        paired = type(native)(
            wavelength=np.array(wave, dtype=np.float64, copy=True),
            flux=native.flux,
            continuum=native.continuum,
            header=native.header,
            metadata=dict(native.metadata),
            provenance_keys=dict(native.provenance_keys),
        )
        return Spectrum(
            native=paired,
            identity=identity,
            parameters=parameters,
            field_specs=dict(_FIELD_SPECS),
            provenance=provenance,
            selection=dict(node.coordinates),
            unified_view=UnifiedView(paired, dict(_FIELD_SPECS)),
        )

    return SpectrumSet(
        nodes=nodes,
        identity=identity,
        loader=loader,
        selectable=_SELECTABLE,
        parameters=parameters,
        provenance=provenance,
        notes={
            "wave_file": str(wave_file),
            "wave_rows": int(wave.size),
            "selection": {
                "am": am,
                "cm": cm,
                "vmicro": vmicro_policy,
                "files_total": total,
                "files_selected": len(all_facts),
            },
            "backend_notes": list(backend_notes("bosz")),
        },
    )
