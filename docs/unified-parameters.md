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
