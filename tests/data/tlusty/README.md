# TLUSTY test fixtures

`make_fixtures.py` writes these — never hand-edit them; regenerate
instead.

Synthetic format-equivalent files (no real TLUSTY file was held locally
at creation time; the format facts they follow are verified against real
downloads 2026-10-09, the archive structure against the real
Gvispec.tar 2026-10-18): two-column gzip ASCII (wavelength in Angstrom,
Eddington flux), names following the published grammar, flux from the
separable law `(Teff/1e4) * log g * lam/1000` so bilinear interpolation
has hand-computable expectations.

- OSTAR2002 2×2 window: `G30000/G32500 × g300/g325`, vmicro 1 km/s,
  with kind-17 continuum siblings (own coarser wavelength grid, as in
  the real archives — the two kinds are one model's two data and are
  never row-aligned).
- BSTAR2006 2×2 window: `BG15000/BG16000 × g175/g200`, vmicro 2 km/s.
- `G27500g300v10.vis.7.gz` — sits in the OSTAR/BSTAR overlap band
  (release inference must refuse without an explicit release).
- `G27500g325v10.vis.17.gz` — a lone kind-17 continuum file (recorded
  by the backend; only-kind-17 sources are refused).
- `G30000g300v10.vis.7` — the suffix-less on-disk name quirk (same
  bytes as the `.gz` sibling).
- `three_columns.vis.7.gz` — wrong column count for the reader.
- `Gvispec_fixture.tar` — the per-composition archive structure as the
  upstream publishes it: flat members, both kinds of every atmosphere,
  gzip members named with the `.gz` suffix (8 members = 4 kind 7 + 4
  kind 17; the real Gvispec.tar holds 138 = 69 + 69).

Real-file excerpts should replace these once a vispec archive is
fetched; the readers and backends consume either identically.
