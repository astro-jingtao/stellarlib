# stellarlib

Faithful and unified access to published stellar spectral libraries.

stellarlib is a provenance-first, model-agnostic Python interface for reading,
preserving, standardizing, and querying tabulated stellar spectra —
model-atmosphere flux grids published on (Teff, log g, [M/H], ...) parameter
nodes (BOSZ 2024, TLUSTY OSTAR2002/BSTAR2006), the semi-empirical BaSeL
SED libraries (2.2 and 3.1), and empirical libraries of observed stars
(MILES, STELIB), each under a query contract faithful to what the data
actually is.

**Status: early development (v0.1.0).** The public API may still change.

## Scope

In scope, for the initial release:

- **BOSZ 2024** (MAST HLSP, resampled text products at several resolutions).
- **TLUSTY OSTAR2002 / BSTAR2006** (tlusty.oca.eu, `*.vis.7.gz` optical
  spectra).
- **BaSeL 2.2 / 3.1** (the author's page at UFABC; semi-empirical SEDs —
  theoretical spectra recalibrated onto empirical photometry; 3.1 ships
  two calibration variants, one grid serves one).
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

stellarlib never silently decides how processed your data is: every spectrum is reached at one of three explicit levels, and this quick start is that walk — open a library, then obtain each level in turn. Two objects carry it: a **grid** is one product's published parameter nodes (what the source published, holes included), and a **spectrum** — `spec` below — is one node's data or one interpolated point's, reached at any level by explicit code. The grid offers the choice as two interfaces with two result types: `grid.node(...)` accepts only coordinates that land exactly on a published node and answers a `NodeSpectrum` (the published spectrum, strictly), while `grid.spectrum(...)` interpolates between nodes and always answers an `InterpSpectrum` whose record traces the answer back to the exact nodes and weights — including the degenerate cases, where an exact hit or a nearest-node answer carries weight 1.0 on the single node it returns. For a scientific application the difference matters: the native layer is what the authors published, the unified layer is that data under one vocabulary, the interpolated layer is a derived product stellarlib computed for you.

Open a local directory of BOSZ resampled spectra (the wavelength sidecar is discovered next to them or pinned explicitly):

```python
import stellarlib

grid = stellarlib.open_grid("path/to/bosz/files", model="bosz/2024")
print(grid.describe())          # axes report what exists; holes are counted
```

### Level 1 — native: the published numbers, verbatim

Every spectrum object carries `spec.native`: the numeric arrays exactly as the upstream file holds them, its verbatim header, and the metadata the filename or header states.

```python
spec = grid.node(teff=4000.0, log_surface_gravity=3.0, m_h=0.0)  # a NodeSpectrum
spec.native.flux             # the flux column, exactly as parsed
spec.native.wavelength       # or None where the file carries no wavelength column
spec.to_table(layer="native")  # astropy Table, units = the published convention
```

Nothing is renamed, converted, clipped, or filled at this level — and where a published format is odd, the oddity is preserved and recorded in the set's notes: BOSZ resampled files carry no wavelength column at all (the grid lives in a separate sidecar file; pairing it is an explicit, recorded step), BaSeL publishes wavelength in nm and flux per Hz per steradian, and 29 BaSeL 2.2 spectra declare a metallicity in their parameter line that contradicts the file they live in. `native` shows you exactly that.

### Level 2 — unified: one field vocabulary over the native arrays

`spec.unified["surface_flux"]` (or the matching attribute, `spec.surface_flux`) maps the native arrays onto a fixed vocabulary — `wavelength` (Angstrom), `wavelength_um`, `eddington_flux` (H), `surface_flux` (F = 4·pi·H), `continuum`, and the empirical-only `observed_flux` fields. Each unified field is exactly one of three things:

- **the native array itself** under its published name, when the library already publishes it in the vocabulary's unit (TLUSTY publishes H_lambda per Angstrom: `eddington_flux` is native);
- **a unit bridge from a closed vocabulary** — `4 pi * x`, `x * 1e-4`, the f_nu-to-f_lambda conversion, and a few more; each is a named, implemented transformation, never free-form code. BaSeL's published f_nu per steradian reaches `surface_flux` (per Angstrom) through `4 pi * c / lambda^2` — the per-steradian reading itself was settled by checking the bolometric closure of the published numbers against sigma·T^4;
- **explicitly absent** (`continuum` for products whose format has no continuum column) — asking for it raises, nothing is synthesized.

```python
flux = spec.unified["surface_flux"]      # F_lambda = 4 pi H_lambda (erg/s/cm^2/A)
wave_um = spec.unified["wavelength_um"]
spec.field_info("surface_flux")          # names the native column + transformation
```

The unified layer still holds **one node's published data** — nothing has been interpolated. What bridging happened is inspectable, not implicit: `field_info` returns the field's spec naming the native column and the transformation, the same statement rides in the spectrum's provenance, and every family's full mapping table is generated into `docs/unified-parameters.md`.

### Level 3 — interpolated: a point between nodes, computed on your request

`grid.node(...)` is the strict interface of Level 1; `grid.spectrum(teff=..., log_surface_gravity=..., m_h=...)` is the interpolating counterpart, and the method is an explicit argument:

- an **exact node hit** returns the published spectrum's data unchanged — no interpolation runs at all (`method="exact-node"` on the record, weight 1.0 on that node);
- `interp="multilinear"` (the default for model grids) is linear in every axis over the bracketing cell's 2^N corner nodes — linear in Teff, log g, [M/H], matching the libraries' own axis labeling. A bracket cell with a missing node raises `GridHoleError` instead of guessing (about 8.5% of the MARCS models behind BOSZ are unpublished, and BaSeL's per-metallicity grids publish different (Teff, log g) node sets, so holes are common there; `grid.holes` lists them, and `grid.nodes` lists every node that does exist);
- `interp="voronoi"` returns the single nearest published node, grid-step-normalized, with a warning and a record — the hole-tolerant path that never invents data between nodes;
- out-of-range coordinates raise (`range_policy="strict"`, no extrapolation) or clamp to the edge cell with a warning (`range_policy="clamp"`).

```python
spec = grid.spectrum(teff=4125.0, log_surface_gravity=3.1, m_h=-0.3)
spec.corner_nodes              # the exact nodes this answer was built from
spec.corner_weights            # their weights (sum to 1; brackets the query)
spec = grid.spectrum(teff=4125.0, log_surface_gravity=3.1, m_h=-0.3,
                     interp="voronoi")   # warns + records the node used
```

The result is **derived data and says so**, in its class and on its body: every `grid.spectrum(...)` call answers an `InterpSpectrum` carrying the full `InterpolationRecord` (method, the exact corner nodes and their parameter values, the weights, the clamp state) as `spec.record` — the same record left on `grid.last_record` — and the spectrum's provenance names the corner nodes it was built from. The same honesty bounds who may be interpolated at all: empirical libraries (MILES, STELIB) refuse `multilinear` outright — a weighted mix of two arbitrarily-normalized observed stars is not a spectrum of anything — and answer with the nearest parameterized star, warned and recorded.

### Empirical libraries: observed stars, nearest-star answers

The flux there is relative with an arbitrary per-star normalization, so interpolation between stars is refused and the nearest-star choice is warned and recorded:

```python
miles = stellarlib.construct("miles/2011", database="my-db")
spec = miles.spectrum(teff=4125.0, log_surface_gravity=1.4, m_h=-0.3)
flux = spec.unified["observed_flux"]     # relative, unitless
```

### BaSeL: the calibration variant is a scientific choice

A BaSeL 3.1 grid additionally states which of the two published calibrations it serves (WLBC99 matches empirical colour-temperature relations; PADOVA2000 trades that for globular-cluster CMDs with Padova 2000 isochrones — no default, the choice is scientific). Its per-metallicity node sets differ, so holes are common and `voronoi` is often the right query. BaSeL 2.2 needs no variant and rejects one; `db.grid("basel/2.2")` serves the whole 19-metallicity library (8,315 nodes, holes reported as everywhere).

```python
db = stellarlib.open_database("my-db")
basel = db.grid("basel/3.1", variant="WLBC99")     # one grid, one calibration
spec = basel.spectrum(teff=4125.0, log_surface_gravity=3.1, m_h=-0.75,
                      interp="voronoi")            # warns + names the node used
flux = spec.unified["surface_flux"]                # F_lambda = 4 pi H_lambda
```

### Getting the files: local databases as whole-package mirrors

Build a local database as a mirror of the complete upstream library. The blueprint is a family-level declaration — it always covers every official package the source publishes (BOSZ: all 112 (broadening, metallicity) directory mirrors — the rorig ones include the line-identification files — plus the shared wavelength grids; TLUSTY: all 20 per-composition vispec tars; MILES: the one official FITS package; STELIB: the one VizieR catalog tree — `source=` picks its channel explicitly, `"cds"` the canonical default or `"adac"` the verified mirror) — and `build_database` completes what is missing, one whole unit at a time (`dry_run` first):

```python
stellarlib.build_database("my-db", dry_run=True,
    declarations=[dict(family="bosz"), dict(family="tlusty"),
                  dict(family="basel"), dict(family="miles"),
                  dict(family="stelib")])
db = stellarlib.open_database("my-db")
```

Fetch rounds choose whole products, never subsets of them: `select=` takes glob patterns over product paths and restricts what one run fetches (the blueprint itself is untouched — later runs complete the rest):

```python
stellarlib.build_database("my-db",
    select=["bosz/rorig/*", "bosz/wavelength_grids/*", "tlusty/*/*"])
```

Large mirror rounds fetch faster with bounded per-pass parallelism — a speed axis only; per-part semantics, the two-state scan, and resume behavior are identical. A BOSZ product is one (broadening, metallicity) directory, so a single metallicity is exactly one product per broadening — `select=["bosz/r500/m+0.00"]` fetches only the solar-metallicity r500 mirror (see `docs/data-sources.md` for the full blueprint inventory):

```python
stellarlib.build_database("my-db", select=["bosz/r20000/*"], workers=8)
```

A full-integrity pass re-checks every recorded part against both its recorded size and sha256, and backfills records for present files whose manifest write was lost — it never fetches anything:

```python
stellarlib.verify_database("my-db", workers=8)
```

Open one mirrored product as a grid. A BOSZ directory interleaves every composition and vmicro variant, so opening it is a selection (defaults: solar-scaled composition, the atmosphere-family vmicro):

```python
grid = db.grid("bosz/2024", collection="r500", label="m+0.00")
grid = db.grid("tlusty/OSTAR2002", label="G")   # one whole vispec tar
miles = db.grid("miles/2011")                   # the whole empirical library
```

Files you already hold (an extracted tar, a curated subset) still enter through registration:

```python
stellarlib.register_local_product("my-db", "tlusty", "ostar_g",
                                  files=vis_files, note="extracted vispec tar")
grid = db.grid("tlusty/OSTAR2002", label="ostar_g")
```

Move a database (or any subset of it) between machines: export a selection on one side, transport it (rsync or any byte copy — the plan travels with the database), and merge it into the other side. The merge only adds what is missing and records conflicts instead of resolving them; the default no-verify merge invalidates exactly the migrated files' signature-cache entries, so the next `verify_database` re-hashes the migrated portion once — never the whole database:

```python
stellarlib.export_database("db-on-a", select=["stelib/library/all"])
stellarlib.run_rsync("db-on-a", "db-on-a/export/selection", "host:/path/db-on-b")
r = stellarlib.merge_database("db-on-b-partial-copy", "db-on-b")
print(r.summary())          # merged / completed / kept / conflicts
stellarlib.verify_database("db-on-b", select=["stelib/library/all"])  # forced re-hash of the migrated portion
```

### The raw corner mechanism and materialized blocks

The corner order and flat indices are exactly what the downstream sparse-matrix consumers expect, and the merged array block is one materialization:

```python
idx, w, clamped = grid.corner_weights(teff=[4125.0], log_surface_gravity=[3.1], m_h=[-0.3])
rows = grid.corner_spectrum(idx[0])                    # (8, n_wave)
block = stellarlib.materialize.materialize_grid(grid, "block")   # npz + sources sidecar
```

The design reasoning behind the three-level contracts lives in `docs/architecture.md`.

## Documentation

- `docs/architecture.md` — the two-layer data model, interpolation policy,
  database design.
- `docs/data-sources.md` — upstream sources, verified facts, pitfalls.
- `docs/unified-parameters.md` — generated unified field/parameter tables.
- `docs/coverage.md` — the downstream capability map (acceptance evidence).
- `TODO.md` — design and feature work deliberately not yet done.

## Physics validation

`tools/probe_channels.py` reports which upstream channels the local
network can reach (run it on any candidate download machine before
choosing where to fetch from). `tools/check_library_physics.py` checks
the Bolometric closure
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
