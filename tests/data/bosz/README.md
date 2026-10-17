# BOSZ test fixtures

`make_fixtures.py` builds these from real upstream files — never hand-edit
them; regenerate instead.

## Real excerpts (verbatim line blocks)

- The 8 corner files of a 2×2×2 (Teff, log g, [M/H]) window at r500 —
  Teff 3500/4000 K, log g +1.0/+2.0, [M/H] +0.00/−1.00, all
  `ms` (MARCS spherical), vmicro 2, [a/M] = [C/M] = 0. Downloaded from
  MAST (`.../bosz2024/r500/m<...>/…`, tree layout verified 2026-10-17).
- `bosz2024_ms_t3000_g+1.5_..._resam.txt.gz` — the cool giant from the
  red_gaint_feedback raw set (same upstream bytes, independent download).
- `bosz2024_wave_r500.txt` — the official per-resolution wavelength grid
  (first 300 optical rows of the 7500-point r500 grid).

Each excerpt is one contiguous block of 300 data lines starting at row
2670 (≈4988–6454 Å) so the flux is real optical signal rather than the
upstream-clipped UV zeros; resam and wave rows align by index, as in the
full files.

## Synthetic format-equivalent files

- `three_columns.txt.gz` — wrong column count for the two-column reader.
- `short_wave.txt` — wavelength grid with mismatched row count.

Both follow the format grammar but encode no real spectrum.
