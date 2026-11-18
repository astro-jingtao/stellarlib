"""BOSZ 2024 raw readers: filename grammar, resam spectra, wave grids.

The released "resam" files are two-column gzip ASCII (Eddington flux
H_lambda, continuum), both erg/s/cm^2/Angstrom, with NO wavelength column —
the log-uniform sampling grid is a separate per-resolution file whose rows
align by index. Values below 1e-6 erg/s/cm^2/Angstrom were clipped to zero
upstream (continuum to 1); the reader preserves the published values
verbatim — no clipping, no un-clipping, no filling.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

import numpy as np

from stellarlib.native import NativeSpectrum

__all__ = [
    "FILENAME_RE",
    "WAVE_FILENAME_RE",
    "parse_filename",
    "read_resam",
    "read_wave_grid",
]

# bosz2024_{mp|ms|ap}_t{Teff}_g{logg}_m{[M/H]}_a{[a/M]}_c{[C/M]}
#        _v{vmicro}_r{resolution}_resam.txt.gz
FILENAME_RE = re.compile(
    r"bosz2024_(mp|ms|ap)_t(\d+)_g([+-][0-9.]+)_m([+-][0-9.]+)"
    r"_a([+-][0-9.]+)_c([+-][0-9.]+)_v(\d+)_r(\d+)_resam\.txt\.gz$"
)

WAVE_FILENAME_RE = re.compile(r"bosz2024_wave_r(\d+)\.txt$")


def parse_filename(name: str) -> dict:
    """Parse a BOSZ 2024 resam filename into its stellar parameters."""
    match = FILENAME_RE.match(name)
    if match is None:
        raise ValueError(f"{name}: not a BOSZ 2024 resam filename")
    atmos, teff, logg, mh, am, cm, vmicro, resolution = match.groups()
    return {
        "family": atmos,
        "teff": int(teff),
        "logg": float(logg),
        "mh": float(mh),
        "am": float(am),
        "cm": float(cm),
        "vmicro": int(vmicro),
        "resolution": int(resolution),
    }


def read_resam(path: str | os.PathLike) -> NativeSpectrum:
    """Read one resam spectrum: flux (H_lambda) and continuum, verbatim.

    The returned native spectrum carries no wavelength (the file has none);
    pairing with the per-resolution wavelength grid is the backend's set
    construction, never the reader's guess.
    """
    data = np.loadtxt(path, ndmin=2)
    if data.ndim != 2 or data.shape[1] != 2:
        raise ValueError(f"{path}: expected two columns (flux, continuum)")
    facts: dict = {}
    name = Path(path).name
    if FILENAME_RE.match(name):
        facts = parse_filename(name)
    return NativeSpectrum(
        wavelength=None,
        flux=data[:, 0],
        continuum=data[:, 1],
        metadata=facts,
        provenance_keys={"file": name},
    )


def read_wave_grid(path: str | os.PathLike) -> np.ndarray:
    """Read a per-resolution wavelength grid file (one wavelength per row)."""
    data = np.loadtxt(path, ndmin=2)
    if data.ndim != 2 or data.shape[1] != 1:
        raise ValueError(f"{path}: expected one column (wavelength)")
    return data[:, 0]


def recognize_bosz(source: str | os.PathLike) -> bool:
    """Cheap sniff: does this source's name follow the resam grammar?"""
    try:
        return FILENAME_RE.match(Path(source).name) is not None
    except (TypeError, ValueError):
        return False


def recognize_wave_grid(source: str | os.PathLike) -> bool:
    try:
        return WAVE_FILENAME_RE.match(Path(source).name) is not None
    except (TypeError, ValueError):
        return False


def mast_url(facts: dict) -> str:
    """The official MAST URL of one resam file (verified tree layout).

    ``.../bosz2024/r<R>/m<[M/H] formatted +d.dd>/<filename>`` — the
    metallicity directory mirrors the filename's own m-token formatting.
    """
    name = (
        f"bosz2024_{facts['family']}_t{facts['teff']}_g{facts['logg']:+.1f}"
        f"_m{facts['mh']:+.2f}_a{facts['am']:+.2f}_c{facts['cm']:+.2f}"
        f"_v{facts['vmicro']}_r{facts['resolution']}_resam.txt.gz"
    )
    return (
        f"https://archive.stsci.edu/hlsps/bosz/bosz2024/"
        f"r{facts['resolution']}/m{facts['mh']:+.2f}/{name}"
    )


def mast_wave_url(resolution: int) -> str:
    """The official MAST URL of one wavelength grid file."""
    return (
        "https://archive.stsci.edu/hlsps/bosz/bosz2024/wavelength_grids/"
        f"bosz2024_wave_r{resolution}.txt"
    )


def assert_wave_rows(path: str | os.PathLike, wave: np.ndarray, flux_rows: int) -> None:
    """The resam/wave row-alignment contract (row i of each pairs by index)."""
    if flux_rows != wave.size:
        raise ValueError(
            f"spectrum rows ({flux_rows}) and wavelength grid rows "
            f"({wave.size}) disagree -- wrong wave file for {path}?"
        )
