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
  construction with hole reporting, cross-node interpolation (trilinear /
  bilinear, plus Voronoi nearest-node for grids with holes), wavelength
  resampling, provenance on every object, local-database construction and
  on-demand fetching, registration of local file subsets, and materialized
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

(To be finalized as the public API lands; see `docs/architecture.md` for the
two-layer native/unified data model and `docs/coverage.md` for the capability
map.)

## Documentation

- `docs/architecture.md` — data model, layers, interpolation policy.
- `docs/data-sources.md` — upstream sources, verified facts, pitfalls.
- `docs/unified-parameters.md` — generated unified field/parameter tables.

## License

MIT — see `LICENSE`.
