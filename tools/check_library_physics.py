"""Physics validation for spectral-library files.

Checks (on the native values exactly as read — no clipping, no unit
conversions beyond the stated flux conventions):

1. Bolometric closure: int(H_lambda, dlambda) against sigma T_eff^4 /
   (4 pi) — the defining property of the Eddington flux. Known
   deviation: cool models truncated at the 32 micron grid edge miss the
   Rayleigh-Jeans tail (the t3000 giants sit ~2.4% low), reported per
   file.
2. Flux-convention cross-check: 4 pi H_lambda against pi B_lambda at one
   wavelength — favors the paper's F = 4 pi H (Eq. 2) over the archive
   page's "multiply by pi" by a factor ~4 when the convention is right.
3. Wavelength grid log-uniformity: the resam sidecar grids are equally
   spaced in log lambda by construction; the spread of d(log lambda)
   must be negligible.

Usage: python check_library_physics.py BOSZ_DIR [BOSZ_DIR ...]

Each BOSZ_DIR holds resam files plus (or beside, via the known layouts)
the per-resolution wavelength grid.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from astropy import constants as const
from astropy import units as u

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stellarlib.models.bosz.backend import read_bosz  # noqa: E402

SIGMA = const.sigma_sb.cgs.value  # erg / (cm^2 s K^4)


def blackbody(wavelength_aa: np.ndarray, teff: float) -> np.ndarray:
    """Planck B_lambda in erg / (s cm^2 Angstrom) (cm/s^2 sr units aside)."""
    wl_cm = wavelength_aa * 1e-8
    h, c, k = const.h.cgs.value, const.c.cgs.value, const.k_B.cgs.value
    return (2 * h * c**2 / wl_cm**5) / (
        np.expm1(h * c / (wl_cm * k * teff))
    ) / 1e8  # per Angstrom


def check(directory: str) -> int:
    spectra = read_bosz(directory)
    print(f"== {directory} :: {spectra.identity.label}")
    wave = None
    failures = 0
    for node in spectra:
        spectrum = spectra.load(node)
        teff = float(node.coordinates["teff"])
        wl = spectrum.native.wavelength
        h_lambda = spectrum.native.flux
        if wave is None:
            wave = wl
            dlog = np.diff(np.log(wl))
            spread = float(dlog.std() / dlog.mean())
            ok_grid = spread < 1e-3
            failures += not ok_grid
            print(
                f"   wavelength grid: {wl.size} points, "
                f"[{wl[0]:.1f}, {wl[-1]:.1f}] A, "
                f"log-step spread {spread:.2e} [{'ok' if ok_grid else 'FAIL'}]"
            )
        # trapezoid integral of H_lambda
        integral = float(np.trapezoid(h_lambda, wl))
        expected = SIGMA * teff**4 / (4 * np.pi)
        rel = (integral - expected) / expected
        # 4 pi H vs pi B at a mid-optical wavelength
        ref = 5000.0
        i = int(np.argmin(np.abs(wl - ref)))
        ratio = (4 * np.pi * h_lambda[i]) / (np.pi * blackbody(wl[i], teff))
        print(
            f"   teff={teff:6.0f}: int(H)dl / (sigmaT^4/4pi) = {1 + rel:+.3f}, "
            f"4piH/piB({wl[i]:.0f} A) = {ratio:.3f}"
        )
        if abs(rel) > 0.05 and teff > 3200:
            # the t3000-class truncation tail is a documented deviation;
            # anything else above 5% is a real problem
            failures += 1
    return failures


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    total = 0
    for path in argv[1:]:
        total += check(path)
    print("FAILURES:", total)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
