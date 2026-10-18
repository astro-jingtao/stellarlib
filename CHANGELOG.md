# Changelog

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
