"""STELIB reader: corrected-spectrum FITS files and the III/232 star table.

One library star is one FITS file in the catalog tree's ``cor_sp``
subdirectory — a 1-D image (``NAXIS=1``, 6700 ``float32`` pixels) whose
``BUNIT`` states the physical unit ``erg /s /cm2 /angstroms``: the
library is absolute spectrophotometry at the observer (the paper states
a 3% overall photometric uncertainty), with the interstellar extinction
and radial-velocity corrections applied (the ``obs_sp`` counterparts
are the raw spectra; the published library is the corrected set). The
wavelength axis is the header's own linear WCS — and every star sits on
its own grid: the (CRVAL1, CDELT1) pairs differ per file (live-measured
across the whole 2026-11-18 fetch), so the axis is computed per file
from exactly those values and never assumed shared.

The star table ``table.dat`` is fixed-width ASCII with the layout the
catalog's own ReadMe documents byte by byte; the positions below are
that documented contract, and a field that fails to parse is a loud
error (a shifted layout is a format change), never a silent guess.
``-`` marks a value the table does not report. The spectrum-file columns
carry the exact ``cor_sp``/``obs_sp`` member names each row joins to.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from astropy.io import fits
import numpy as np

from stellarlib.native import NativeSpectrum

__all__ = [
    "COR_SUFFIX_RE",
    "OBS_SUFFIX_RE",
    "TABLE_NAME",
    "is_corrected_spectrum",
    "parse_star_table",
    "read_star_table",
    "read_stelib_fits",
    "recognize_stelib_fits",
    "star_table_among",
]

TABLE_NAME = "table.dat"

# the tree's own naming, measured across every file of the 2026-11-18
# fetch (all 255 cor, all 258 obs): corrected spectra end in
# _V<version>.fits (HD002857_V3.2.fits, also the second-spectrum form
# HD053929ss_V3.2.fits), observed counterparts in _moy.fits ("moyenne",
# the per-run mean) optionally followed by the run's tag
# (HD53929_moy_sso.fits, _jkt, LTT4364_moy_save.fits)
COR_SUFFIX_RE = re.compile(r"_V\d+\.\d+\.fits$")
OBS_SUFFIX_RE = re.compile(r"_moy(_[a-z0-9]+)?\.fits$")

# table.dat's fixed-width layout, exactly as the catalog's ReadMe
# documents it (bytes are 1-based inclusive; the ReadMe is the contract,
# mirrored as a product part). Slice bounds are 0-based half-open.
_TABLE_COLUMNS = {
    "star": (0, 10),
    "rah": (11, 13),
    "ram": (14, 16),
    "ras": (17, 22),
    "de_sign": (23, 24),
    "ded": (24, 26),
    "dem": (27, 29),
    "des": (30, 34),
    "sptype": (35, 45),
    "teff": (47, 52),
    "logg": (53, 57),
    "feh": (58, 63),
    "vmag": (64, 70),
    "evmag": (71, 75),
    "av": (84, 88),
    "oname": (89, 118),
    "add": (119, 122),
    "cfile1": (123, 143),
    "ofile1": (144, 165),
    "cfile2": (166, 186),
    "ofile2": (187, 208),
}
# every row reaches at least the cSpFile2 column's end byte (186); rows
# whose oSpFile2 is present extend to 208 (live-measured: the real table
# holds 186..208-byte lines — trailing empty columns are simply absent)
_TABLE_LINE_MIN = _TABLE_COLUMNS["cfile2"][1]


def recognize_stelib_fits(path: str | Path) -> bool:
    """True for names following the tree's spectrum-file grammar
    (corrected or observed)."""
    name = Path(path).name
    return bool(COR_SUFFIX_RE.search(name) or OBS_SUFFIX_RE.search(name))


def is_corrected_spectrum(path: str | Path) -> bool:
    """True for the corrected-spectrum form (``_V<version>.fits``)."""
    return COR_SUFFIX_RE.search(Path(path).name) is not None


def read_stelib_fits(source: Any, name: str) -> NativeSpectrum:
    """Read one STELIB spectrum FITS verbatim.

    ``source`` is a path or open FITS payload; ``name`` is the member
    name. The flux array is the 1-D image as stored (float32, negatives
    included — no clipping); the wavelength axis is computed from the
    header's linear WCS and the derivation is recorded.
    """
    with fits.open(source) as hdul:
        hdu = hdul[0]
        header = hdu.header
        if header.get("NAXIS") != 1:
            raise ValueError(
                f"{name}: expected a 1-D image HDU (NAXIS=1); the header "
                f"states NAXIS={header.get('NAXIS')} — the published "
                "format changed"
            )
        flux = np.asarray(hdu.data)
        crpix1 = float(header["CRPIX1"])
        crval1 = float(header["CRVAL1"])
        cdelt1 = float(header["CDELT1"])
    wavelength = crval1 + cdelt1 * (np.arange(len(flux), dtype=float) + 1.0 - crpix1)
    metadata = {"n_pixels": len(flux)}
    for key in ("OBJECT", "BUNIT", "SPECTYPE", "ISEXTINC", "RADVEL", "EQUINOX"):
        if key in header:
            metadata[key.lower()] = header[key]
    metadata["corrected"] = bool(COR_SUFFIX_RE.search(name))
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
                "absolute observed flux density at the observer "
                "(BUNIT erg/s/cm^2/Angstrom; 3% overall photometric "
                "uncertainty per the paper); corrected spectra have the "
                "interstellar extinction and radial-velocity corrections "
                "applied (header ISEXTINC, RADVEL)"
            ),
        },
    )


def _field(line: str, name: str) -> str:
    start, end = _TABLE_COLUMNS[name]
    return line[start:end].strip()


def _optional_int(field_text: str) -> int | None:
    if field_text in ("", "-"):
        return None
    return int(field_text)


def _optional_float(field_text: str) -> float | None:
    if field_text in ("", "-"):
        return None
    return float(field_text)


def parse_star_table(text: str) -> list[dict]:
    """Parse ``table.dat`` into star rows, in file order.

    Columns are fixed-width at the ReadMe's documented byte positions;
    ``-`` marks a value the table does not report (kept as ``None``,
    never invented). A coordinate field that is neither a number, ``-``,
    nor empty is a loud error — the published layout changed.
    """
    rows: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if len(line) < _TABLE_LINE_MIN:
            raise ValueError(
                f"star table line {number}: {len(line)} characters, shorter "
                f"than the documented {_TABLE_LINE_MIN}-byte layout — the "
                "published format changed"
            )
        try:
            teff = _optional_int(_field(line, "teff"))
            logg = _optional_float(_field(line, "logg"))
            feh = _optional_float(_field(line, "feh"))
        except ValueError as exc:
            raise ValueError(
                f"star table line {number}: field parse failed ({exc}); "
                "the published layout changed"
            ) from None
        row = {
            "star": _field(line, "star") or None,
            "spectral_type": _field(line, "sptype") or None,
            "teff": teff,
            "log_surface_gravity": logg,
            "m_h": feh,
            "vmag": _optional_float(_field(line, "vmag")),
            "av": _optional_float(_field(line, "av")),
            "oname": _field(line, "oname") or None,
            "additional": _field(line, "add") or None,
            "cor_files": [
                name
                for name in (_field(line, "cfile1"), _field(line, "cfile2"))
                if name
            ],
            "obs_files": [
                name
                for name in (_field(line, "ofile1"), _field(line, "ofile2"))
                if name
            ],
            "ra": (
                f"{_field(line, 'rah')}h {_field(line, 'ram')}m {_field(line, 'ras')}s"
            ),
            "dec": (
                f"{_field(line, 'de_sign')}{_field(line, 'ded')}d "
                f"{_field(line, 'dem')}m {_field(line, 'des')}s"
            ),
        }
        rows.append(row)
    if not rows:
        raise ValueError("star table holds no star rows")
    return rows


def read_star_table(path: str | Path) -> list[dict]:
    """Read and parse a star-table file (see :func:`parse_star_table`)."""
    rows = parse_star_table(Path(path).read_text(encoding="utf-8"))
    for row in rows:
        row["table_file"] = Path(path).name
    return rows


def star_table_among(files: list[Path]) -> Path | None:
    """The star table among a source list, or None when absent."""
    for path in files:
        if path.name == TABLE_NAME:
            return path
    return None
