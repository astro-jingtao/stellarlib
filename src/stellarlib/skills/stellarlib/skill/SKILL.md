---
name: stellarlib
description: Use the installed stellarlib Python package to read, mirror, verify, and query published stellar spectral libraries — BOSZ 2024 and TLUSTY OSTAR2002/BSTAR2006 model grids plus the MILES and STELIB empirical libraries. Use when a task names stellarlib or asks to build or verify a stellar-spectra database (build_database, verify_database, register_local_product), to open a grid (open_grid, construct, db.grid), to query a spectrum (multilinear or voronoi nearest node; MILES/STELIB nearest star), to read unified fields (surface_flux, eddington_flux, observed_flux, observed_flux_density), to resample wavelengths, or to materialize a grid — or when an error such as GridHoleError, UnsafeInterpolationError, or WavelengthGridError appears in stellarlib output.
---

# stellarlib usage

## Role

You operate the installed stellarlib package: read published stellar
spectral libraries faithfully, mirror them into a local database,
verify integrity, and query spectra at physical parameters. stellarlib
is read-side tooling — it fetches only what a build is told to mirror,
and never transforms the published data.

## The libraries and their model strings

| model string | kind | flux you read | queries |
| --- | --- | --- | --- |
| `bosz/2024` | model grid (8 broadenings × 14 metallicities) | `surface_flux` (F_λ = 4πH_λ) or `eddington_flux` | multilinear (default), voronoi on holed cells |
| `tlusty/OSTAR2002`, `tlusty/BSTAR2006` | model grid (per-composition tars) | `surface_flux` or `eddington_flux` | multilinear (default), voronoi |
| `miles/2011` | empirical library (985 observed stars) | `observed_flux` (relative, unitless) | nearest star only |
| `stelib/2003` | empirical library (255 observed stars, VizieR III/232) | `observed_flux_density` (erg/(s cm² Å), absolute) | nearest star only |

Read `references/families.md` for each family's coverage, flux
convention, and pitfalls — before answering any question about what a
number means or which family fits a temperature range.

## Common tasks

### Query one spectrum

```python
import stellarlib

miles = stellarlib.construct("miles/2011", database="my-db")
spec = miles.spectrum(teff=4125.0, log_surface_gravity=1.4, m_h=-0.3)
flux = spec.unified["observed_flux"]        # relative, per-star normalization
```

- Exact node hits return the published spectrum unchanged
  (`selection_info["method"] == "exact-node"`).
- `interp="voronoi"` returns the nearest node/star — a warning fires and
  `last_record` names the node; this is the hole-tolerant path for
  BOSZ/TLUSTY and the ONLY path for the empirical libraries MILES and
  STELIB (multilinear is refused there: mixing two observed stars is
  not an observation of anything, and STELIB stars sit on per-star
  wavelength grids).
- `range_policy="strict"` (default) refuses out-of-range coordinates;
  `"clamp"` clamps with a warning.

### Open a grid from files or a database

```python
grid = stellarlib.open_grid("path/to/files", model="bosz/2024")
db = stellarlib.open_database("my-db")
grid = db.grid("bosz/2024", collection="r500", label="m+0.00")
```

`grid.axes` lists the values that exist; `grid.holes` the missing cells
of the axis product. A BOSZ mirror needs its composition selection
(`am`/`cm` default solar-scaled, `vmicro` the family policy) and pins
its wavelength sidecar automatically from the database.

### Build (mirror) a database

```python
stellarlib.build_database("my-db", dry_run=True,
    declarations=[dict(family="bosz"), dict(family="tlusty"),
                  dict(family="miles")])
```

Always `dry_run` first. The blueprint declares the family's COMPLETE
upstream surface; a plain run without `select=` starts mirroring
everything declared (BOSZ alone is ~13 TB — pass `select=` glob
patterns to fetch whole products one round at a time). `workers=N`
parallelizes one fetch pass; per-part semantics are identical.

### Verify integrity

```python
stellarlib.verify_database("my-db", workers=8)
```

Re-checks every recorded part against size + sha256, backfills records
for present files whose manifest write was lost, learns
`signature_cache.json` (unchanged files then cost one stat), and never
fetches. After migrating a database to another machine, delete
`signature_cache.json` and verify once (mtime-preserving copies do not
verify content in transit).

### Register files you already hold

```python
stellarlib.register_local_product("my-db", "tlusty", "ostar_g",
    files=vis_files, note="extracted vispec tar")
```

### Resample and bulk access

```python
from stellarlib import resample_wavelengths, corner_weights
spec2 = resample_wavelengths(spec, wavelengths=new_grid)   # recorded, never native
idx, w, clamped = grid.corner_weights(teff=[4125.0], log_surface_gravity=[3.1], m_h=[-0.3])
rows = grid.corner_spectrum(idx[0])          # (2^N, n_wave)
```

`stellarlib.materialize.materialize_grid(grid, "block")` writes a
merged npz block plus a sources sidecar.

## Reading a result

- `spec.unified[field]` — arrays through the registry's field specs;
  `spec.field_info(field)` states the mapping.
- `spec.native` — the verbatim file arrays; nothing is clipped, zeroed,
  or renamed.
- `spec.provenance` — assets, source records, references on every
  object.
- `facade.last_record` — the InterpolationRecord of the last point
  query (method, nodes, weights, clamping).

## Errors and what they mean

| error | meaning | usual next step |
| --- | --- | --- |
| `GridHoleError` | the bracket cell misses a published node | `interp="voronoi"`, or pick a hole-free collection |
| `UnsafeInterpolationError` | query outside the axis ranges | `range_policy="clamp"` if clamping is acceptable |
| `WavelengthGridError` | a BOSZ product has no wavelength sidecar paired | pass `wave_path=`, or mirror the `wavelength_grids` product |
| `GridPointNotFoundError` | no node at the coordinates (and not a hole) | check `grid.axes` for what exists |
| `DataIntegrityError` | a file fails its recorded evidence | `verify_database` to localize; never re-fetch over it silently |

## Out of Scope / Limits

- No stellar evolution tracks or isochrones — that is the companion
  package `stellariso`.
- No SED weighting, extinction, photometry, or population synthesis;
  the application does its own physics with stellarlib's primitives.
- No interpolation between empirical stars and no unit conversion of
  MILES flux — the data does not support either.

## Supporting Files

- `references/families.md` — per-family coverage, formats, flux
  conventions, and pitfalls. Read before interpreting any family's
  numbers or choosing a family for a parameter range.
