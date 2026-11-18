# Family guide — coverage, formats, conventions

One section per library family. Values stated here are the published
facts stellarlib records in its registries; the package's
`docs/data-sources.md` carries the verification evidence.

## BOSZ 2024 (`bosz/2024`)

- Source: MAST HLSP `bosz2024`. One directory per (broadening,
  metallicity): broadenings r500, r1000, r2000, r5000, r10000, r20000,
  r50000, rorig; metallicities +0.75 … −2.50 in 0.25 steps. Each
  directory holds 43,204–45,724 spectra (628,620 per broadening); the
  rorig directories also hold line-identification files.
- Sampling: log-uniform wavelength grids, 500 Å–31.97 µm; each
  resolution has its own grid file (mirrored as the
  `wavelength_grids` product — spectra files carry NO wavelength
  column; pairing is stellarlib's explicit step).
- Flux: Eddington flux H_λ in erg/s/cm²/Å (`eddington_flux`); surface
  flux F_λ = 4πH_λ (`surface_flux`) — the same 4π convention as
  TLUSTY, so mixed populations sit on one scale.
- Axes: Teff, log g, [M/H], plus per-node composition (α/Č enriched
  variants interleave in one directory; a grid selection pins one —
  `am`/`cm` default 0) and microturbulence (family policy: mp 1,
  ms/ap 2 km/s).
- Gaps: log g strictly between 3.0 and 3.5 has no models; 8000–8250 K
  at log g > 3 either; ~8.5% of MARCS nodes are unpublished (holes).
  Queries: multilinear refuses holed cells with `GridHoleError`;
  `interp="voronoi"` returns the nearest node.
- Quirks preserved verbatim: upstream clips H_λ below a threshold to
  exact zeros — handle in ratios, never "fix".

## TLUSTY OSTAR2002 / BSTAR2006 (`tlusty/OSTAR2002`, `tlusty/BSTAR2006`)

- Source: tlusty.oca.eu, NLTE model atmospheres with SYNSPEC optical
  spectra. OSTAR2002: 27500–55000 K (2500 K steps); BSTAR2006:
  15000–30000 K (1000 K steps); log g 1.75–4.75 (0.25 dex). 20
  per-composition tar archives (OSTAR 10 compositions Z/Z☉ 2…0; BSTAR
  5 compositions × vmicro 2/10 km/s) — one tar = one product.
- Sampling: native SYNSPEC ~0.03 Å/point (R≈2×10⁵), air wavelengths in
  the optical.
- Flux: H_λ (`eddington_flux`) and F_λ = 4πH_λ (`surface_flux`), same
  convention as BOSZ.
- Axes: Teff, log g; composition and vmicro fixed per product.
- The 27500–30000 K overlap band belongs to both releases — a set
  confined to it needs an explicit `release=`.

## MILES (`miles/2011`)

- Source: research.iac.es — the Medium-resolution INT Library of
  Empirical Spectra: 985 OBSERVED stars. Product = one official FITS
  tarball + the parameter table (joined by s-number) + the library
  page snapshot.
- Coverage: 3525–7500 Å cited, 2.5 Å FWHM; the FITS WCS pixel domain
  is 3500.0–7429.4 Å (both recorded; read `wavelength` for the actual
  axis).
- Flux: RELATIVE, arbitrary per-star normalization, no unit
  (`observed_flux`, unitless). Only a subset is absolutely calibrated
  and only on request from the authors — never label MILES flux with a
  physical flux unit, and never interpolate between stars
  (`multilinear` is refused; `voronoi` nearest star is the default and
  only query).
- Coordinates: (Teff, log g, [Fe/H]) scatter from the parameter table;
  946 stars carry all three; 39 report no [Fe/H] (listed in the set's
  `notes["stars_without_parameters"]`, not queryable).

## STELIB (`stelib/2003`)

- Source: VizieR catalog III/232 (Le Borgne et al. 2003) — 255 OBSERVED
  stars, 3200-9500 A cited coverage at ~3 A FWHM, 1 A sampling. Product
  = the whole catalog tree (ReadMe + table.dat + the corrected spectra
  cor_sp/ + their raw counterparts obs_sp/ + the published atlas); the
  blueprint channel is explicit: `dict(family="stelib")` fetches CDS
  (canonical), `dict(family="stelib", source="adac")` the verified NAOJ
  mirror — no fallback between channels.
- Coverage: pixel domain ~3196-9898 A per star (the paper cites
  3200-9500 A; both recorded).
- Flux: ABSOLUTE spectrophotometry at the observer
  (`observed_flux_density`, erg/(s cm^2/A) — BUNIT is in every file;
  3% overall photometric uncertainty; the served cor_sp set has the
  interstellar-extinction and radial-velocity corrections applied).
  Never map it onto `eddington_flux`/`surface_flux`, and never
  interpolate between stars (`multilinear` refused; `voronoi` nearest
  star is the default and only query — the stars also sit on per-star
  wavelength grids, so there is no common sampling to mix on).
- Coordinates: (Teff, log g, [Fe/H]) scatter from table.dat; 207 of
  255 stars carry all three (the rest are listed in
  `notes["stars_without_parameters"]`, not queryable). HD 053929's two
  corrected spectra are one scatter point (cSpFile1 serves; the second
  is recorded in `notes["second_corrected_spectra"]`).

## Choosing a family by parameters

- Teff ≲ 3750 K or empirical stars wanted → MILES (nearest star; check
  the returned star's parameters in `selection_info`).
- Absolute optical spectrophotometry of an observed star wanted (or the
  3200-3500 / 7500-9500 A wings beyond MILES) → STELIB (nearest star).
- 15000–55000 K, NLTE hot stars → TLUSTY.
- Wide coverage 3500–31,970 Å at fixed resolution, theory → BOSZ
  (pick the broadening matching your resolution; r500 ≈ R≈500).
