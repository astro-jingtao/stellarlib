README file for BOSZ: The "BOSZ" Synthetic Stellar Spectral Library
MAST webpage: https://archive.stsci.edu/hlsp/bosz/
Refer to this HLSP with DOI: https://dx.doi.org/10.17909/T95G68

# INTRODUCTION
"BOSZ" synthetic stellar spectra are based on ATLAS-APOGEE ATLAS9 (Mészáros et al. 2012) and MARCS (Gustafsson et al. 2008) model atmospheres, and cover a broad range of effective temperature, surface gravity, metallicity, carbon abundance, and alpha-element abundance. The name "BOSZ" comes from a combination of letters in the first and second authors' names. Versions at different instrumental broadenings are provided. See the tables in this section that describe the parameter ranges. Two versions are available, the original 2017 version and a newer 2024 version. The 2024 version supersedes the legacy 2017 version.

## Update: 2025 Sept. 24: The team have successfully recomputed all models by fixing both issues reported on  2024 October 25. A higher-level Hydrogen atom model is used now, thus all Hydrogen lines up 32 microns are now present in the spectra. In the models published in 2024, "synspec" was using the partition function of OH instead of OH+ causing stronger than expected OH+ lines. This issue has been fixed in the code providing the correct OH+ line strength. Users are strongly advised to download the new models and replace the old ones!

## Known Issues: 2024 October 25 - the team discovered some issues with the database after publication. The team is currently working on recalculating the entire grid and will update the database after the availability of these new spectra. The following issues are present:

  1. OH+ was incorrectly included in the calculation resulting in stronger than expected molecular absorption lines between 300 and 450 nm, and 1
and 8 microns for models with temperatures below 6500K. Higher temperature models are unaffected.

  2. The published spectra were generated using an eight-level H atom, which results in a lack of higher-level H lines above 5.8 microns. It is
important to note that the 50 nm - 5.8 micron region is not affected by this issue.

##2024 Version

The new BOSZ synthetic spectral grid has been calculated with Synspec using LTE approximation covering metallicities [M/H] from -2.5 to 0.75 dex, alpha-element abundances [α/M] from -0.25 to 0.5 dex, and carbon abundances [C/M] from -0.75 to 0.5 dex, providing spectra for 336 unique compositions. Calculations for stars between 2800 and 8000 K use MARCS model atmospheres; and ATLAS9 is used between 7500 and 16,000K, continuous opacities, and molecular line lists have also been updated. See Figure 1 in Mészáros et al. 2024 for the grid coverage compared to stars observed by the APOGEE survey.

The new BOSZ grid includes 628,620 synthetic spectra from 50 nm to 32 microns with models for 495 Teff, log(g) parameter pairs per composition and per microturbulent velocity. Each spectrum has eight different resolutions spanning a range from R = 500 to 50,000 and also at the original resolution of the synthesis. The original resolutions vary between 200,000 and 600,000 depending on the main atmospheric parameters. Parameter ranges are summarized in the table below.

The filenames of these original resolution spectra contains the "rorig" string and have three columns: the wavelength in Angstroms, the Eddington first moment (H, surface brightness) of the spectra, and the continuum. The flux (F) at the stellar surface is calculated as:


F = 4πH , where H is in erg s-1 cm-2 Å^-1.

To save disk space, each of the seven sets of lower resolutions has its own equally sampled logarithmic wavelength space that is not stored within the files, only the fluxes and continuum values are stored at these lower instrumental broadenings. These can be downloaded from the table in the Data Access section on this webpage, or calculated by adding Equations 3 and 4 from Mészáros et al. 2024.

Included in the original resolution ("rorig") collection are line identification files for each microturbulence v=0.0 synthetic spectrum. These files have three columns: wavelength in Angstroms, a string of line identifications, and the equivalent width (W) in Angstroms. Weak lines with W < 0.1 Angstroms are not listed.

NOTE: The published spectra were generated using an eight-level H atom, which results in a lack of higher-level H lines above 5.8 microns. The team is currently working on recalculating the 5.8 and 32 micron region, and will update the database after the availability of these new spectra. It is important to note that the 50 nm - 5.8 micron region is not affected by this issue.

##2017 Version (Legacy, Superseded by 2024 Models)

The models are fit to O,B,A,F,G stars observed with HST/STIS, and those results were compared to fits for the LTE grid from Castelli & Kurucz (2004). The primary reference that describes the model synthesis for this version of the BOSZ models is Bohlin et al. 2017. Two version of the files are available, an ASCII text table zipped using bzip2, and a FITS version with the data stored in a binary FITS table.

The models include wavelengths from 1000 Å < λ < 32 μm. The ASCII files contain the wavelengths (measured in vacuum) in Å, surface brightness (H_BOSZ), and theoretical continuum level. The FITS files have these data in the first extension, in columns called "WAVELENGTH", "SPECIFICINTENSITY", and "CONTINUUM".  Note that to convert from surface brightness to flux, use the equation:

F = π * H_BOSZ

where H_BOSZ is the surface brightness in the file. Normally the equation includes a factor of 4*π, but the BOSZ models are already four times larger, so it is only necessary to multiply the surface brightness values by π to convert to flux in erg cm-2 s-1 Å-1.

# DATA PRODUCTS

##2024 Version

The files are organized into subdirectories based on parameter grouping as follows:

<insbroad>/<metallicity>/
where:
  <insbroad> is the instrumental broadening, one of {r500, r1000, r2000, r5000, r10000, r20000, r50000, rorig}
  <metallicity> is the metallicity used, one of {m+0.00, m+0.25, m+0.50, m+0.75, m-0.25, m-0.50, m-0,75, m-1.00, m-1.25, m-1.50, m-1.75, m-2.00, m-2.25, m-2.50}

For example, ASCII models with an instrumental broadening R of 500 and solar metalicity can be found in:

/r500/m+0.00/

Filesnames are consistent, so that the parameters of any spectra can be easily recovered. The following naming scheme applies:

bosz2024_<atmos>_<teff>_<logg>_<metal>_<alpha>_<carbon>_<micro>_<insbroad>_<prod>.txt.gz
where:
  <atmos> = model atmosphere: "mp" = MARCS plane-parallel model, "ms" = MARCS spherical model, "ap" = ATLAS9 plane-parallel model
  <teff> = "t####[#]" indicating the effective temperature, e.g., "t5000" for 5000 K or "t16000" for 16000 K; ranges from 2800-16000 K
  <logg> = "g{+,-}#.#" indicating the surface gravity, e.g., "g+4.5" for log(g) = 4.5; ranges from -0.5 to +5.5
  <metal> = "m{+,-}#.##" indicating the metallicity, e.g, "m-2.50" for metallicity = -2.50; ranges from -2.50 to +0.75
  <alpha> = "a{+,-}#.##" indicating the alpha element enrichment, e.g, "a+0.25" for α/M = +0.25; ranges from -0.25 to +0.50
  <carbon> = "c{+,-}#.##" indicating the carbon abundance, e.g, "c-0.75" for [C/M] = -0.75; ranges from -0.75 to +0.50
  <micro> = "v#" indicating microturbulence velocity, either 0, 1, 2, or 4 km/s
  <insbroad> = "r###[#][#]" indicating the instrumental broadening, e.g., "r2000" for R = 2000; either 500, 1000, 2000, 5000, 10000, 20000, 50000, or it is set to "rorig" indicating the original resolution; the original resolution depends on the other main atmospheric parameters, but ranges from ~200000 to ~600000
  <prod> = type of product, either "resam" indicating the spectra are resampled to a given instrumental broadening, "noresam" for the original resolution spectra indicating no resampling of the wavelength spacing was done, or "lineid" for files containing the line identifications
An example file name of a spectrum with Teff =  5000 K, logg = 5, [M/H] = 0, vmicro = 0, R = 500,  and solar scaled [A/M], [C/M] abundances is:

bosz2024_mp_t5000_g+5.0_m+0.00_a+0.00_c+0.00_v0_r500_resam

Data file types:
_wave_<res>.txt = BOSZ 2024 wavelength grids.
_resam.txt.gz = BOSZ 2024 synthetic stellar spectrum
_lineid.txt.gz = BOSZ 2024 line IDs (only for "rorig" resolution)


##2017 Version

For historical information on the file organization and naming convention of the  legacy 2017 version, expand the section below.

The files are organized into subdirectories based on parameter grouping as follows:

<u>/insbroad_<w>/metal_<x>/carbon_<y>/alpha_<z>/

where:

  <u> is either "ascii" or "fits", depending on the data format
  <w> is a 6-digit, zero-padded instrumental broadening
  <x> is the metallicity value, including '+' or '-'
  <y> is the carbon abundance value, including '+' or '-'
  <z> is the alpha-element value, including '+' or '-'

For example, ASCII models with an instrumental broadening R of 200, solar metalicity, solar carbon abundance, and solar alpha-element abundance can be found in:

ascii/insbroad_000200/metal_+0.00/carbon_+0.00/alpha_+0.00/

The format for each part of the file name is described in detail in the Appendix of Bohlin et al. 2017, since, for example, decimals are avoided and some values are rounded in the file names. One minor change is the prepending of "bosz2017" to the front, to ensure they are differentiated from newer model versions, An example file name is:

bosz2017_amm13cp00op00t3500g45v20modrt0b50000rs.asc.bz2

where:

  mm13 represents [M/H] = -1.25
  cp00 represents [C/H] = 0.00
  op00 represents [α/H] = 0.00
  t3500 represents Teff = 3500 K
  g45 represents log(g) = 4.5 K
  v20 represents Microturbulent velocity = 2.0 km/s
  rt0 represents Rotational broadening = 0.0 km/s
  b50000 represents Instrumental broadening = 50,000

Data file types:
_rs.asc.bz2 = BOSZ 2017 ASCII version.
_rs.fits = BOSZ 2017 FITS version.
