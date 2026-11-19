"""MILES reader: single-star FITS spectra and the official parameter table.

One library star is one FITS file — a REDUCEME product: a primary image
HDU of a single row whose pixels are the fluxes. The wavelength axis is
the header's own linear WCS (``CRPIX1``/``CRVAL1``/``CDELT1``), stated
by the file as metadata; stellarlib computes the axis from exactly those
values and records the derivation. The flux pixels carry the library's
relative calibration: the FITS header states no flux unit, and the
official library page says only a subset is absolutely calibrated
(available from the authors upon request) — so the flux is read verbatim
as relative and never mapped onto a physical flux field.

The parameter table is fixed-width ASCII; its MILES column holds the
s-number each FITS file is named for, and ``--`` marks a value the table
does not report for that star.
"""

from __future__ import annotations

import io
from pathlib import Path
import re
from typing import Any, BinaryIO

from astropy.io import fits
import numpy as np

from stellarlib.native import NativeSpectrum

__all__ = [
    "MILES_FITS_RE",
    "PARAMS_NAME_RE",
    "parse_params_table",
    "read_miles_fits",
    "read_params_table",
    "recognize_miles",
    "s_number_of",
]

# the library's own naming: s0001.fits .. s0985.fits, assigned by
# right-ascension order (a station fact; the s-numbers are the join key
# between the FITS files and the parameter table's MILES column)
MILES_FITS_RE = re.compile(r"^s(\d{4})\.fits$")
PARAMS_NAME_RE = re.compile(r"^paramsMILES.*\.txt$")

# the parameter table's fixed-width columns, located by their header
# labels in order (positions are read from each file's own header line,
# never hardcoded — a shifted layout is a format change, caught loudly)
_PARAM_LABELS = (
    "Name", "MILES", "CaT", "SpT", "Teff", "log g", "[Fe/H]",
    "Refs", "Libraries",
)


def recognize_miles(path: str | Path) -> bool:
    """True for names following the library's s-number FITS grammar."""
    return MILES_FITS_RE.match(Path(path).name) is not None


def s_number_of(name: str) -> int | None:
    """The s-number a filename carries, or None when it carries none."""
    match = MILES_FITS_RE.match(Path(name).name)
    return int(match.group(1)) if match else None


def read_miles_fits(
    source: str | Path | bytes | BinaryIO, name: str
) -> NativeSpectrum:
    """Read one MILES FITS spectrum verbatim.

    ``source`` is a path, raw bytes (a tar member), or an open binary
    file; ``name`` is the member name (the s-number filename, also
    expected in the header's FITSFI card). The flux array is the image
    row as stored (float32, negatives included — no clipping); the
    wavelength axis is computed from the header's linear WCS and the
    derivation is recorded.
    """
    if isinstance(source, (str, Path)):
        with fits.open(source) as hdul:
            return _read_primary(hdul, name)
    payload = (
        io.BytesIO(source) if isinstance(source, bytes) else io.BytesIO(source.read())
    )
    with fits.open(payload) as hdul:
        return _read_primary(hdul, name)


def _read_primary(hdul: Any, name: str) -> NativeSpectrum:
    hdu = hdul[0]
    header = hdu.header
    if header.get("NAXIS") != 2 or header.get("NAXIS2") != 1:
        raise ValueError(
            f"{name}: expected a single-row image HDU (NAXIS=2, NAXIS2=1); "
            f"the header states NAXIS={header.get('NAXIS')}, "
            f"NAXIS2={header.get('NAXIS2')} — the published format changed"
        )
    flux = np.asarray(hdu.data[0])
    crpix1 = float(header["CRPIX1"])
    crval1 = float(header["CRVAL1"])
    cdelt1 = float(header["CDELT1"])
    wavelength = crval1 + cdelt1 * (np.arange(len(flux), dtype=float) + 1.0 - crpix1)
    metadata = {
        "s_number": s_number_of(name),
        "n_pixels": int(header["NAXIS1"]),
    }
    for key in ("OBJECT", "FITSFI", "AIRMASS", "TIMEXPO"):
        if key in header:
            metadata[key.lower()] = header[key]
    return NativeSpectrum(
        wavelength=wavelength,
        flux=flux,
        continuum=None,
        header=tuple(str(card) for card in header.cards),
        metadata=metadata,
        provenance_keys={
            "spectrum_file": name,
            "wavelength_from": (
                "header linear WCS: value = CRVAL1 + CDELT1 * "
                "(pixel - CRPIX1), pixel 1-based"
            ),
            "flux_convention": (
                "relative flux, arbitrary per-star normalization; the "
                "header states no flux unit"
            ),
        },
    )


def parse_params_table(text: str) -> dict[int, dict]:
    """Parse the official parameter table into rows keyed by s-number.

    Columns are fixed-width, located by the header labels; ``--`` marks a
    value the table does not report (kept as ``None``, never invented).
    Duplicate s-numbers and unlocatable labels are loud errors — the join
    between this table and the FITS files is the product's contract.
    """
    lines = text.splitlines()
    if not lines:
        raise ValueError("params table is empty")
    header = lines[0]
    starts: list[int] = []
    position = 0
    for label in _PARAM_LABELS:
        try:
            index = header.index(label, position)
        except ValueError:
            raise ValueError(
                f"params table header does not state the expected columns "
                f"{list(_PARAM_LABELS)} (missing {label!r}) — the published "
                "format changed"
            ) from None
        starts.append(index)
        position = index + len(label)

    rows: dict[int, dict] = {}
    for number, line in enumerate(lines[3:], start=4):
        if not line.strip():
            continue
        fields = [
            line[starts[i]:(starts[i + 1] if i + 1 < len(starts) else len(line))].strip()
            for i in range(len(_PARAM_LABELS))
        ]
        miles_field = fields[1]
        if not miles_field.isdigit():
            raise ValueError(
                f"params table line {number}: the MILES column holds "
                f"{miles_field!r}, not an s-number — the published format "
                "changed"
            )
        s_number = int(miles_field)
        if s_number in rows:
            raise ValueError(
                f"params table states s{s_number:04d} twice "
                f"({rows[s_number]['name']!r}, {fields[0]!r})"
            )
        rows[s_number] = {
            "name": fields[0] or None,
            "cat_number": fields[2] or None,
            "spectral_type": fields[3] or None,
            "teff": int(fields[4]) if fields[4].isdigit() else None,
            "log_surface_gravity": _optional_float(fields[5]),
            "m_h": _optional_float(fields[6]),
            "refs": fields[7] or None,
            "libraries": fields[8] or None,
        }
    if not rows:
        raise ValueError("params table holds no star rows")
    return rows


def _optional_float(field: str) -> float | None:
    if field in ("", "--"):
        return None
    return float(field)


def read_params_table(path: str | Path) -> dict[int, dict]:
    """Read and parse a parameter-table file (see :func:`parse_params_table`)."""
    rows = parse_params_table(Path(path).read_text(encoding="utf-8"))
    for row in rows.values():
        row["table_file"] = Path(path).name
    return rows


def params_table_among(files: list[Path]) -> Path | None:
    """The parameter table among a source list, or None when absent."""
    for path in files:
        if PARAMS_NAME_RE.match(path.name):
            return path
    return None
