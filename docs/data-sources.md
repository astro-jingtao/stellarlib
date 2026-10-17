# Data sources

What each upstream source publishes, what stellarlib verified directly
(and when), and the pitfalls the code defends against. Facts carry their
verification evidence; unverified statements say so.

## BOSZ 2024 (MAST HLSP)

Source: <https://archive.stsci.edu/hlsps/bosz> — Meszaros et al. 2024,
A&A, 688, A197 (arXiv:2407.10872); the legacy library is Bohlin et al.
2017, AJ, 154, 106.

### Release tree (verified live 2026-10-17)

- `.../bosz2024/r<R>/<metallicity>/<file>` with R ∈ {500, 1000, 2000,
  5000, 10000, 20000, 50000} (plus `rorig/`, the original-sampling
  variant) and 14 metallicity directories `m+0.75` ... `m-2.50`.
- Wavelength grids live in `.../bosz2024/wavelength_grids/
  bosz2024_wave_r<R>.txt` — one file per resolution, not per metallicity.
- One (R = 500, [M/H] = 0) directory holds 45,724 files: the (Teff,
  log g) nodes interleaved with their [a/M] (−0.25/0/+0.25), [C/M]
  (−0.75/−0.25/0/+0.25), and vmicro (0/1/2/4 km/s) variants. A grid
  window pins one composition choice and enumerates its files by the
  filename grammar.
- Eight r500 files downloaded 2026-10-17 and checksum-recorded in the
  catalog (sizes 40,499–42,308 bytes); the network test re-verifies
  them.

### File format (verified against downloads)

- "resam" files are two-column gzip ASCII: the Eddington flux H_lambda
  and the continuum, both erg/s/cm²/Å — **no wavelength column**. The
  log-uniform sampling grid (7500 points, 500 Å–31.97 µm at r500;
  150,000 points at r10000) comes from the per-resolution sidecar file;
  rows align by index across every file of that resolution. Naive
  (wavelength, flux) parsing silently produces flux-vs-continuum
  garbage — the reader therefore never guesses, and the pairing is an
  explicit set-construction decision.
- Wavelengths are vacuum below 200 nm and air above; never converted.

### Upstream clipping is the library's own — preserved verbatim

The published resam files clip H_lambda below 1e-6 erg/s/cm²/Å to zero
and the continuum to 1; cool-giant grid points accordingly contain many
exact-zero flux rows (e.g. 1303/7500 at t3500, g+1.5 — counted on the
r500 giant set). This is a property of the published files, not a
downstream convention, and stellarlib neither replicates nor undoes it:
values are preserved exactly as parsed, and ratio consumers must handle
the zeros explicitly. (The continuum can also dip below the flux by
~1e-3 at some wavelengths; likewise preserved.)

### Flux convention: 4 pi, not pi

The MAST page instructs multiplying by π; the paper (Eq. 2) says
F_lambda = 4π H_lambda. Blackbody cross-checks favor 4π: on the r500
giant set (10 full spectra, run 2026-10-17 with
`tools/check_library_physics.py`), 4πH/(πBλ) at 5001 Å passes through
≈1.0 at Teff = 5000 K (0.03 at 3000 K to 1.13 at 6000 K — line
blanketing, as expected), whereas πH would sit a factor ~4 low. The
unified transformation `"4 pi * x"` implements the paper's convention.
The bolometric closure ∫H dλ vs σT⁴/4π holds to 0.7–2.4% on the same
set (the +2.4% at t3000 is the 32 µm grid edge truncating the
Rayleigh-Jeans tail — a documented deviation, not corruption); the
wavelength grid's log-step spread is 2.3e-5.

### Atmosphere families and the seams

Three families carry the grid: `mp` (MARCS plane-parallel, log g ≥ 3.5,
vmicro 1 km/s), `ms` (MARCS spherical, log g ≤ 3, vmicro 2), `ap`
(ATLAS9, vmicro 2, Teff ≥ 8250 K, plus 7500–8000 K where mp is
unpublished). Log g strictly between 3 and 3.5 has no family (the
published grids step over the seam; the paper bounds MARCS↔ATLAS9 seam
errors at ≲0.5% optical, 5–10% at 3500–4000 K). The window design's
family policy implements exactly this assignment; a fetch 404 records
the point as an unpublished hole.

### Coverage truth (verified 2026-10-09 on MAST, by the downstream project)

- Complete interpolation rectangle: Teff 2800–12000 K (MARCS floor
  2800 K). Above 12000 K the release ceiling is 16000 K but models are
  sparse (500 K steps, missing low-log g rows); ~8.5% of MARCS models
  are unpublished (holes, mostly at low log g).
- Combined with TLUSTY BSTAR2006 starting at 15000 K, the interval
  12000–15000 K has no interpolable published library. `grid.axes` and
  `grid.holes` report the truth; routing across the gap belongs to the
  application.

## TLUSTY OSTAR2002 / BSTAR2006

Source: <https://tlusty.oca.eu/> — Lanz & Hubeny 2003, ApJS 146, 417
(OSTAR2002: 27500–55000 K, 2500 K steps, log g 3.00–4.75, 0.25 dex);
Lanz & Hubeny 2007, ApJS 169, 83 (BSTAR2006: 15000–30000 K, 1000 K
steps, log g 1.75–4.75). The published set narrows at the hottest
models.

### File format (verified against downloads 2026-10-09)

- One gzip ASCII file per atmosphere and spectrum kind:
  `{comp}{teff:05d}g{logg*100:03d}v{vmicro*10:02d}.vis.{7|17}.gz`; kind
  7 is the detailed spectrum, kind 17 the continuum (a separate
  published product — stellarlib consumes 7 and skips 17 with a warning
  and a record, never mixing kinds).
- Two columns: wavelength (Å, **air** in the optical — NIST air line
  centroids match to 0.01–0.02 Å; vacuum would be off by ~1.8 Å) and
  H_lambda in erg/s/cm²/Å. Surface flux F = 4πH — same convention as
  BOSZ, so populations mixed across the libraries sit on one flux
  scale.
- SYNSPEC native sampling ≈ 0.03 Å/point in the red optical
  (R ≈ 2×10⁵).
- On-disk sets may drop the `.gz` suffix; the reader opens
  `path + ".gz"` in that case.
- The 27500–30000 K overlap band belongs to both releases; a set
  confined to it cannot infer its release and requires an explicit
  `release=`.

### Channel (NOT yet verified live)

TLUSTY distributes per-composition `*vispec*.tar` archives; the exact
archive inventory is unverified, so the fetch channel is closed by
design and products enter through `register_local_product`. Verified
on-disk sets seen: OSTAR2002 G (Z/Z☉ = 1, vmicro 1 km/s, 69 kind-7
files), BSTAR2006 BG (vmicro 2 km/s, 163 kind-7 files).

## Pitfall index (all recorded in the registry notes too)

1. resam has no wavelength column — pairing is explicit; row mismatch
   fails loudly.
2. Two wave-file layouts exist in the wild (flat next to the spectra;
   `wavelength_grids/` subdirectory) — both are discovered; ambiguity
   lists the candidates.
3. Exact-zero flux rows exist by upstream design — handle them in
   ratios; never "fix" them.
4. vmicro differs per atmosphere family within one BOSZ set (mp 1;
   ms/ap 2 km/s) — the set-level parameter reports the mixed state
   instead of a false constant.
5. Never mix air/vacuum or BOSZ/TLUSTY wavelength grids silently.
6. Holes exist; `grid.holes` is the truth; multilinear refuses holed
   cells; voronoi is the hole-tolerant path.
