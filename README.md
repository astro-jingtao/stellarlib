# stellarlib

Faithful and unified access to published stellar spectral libraries.

stellarlib is a provenance-first, model-agnostic Python interface for reading,
preserving, standardizing, and querying tabulated stellar spectra —
model-atmosphere flux grids published on (Teff, log g, [M/H], ...) parameter
nodes (BOSZ 2024, TLUSTY OSTAR2002/BSTAR2006) and empirical libraries of
observed stars (MILES, STELIB), each under a query contract faithful to
what the data actually is.

**Status: early development (v0.1.0).** The public API may still change.

## Scope

In scope, for the initial release:

- **BOSZ 2024** (MAST HLSP, resampled text products at several resolutions).
- **TLUSTY OSTAR2002 / BSTAR2006** (tlusty.oca.eu, `*.vis.7.gz` optical
  spectra).
- **MILES** (research.iac.es, the 985-star empirical library: official FITS
  package + parameter table; relative flux, nearest-star queries).
- **STELIB** (VizieR III/232, the 255-star empirical library: the whole
  catalog tree; absolute spectrophotometry, nearest-star queries).
- Native-file reading with full fidelity, unified field vocabulary, grid
  construction with hole reporting, cross-node interpolation (multilinear,
  plus Voronoi nearest-node for grids with holes), wavelength resampling,
  provenance on every object, local-database construction as whole
  official-package mirrors (the blueprint always declares the full
  upstream library; fetching happens at exactly the granularity the
  source publishes), registration of local file sets, and materialized
  array side-products with complete provenance.

Out of scope, deliberately:

- **Stellar evolution tracks and isochrones** (MIST, PARSEC, ...). That is
  evolution-model territory; see the companion package `stellariso`.
- SED weighting, dust extinction, photometry, population synthesis, CMD
  modeling. Application-level physics stays with the application; stellarlib
  keeps the primitives clean.

## Installation

```
pip install .
```

Development install with test dependencies:

```
pip install -e .[test]
```

## Quick start

Open a local directory of BOSZ resampled spectra (the wavelength sidecar is
discovered next to them or pinned explicitly) and query one star's spectrum:

```python
import stellarlib

grid = stellarlib.open_grid("path/to/bosz/files", model="bosz/2024")
print(grid.describe())          # axes report what exists; holes are counted

spec = grid.spectrum(teff=4125.0, log_surface_gravity=3.1, m_h=-0.3)
flux = spec.unified["surface_flux"]     # F_lambda = 4 pi H_lambda (erg/s/cm^2/A)
wave_um = spec.unified["wavelength_um"]
```

Grids with holes (about 8.5% of MARCS models are unpublished) refuse
rectangular interpolation on the holed cell and say why; the nearest
published node is one explicit choice:

```python
spec = grid.spectrum(teff=4125.0, log_surface_gravity=3.1, m_h=-0.3,
                     interp="voronoi")   # warns + records the node used
```

Empirical libraries open the same way and answer with the nearest
observed star — the flux is relative with an arbitrary per-star
normalization, so interpolation between stars is refused, and the
nearest-star choice is warned and recorded:

```python
miles = stellarlib.construct("miles/2011", database="my-db")
spec = miles.spectrum(teff=4125.0, log_surface_gravity=1.4, m_h=-0.3)
flux = spec.unified["observed_flux"]     # relative, unitless
```

The raw corner mechanism (corner order and flat indices exactly as the
downstream sparse-matrix consumers expect) and the merged array block are
both available:

```python
idx, w, clamped = grid.corner_weights(teff=[4125.0], log_surface_gravity=[3.1], m_h=[-0.3])
rows = grid.corner_spectrum(idx[0])                    # (8, n_wave)
block = stellarlib.materialize.materialize_grid(grid, "block")   # npz + sources sidecar
```

Build a local database as a mirror of the complete upstream library.
The blueprint is a family-level declaration — it always covers every
official package the source publishes (BOSZ: all 112 (broadening,
metallicity) directory mirrors — the rorig ones include the
line-identification files — plus the shared wavelength grids; TLUSTY:
all 20 per-composition vispec tars; MILES: the one official FITS
package; STELIB: the one VizieR catalog tree — `source=` picks its
channel explicitly, `"cds"` the canonical default or `"adac"` the
verified mirror) — and `build_database` completes what is missing, one
whole unit at a time (`dry_run` first):

```python
stellarlib.build_database("my-db", dry_run=True,
    declarations=[dict(family="bosz"), dict(family="tlusty"),
                  dict(family="miles"), dict(family="stelib")])
db = stellarlib.open_database("my-db")
```

Fetch rounds choose whole products, never subsets of them:
`select=` takes glob patterns over product paths and restricts what one
run fetches (the blueprint itself is untouched — later runs complete
the rest):

```python
stellarlib.build_database("my-db",
    select=["bosz/rorig/*", "bosz/wavelength_grids/*", "tlusty/*/*"])
```

Large mirror rounds fetch faster with bounded per-pass parallelism — a
speed axis only; per-part semantics, the two-state scan, and resume
behavior are identical:

```python
stellarlib.build_database("my-db", select=["bosz/r20000/*"], workers=8)
```

A BOSZ product is one (broadening, metallicity) directory, so a single metallicity is exactly one product per broadening — `select=["bosz/r500/m+0.00"]` fetches only the solar-metallicity r500 mirror (see `docs/data-sources.md` for the full blueprint inventory).

A full-integrity pass re-checks every recorded part against both its
recorded size and sha256, and backfills records for present files whose
manifest write was lost — it never fetches anything:

```python
stellarlib.verify_database("my-db", workers=8)
```

Open one mirrored product as a grid. A BOSZ directory interleaves every
composition and vmicro variant, so opening it is a selection (defaults:
solar-scaled composition, the atmosphere-family vmicro):

```python
grid = db.grid("bosz/2024", collection="r500", label="m+0.00")
grid = db.grid("tlusty/OSTAR2002", label="G")   # one whole vispec tar
miles = db.grid("miles/2011")                   # the whole empirical library
```

Files you already hold (an extracted tar, a curated subset) still enter
through registration:

```python
stellarlib.register_local_product("my-db", "tlusty", "ostar_g",
                                  files=vis_files, note="extracted vispec tar")
grid = db.grid("tlusty/OSTAR2002", label="ostar_g")
```

## Documentation

- `docs/architecture.md` — the two-layer data model, interpolation policy,
  database design.
- `docs/data-sources.md` — upstream sources, verified facts, pitfalls.
- `docs/unified-parameters.md` — generated unified field/parameter tables.
- `docs/coverage.md` — the downstream capability map (acceptance evidence).
- `TODO.md` — design and feature work deliberately not yet done.

## Physics validation

`tools/check_library_physics.py` checks the Bolometric closure
(∫H_λ dλ vs σT⁴/4π), the 4π flux convention against blackbodies, and the
wavelength grid's log uniformity on real files.

## Testing

```
python -m pytest                      # offline suite (fixtures: verbatim
                                      # real-file excerpts + documented
                                      # synthetic format-equivalents)
python -m pytest -m network           # catalog URL liveness (explicit)
```

## License

MIT — see `LICENSE`.
