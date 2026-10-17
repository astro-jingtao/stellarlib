# Architecture

stellarlib gives unified, provenance-first access to theoretical stellar
spectral libraries — model-atmosphere flux grids published on
(Teff, log g, [M/H], ...) parameter nodes. The design follows the
companion package `stellariso` (isochrones): native fidelity first, a
traceable unified layer above it, provenance on every object, data
acquired in the form the source publishes, and no silent fallbacks. The
places where a spectral library is not an isochrone table are called out
below.

## The two layers

```mermaid
flowchart TB
    FILES["published files<br/>BOSZ resam (flux, continuum; no wavelength column)<br/>+ per-resolution wave sidecar<br/>TLUSTY vis (wavelength, H)"]
    subgraph NATIVE["native layer (verbatim)"]
        NS["NativeSpectrum<br/>arrays exactly as parsed<br/>wavelength may be None (resam)<br/>continuum may be None (vis)<br/>no clipping, no unit conversion"]
    end
    subgraph UNIFIED["unified layer (traceable)"]
        UV["UnifiedView<br/>wavelength / wavelength_um /<br/>eddington_flux / surface_flux / continuum<br/>fixed transformation vocabulary"]
    end
    SP["Spectrum (one node)<br/>+ SpectrumSet (lightweight node index<br/>+ lazy loader)"]
    GRID["SpectrumGrid / UnifiedSpectra<br/>axes tell the truth; holes reported<br/>exact / nearest selection<br/>multilinear / voronoi queries"]
    DB["database<br/>design + two-state scan<br/>on-demand per-file fetch<br/>register_local_product"]
    MAT["materialize<br/>merged (axes, wavelength) block<br/>sources sidecar + verification"]
    FILES -->|"reader"| NS --> SP --> GRID
    NS --> UV
    GRID --> MAT
    DB --> GRID
```

**Native layer.** One `NativeSpectrum` per published file: the numeric
arrays exactly as parsed, the verbatim header lines, and the metadata the
filename or header states. Where the format omits a column the attribute
is `None` — BOSZ resam files carry no wavelength (the sidecar grid is a
separate file and a separate, explicit pairing decision); TLUSTY files
carry no continuum. Nothing is renamed, converted, clipped, or filled.

**Unified layer.** A lazy view mapping the native arrays onto a fixed
field vocabulary (`wavelength`, `wavelength_um`, `eddington_flux`,
`surface_flux`, `continuum`) through per-family field specs declared in
the registry. Transformations come from a closed vocabulary — `4 pi * x`,
`x * 1e-4`, `10 ** x` — never free-form code. The air/vacuum wavelength
convention is the product's own and is never converted.

### Where this deliberately differs from stellariso

1. **Cross-node interpolation is a core feature, not a refused
   operation.** stellariso refuses interpolating between isochrones
   because no trusted alignment coordinate exists; a spectral library's
   (Teff, log g, [M/H]) axes exist precisely to be interpolated. Every
   interpolation is therefore audited instead of forbidden: the
   `InterpolationRecord` on `last_record` carries the method, the exact
   corner nodes and their parameter values, the weights, and the clamp
   state.
2. **Lazy node index + materialized side-products.** An isochrone
   product is one file; a spectral library is hundreds to thousands of
   gzip files. A `SpectrumSet` is a lightweight index (coordinates,
   paths, filename facts); spectra materialize on demand and cache on
   the grid. The inverse operation is explicit: `materialize_grid`
   merges a complete-rectangle grid into the array block sparse-matrix
   consumers want, with a sidecar recording every source checksum — the
   block is a derived artifact, the source files remain the truth.

## Grids, holes, and interpolation policy

`grid.axes` reports the values that actually exist — never a declared
rectangle. `grid.holes` lists the cells of the axis-product that have no
published node (about 8.5% of MARCS models are unpublished, mostly at
low log g); holes are reported, never silently filled.

Selection and interpolation are separate decisions:

- **Selection** (`get_spectrum(method=...)`): `exact` fails loudly;
  `nearest` snaps with a warning and a record — as in stellariso.
- **Point queries** (`spectrum(interp=..., range_policy=...)`):
  - an exact node hit returns the published spectrum unchanged
    (`method="exact-node"` — no interpolation happens at all);
  - `interp="multilinear"` (default) interpolates linearly in every
    axis over the bracket cell's 2^N corners — linear in Teff, log g,
    [M/H], matching the libraries' own axis labeling and the downstream
    applications' validated behavior. A holed bracket cell raises
    `GridHoleError`;
  - `interp="voronoi"` answers with the single nearest published node
    (distances step-normalized per axis, so a 250 K Teff step and a
    0.25 dex log g step are comparable). This is the hole-tolerant
    path: it never invents data between nodes, and says so — a
    `VoronoiInterpolationWarning` fires and the record names the node;
  - `range_policy="strict"` refuses out-of-range coordinates (no
    extrapolation); `"clamp"` clamps to the edge cell with a warning
    and a record. The clamped-light fraction of a population is an
    application-level quantity, not the library's.
- **The raw corner mechanism** (`corner_weights` / `corner_spectrum`)
  exposes the rectangular machinery itself: corner order is C order over
  the axes in declaration order (for (teff, log g, [M/H]):
  `c = 4*dt + 2*dg + d*m`; for (teff, log g): `c = 2*dt + d*g`), flat
  indices ravel the axis product, degenerate single-value axes are
  legal (Teff ladders). This is the contract the downstream
  sparse-matrix consumers (and `materialize`) pair with.
- **Wavelength resampling** (`resample_wavelengths`) is linear in
  wavelength, refuses extrapolation, and records itself in provenance —
  the result is a derived product and never masquerades as native.

Flux interpolation runs in the linear space the libraries publish
(including the upstream-clipped exact-zero rows); quirks are recorded in
the registry notes, never patched in code.

## The local database

```mermaid
flowchart LR
    DECL["design.json<br/>(declared windows)"] --> SCAN
    subgraph CORE["database package (generic)"]
        SCAN["scan.py<br/>two-state: complete / incomplete<br/>ok | missing | nonconforming"]
        BUILD["build.py<br/>fetch missing parts on demand<br/>never touch nonconforming"]
        INV["inventory.py<br/>database.json / README /<br/>unified-parameters.md / db_status.*"]
    end
    subgraph ADAPT["family adapters"]
        BA["bosz: window enumeration<br/>+ per-file MAST fetch"]
        TA["tlusty: local-only channel<br/>(vispec tars unverified)"]
    end
    REG["register_local_product<br/>copy or by-reference"]
    SCAN --> BUILD --> INV
    BA --> SCAN
    TA --> SCAN
    REG --> SCAN
    NET["MAST (HTTP)"] --> BUILD
```

The database follows the unified management architecture: a **Product**
is one directory (one coherent set of files + MANIFEST), a **Part** is
one file (the unit of fetching and judgment), the **Design** is the
complete declared target (windows enumerated through the filename
grammar — the blueprint is complete), and fetching is per-part and on
demand. The **Scanner** judges every part (`ok` / `missing` /
`nonconforming`, products `complete` / `incomplete`); the **Builder**
fetches only what is missing and never completes a product that holds
nonconforming parts (a checksum proves integrity, not design
membership). An upstream 404 is recorded as a failed fetch attempt
(status, url, time in the MANIFEST) — the part stays missing, shows in
`db_status`, and is retried on the next build; no publication claim is
inferred from an HTTP status (unpublished nodes are known only through
the confirmed family-policy constants and the holes computed from the
files actually held). Everything at the root (`database.json`, `README.md`,
`unified-parameters.md`, `db_status.*`) is regenerated from directory
contents alone, never copied between machines.

A window declaration pins what its product holds: one resolution, one
composition ([a/M], [C/M]), and the declared (Teff, log g, [M/H]) axis
lists. The design enumerates exactly one spectrum file per declared
point plus that resolution's wavelength sidecar, and the builder fetches
exactly those files — nothing beyond the declaration, ever. The
upstream's unit of organization is coarser: one MAST (R, [M/H])
directory interleaves every composition and vmicro variant (~45,700
files; docs/data-sources.md), but stellarlib never mirrors a directory;
holding a whole slice is itself expressed by declaring the full axis
lists. Materialization inherits the same bound — `materialize_grid`
merges the nodes of one grid, a grid opens one product, and a product
holds only what its window declared — so no code path can materialize
an undeclared volume.

TLUSTY's fetch channel is deliberately closed: the per-composition
vispec tar inventory is not yet live-verified, so TLUSTY products enter
through `register_local_product` until it is.

## Provenance

Every object carries a `Provenance` bundle (source records with trust
classes, data assets with checksums, transformation chains, scientific
vs software references) — the same schema as stellariso, so
cross-package tooling sees one shape. Per-file MAST URLs are recorded
for fetched BOSZ files; registered local products record their
`local_user_file` origin and checksums.
