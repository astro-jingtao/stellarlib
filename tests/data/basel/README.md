# BaSeL test fixtures

Trimmed excerpts of the real upstream files, plus two synthetic tar
wrappers reproducing the published tarball layouts (the repo
convention; never hand-edit — regenerate with `make_fixtures.py` from
the acquisition tree at
`/work/jingtao/database/stellarlib/acquisition/basel/`, fetched
2026-11-18 from the author's page; per-file provenance in the
acquisition tree's `SOURCES.md`).

Every `*.cor` fixture keeps the complete 153-line wavelength header
verbatim (1221 points, nm) plus whole 176-line blocks at chosen nodes
(line ranges copied byte-for-byte from the upstream files; spectrum
numbers are the upstream ones and stay non-contiguous):

- `wlbc99_p00.cor`, `wlbc99_m20.cor` — BaSeL 3.1 (WLBC99 variant), the
  2x2 (Teff, log g) rectangle (4000, 4500) x (0.5, 1.0) at [M/H] = 0.0
  and -2.0: a hole-free bracket cell for multilinear interpolation
  across all three axes. Upstream blocks 74/75/96/97 (p00) and 73/74/
  95/96 (m20).
- `lbc96_p00.cor` — BaSeL 2.2 at [M/H] = 0.0: the same rectangle plus
  the (6750, 1.0) node (upstream block 201).
- `lbc96_p01.cor` — BaSeL 2.2 at [M/H] = +0.1: the (6750, 0.5) and
  (6750, 1.0) nodes, upstream blocks 192/193 — two of the 29 real
  published blocks whose [Fe/H] parameter column declares 0.2 against
  the filename's +0.1 (the mismatch quirk the reader records, never
  fixes).
- `basel3_1_fixture.tar.gz` — the two 3.1 fixtures under BOTH variant
  directories (`BaSeL3.1/WLBC99/`, `BaSeL3.1/PADOVA2000/`), the
  published tarball's layout: tests tar streaming and the
  variant-selection contract (ambiguous without `variant=`, one set
  per variant with it).
- `basel2_2_fixture.tar.gz` — the two 2.2 fixtures under
  `BaSeL2.2/`, the published 2.2 tarball's layout.

Full-library facts (node counts, the shared wavelength grid, the
bolometric-closure flux-unit ruling, variant semantics) are pinned in
`src/stellarlib/models/basel/catalog.json` and the family's
`unified_parameters.json` notes.
