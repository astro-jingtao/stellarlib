# stellarlib

Faithful and unified access to theoretical stellar spectral libraries.

stellarlib is a provenance-first, model-agnostic Python interface for reading,
preserving, standardizing, and querying tabulated theoretical stellar spectra
— model-atmosphere flux grids published on (Teff, log g, [M/H], ...) parameter
nodes, starting with BOSZ 2024 and TLUSTY OSTAR2002/BSTAR2006.

**Status: early development (v0.1.0).** The public API may still change.

## Scope

In scope, for the initial release:

- **BOSZ 2024** (MAST HLSP, resampled text products at several resolutions).
- **TLUSTY OSTAR2002 / BSTAR2006** (tlusty.oca.eu, `*.vis.7.gz` optical
  spectra).
- Native-file reading with full fidelity, unified field vocabulary, grid
  construction with hole reporting, cross-node interpolation (multilinear,
  plus Voronoi nearest-node for grids with holes), wavelength resampling,
  provenance on every object, local-database construction with on-demand
  fetching, registration of local file subsets, and materialized array
  side-products with complete provenance.

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

The raw corner mechanism (corner order and flat indices exactly as the
downstream sparse-matrix consumers expect) and the merged array block are
both available:

```python
idx, w, clamped = grid.corner_weights(teff=[4125.0], log_surface_gravity=[3.1], m_h=[-0.3])
rows = grid.corner_spectrum(idx[0])                    # (8, n_wave)
block = stellarlib.materialize.materialize_grid(grid, "block")   # npz + sources sidecar
```

Build a local database against a declared window (design complete,
fetching on demand; `dry_run` first) or register files you already hold:

```python
stellarlib.build_database("my-db", dry_run=True,
    declarations=[dict(family="bosz", label="giants", resolution=500,
                       teffs=[3000, 3500, 4000], loggs=[1.0, 1.5, 2.0],
                       mhs=[0.0])])
db = stellarlib.open_database("my-db")
grid = db.grid("bosz/2024", label="giants")
```

TLUSTY enters through registration (its per-composition tar inventory is
not yet live-verified):

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
