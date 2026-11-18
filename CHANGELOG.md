# Changelog

## Unreleased

- The STELIB empirical stellar library (255 observed stars, VizieR
  catalog III/232) joins as the family `stelib/2003` — the second
  empirical library, under the same scatter paradigm: the product
  mirrors the whole catalog tree (ReadMe, the star table, the 255
  corrected spectra `cor_sp/` that are the published library, their 258
  raw counterparts `obs_sp/`, and the published atlas; no subsets), and
  the star table acts as the upstream's own bulk manifest — the design
  expands from it once held (the BOSZ bulkdl pattern). The channel is
  an explicit blueprint choice: `source="cds"` (canonical, default) or
  `source="adac"` (the NAOJ mirror the whole tree was fetched and
  verified through on 2026-11-18); no fallback between channels, and
  every part's manifest entry records the URL it came from. The flux is
  absolute spectrophotometry (BUNIT erg/s/cm^2/Angstrom in every file;
  3% overall photometric uncertainty; cor_sp carries the
  interstellar-extinction and radial-velocity corrections) and maps to
  the new unified field `observed_flux_density` — never onto
  `eddington_flux`/`surface_flux`. Every star sits on its own
  wavelength grid (per-file linear WCS), so queries are nearest-star
  only (`StelibSpectra` defaults to `voronoi`, `multilinear` refused).
  The 48 stars without all three parameters stay held and mirrored but
  carry no query coordinates (recorded in the set's notes); HD 053929's
  second corrected spectrum is one recorded held file, not a second
  node at the same coordinates.
- The MILES empirical stellar library (985 observed stars,
  research.iac.es) joins as the family `miles/2011` — the first
  empirical library, under the scatter paradigm: the product mirrors the
  whole official package (the FITS tarball, the parameter table, the
  library page snapshot; three parts, no subsets), the stars join their
  parameter rows by s-number, and the relative flux (arbitrary
  per-star normalization, no unit keyword; the absolutely-calibrated
  subset is available only from the authors) maps to the new unitless
  unified field `observed_flux` — never onto `eddington_flux` or
  `surface_flux`. Queries are nearest-star only: `voronoi` is
  `MilesSpectra`'s default and `multilinear` is refused (a weighted mix
  of two arbitrary normalizations is not a spectrum of anything). The
  39 stars whose table row reports no [Fe/H] stay held and mirrored but
  carry no query coordinates — recorded by s-number and name in the
  set's notes, never silently dropped. The site's ASCII re-encoding of
  the same spectra and the CaT library are documented but out of the
  product. The wavelength axis is the FITS header's own linear WCS
  (3500.0-7429.4 Å pixel domain vs the cited 3525-7500 Å window — both
  recorded); README's scope now states empirical libraries explicitly.
- The streamed part fetch (atomic temporary, on-the-fly sha256, 404
  recorded as a failed attempt) moves to a shared
  `fetch_streamed_part`; the BOSZ and TLUSTY adapters delegate to it.
- `verify_database` runs one full-integrity pass over a database
  against its blueprint: recorded parts are re-checked against both
  recorded quantities, and present parts with no recorded sha256 —
  files whose fetch round arrived but whose manifest write was lost —
  are hashed in place and recorded with `evidence:
  sha256_verified_in_place`, keeping any history a half-written entry
  already held. It never fetches: missing parts are reported,
  nonconforming parts are left for human decision, and the pass learns
  the signature cache as it reads (piggybacking the same chunked
  manifest flushes as fetch arrivals), so a re-run after an abort
  reads only what it never reached. `workers=` bounds the hashing
  parallelism — a speed axis only, per-part semantics identical.
- `BoszSpectra.construct(database=...)` now honors an explicit
  `wave_path=` pin: the database branch previously accepted the
  parameter and silently dropped it (only `am`/`cm`/`vmicro` rode
  along), so a stated sidecar never reached the reader. Left unset,
  nothing changes — the sidecar is discovered across the database's
  products, which covers mirror products (the sidecar mirrors as its
  own product, never next to the spectra).
- Part classification verifies a present part against both recorded
  quantities: a size mismatch is nonconforming on its own (identical
  bytes cannot have different sizes), and a matching size still pays
  the sha256 comparison — size equality alone never clears a part. The
  sha256 answer is served from a new database-level signature cache
  (`signature_cache.json`, built on the `file_quick_signature` key
  grammar with paths relative to the database root): an unchanged file
  costs one `stat`, so an incremental build against a large existing
  database pays a full read at most once per file, not once per run
  (the earlier size-only fast path, which skipped the hash entirely on
  size equality, is superseded). Fetch-time and registration hashes
  feed the cache; deleting the file is always safe and forces one full
  re-verify — the recommended action after migrating a database to
  another machine, since mtime-preserving copies transfer content
  unverified. A first scan after this change re-hashes every present
  part once to populate the cache.
- The supported Python floor drops to 3.9 (was 3.10): the only 3.10-only
  API in use was `Path.write_text(..., newline=...)` (test/tool
  infrastructure plus a few database write paths), now written as the
  equivalent `open(..., "w", newline=...)`; `requires-python` follows.
  The reading stack was already 3.9-clean.
- A fetch pass persists the manifest as parts arrive: mirror products
  fetch tens of thousands of parts in one pass, and the manifest was
  previously written only when the whole product completed — a run that
  died mid-product (transport failure, hard kill) left its arrived
  files present-but-unrecorded, a state that classifies ok with no
  integrity evidence and is never re-verified. The manifest now
  flushes every 2,000 arrivals and once more after a failed batch
  drains (and on the sequential path's failure), so the next run
  resumes with full integrity records for every present file.

- `build_database` gains `workers=`: bounded parallelism for one
  product's fetch pass (default 1, strictly sequential). A
  `workers > 1` pass fetches pending parts through that many worker
  threads, each holding its own session (`session_factory=` overrides
  session creation; `session=` remains the sequential injection point
  and the two are refused in the wrong combination). Per-part semantics
  are unchanged: atomic tmp+replace per file, manifest entry on
  arrival, 404 as a recorded failed fetch, and a transport error fails
  the run loudly after the in-flight batch drains — the next build
  resumes where this one stopped. The sequential default now also
  reuses one session across the whole build instead of paying
  connection setup on every file (measured on a 628,620-file mirror
  round: per-file fetches are latency-bound, ~2.2 s/file at one
  session per part).
- The facades' `construct(database=...)` path works again: it imported
  `open_database` from a module that never exported it and passed the
  bare family name to `Database.grid` (which requires
  version-qualified models). The TLUSTY facade keeps its documented
  release inference — the label must pin one product across the
  family's releases.
- `stellarlib.__version__` follows `pyproject.toml` again (the 0.1.2 bump
  had missed the constant).
- `TODO.md` records the pending noresam (rorig) read-side design: the
  three-column native-sampling grammar, the lineid reader, and the open
  interpolation question (an explicit, provenance-recorded resampling
  onto a shared wavelength grid — which grid, and how it is exposed).

## 0.1.2 (2026-10-18)

The rorig line-identification product line enters the blueprint, and the
BOSZ catalog facts are re-verified against the official documentation.

- BOSZ rorig mirrors now include the lineid files: each rorig product
  fetches the directory's own Apache index as a designed part
  (`directory_index.html`, role `directory_index`) — the one complete
  authoritative listing that includes them, since the official bulkdl
  scripts omit the lineid product line and the nominally covering
  `bulkdl-lineids.sh` is an upstream defect ("all" in name, m-2.50 only
  in content) — and enumerates one `lineid` part per v0 spectrum from
  it, on the same fetch/scan/retry semantics as every other part.
- Catalog corrections (all re-verified live 2026-10-18 against the
  official README, the landing page, all 14 rorig bulkdl scripts, and
  the directory indexes): spectra per directory differ by metallicity
  (43,204…45,724; 628,620 total, equal to the README's stated total —
  the previous "45,724 everywhere / 5,121,088 files" figures were the
  m+0.00 values over-generalized); variant axes are [a/M]
  −0.25…+0.50 and [C/M] −0.75…+0.50; the README's real URL carries no
  "2024" in its filename. The README's 2025-09-24 recomputation notice
  and the CC BY 4.0 license are recorded.
- `build_database` gains `select=`: glob patterns over product paths
  (`"bosz/rorig/*"`, `"tlusty/OSTAR2002/G"`) restricting what one run
  fetches to whole products. The blueprint is untouched — unselected
  products keep their state and later runs complete them; a pattern
  matching no declared product is an error, never a silent empty round.
- Official upstream documentation (README + dated landing-page snapshot)
  is archived verbatim under `docs/upstream/bosz2024/`, kept
  byte-identical via a `-text` git attribute.

## 0.1.1 (2026-10-18)

The database acquires whole official packages, never subsets. The
window mechanism (declared parameter-point lists) is removed as a design
flaw: the fetch side now mirrors exactly what the source publishes, at
the source's own granularity.

- The blueprint (design.json, schema `stellarlib-design/2`) is a local
  record of the complete upstream library: a family declaration expands
  to every upstream unit, whole. There is no partial form, and the old
  schema is rejected with guidance.
- BOSZ: a product is the complete mirror of one (broadening,
  metallicity) directory — enumerated by the official bulkdl.sh script
  (upstream's own manifest, 112 directories × 45,724 spectra), fetched
  as the product's first part and expanded in-build. The shared
  wavelength grids mirror as their own product and pair automatically
  at grid-open. Reading a mirror is a selection: `am`/`cm` (default
  solar) and `vmicro` (default family policy) pick the composition,
  recorded in the set notes; `Database.grid` takes `collection=` to
  disambiguate metallicity labels across broadenings.
- TLUSTY: the fetch channel opens at exactly the upstream granularity —
  the per-composition vispec tar (20 archives, inventory live-verified
  2026-10-18). A product is one tar, fetched whole and stored verbatim;
  spectra stream from members. Kind 7 and kind 17 are one model's two
  data and are held together: kind-17 presence is expected (no warning),
  each node records its sibling member, and no row-aligned continuum is
  fabricated (the kinds sit on different wavelength grids).
- Materialization is no longer described as bounded by declarations —
  it merges whatever grid is opened, and grids open whole mirrors.

## 0.1.0 (2026-10-17)

Initial release.

- Core schema (`ParameterSpec`, `FieldSpec`, `ModelIdentity`), provenance
  bundle, exception/warning hierarchies, and `NativeSpectrum` — the
  verbatim one-file spectrum container whose wavelength/continuum are
  `None` where the published format omits the column.
- Unified-parameter registry (definitions + per-family mappings for BOSZ
  2024 and TLUSTY) with a generated, test-pinned reference document.
- Unified view over native spectra with the fixed transformation
  vocabulary (`4 pi * x`, `x * 1e-4`, `10 ** x`).
- Lazy node-index grids: `SpectrumSet` (paths + facts + loader),
  `SpectrumGrid` with truthful axes and hole reporting, exact/nearest
  selection.
- Audited interpolation: N-dimensional corner machinery with the exact
  downstream contract (C-order corners, degenerate axes), multilinear
  point queries with strict/clamp range policy, Voronoi nearest-node
  for holed cells, wavelength resampling recorded in provenance.
- BOSZ 2024 backend (resam + wave-sidecar pairing, family/vmicro
  knowledge, per-file MAST URLs) with real-excerpt fixtures.
- TLUSTY OSTAR2002/BSTAR2006 backend (release inference with overlap
  refusal, whole-model kind-7/17 handling) with synthetic
  format-equivalent fixtures.
- Local database implementing the blueprint/two-state architecture:
  whole-package mirrors, per-part ok/missing/nonconforming scan,
  checksummed fetching (a 404 is recorded as a failed fetch and
  retried on the next build — no publication claim inferred),
  `register_local_product` (copy or by-reference), regenerated root
  inventory.
- Materialized array side-products with a sources sidecar and
  gone/changed source detection.
- Physics validation tool (Boltzmann closure, 4π convention, wavelength
  grid uniformity) verified on real full spectra.
