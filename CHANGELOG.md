# Changelog

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
  refusal, kind-17 skipping) with synthetic format-equivalent fixtures.
- Local database implementing the design/two-state architecture:
  declared windows, per-part ok/missing/nonconforming scan, on-demand
  checksummed fetching (a 404 is recorded as a failed fetch and
  retried on the next build — no publication claim inferred),
  `register_local_product` (copy or by-reference), regenerated root
  inventory.
- Materialized array side-products with a sources sidecar and
  gone/changed source detection.
- Physics validation tool (Boltzmann closure, 4π convention, wavelength
  grid uniformity) verified on real full spectra.
