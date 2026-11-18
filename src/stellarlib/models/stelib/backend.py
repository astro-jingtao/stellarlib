"""STELIB backend: the empirical-library product around the raw parser.

The upstream publishing unit is the VizieR catalog tree III/232 itself:
the star table ``table.dat``, the corrected spectra ``cor_sp/`` (the
published library — interstellar-extinction and radial-velocity
corrected, 255 files), their raw counterparts ``obs_sp/`` (258 files),
the catalog's own ``ReadMe``, and the published atlas ``stelib.ps.gz``.
:func:`read_stelib` builds the set from the tree's ``cor_sp`` spectra
joined to the star table by the exact member names the table's
spectrum-file columns carry.

The set is an empirical scatter, not a rectangular model grid, and
every star sits on its own wavelength grid: queries are nearest-star,
never interpolation — a weighted mix of two observed stars is not an
observation of anything, and there is no shared sampling grid to mix
on. Stars whose table row reports no value for any of the three
parameters (48 of 255) stay held data — the product mirrors them like
every other file — but carry no query coordinates; they are recorded
by star name in the set's notes, never silently dropped.
"""

from __future__ import annotations

import os
from pathlib import Path

from stellarlib.core import ModelIdentity
from stellarlib.exceptions import (
    MetadataConflictError,
    UnrecognizedFormatError,
)
from stellarlib.models.stelib.reader import (
    is_corrected_spectrum,
    read_star_table,
    read_stelib_fits,
    recognize_stelib_fits,
    star_table_among,
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

__all__ = ["STELIB_REFERENCES", "read_stelib", "recognize_stelib", "stelib_identity"]

_SELECTABLE = ("teff", "log_surface_gravity", "m_h")
STELIB_REFERENCES = references("stelib")


def stelib_identity() -> ModelIdentity:
    return ModelIdentity(
        family="stelib",
        release="2003",
        product="library",
        physics_configuration="empirical library (whole corrected-spectrum sample)",
        description=(
            "STELIB empirical stellar spectra (JKT 1m and SSO 2.3m; Le "
            "Borgne 2003, VizieR III/232), absolute flux density per star"
        ),
    )


def recognize_stelib(path: str | Path) -> bool:
    """The backend's sniffer: the tree's spectrum-file naming."""
    return recognize_stelib_fits(path)


def _collect_files(sources) -> list[Path]:
    files: list[Path] = []
    for source in [sources] if isinstance(sources, (str, os.PathLike)) else sources:
        path = Path(source)
        if path.is_dir():
            files.extend(
                sorted(
                    q
                    for q in path.rglob("*")
                    if q.is_file() and not q.name.startswith(".")
                )
            )
        else:
            files.append(path)
    if not files:
        raise ValueError("sources must not be empty")
    return files


def read_stelib(sources, table_path=None) -> SpectrumSet:
    """Read the STELIB library into a set: the catalog tree (or its
    ``cor_sp`` subdirectory), or a list of spectrum files plus the star
    table (``table_path=``; discovered among the sources when omitted; a
    set without the table is refused). ``obs_sp`` files among the
    sources are held counterparts, not set members — the published
    library is the corrected set."""
    files = _collect_files(sources)
    table = Path(table_path) if table_path is not None else star_table_among(files)
    if table is None:
        raise ValueError(
            "no STELIB star table (table.dat) among the sources or given "
            "as table_path=; the table is part of the product — without "
            "it the set cannot state its stars' coordinates"
        )
    rows = read_star_table(table)

    by_cor_name: dict[str, dict] = {}
    for row in rows:
        for name in row["cor_files"]:
            if name in by_cor_name:
                raise MetadataConflictError(
                    f"the star table states {name} for both "
                    f"{by_cor_name[name]['star']!r} and {row['star']!r}"
                )
            by_cor_name[name] = row

    members: dict[str, Path] = {}
    observed: list[Path] = []
    for path in files:
        if not recognize_stelib_fits(path):
            continue
        if is_corrected_spectrum(path):
            if path.name in members:
                raise MetadataConflictError(
                    f"the sources state {path.name} twice ({members[path.name]}, {path})"
                )
            members[path.name] = path
        else:
            observed.append(path)
    if not members:
        raise UnrecognizedFormatError(
            f"no STELIB corrected spectra (*_V<version>.fits) found among "
            f"{Path(files[0]).parent}"
        )
    missing_rows = sorted(set(members) - set(by_cor_name))
    if missing_rows:
        raise MetadataConflictError(
            f"{len(missing_rows)} held corrected spectrum file(s) have no "
            f"star-table row (first {missing_rows[0]}); the package is "
            "inconsistent — re-fetch the table with the spectra"
        )

    parameterized: list[tuple[Path, dict]] = []
    without_parameters: list[dict] = []
    second_spectra: list[dict] = []
    for row in rows:
        held = [name for name in row["cor_files"] if name in members]
        if not held:
            continue
        # a star with two corrected spectra (HD 053929: the two observing
        # runs) is one scatter point: the table's own cSpFile1-before-
        # cSpFile2 order picks the served file; the extra file stays
        # held data, recorded — two nodes at one coordinate would not be
        # a scatter anymore
        second_spectra.extend(
            {"star": row["star"], "spectrum_file": name} for name in held[1:]
        )
        if None in (row["teff"], row["log_surface_gravity"], row["m_h"]):
            without_parameters.append({"star": row["star"], "spectrum_file": held[0]})
        else:
            parameterized.append((members[held[0]], row))
    rows_without_corrected = [
        {"star": row["star"], "observed_only": row["obs_files"]}
        for row in rows
        if not row["cor_files"]
    ]

    identity = stelib_identity()
    parameters = resolve_parameters(
        "stelib",
        "library",
        {
            "teff": tuple(float(row["teff"]) for _, row in parameterized),
            "logg": tuple(
                float(row["log_surface_gravity"]) for _, row in parameterized
            ),
            "m_h": tuple(float(row["m_h"]) for _, row in parameterized),
        },
    )
    field_specs = field_spec_dict("stelib", "library")
    provenance = _provenance(members, table)

    nodes = tuple(
        GridNode(
            coordinates={
                "teff": float(row["teff"]),
                "log_surface_gravity": float(row["log_surface_gravity"]),
                "m_h": float(row["m_h"]),
            },
            path=path,
            facts={
                "star_name": row["star"],
                "spectral_type": row["spectral_type"],
                "spectrum_file": path.name,
                "observed_counterparts": row["obs_files"],
                "table_row_ra": row["ra"],
                "table_row_dec": row["dec"],
            },
        )
        for path, row in parameterized
    )

    def loader(node: GridNode) -> Spectrum:
        native = read_stelib_fits(node.path, node.facts["spectrum_file"])
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
            "star_table": table.name,
            "spectra_held": len(members),
            "spectra_served": len(parameterized),
            "observed_counterparts_held": len(observed),
            "stars_without_parameters": without_parameters,
            "second_corrected_spectra": second_spectra,
            "rows_without_corrected_spectrum": rows_without_corrected,
            "backend_notes": list(backend_notes("stelib")),
        },
    )


def _provenance(members: dict[str, Path], table: Path) -> Provenance:
    from stellarlib.models.stelib.design import CATALOG_BASES

    assets = [
        DataAsset(
            filename=name,
            size_bytes=path.stat().st_size,
            source=SourceRecord(
                source_class=SourceClass.OFFICIAL_ORIGIN,
                url=None,
                status=SourceStatus.NOT_CHECKED,
                note=(
                    "corrected-spectrum FITS of the VizieR III/232 tree "
                    "(cor_sp/); the exact fetch URL is the manifest's own "
                    f"record — the catalog's bases: {sorted(CATALOG_BASES)}"
                ),
            ),
        )
        for name, path in sorted(members.items())
    ]
    assets.append(
        DataAsset(
            filename=table.name,
            size_bytes=table.stat().st_size,
            source=SourceRecord(
                source_class=SourceClass.OFFICIAL_ARCHIVE,
                url=None,
                status=SourceStatus.NOT_CHECKED,
                note="the catalog's star table, joined to the corrected "
                "spectra by the table's own spectrum-file columns",
            ),
        )
    )
    return Provenance(assets=tuple(assets), scientific_references=STELIB_REFERENCES)
