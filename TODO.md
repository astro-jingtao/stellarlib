# TODO

Design and feature work deliberately not yet done. Every item names the
gap and the constraint any solution must respect; none of these are bugs.

## ruff full-compliance pass — remainder is two public-signature designs

- The 2026-11-18 seed left 409 findings; the 2026-11-19 sweeps cleared
  all but 3 in the code, no rule edits, no `noqa`: the mechanical
  families (imports, docstrings, annotations, pytest style, redundant
  constructs) are at zero, and the complexity family's decomposition
  work is done — the fetch/merge state machines (`_build_product`,
  `_merge_product`, `_verify_product`, `register_local_product`,
  `scan_database`, `build_database`), the family readers
  (`read_bosz`/`read_miles`/`read_stelib`), `corner_weights`, and the
  registry rendering all extract their phases into module-level
  helpers now.
- The 3 findings that remain are one design decision each, not
  suppression candidates: `empty_manifest` (10 keyword-only args — the
  arguments ARE the manifest schema) and `BoszSpectra.construct` (9
  positional of 9 — the arguments ARE the family's option surface).
  Resolving them means a parameter object, a change to public
  signatures; decide separately.

## Multi-version / multi-platform test CI — not yet started

- Local rounds verify in one env only (the machine's `science` env, python
  3.9 — the package's floor); dual-env local verification was ruled
  redundant on 2026-11-19. Multi-version and multi-platform verification
  belongs to a GitHub Actions matrix when the project publishes there:
  the floor CPython plus newer ones, Linux plus at least one more
  platform, running the offline suite (`pytest -m "not network"`) and a
  lint job. The `network` suite stays an explicit opt-in job — upstream
  availability flakes must never gate merges.

## BOSZ rorig storage and fetch rounds — indefinitely deferred

- The remaining rorig storage decision (~10.7 TB against ~9.4 TB free)
  and the r5000–r50000 fetch rounds are deferred indefinitely by user
  decision (2026-11-18) and stay parked until the user reopens them.
  Nothing here blocks the read-side design below, and the database
  transfer channel (`export_database`/`merge_database`) already supports
  the mirror-elsewhere-then-merge route if a future round takes it.

## noresam (rorig) read side — design pending

- The blueprint mirrors the BOSZ `rorig` directories — all 14 products,
  line-identification files included — but nothing reads them yet: the
  spectrum readers recognize the resam grammar only, and opening an rorig
  mirror as a grid is unsupported (`docs/data-sources.md`, "File format").
- The native grammar to read: `noresam` files are three-column
  (wavelength, H_lambda, continuum) at the synthesis's own per-file
  sampling (R ≈ 200,000–600,000, varying per model — no sidecar pairing,
  each file carries its own wavelength column); `lineid` files are
  three-column (wavelength, line-ID string, equivalent width).
- The open design question is the read side's interpolation story. rorig
  spectra publish at per-file native samplings with no shared grid, so
  cross-node interpolation requires an explicit resampling onto a shared
  wavelength grid before the corner machinery can combine nodes. Any
  solution rests on the clause that the no-transform principle bounds the
  native layer, not the unified one (`docs/architecture.md`): the
  resample is a derived, provenance-recorded operation that never
  rewrites or replaces the native files. Undecided so far: which shared
  grid (a fixed per-product grid versus a per-query target grid), and how
  the choice is exposed on the grid/materialize API.

## combined wide-band channel — deliberately deferred here

- Downstream needs spectra of one star stitched across families into one
  wide band (an empirical optical library such as MILES/STELIB alongside
  a theoretical BOSZ/TLUSTY section), integrated over a stated κ-domain
  contract. v1 is downstream-side assembly: stellarlib's part is to keep
  every family readable and to expose an explicit, provenance-recorded
  resample onto a shared wavelength grid — no combined facade yet.
- The mixing rule is the open design question any combined facade must
  settle first: empirical libraries publish relative flux while the
  theoretical families publish F_λ surface flux, so a combined channel
  needs an explicit renormalization rule (where the anchor segment is,
  how the scale factor is computed and recorded in provenance). That
  rule must be documented on both sides before a facade here is more
  than an implicit, unrecorded scaling.
