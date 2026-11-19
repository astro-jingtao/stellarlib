"""BaSeL raw readers: filename grammar and Kurucz-format ``*.cor`` files.

A BaSeL library version is one set of ``*.cor`` files, one per
metallicity, each holding every spectrum of that metallicity in
Kurucz-format blocks: a 153-line wavelength header (1221 points, nm,
FORTRAN ``8f10.1``) stated once per file, then one 176-line block per
spectrum — a ``(2i6, 4f6.2)`` parameter line (spectrum number, Teff,
log g, [Fe/H], plus two columns the author marks unused) followed by
175 lines of 1221 flux values (``7e11.4``, stated unit
erg/s/cm/cm/Hz; the closure test pins it as the Eddington flux per
steradian, see the backend docstring). Both published versions (2.2
``lbc96_*.cor``, 3.1 ``wlbc99_*.cor``) share this grammar and the same
wavelength grid.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
import re

import numpy as np

from stellarlib.exceptions import DataIntegrityError

__all__ = [
    "BLOCK_LINES",
    "FILENAME_RE",
    "HEADER_LINES",
    "N_WAVE",
    "CorBlock",
    "CorFile",
    "parse_cor",
    "parse_cor_index",
    "parse_filename",
    "recognize_basel",
]

# {lbc96|wlbc99}_{metallicity}.cor — the prefix names the version's base
# models (lbc96 = Lejeune/Cuisinier/Buser 1996, BaSeL 2.2; wlbc99 =
# Westera/Lejeune/Buser/Cuisinier 1999 recalibration, BaSeL 3.1) and the
# metallicity token encodes [M/H] = -/+ NN/10 dex (m05 = -0.5, p10 = +1.0)
FILENAME_RE = re.compile(r"(lbc96|wlbc99)_([mp]\d{2})\.cor$")

# The published files' fixed structure: one wavelength header of 153
# lines (1221 values in 8f10.1), then 176-line blocks (one parameter
# line + 175 flux lines of 7e11.4; the last flux line carries the 3
# remaining values). Both versions publish exactly this shape.
HEADER_LINES = 153
N_WAVE = 1221
BLOCK_LINES = 176

_RELEASE_OF_PREFIX = {"lbc96": "2.2", "wlbc99": "3.1"}


def parse_filename(name: str) -> dict:
    """Parse a BaSeL ``*.cor`` filename into version and metallicity."""
    match = FILENAME_RE.match(Path(name).name)
    if match is None:
        raise ValueError(f"{name}: not a BaSeL cor filename")
    prefix, token = match.groups()
    sign = -1.0 if token[0] == "m" else 1.0
    return {
        "prefix": prefix,
        "release": _RELEASE_OF_PREFIX[prefix],
        "m_h": sign * int(token[1:]) / 10.0,
    }


def recognize_basel(source: str | os.PathLike) -> bool:
    """Cheap sniff: does this source's name follow the cor grammar?"""
    try:
        return FILENAME_RE.match(Path(source).name) is not None
    except (TypeError, ValueError):
        return False


@dataclasses.dataclass(frozen=True)
class CorBlock:
    """One spectrum block's parameter line, verbatim in its values.

    ``vturb_column``/``h_fraction_column`` are the parameter line's last
    two fields. The author's ReadMe marks them unused ("the turbulent
    velocity ... is always zero, and the number fraction of hydrogen
    also not used") — the published files carry 0.0 or 2.0 and 0.0 or
    1.25 there; they are recorded exactly as stated, never interpreted.
    """

    spectrum_number: int
    teff: float
    logg: float
    feh_column: float
    vturb_column: float
    h_fraction_column: float
    param_line: int  # 0-based line index of the parameter line


@dataclasses.dataclass(frozen=True)
class CorFile:
    """One ``*.cor`` file fully parsed: the shared wavelength grid and
    every block with its flux array."""

    name: str
    wavelength: np.ndarray
    blocks: tuple[CorBlock, ...]
    fluxes: tuple[np.ndarray, ...]


def _strip_trailing_blanks(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _parse_header(lines: list[str], name: str) -> np.ndarray:
    values = []
    for ln in lines[:HEADER_LINES]:
        for i in range(0, len(ln), 10):
            token = ln[i : i + 10].strip()
            if not token:
                continue
            try:
                values.append(float(token))
            except ValueError:
                raise DataIntegrityError(
                    f"{name}: wavelength header token {token!r} is not a "
                    "number (FORTRAN 8f10.1)"
                ) from None
    if len(values) != N_WAVE:
        raise DataIntegrityError(
            f"{name}: wavelength header holds {len(values)} values, "
            f"the published format states {N_WAVE}"
        )
    return np.asarray(values)


def _parse_param_line(ln: str, name: str, line_no: int) -> CorBlock:
    try:
        return CorBlock(
            spectrum_number=int(ln[0:6]),
            teff=float(ln[6:12]),
            logg=float(ln[12:18]),
            feh_column=float(ln[18:24]),
            vturb_column=float(ln[24:30]),
            h_fraction_column=float(ln[30:36]),
            param_line=line_no,
        )
    except ValueError as exc:
        raise DataIntegrityError(
            f"{name}: parameter line {line_no + 1} does not follow the "
            f"(2i6, 4f6.2) grammar: {ln!r} ({exc})"
        ) from None


def _block_flux(lines: list[str], block: CorBlock, name: str) -> np.ndarray:
    start = block.param_line + 1
    values = []
    for ln in lines[start : start + BLOCK_LINES - 1]:
        stripped = ln.rstrip()
        for i in range(0, len(stripped), 11):
            token = stripped[i : i + 11].strip()
            if not token:
                continue
            try:
                values.append(float(token))
            except ValueError:
                raise DataIntegrityError(
                    f"{name}: block {block.spectrum_number} flux token "
                    f"{token!r} is not a number (FORTRAN 7e11.4)"
                ) from None
    if len(values) != N_WAVE:
        raise DataIntegrityError(
            f"{name}: block {block.spectrum_number} (Teff {block.teff}, "
            f"log g {block.logg}) holds {len(values)} flux values, "
            f"the wavelength grid has {N_WAVE}"
        )
    return np.asarray(values)


def _check_structure(lines: list[str], name: str) -> int:
    n_body = len(lines) - HEADER_LINES
    if n_body < 0 or n_body % BLOCK_LINES != 0:
        raise DataIntegrityError(
            f"{name}: {len(lines)} lines do not fit the published "
            f"structure ({HEADER_LINES} header + {BLOCK_LINES}-line "
            "blocks)"
        )
    return n_body // BLOCK_LINES


def parse_cor_index(text: str, name: str) -> tuple[np.ndarray, tuple[CorBlock, ...]]:
    """Parse the wavelength grid and every block's parameter line.

    The flux lines are skipped — this is the cheap index pass for set
    construction; spectra materialize through :func:`parse_cor` on
    demand.
    """
    lines = _strip_trailing_blanks(text.splitlines())
    n_blocks = _check_structure(lines, name)
    wavelength = _parse_header(lines, name)
    blocks = tuple(
        _parse_param_line(lines[HEADER_LINES + k * BLOCK_LINES], name,
                          HEADER_LINES + k * BLOCK_LINES)
        for k in range(n_blocks)
    )
    return wavelength, blocks


def parse_cor(text: str, name: str) -> CorFile:
    """Parse one ``*.cor`` file completely, fluxes included."""
    lines = _strip_trailing_blanks(text.splitlines())
    n_blocks = _check_structure(lines, name)
    wavelength = _parse_header(lines, name)
    blocks = []
    fluxes = []
    for k in range(n_blocks):
        block = _parse_param_line(lines[HEADER_LINES + k * BLOCK_LINES], name,
                                  HEADER_LINES + k * BLOCK_LINES)
        blocks.append(block)
        fluxes.append(_block_flux(lines, block, name))
    return CorFile(
        name=name,
        wavelength=wavelength,
        blocks=tuple(blocks),
        fluxes=tuple(fluxes),
    )
