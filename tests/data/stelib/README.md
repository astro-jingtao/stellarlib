# STELIB test fixtures

Trimmed excerpts of real upstream files (the bosz convention; never
hand-edit — download anew instead).

Downloaded 2026-11-18 from the NAOJ ADAC VizieR mirror of the catalog's
native tree (`http://vizier.nao.ac.jp/ftp/cats/III/232/` — CDS is the
canonical source but unreachable from the download site that day; the
whole tree was fetched through the mirror and these files are verbatim
copies from it):

- `ReadMe` — verbatim (5,989 bytes): the catalog's own format contract
  (the byte-by-byte table layout the reader encodes) and coverage
  statements.
- `table.dat` — the six fixture stars' rows of the catalog table
  (47,771 bytes, 255 rows in full; fixed-width per the ReadMe; `-`
  marks a not-reported value). The six rows exercise every path: HD
  002857 (7611 K, 2.67, -1.51), HD 006268 (4712 K, 1.13, -2.41), and
  HD 053929 (14011 K, 3.63, +0.29) are fully parameterized; HD 053929
  additionally lists a second corrected spectrum (cSpFile2
  `HD053929ss_V3.2.fits`) and two observed counterparts (the two
  observing runs' `_sso`/`_jkt` files); HD 015318 reports Teff only
  (its observed counterpart is cross-identified as HR 718); HD 005820
  reports none of the three parameters; LHS 235 lists no spectrum
  files at all — the table-only row the Add column marks.
- `cor_sp/` — six verbatim corrected-spectrum FITS (the six rows'
  cSpFile1/cSpFile2 members; each 34,560 bytes, 1-D 6700-pixel float32,
  BUNIT `erg /s /cm2 /angstroms`, per-star linear WCS).
- `obs_sp/` — six verbatim observed-counterpart FITS (`_moy.fits` and
  `_moy_<run>.fits`; same format): the held-not-served side of the
  product.

In the full tree (facts pinned 2026-11-18, recorded in
`src/stellarlib/models/stelib/catalog.json`): 255 rows, 255 cor_sp
files (254 cSpFile1 + 1 cSpFile2; LHS 235 the only row without), 258
obs_sp files (254 oSpFile1 + 4 oSpFile2), 207 stars fully
parameterized (32 missing Teff, 44 missing log g, 44 missing [Fe/H]).
