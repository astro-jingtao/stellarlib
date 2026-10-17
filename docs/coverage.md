# Coverage: the downstream capability map

The initial-release acceptance criterion: every processing point the two
downstream applications perform on BOSZ/TLUSTY spectra is expressible —
and only needs — the stellarlib API. This table is that evidence; the
cited tests reproduce the downstream expectations against stellarlib
(数值 parity). The applications' own dependency switch is deliberately
out of scope.

## post_processor `src/post_processor/stars/`

| Downstream today | Where | stellarlib expression | Parity anchor |
|---|---|---|---|
| `FILENAME_RE` + `parse_filename` (8-token grammar) | bosz.py:28-45 | `models/bosz/reader.parse_filename` (identical grammar and keys) | `tests/bosz/test_bosz_reader.py::TestParseFilename` (same cases) |
| `read_resam` (two columns, no wavelength) | bosz.py:48-53 | `reader.read_resam` → `NativeSpectrum(wavelength=None, flux, continuum)` — verbatim, no clipping | `TestReadResam` (same cases) |
| `FOUR_PI` applied on load (F = 4πH, Eq. 2) | bosz.py:34 | unified field `surface_flux` = transformation `"4 pi * x"` (recorded, not baked in) | `test_bosz_backend.py::TestLoadingAndUnified` |
| `BoszGrid` rectangle container + axis/wave validation | bosz.py:56-100 | `SpectrumGrid` (axes validation) + `materialize_grid` (merged block for array consumers) | `test_grid_facade.py`, `test_materialize.py` |
| `corner_weights` contract: corner order `c=4dt+2dg+dm`, (n,8) shapes, clamp masks | bosz.py:114-153 | `interp.corner_weights` (N-dim; identical algorithm — compared against the transplanted downstream code) + `UnifiedSpectra.corner_weights` (with per-cell hole check) | `test_interp.py::TestCornerWeightsContract` (random-query equality vs the downstream algorithm) |
| `corner_spectrum` flat-index gathering | bosz.py:155-157 | `UnifiedSpectra.corner_spectrum` / `MaterializedGrid.corner_spectrum` | `test_grid_facade.py`, `test_materialize.py` |
| `BoszGrid.dump` / `from_npz` | bosz.py:101-110 | `materialize_grid` / `open_materialized` (plus sources sidecar with sha256 and change detection) | `test_materialize.py` |
| TLUSTY `FILENAME_RE`/`parse_filename`/`read_vis` (gz-suffix quirk, air wavelengths) | tlusty.py:31-52 | `models/tlusty/reader` (identical grammar; suffix quirk preserved) | `tests/tlusty/test_tlusty_reader.py` (same cases) |
| `TlustyGrid` 2-D container + bilinear `corner_weights` order `(0,0),(0,1),(1,0),(1,1)` | tlusty.py:55-148 | `SpectrumGrid` with 2 axes; corner order is the N-dim mechanism at N=2 (verified equal) | `test_interp.py::test_two_axis_case_matches_tlusty_order`, `test_tlusty_backend.py` (hand-computed bilinear law) |
| Application-side assembly: corner download, wave pairing, family/vmicro selection, abort on rectangle holes | docs/stars/bosz.md | `build_database` window declarations (family/vmicro policy built in; failed fetches recorded, not fatal), wave pairing by discovery or explicit `wave_path`, `grid.holes` + `GridHoleError`/voronoi instead of abort | `test_database.py::TestBuild`, `test_bosz_backend.py::TestWavePairing` |
| `mh_from_z` (Z→[M/H], Z_sun = 0.0171 Grevesse 2007) | star_sed.py:28-30 | not a library concern (a composition convention); the registry carries the library-side composition facts, the application keeps its Z mapping | — (out of scope by design) |

## red_gaint_feedback `code/rg_feedback/`

| Downstream today | Where | stellarlib expression | Parity anchor |
|---|---|---|---|
| `load_bosz_spectrum` (resam + wave pairing; µm + H_λ units; row-mismatch error; single-column error) | spectral.py:31 | `open_grid`/`BoszSpectra` (pairing + row check) + unified fields `wavelength_um` (`x * 1e-4`) and `eddington_flux` (native H, no 4π) | `test_bosz_backend.py::TestWavePairing` (row-mismatch loud), `TestLoadingAndUnified` (µm values) |
| `TeffSEDGrid` (1-D Teff ladder @ fixed g, mh; linear flux interpolation + clamp) | population.py:188 | degenerate-axis grid: `spectrum(teff=...)` with single-valued axes auto-filled, multilinear over the 1-D cell, `range_policy="clamp"` + warning + record | `test_grid_facade.py::test_single_valued_axes_fill_automatically`, `test_materialize.py::test_degenerate_one_axis_ladder` |
| Filename Teff parsing `split("_")[2]` | population.py:207 | the unified grammar parser (`parse_filename`) | `test_bosz_reader.py` |
| Validation checks (Boltzmann closure ∫H dλ vs σT⁴/4π, sha256 pinning, grid monotonicity) | validation/validate_bosz.py | `tools/check_library_physics.py` (closure + 4π convention + log-grid uniformity; reproduced on the same r500 set: 0.7–2.4% closure, 4π confirmed) and database checksums (MANIFEST sha256 per part) | `tools/check_library_physics.py` run 2026-10-17 recorded in `docs/data-sources.md`; `test_database.py` checksum paths |

## Explicitly out of scope (application-level, unchanged)

SED trapezoid weights (`sed_weights`), dust extinction/opacity, MIST
tracks/isochrones, `KabsTeffTable`, `star_luminosity_density`,
photometry, population synthesis. stellarlib guarantees the primitives
these consume (flux fields, interpolation, provenance) and nothing
above them.
