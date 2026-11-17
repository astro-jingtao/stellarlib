# TODO

Design and feature work deliberately not yet done. Every item names the
gap and the constraint any solution must respect; none of these are bugs.

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
