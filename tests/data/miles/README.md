# MILES test fixtures

Trimmed excerpts of real upstream files (the bosz convention; never
hand-edit — download anew instead).

Downloaded 2026-11-17 from the official MILES site
(`https://research.iac.es/proyecto/miles`):

- `s0001.fits`, `s0002.fits`, `s0838.fits`, `s0985.fits` — verbatim
  single-star FITS spectra extracted from the official package
  `media/tarfiles/Stellar_Libraries/MILES_library_v9.1_FITS.tar.gz`
  (14,776,258 bytes, exactly 985 flat members `s0001.fits`..`s0985.fits`,
  each 23,040 bytes; site Last-Modified 2015-04-17).
- `paramsMILES_v9.1_excerpt.txt` — the first three lines (header, units
  row, dash rule) plus the four fixture stars' rows of the official
  parameter table `media/tarfiles/Stellar_Libraries/paramsMILES_v9.1.txt`
  (93,533 bytes, 985 data rows; fixed-width columns at Name 0 / MILES 22
  / CaT 36-offset per the header labels; `--` marks a missing value).
- `miles_library_fixture.tar.gz` — the four FITS packed flat, mirroring
  the official package's tar structure for the archive-reading path.

The fixture stars exercise the join and the scatter semantics: s0001
(5305 K, 4.49, -0.75), s0002 (4167 K, 0.95, +0.02), s0985 (4020 K,
1.32, -0.13) are fully parameterized; s0838 (2748 K, 5.09, `--`) is one
of the library's 39 stars with a missing `[Fe/H]` — real bytes for the
unparameterized-star path.
