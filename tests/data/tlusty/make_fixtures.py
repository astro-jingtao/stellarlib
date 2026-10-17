"""Create the TLUSTY test fixtures (synthetic, format-equivalent).

No real TLUSTY file was held locally when these were written, so the
fixtures follow the documented format grammar exactly (two-column gzip
ASCII, ``{comp}{teff:05d}g{logg*100:03d}v{vmicro*10:02d}.vis.7.gz``) with
a separable synthetic law

    H(Teff, log g, lam) = (Teff / 1e4) * log g * lam / 1000

so bilinear interpolation has hand-computable expectations. Real-file
excerpts should replace these once a vispec archive is fetched; the
format facts are verified against real downloads (2026-10-09) and the
synthetic files violate none of them.

Usage: python make_fixtures.py
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent

WAVE = np.array([5000.0, 5001.0, 5002.0])

OSTAR_WINDOW = [  # (teff, logg) corners, composition G, vmicro 1 km/s
    (30000, 3.00),
    (30000, 3.25),
    (32500, 3.00),
    (32500, 3.25),
]

BSTAR_WINDOW = [  # composition BG, vmicro 2 km/s
    (15000, 1.75),
    (15000, 2.00),
    (16000, 1.75),
    (16000, 2.00),
]


def _flux(teff: int, logg: float) -> np.ndarray:
    return (teff / 1e4) * logg * WAVE / 1000.0


def _write(path: Path, teff: int, logg: float) -> None:
    with gzip.open(path, "wt", newline="") as fh:
        for w, v in zip(WAVE, _flux(teff, logg)):
            fh.write(f"{w:.4f} {v:.6e}\n")


def main(argv: list[str]) -> int:
    for teff, logg in OSTAR_WINDOW:
        _write(
            HERE / f"G{teff:05d}g{int(round(logg * 100)):03d}v10.vis.7.gz",
            teff,
            logg,
        )
    for teff, logg in BSTAR_WINDOW:
        _write(
            HERE / f"BG{teff:05d}g{int(round(logg * 100)):03d}v20.vis.7.gz",
            teff,
            logg,
        )
    # one file of each error/edge kind
    _write(HERE / "G27500g300v10.vis.7.gz", 27500, 3.0)  # overlap band
    with gzip.open(HERE / "G27500g325v10.vis.17.gz", "wt", newline="") as fh:
        for w in WAVE:
            fh.write(f"{w:.4f} {1.0:.6e}\n")  # kind-17 continuum file
    with gzip.open(HERE / "three_columns.vis.7.gz", "wt", newline="") as fh:
        fh.write("5000.0 1e7 0.5\n5001.0 2e7 0.5\n")
    # the suffix-less on-disk name quirk: same bytes, no .gz in the name
    (HERE / "G30000g300v10.vis.7").write_bytes(
        (HERE / "G30000g300v10.vis.7.gz").read_bytes()
    )
    print(f"wrote fixtures under {HERE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
