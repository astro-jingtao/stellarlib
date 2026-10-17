"""TLUSTY raw readers: filename grammar and ``*.vis`` optical spectra.

The released ``*.vis.7.gz`` files are two-column gzip ASCII — wavelength
in Angstrom (AIR in the optical) and the Eddington flux H_lambda in
erg/s/cm^2/Angstrom — on the SYNSPEC native fine sampling. Kind 7 is the
detailed spectrum, kind 17 the continuum (a separate published file).
The published set's on-disk names may drop the ``.gz`` suffix; a reader
given such a path opens ``path + '.gz'``.
"""

from __future__ import annotations

import gzip
import os
import re
from pathlib import Path

import numpy as np

from stellarlib.native import NativeSpectrum

__all__ = ["FILENAME_RE", "parse_filename", "read_vis", "recognize_tlusty"]

# {comp}{teff:05d}g{logg*100:03d}v{vmicro*10:02d}.vis.{7|17}.gz — comp is
# the one-to-two-letter composition tag (OSTAR2002: "G" = Z/Z_sun = 1;
# BSTAR2006: "BG"), e.g. G27500g300v10.vis.7.gz (Teff 27500 K,
# log g 3.00, v_micro 1.0 km/s, scaled-solar)
FILENAME_RE = re.compile(r"([A-Z]{1,2})(\d{5})g(\d{3})v(\d{1,2})\.vis\.(7|17)\.gz$")


def parse_filename(name: str) -> dict:
    """Parse a TLUSTY ``*.vis`` filename into its stellar parameters."""
    match = FILENAME_RE.match(name)
    if match is None:
        raise ValueError(f"{name}: not a TLUSTY vis filename")
    comp, teff, logg, vmicro, kind = match.groups()
    return {
        "composition": comp,
        "teff": int(teff),
        "logg": int(logg) / 100.0,
        "vmicro": int(vmicro) / 10.0,
        "kind": kind,
    }


def read_vis(path: str | os.PathLike) -> NativeSpectrum:
    """Read one ``*.vis.7`` spectrum: (wavelength [A, air], H_lambda)."""
    text = str(path)
    handle = (
        gzip.open(text, "rb")
        if text.endswith(".gz")
        else gzip.open(text + ".gz", "rb")
    )
    try:
        data = np.loadtxt(handle, ndmin=2)
    finally:
        handle.close()
    if data.ndim != 2 or data.shape[1] != 2:
        raise ValueError(f"{path}: expected two columns (wave, H)")
    name = Path(text + ("" if text.endswith(".gz") else ".gz")).name
    metadata = parse_filename(name) if FILENAME_RE.match(name) else {}
    return NativeSpectrum(
        wavelength=data[:, 0],
        flux=data[:, 1],
        continuum=None,
        metadata=metadata,
        provenance_keys={"file": name},
    )


def read_vis_payload(payload: bytes, name: str) -> NativeSpectrum:
    """Read one vis spectrum from in-memory bytes (a tar member).

    ``name`` is the member's own filename (``*.vis.7.gz`` or
    ``*.vis.17.gz`` inside a vispec archive); the payload is the gzip
    member exactly as published.
    """
    import io

    raw = gzip.decompress(payload) if name.endswith(".gz") else payload
    data = np.loadtxt(io.BytesIO(raw), ndmin=2)
    if data.ndim != 2 or data.shape[1] != 2:
        raise ValueError(f"{name}: expected two columns (wave, H)")
    metadata = parse_filename(name) if FILENAME_RE.match(name) else {}
    return NativeSpectrum(
        wavelength=data[:, 0],
        flux=data[:, 1],
        continuum=None,
        metadata=metadata,
        provenance_keys={"file": name},
    )


def recognize_tlusty(source: str | os.PathLike) -> bool:
    """Cheap sniff: does this source's name follow the vis grammar?"""
    try:
        name = Path(source).name
    except (TypeError, ValueError):
        return False
    if FILENAME_RE.match(name):
        return True
    # the on-disk name may drop the .gz suffix
    return FILENAME_RE.match(name + ".gz") is not None
