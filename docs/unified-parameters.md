# Unified parameters and field mappings

This document records the unified vocabulary every data source maps into, each source's own fields and physical parameters, and the mapping between the two layers. It is generated from the registry data files: `src/stellarlib/registry/unified_parameters.json` holds stellarlib's own definitions (unified fields, physical parameters), and each family's characteristics and mappings live in `src/stellarlib/models/<family>/unified_parameters.json` next to its `catalog.json`. The library code loads and validates them as one registry — the backends read their field specs and static parameter knowledge from it.

Regenerate the repository copy after editing the registry:

```bash
python tools/generate_parameter_docs.py
```

## Unified field vocabulary

Every field is traceable back to a native array. Transformations come from a fixed named vocabulary (`4 pi * x`, `x * 1e-4`, `10 ** x`); no free-form conversion exists, and the air/vacuum wavelength convention of a product is preserved, never converted.

| Unified field | Unit | Representation | Description |
|---|---|---|---|
| `wavelength` | Angstrom | linear | wavelength of the sampling grid; the air/vacuum convention is the product's own (declared per family in notes) and is never converted |
| `wavelength_um` | um | linear | wavelength in micron (x * 1e-4) |
| `observed_flux` | — | linear | observed flux of an empirical library's star — relative, with an arbitrary per-star normalization (unitless); mapped only by empirical families and never onto or from the model flux fields |
| `observed_flux_density` | erg/(s cm^2 Angstrom) | linear | the observed energy flux density F_lambda at the observer as published — absolutely calibrated empirical spectrophotometry; mapped by empirical families whose calibration is absolute, and never onto or from the model flux fields |
| `eddington_flux` | erg/(s cm^2 Angstrom) | linear | the Eddington flux H_lambda as published — the first angular moment of the specific intensity |
| `surface_flux` | erg/(s cm^2 Angstrom) | linear | the astrophysical surface flux F_lambda = 4 pi H_lambda (4 pi * x) |
| `continuum` | erg/(s cm^2 Angstrom) | linear | the continuum level as published, same units as the flux |

## Physical parameter vocabulary

Physical parameters a library product can carry. The role a parameter plays in a specific source (axis, fixed, implicit) is listed per source below; the value notions — available grid values, fixed values, selection pins — stay separate in the API.

| Parameter | Unit | Convention | Description |
|---|---|---|---|
| `teff` | K | — | effective temperature; the primary axis of every spectral library |
| `log_surface_gravity` | dex(cm / s2) | — | log10 surface gravity (same name and unit as stellariso, for cross-package consistency) |
| `m_h` | dex | [M/H] | overall metallicity as [M/H] of the scaled-solar composition set |
| `alpha_m` | dex | [a/M] | alpha-element enhancement |
| `carbon_m` | dex | [C/M] | carbon abundance offset |
| `microturbulence` | km/s | — | microturbulent velocity of the model atmosphere |
| `spectral_resolution` | — | resolving power R = lambda / dlambda | the instrumental broadening the published sampling encodes |
| `composition` | — | the library's own composition tag, verbatim | composition selector of a per-composition product (TLUSTY tags) |

## basel (`basel`)

Field mapping version: `1`.

- Semi-empirical library: theoretical SEDs (Kurucz ATLAS9, plus Fluks/HD M giants and hotter-star extensions) recalibrated onto empirical color-temperature relations; the recalibration conserves each model's bolometric flux (verified on the published numbers: closure to 0.1-0.5%).
- The published flux is the Eddington flux H_nu per steradian (erg/s/cm^2/Hz/sr) — settled by the bolometric closure of the files themselves (the author's ReadMe flags the 4pi ambiguity; the numbers resolve it). The unified layer converts f_nu to the per-Angstrom convention: H_lambda = f_nu * c / lambda^2, surface flux F_lambda = 4 pi H_lambda.
- Native wavelength is nm on the library's one shared 1221-point grid (9.1 nm - 160000 nm), identical in every file of both versions; never resampled, never converted in the native layer.
- BaSeL 3.1 publishes two calibration variants (WLBC99, PADOVA2000) with DIFFERENT fluxes at the same nodes; one grid serves exactly one variant, stated on the identity and in the set notes. The paper frames them as tools for different applications, so there is no default: WLBC99 consistently matches empirical colour-temperature relations (individual-star work); PADOVA2000 reproduces globular-cluster colour-magnitude diagrams when combined with Padova 2000 isochrones (population synthesis), at the price of consistency with empirical Te-colour relations — its model temperatures are to be treated with scepticism (giant-branch Te deviations up to ~500 K). At solar metallicity the two nearly coincide (same calibration file; only the Te-log g relation differs), and the [M/H] = +0.5 file is BaSeL 2.2 unmodified in both.
- The 3-axis (Teff, log g, [M/H]) grid has large honest holes: each metallicity file publishes its own (Teff, log g) node set (per-file counts 450-459 in 3.1, 375-467 in 2.2). Holes are reported, never filled; interp='voronoi' is the hole-tolerant query path.
- BaSeL 2.2 holds 29 blocks whose [Fe/H] parameter column disagrees with the filename's metallicity; the FILE names the node's metallicity (the library's own partition) and every mismatch is recorded per node and in the set notes — never relabeled.
- Coverage: Teff 2000-50000 K, log g -1.02 to 5.5 (dwarf/giant sequences), [M/H] -2.0..+0.5 (3.1, 6 values) or -5.0..+1.0 (2.2, 19 values).

### 3.1 (`3.1`)

#### Field mappings

| Unified field | Native source | Origin | Transformation | Notes |
|---|---|---|---|---|
| `wavelength` | `wavelength` | transformed_native | x * 10 | the file header's shared grid, nm converted to Angstrom |
| `wavelength_um` | `wavelength` | transformed_native | x * 1e-3 | the shared grid in micron |
| `eddington_flux` | `flux` | transformed_native | x * c / lambda_nm^2 / 10 | the published f_nu per steradian converted to H_lambda per Angstrom per steradian |
| `surface_flux` | `flux` | transformed_native | 4 pi * x * c / lambda_nm^2 / 10 | F_lambda = 4 pi H_lambda per Angstrom (the closure-verified reading of the published f_nu) |
| `continuum` | — | missing | — | BaSeL publishes recalibrated SEDs only; no continuum column exists |

#### Physical parameters

| Parameter | Role | Value / source | Notes |
|---|---|---|---|
| `teff` | axis (varies across the published grid) | file values `teff` | — |
| `log_surface_gravity` | axis (varies across the published grid) | file values `logg` | — |
| `m_h` | axis (varies across the published grid) | file values `m_h` | from the filename's metallicity token (the library's own one-file-per-metallicity partition) |
| `spectral_resolution` | implicit (used by the model, not recoverable) | — | broadband SED grid: 1221 points over 9.1 nm - 160000 nm, no instrumental broadening; not a selectable axis |

### 2.2 (`2.2`)

#### Field mappings

| Unified field | Native source | Origin | Transformation | Notes |
|---|---|---|---|---|
| `wavelength` | `wavelength` | transformed_native | x * 10 | the file header's shared grid, nm converted to Angstrom |
| `wavelength_um` | `wavelength` | transformed_native | x * 1e-3 | the shared grid in micron |
| `eddington_flux` | `flux` | transformed_native | x * c / lambda_nm^2 / 10 | the published f_nu per steradian converted to H_lambda per Angstrom per steradian |
| `surface_flux` | `flux` | transformed_native | 4 pi * x * c / lambda_nm^2 / 10 | F_lambda = 4 pi H_lambda per Angstrom (the closure-verified reading of the published f_nu) |
| `continuum` | — | missing | — | BaSeL publishes recalibrated SEDs only; no continuum column exists |

#### Physical parameters

| Parameter | Role | Value / source | Notes |
|---|---|---|---|
| `teff` | axis (varies across the published grid) | file values `teff` | — |
| `log_surface_gravity` | axis (varies across the published grid) | file values `logg` | — |
| `m_h` | axis (varies across the published grid) | file values `m_h` | from the filename's metallicity token (the library's own one-file-per-metallicity partition) |
| `spectral_resolution` | implicit (used by the model, not recoverable) | — | broadband SED grid: 1221 points over 9.1 nm - 160000 nm, no instrumental broadening; not a selectable axis |

### Scientific references

- Lejeune, T., Cuisinier, F., & Buser, R. 1998, A&AS 130, 65 (bibcode `1998A&AS..130...65L`)
- Westera, P., Lejeune, T., Buser, R., Cuisinier, F., & Bruzual A., G. 2002, A&A 381, 524 (bibcode `2002A&A...381..524W`)

## BOSZ 2024 (`bosz`)

Field mapping version: `1`.

- Resampled spectra store two columns (Eddington flux H_lambda, continuum) with NO wavelength column; the log-uniform sampling grid is published per resolution as a separate file (bosz2024_wave_r<R>.txt) and rows align by index across every file of that resolution — naive (wavelength, flux) parsing silently produces flux-vs-continuum garbage.
- The published flux is the Eddington flux H_lambda; the surface flux is F_lambda = 4 pi H_lambda (Meszaros et al. 2024, Eq. 2). The MAST HLSP page instead instructs multiplying by pi; a blackbody cross-check (Teff 5000 K, log g 4.5, at 5000 Angstrom) favors 4 pi at the ~1% level, so the transformation '4 pi * x' implements the paper's convention.
- Upstream clips H_lambda below 1e-6 erg/s/cm^2/Angstrom to zero and the continuum to 1 — a property of the published resam files. stellarlib preserves the published values verbatim: no clipping, no un-clipping, no filling. Cool-giant grid points accordingly contain exact-zero flux rows (the published clipping floor, not corruption).
- The continuum column can dip below the flux by ~1e-3 erg/s/cm^2/Angstrom at some wavelengths (observed on the r500 giant set); both columns are preserved verbatim and no normalization is applied.
- Three atmosphere families carry the grid: mp (MARCS plane-parallel, log g >= 3.5, model vmicro 1 km/s), ms (MARCS spherical, log g <= 3, vmicro 2 km/s), ap (ATLAS9 plane-parallel, vmicro 2 km/s, Teff >= 8250 K plus 7500-8000 K where mp is unpublished). Queries between log g 3 and 3.5 interpolate across the spherical/plane-parallel MARCS seam; the paper's own MARCS-ATLAS9 comparisons bound such seams at <=0.5% in the optical (5-10% at 3500-4000 K).
- Wavelengths are vacuum below 200 nm and air above; the sidecar grid encodes this and stellarlib never converts between conventions.
- The complete interpolation rectangle spans Teff 2800-12000 K (MARCS floor 2800 K). The release ceiling is 16000 K, but above 12000 K the models are sparse (500 K steps with missing low-log g rows), and ~8.5% of MARCS models are unpublished — holes, mostly at low log g. grid axes and the holes report the truth; rectangular interpolation across a holed cell is refused (Voronoi nearest-node interpolation is the hole-tolerant alternative).

### Resampled spectra (`*_resam.txt.gz`) (`resampled_spectra`)

#### Field mappings

| Unified field | Native source | Origin | Transformation | Notes |
|---|---|---|---|---|
| `wavelength` | `wavelength` | native | — | the per-resolution log-uniform sidecar grid, attached by explicit pairing; rows align by index with the flux file |
| `wavelength_um` | `wavelength` | transformed_native | x * 1e-4 | — |
| `eddington_flux` | `flux` | native | — | column 1 of the resam file (H_lambda), verbatim including upstream-clipped zeros |
| `surface_flux` | `flux` | transformed_native | 4 pi * x | Meszaros et al. 2024 Eq. 2; see the family notes for the pi-vs-4pi dispute resolution |
| `continuum` | `continuum` | native | — | column 2 of the resam file, verbatim including the clipped-to-1 floor |

#### Physical parameters

| Parameter | Role | Value / source | Notes |
|---|---|---|---|
| `teff` | axis (varies across the published grid) | file values `teff` | — |
| `log_surface_gravity` | axis (varies across the published grid) | file values `logg` | — |
| `m_h` | axis (varies across the published grid) | file values `mh` | — |
| `alpha_m` | fixed (known value for the whole product) | file value `am` | the standard downloaded set is scaled-solar ([a/M] = 0); a grid holds one value and refuses mixed-composition files |
| `carbon_m` | fixed (known value for the whole product) | file value `cm` | the standard downloaded set is [C/M] = 0; a grid holds one value and refuses mixed-composition files |
| `microturbulence` | — | file value `vmicro`; only when {'available': 'vmicro'} or only when {'missing': 'vmicro'} | — |
| `spectral_resolution` | fixed (known value for the whole product) | file value `resolution` | the r-token of the filename; one grid holds one resolution (the wavelength grids differ across resolutions) |

### Scientific references

- Meszaros, S., et al. 2024, A&A, 688, A197 (bibcode `2024A&A...688A.197M`)
- Bohlin, G., et al. 2017, AJ, 154, 106 (the legacy BOSZ library) (bibcode `2017AJ....154..106B`)

## miles (`miles`)

Field mapping version: `1`.

- One FITS file per star (REDUCEME product: a single-row image HDU, 4367 float32 pixels, 23,040 bytes each), named s0001.fits..s0985.fits in right-ascension order; the s-number is the join key to the parameter table's MILES column. The wavelength axis is the header's own linear WCS (CRPIX1=1, CRVAL1=3500.0, CDELT1=0.9 A/pix), giving the pixel domain 3500.0-7429.4 A; the library's cited coverage window is 3525-7500 A at 2.5 A FWHM — both are the upstream's own statements, recorded, never adjusted.
- The flux carries no unit keyword and no physical calibration: the official library page states the continuum shape is carefully calibrated while an absolutely-calibrated subset is available only upon request from the authors. observed_flux is therefore unitless and relative, with an arbitrary per-star normalization, and eddington_flux/surface_flux are deliberately not mapped — a model surface-flux label on a relatively-normalized observed spectrum would be a false claim.
- No interpolation is offered: the stars are an observed scatter in (Teff, log g, [Fe/H]) and each spectrum's normalization is arbitrary, so a weighted mix of two stars' fluxes is not a spectrum of anything. Queries are nearest-star (voronoi semantics: warned, recorded).
- The parameter table is fixed-width ASCII; '--' marks a value it does not report. 39 of 985 stars report no [Fe/H] (among them nearly all the M-dwarf and hot-star tail): their files are held and mirrored like every other part, but they carry no query coordinates and are listed by s-number and name in the set's notes — never silently dropped.
- NativeSpectrum.to_table() attaches the model-library flux unit (erg/s/cm^2/A) as its documentation convention; MILES flux is relative, so the unified view is the calibrated statement of what the numbers are.

### library (`library`)

#### Field mappings

| Unified field | Native source | Origin | Transformation | Notes |
|---|---|---|---|---|
| `wavelength` | `wavelength` | native | — | the header's linear WCS axis (CRVAL1 + CDELT1 * (pixel - CRPIX1)) |
| `wavelength_um` | `wavelength` | transformed_native | x * 1e-4 | — |
| `observed_flux` | `flux` | native | — | the star's observed flux, relative (arbitrary per-star normalization; no unit keyword in the files) |
| `eddington_flux` | — | missing | — | not mapped: the distributed library is not flux-calibrated in absolute terms (the calibrated subset is available from the authors upon request) |
| `surface_flux` | — | missing | — | not mapped: an empirical observed spectrum has no model surface-flux meaning |
| `continuum` | — | missing | — | no continuum column is published |

#### Physical parameters

| Parameter | Role | Value / source | Notes |
|---|---|---|---|
| `teff` | axis (varies across the published grid) | file values `teff` | — |
| `log_surface_gravity` | axis (varies across the published grid) | file values `logg` | — |
| `m_h` | axis (varies across the published grid) | file values `m_h` | — |
| `spectral_resolution` | implicit (used by the model, not recoverable) | — | 2.5 A FWHM (Falcón-Barroso et al. 2011 re-assessment; the v9.1 revision's headline change); not a selectable product axis |

### Scientific references

- Sánchez-Blázquez, V., et al. 2006, MNRAS 371, 703 (the MILES library) (bibcode `2006MNRAS.371..703S`)
- Falcón-Barroso, J., et al. 2011, A&A 532, A95 (updated stellar parameters and resolution) (bibcode `2011A&A...532A..95F`)

## stelib (`stelib`)

Field mapping version: `1`.

- One FITS file per spectrum, 1-D 6700-pixel float32 image with BUNIT 'erg /s /cm2 /angstroms': the library is absolute spectrophotometry at the observer (the paper states a 3% overall photometric uncertainty). The served library is cor_sp/ — the interstellar-extinction and radial-velocity corrected set the paper publishes; the obs_sp/ raw counterparts are held and mirrored, never served as library members.
- Every star sits on its own wavelength grid: the per-file linear-WCS (CRVAL1, CDELT1) pairs differ across the whole tree (live-measured 2026-11-18), so the axis is computed per file from its own header and no shared grid is assumed anywhere. The pixel domain runs ~3196-9898 A while the paper cites 3200-9500 A — both statements are the upstream's own and both are recorded.
- observed_flux_density carries the absolutely-calibrated observed F_lambda; eddington_flux/surface_flux are deliberately not mapped — a model atmosphere flux label on a spectrum of a star at Earth would be a false claim (the air/vacuum convention of the wavelength axis is likewise not stated by the source and stays unset).
- No interpolation is offered: the stars are an observed scatter in (Teff, log g, [Fe/H]) and each sits on its own grid, so a weighted mix of two stars' spectra is neither an observation of anything nor defined on a common sampling. Queries are nearest-star (voronoi semantics: warned, recorded).
- The star table is fixed-width ASCII with the layout its ReadMe documents byte by byte; '-' marks a value it does not report. 48 of 255 stars miss at least one of Teff/log g/[Fe/H] (32 miss Teff, 44 miss log g, 44 miss [Fe/H]; 207 are fully parameterized): their files are held and mirrored like every other part, but they carry no query coordinates and are listed by star name in the set's notes — never silently dropped. One row (LHS 235) lists no corrected spectrum at all; it exists in the table and its observed counterpart is mirrored.

### library (`library`)

#### Field mappings

| Unified field | Native source | Origin | Transformation | Notes |
|---|---|---|---|---|
| `wavelength` | `wavelength` | native | — | the star's own linear WCS axis (CRVAL1 + CDELT1 * (pixel - CRPIX1)); per-star grids, never assumed shared |
| `wavelength_um` | `wavelength` | transformed_native | x * 1e-4 | — |
| `observed_flux` | — | missing | — | not mapped: STELIB fluxes are absolutely calibrated (BUNIT erg/s/cm^2/Angstrom), not the unitless relative flux this field names |
| `observed_flux_density` | `flux` | native | — | the star's observed flux density at the observer as published (absolute spectrophotometry, 3% overall uncertainty; cor_sp: extinction- and RV-corrected) |
| `eddington_flux` | — | missing | — | not mapped: an empirical observed spectrum has no model Eddington-flux meaning |
| `surface_flux` | — | missing | — | not mapped: an empirical observed spectrum has no model surface-flux meaning |
| `continuum` | — | missing | — | no continuum column is published |

#### Physical parameters

| Parameter | Role | Value / source | Notes |
|---|---|---|---|
| `teff` | axis (varies across the published grid) | file values `teff` | — |
| `log_surface_gravity` | axis (varies across the published grid) | file values `logg` | — |
| `m_h` | axis (varies across the published grid) | file values `m_h` | — |
| `spectral_resolution` | implicit (used by the model, not recoverable) | — | ~3 A FWHM (the paper's '<~3 A' resolution statement; 1 A sampling); not a selectable product axis |

### Scientific references

- Le Borgne, J.-F., et al. 2003, A&A 402, 433 (the STELIB library) (bibcode `2003A&A...402..433L`)

## TLUSTY OSTAR2002 / BSTAR2006 (`tlusty`)

Field mapping version: `1`.

- One gzip ASCII file per atmosphere and spectrum kind ({comp}{teff:05d}g{logg*100:03d}v{vmicro*10:02d}.vis.{7|17}.gz); kind 7 is the detailed spectrum and kind 17 the continuum — stellarlib consumes kind 7 and never mixes kinds within one grid. A path without the .gz suffix opens path + '.gz' (the published set's on-disk names drop the suffix).
- Wavelengths are AIR in the optical: line centroids of H-alpha, He I 5876, and He I 7065 in the OSTAR solar-composition spectrum sit at the NIST air values to 0.01-0.02 Angstrom (vacuum would be off by ~1.8 Angstrom). stellarlib never converts between conventions.
- The sampling is the SYNSPEC native grid, ~0.03 Angstrom per point in the red optical (R ~ 2e5 at 6500 Angstrom) — far finer than typical instrumental requirements; no instrumental broadening is applied.
- The published flux is the Eddington flux H_lambda; the surface flux is F_lambda = 4 pi H_lambda — the same convention as BOSZ 2024 (both libraries state it), so populations mixed across the two libraries sit on one flux scale with no cross-calibration.
- The vispec archives are per scaled-solar composition (OSTAR2002: 10 values of Z/Z_sun from 0 to 2; BSTAR2006: 5). A grid is built at ONE composition; the composition tag is preserved verbatim and a grid refuses mixed-composition files.
- Coverage: OSTAR2002 spans Teff 27500-55000 K (2500 K steps), log g 3.00-4.75 (0.25 dex steps); BSTAR2006 spans 15000-30000 K (1000 K steps), log g 1.75-4.75 (0.25 dex steps); the published set narrows at the hottest models. Combined with BOSZ 2024's complete rectangle ending at 12000 K, the interval 12000-15000 K has no interpolable published library — grids report their true axes and routing across the gap belongs to the application.

### OSTAR2002 optical spectra (`*.vis.7.gz`) (`ostar2002`)

#### Field mappings

| Unified field | Native source | Origin | Transformation | Notes |
|---|---|---|---|---|
| `wavelength` | `wavelength` | native | — | the file's own column 1 (air wavelengths, SYNSPEC native sampling) |
| `wavelength_um` | `wavelength` | transformed_native | x * 1e-4 | — |
| `eddington_flux` | `flux` | native | — | — |
| `surface_flux` | `flux` | transformed_native | 4 pi * x | — |
| `continuum` | — | missing | — | kind 17 continuum files are a separate published file, not a column of the kind 7 spectrum |

#### Physical parameters

| Parameter | Role | Value / source | Notes |
|---|---|---|---|
| `teff` | axis (varies across the published grid) | file values `teff` | — |
| `log_surface_gravity` | axis (varies across the published grid) | file values `logg` | — |
| `composition` | fixed (known value for the whole product) | file value `composition` | the one-to-two-letter composition tag of the vispec archive (e.g. G = scaled-solar Z/Z_sun = 1) |
| `microturbulence` | fixed (known value for the whole product) | file value `vmicro` | OSTAR2002 standard set: 1 km/s (chosen to match the BOSZ ATLAS9 nodes) |
| `spectral_resolution` | implicit (used by the model, not recoverable) | — | SYNSPEC native ~0.03 Angstrom sampling (R ~ 2e5 in the red optical); not a selectable product axis |

### BSTAR2006 optical spectra (`*.vis.7.gz`) (`bstar2006`)

#### Field mappings

| Unified field | Native source | Origin | Transformation | Notes |
|---|---|---|---|---|
| `wavelength` | `wavelength` | native | — | the file's own column 1 (air wavelengths, SYNSPEC native sampling) |
| `wavelength_um` | `wavelength` | transformed_native | x * 1e-4 | — |
| `eddington_flux` | `flux` | native | — | — |
| `surface_flux` | `flux` | transformed_native | 4 pi * x | — |
| `continuum` | — | missing | — | kind 17 continuum files are a separate published file, not a column of the kind 7 spectrum |

#### Physical parameters

| Parameter | Role | Value / source | Notes |
|---|---|---|---|
| `teff` | axis (varies across the published grid) | file values `teff` | — |
| `log_surface_gravity` | axis (varies across the published grid) | file values `logg` | — |
| `composition` | fixed (known value for the whole product) | file value `composition` | the one-to-two-letter composition tag of the vispec archive (e.g. BG = scaled-solar Z/Z_sun = 1) |
| `microturbulence` | fixed (known value for the whole product) | file value `vmicro` | BSTAR2006 standard set: 2 km/s (chosen to match the BOSZ ATLAS9 nodes) |
| `spectral_resolution` | implicit (used by the model, not recoverable) | — | SYNSPEC native ~0.03 Angstrom sampling (R ~ 2e5 in the red optical); not a selectable product axis |

### Scientific references

- Lanz, T., & Hubeny, I. 2003, ApJS 146, 417 (OSTAR2002) (bibcode `2003ApJS..146..417L`)
- Lanz, T., & Hubeny, I. 2007, ApJS 169, 83 (BSTAR2006) (bibcode `2007ApJS..169...83L`)
