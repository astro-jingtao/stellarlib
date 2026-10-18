# Data sources

What each upstream source publishes, what stellarlib verified directly
(and when), and the pitfalls the code defends against. Facts carry their
verification evidence; unverified statements say so.

## BOSZ 2024 (MAST HLSP)

Source: <https://archive.stsci.edu/hlps/bosz> — Meszaros et al. 2024,
A&A, 688, A197 (arXiv:2407.10872); the legacy library is Bohlin et al.
2017, AJ, 154, 106. Verbatim copies of the official README and a dated
snapshot of the landing page are archived in
[upstream/bosz2024/](upstream/bosz2024/) (CC BY 4.0, per the landing
page).

### Release tree (verified live 2026-10-17; counts and indexes 2026-10-18)

- `.../bosz2024/r<R>/<metallicity>/<file>` with R ∈ {500, 1000, 2000,
  5000, 10000, 20000, 50000} (plus `rorig/`, the original-sampling
  variant) and 14 metallicity directories `m+0.75` ... `m-2.50`.
- Wavelength grids live in `.../bosz2024/wavelength_grids/
  bosz2024_wave_r<R>.txt` — one file per numbered resolution, not per
  metallicity; rorig needs none (its files carry their own wavelength
  column).
- One (R, [M/H]) directory holds one spectrum file per (node,
  composition, vmicro): the (Teff, log g) nodes interleaved with their
  [a/M] (−0.25/0/+0.25/+0.50), [C/M] (−0.75/−0.50/−0.25/0/+0.25/+0.50),
  and vmicro (0/1/2/4 km/s) variants — 24 compositions, the README's
  "336 unique compositions" with the 14 metallicities. The count differs
  by metallicity (all 14 rorig scripts downloaded and counted:
  43,204 at m-2.25 up to 45,724 at m+0.00; 628,620 in total — the
  README states the same total). The directories are the upstream
  publishing units; a stellarlib product is one directory's complete
  mirror (below).
- Eight r500 files downloaded 2026-10-17 and checksum-recorded in the
  catalog (sizes 40,499–42,308 bytes); the network test re-verifies
  them.
- Official documentation is archived verbatim in
  [upstream/bosz2024/](upstream/bosz2024/) — the release-tree README
  (note: no "2024" in its filename; the landing page's own Read Me link
  inserts one and 404s) and a dated snapshot of the landing page. The
  README records a 2025-09-24 recomputation of the whole grid (OH+
  partition-function fix; higher-level hydrogen atom) — the served files
  are that recomputed set, and stellarlib's checksums were taken against
  it. The release root also carries a `readme.txt~` editor leftover,
  recorded and never mirrored.

### Official bulk packages (verified live 2026-10-18)

- MAST publishes one official bulk-download script per directory:
  `.../download_scripts/hlsp_bosz_bosz2024_sim_r<R>_m{±d.dd}_v1_bulkdl.sh`
  — 112 scripts (8 broadenings × 14 metallicities), each a plain list of
  per-file `curl` commands enumerating that directory's complete spectrum
  content (resam for the numbered resolutions, noresam for rorig). No
  directory-level tarballs exist (probes 404). The script is upstream's
  own manifest of the directory: stellarlib mirrors whole directories by
  fetching the script first and then every file it lists — the complete
  official package, never a subset.
- The rorig directories additionally hold the line-identification
  product line — one `_lineid.txt.gz` per v0 spectrum (three columns:
  wavelength, line-ID string, equivalent width; lines with W < 0.1 Å are
  not listed), i.e. exactly one quarter of the directory's spectra
  (11,431 at m+0.00, 10,829 at m-2.50; 157,155 in total). The official
  scripts omit them, and the one script that nominally covers them
  (`hlsp_bosz_bosz2024_sim_rorig_all_v1_bulkdl-lineids.sh`, "all" in the
  landing-page table) is an upstream defect: it lists m-2.50 only. The
  complete authoritative listing that includes the lineid files is the
  directory's own Apache index (served at the directory URL; verified to
  list exactly the spectra plus the lineid files and nothing else, with
  the lineid set equal to the script's v0 names with the product slot
  swapped). Each rorig mirror therefore fetches and caches that index as
  a designed part (`directory_index.html`) and enumerates the lineid
  parts from it — the lineid files are auxiliary data inside the rorig
  mirror products, on the same two-state fetch/scan/retry semantics as
  everything else.
- Library scale (the blueprint declares all of it; one HEAD-sampled file
  size per resolution, gz sizes vary with model): r500 ≈ 43 KB and
  r10000 ≈ 650 KB per file, rorig ≈ 17 MB, lineid ≈ 0.9 MB. Spectra
  total 5,028,960 files across the 112 directories, order 14 TB, of
  which the rorig directories alone hold 628,620 files ≈ 10.5 TB (~74%,
  the dominant share); lineid adds 157,155 files ≈ 0.15 TB. The
  per-part fetch, two-state scan, and retry semantics are unchanged by
  scale; the design never subsets, and how much is fetched at once is
  the builder's iteration order, not a design axis.

### File format (verified against downloads)

- "resam" files are two-column gzip ASCII: the Eddington flux H_lambda
  and the continuum, both erg/s/cm²/Å — **no wavelength column**. The
  log-uniform sampling grid (7500 points, 500 Å–31.97 µm at r500;
  150,000 points at r10000) comes from the per-resolution sidecar file;
  rows align by index across every file of that resolution. Naive
  (wavelength, flux) parsing silently produces flux-vs-continuum
  garbage — the reader therefore never guesses, and the pairing is an
  explicit set-construction decision.
- rorig "noresam" files are three-column (wavelength, H_lambda,
  continuum) at the synthesis's native sampling (R ≈ 200,000–600,000,
  varying per model); "lineid" files are three-column (wavelength,
  line-ID string, equivalent width). The spectrum readers currently
  recognize the resam grammar only — mirroring rorig is supported,
  opening it as a grid is not (yet).
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
errors at ≲0.5% optical, 5–10% at 3500–4000 K). `family_for` encodes
exactly this assignment — it is query-side knowledge (how a grid picks
each node's file inside a mirror), and these constants, not anything
observed over HTTP, are the only place publication knowledge is
encoded. A fetch 404 is recorded as a failed fetch attempt (status, url,
time in the MANIFEST; the part stays missing and is retried on the next
build); no publication claim is ever inferred from an HTTP status.

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
  7 is the detailed spectrum, kind 17 the continuum. Both kinds are
  packed in the same per-composition vispec tar — one model's two data,
  always held together. stellarlib records each node's kind-17 sibling
  (facts + set notes) and never warns about their presence: an archive
  without them would be the anomaly. The two kinds are NOT row-aligned
  (kind 17 samples its own coarser wavelength grid — 1,697 vs 198,345
  rows on G27500g300v10), so the continuum is addressable by its member
  name but never fused into a per-row array.
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

### Channel (verified live 2026-10-18)

TLUSTY distributes per-composition `*vispec*.tar` archives; the complete
inventory is now live-verified (catalog `tar_inventory`): OSTAR2002
publishes 10 tars (compositions C G L S T V W X Y Z = Z/Z☉ 2, 1, 1/2,
1/5, 1/10, 1/30, 1/50, 1/100, 1/1000, 0; 87–90 MB each; 138 members:
69 kind-7 + 69 kind-17), BSTAR2006 publishes 10 tars (BC BG BL BS BT ×
vmicro 2/10 km/s; 204–345 MB each) — 20 archives, ~3.6 GB in all,
fetched from `.../tlusty/Tlusty2002/database/{archive}.tar`. The tar is
the upstream publishing unit and the stellarlib product: fetched whole,
stored verbatim, spectra streamed from its members. Kind 7 and kind 17
are one model's two data and always arrive together — the reader records
each node's kind-17 sibling (they are not row-aligned: kind 17 samples
its own coarser wavelength grid, ~1,697 vs ~198,345 rows on
G27500g300v10) and never fakes an aligned continuum. The site's root
vhost currently redirects to an unrelated page; content is served under
`/tlusty/Tlusty2002/` (recorded in the catalog's `base_url`).

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
