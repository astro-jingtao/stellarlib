"""MILES backend: the empirical-library product around the raw parser.

The upstream publishing unit is the whole library: one official FITS
tarball (985 single-star files, the s-number naming), one official
parameter table, and the library's own web page stating the coverage
facts. :func:`read_miles` builds the set from that package — the tarball
itself (spectra stream from its members), an extracted directory, or a
list of files — joining every held spectrum to its parameter-table row
by s-number.

The set is an empirical scatter, not a rectangular model grid: each star
is one observed point in (Teff, log g, [Fe/H]) with the table's stated
values, and queries are nearest-star, never interpolation — the fluxes
carry arbitrary per-star normalizations, so a weighted mix of two stars'
spectra is not a spectrum of anything. Stars whose table row reports no
value for any of the three parameters (39 of 985 hold ``--`` in
``[Fe/H]``) stay held data — the product mirrors them like every other
file — but carry no query coordinates; they are recorded by s-number and
name in the set's notes, never silently dropped.
"""

from __future__ import annotations

import os
from pathlib import Path
import tarfile

from stellarlib.core import ModelIdentity
from stellarlib.exceptions import (
    MetadataConflictError,
    UnrecognizedFormatError,
)
from stellarlib.models.miles.design import (
    LIBRARY_TAR_NAME,
    LIBRARY_TAR_URL,
    PARAMS_NAME,
    PARAMS_URL,
)
from stellarlib.models.miles.reader import (
    MILES_FITS_RE,
    params_table_among,
    read_miles_fits,
    read_params_table,
    recognize_miles,
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

__all__ = ["MILES_REFERENCES", "miles_identity", "read_miles", "recognize_miles"]

_SELECTABLE = ("teff", "log_surface_gravity", "m_h")
_PER_FILE_URL = "https://research.iac.es/proyecto/miles/media/stellar_libraries/MILES"
MILES_REFERENCES = references("miles")


def miles_identity() -> ModelIdentity:
    return ModelIdentity(
        family="miles",
        release="2011",
        product="library",
        version="v9.1",
        physics_configuration="empirical library (whole observed sample)",
        description=(
            "MILES empirical stellar spectra (INT 2.5m; Sánchez-Blázquez "
            "2006 library with Falcón-Barroso 2011 updated parameters), "
            "relative flux per star"
        ),
    )


def _collect_files(sources) -> list[Path]:
    files: list[Path] = []
    for source in (
        [sources] if isinstance(sources, (str, os.PathLike)) else sources
    ):
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


def read_miles(sources, params_path=None) -> SpectrumSet:
    """Read the MILES library into a set: the official FITS tarball, an
    extracted directory, or a list of FITS files plus the parameter table
    (``params_path=``; discovered among the sources — or next to a
    lone tarball — when omitted; a set without the table is refused)."""
    files = _collect_files(sources)
    tars = [
        f for f in files if f.name.endswith((".tar", ".tar.gz", ".tgz"))
    ]
    if len(tars) > 1:
        raise ValueError(
            "one set holds one library tarball; found "
            f"{[f.name for f in tars]} — split the sources per product"
        )
    table = (
        Path(params_path)
        if params_path is not None
        else params_table_among(files)
    )
    if table is None and tars:
        table = params_table_among(
            sorted(p for p in tars[0].parent.iterdir() if p.is_file())
        )
    if table is None:
        raise ValueError(
            "no MILES parameter table (paramsMILES*.txt) among the "
            "sources, next to the tarball, or given as params_path=; the "
            "table is part of the product — without it the set cannot "
            "state its stars' coordinates"
        )
    rows = read_params_table(table)

    members: dict[int, Path | str]
    tar_path = tars[0] if tars else None
    if tar_path is not None:
        loose = [f for f in files if recognize_miles(f)]
        if loose:
            raise ValueError(
                f"the sources hold the tarball {tar_path.name} AND loose "
                f"FITS files ({[f.name for f in loose[:3]]}"
                f"{'...' if len(loose) > 3 else ''}) — one set is built "
                "from either the tarball or the files, never both; split "
                "the sources"
            )
        members = _tar_members(tar_path)
    else:
        members = {}
        for f in files:
            if not recognize_miles(f):
                continue
            s = int(MILES_FITS_RE.match(f.name).group(1))
            if s in members:
                raise MetadataConflictError(
                    f"the sources state s{s:04d} twice "
                    f"({members[s]}, {f.name})"
                )
            members[s] = f
    if not members:
        raise UnrecognizedFormatError(
            f"no MILES spectra (s????.fits) found among {files[0].parent}"
        )
    missing_rows = sorted(set(members) - set(rows))
    if missing_rows:
        raise MetadataConflictError(
            f"{len(missing_rows)} held spectrum file(s) have no parameter-"
            f"table row (first s{missing_rows[0]:04d}); the package is "
            "inconsistent — re-fetch the table with the spectra"
        )

    parameterized: list[tuple[int, dict]] = []
    without_parameters: list[dict] = []
    for s in sorted(members):
        row = rows[s]
        if None in (row["teff"], row["log_surface_gravity"], row["m_h"]):
            without_parameters.append({"s_number": s, "name": row["name"]})
        else:
            parameterized.append((s, row))

    identity = miles_identity()
    parameters = resolve_parameters(
        "miles",
        "library",
        {
            "teff": tuple(row["teff"] for _, row in parameterized),
            "logg": tuple(
                row["log_surface_gravity"] for _, row in parameterized
            ),
            "m_h": tuple(row["m_h"] for _, row in parameterized),
        },
    )
    field_specs = field_spec_dict("miles", "library")
    provenance = _provenance(tar_path, table, members)

    nodes = tuple(
        GridNode(
            coordinates={
                "teff": float(row["teff"]),
                "log_surface_gravity": float(row["log_surface_gravity"]),
                "m_h": float(row["m_h"]),
            },
            path=tar_path if tar_path is not None else members[s],
            facts=(
                {"s_number": s, "star_name": row["name"],
                 "spectral_type": row["spectral_type"],
                 "cat_number": row["cat_number"],
                 "parameter_refs": row["refs"],
                 "libraries": row["libraries"],
                 "member": members[s]}
                if tar_path is not None
                else {"s_number": s, "star_name": row["name"],
                      "spectral_type": row["spectral_type"],
                      "cat_number": row["cat_number"],
                      "parameter_refs": row["refs"],
                      "libraries": row["libraries"],
                      "file": members[s].name}
            ),
        )
        for s, row in parameterized
    )

    def loader(node: GridNode) -> Spectrum:
        name = f"s{node.facts['s_number']:04d}.fits"
        if "member" in node.facts:
            with tarfile.open(node.path, "r") as tf:
                payload = tf.extractfile(node.facts["member"]).read()
            native = read_miles_fits(payload, name)
        else:
            native = read_miles_fits(node.path, name)
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
            "params_table": table.name,
            "spectra_held": len(members),
            "stars_without_parameters": without_parameters,
            "backend_notes": list(backend_notes("miles")),
        },
    )


def _tar_members(tar: Path) -> dict[int, str]:
    """The library's s-number members of one tarball, flat, as published."""
    with tarfile.open(tar, "r") as tf:
        names = [
            m.name.rsplit("/", 1)[-1]
            for m in tf.getmembers()
            if m.isfile() and recognize_miles(m.name.rsplit("/", 1)[-1])
        ]
    members: dict[int, str] = {}
    for name in names:
        s = int(MILES_FITS_RE.match(name).group(1))
        if s in members:
            raise MetadataConflictError(f"{tar.name} holds s{s:04d} twice")
        members[s] = name
    return members


def _provenance(tar: Path | None, table: Path, members: dict) -> Provenance:
    assets = []
    if tar is not None:
        assets.append(
            DataAsset(
                filename=tar.name,
                size_bytes=tar.stat().st_size,
                source=SourceRecord(
                    source_class=SourceClass.OFFICIAL_ARCHIVE,
                    url=LIBRARY_TAR_URL if tar.name == LIBRARY_TAR_NAME else None,
                    status=SourceStatus.NOT_CHECKED,
                    note="whole official FITS tarball (985 single-star "
                    "files); no upstream checksum is published",
                ),
            )
        )
    else:
        assets.extend(
            DataAsset(
                filename=Path(path).name,
                size_bytes=Path(path).stat().st_size,
                source=SourceRecord(
                    source_class=SourceClass.OFFICIAL_ORIGIN,
                    url=f"{_PER_FILE_URL}/{Path(path).name}",
                    status=SourceStatus.NOT_CHECKED,
                    note="single-star FITS as also published per-file "
                    "beside the official tarball",
                ),
            )
            for _, path in sorted(members.items())
        )
    assets.append(
        DataAsset(
            filename=table.name,
            size_bytes=table.stat().st_size,
            source=SourceRecord(
                source_class=SourceClass.OFFICIAL_ARCHIVE,
                url=PARAMS_URL if table.name == PARAMS_NAME else None,
                status=SourceStatus.NOT_CHECKED,
                note="the official parameter table, joined to the spectra "
                "by s-number",
            ),
        )
    )
    return Provenance(assets=tuple(assets), scientific_references=MILES_REFERENCES)
