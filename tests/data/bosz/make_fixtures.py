"""Create the BOSZ test fixtures from real upstream files.

Real-excerpt fixtures: the 8 corner files of a 2x2x2 (Teff, log g, [M/H])
window at r500 (downloaded from MAST, tree verified 2026-10-17), the cool
t3000 file, and the r500 wavelength grid — each trimmed to one contiguous
block of ``ROWS`` data lines (byte-for-byte), taken at ``OFFSET`` so the
excerpt covers the optical (~5000-6900 Angstrom, where the flux is not the
upstream-clipped UV zeros), rows aligned by index across resam and wave.

Synthetic format-equivalent fixtures cover the error paths (wrong column
count, mismatched wave rows).

Usage: python make_fixtures.py [staging_dir]
``staging_dir`` defaults to the MAST download staging directory used
during development; the rgf raw set is accepted for the t3000 file.
"""

from __future__ import annotations

import gzip
from pathlib import Path
import sys

HERE = Path(__file__).parent
ROWS = 300
# r500 grid: 7500 log-uniform points 500 A .. 31.97 um; row 2670 sits
# near 5000 A, row 2970 near 6900 A
OFFSET = 2670

WINDOW = [  # (teff, logg, mh) corners of the 2x2x2 fixture grid
    (3500, "+1.0", "+0.00"),
    (3500, "+1.0", "-1.00"),
    (3500, "+2.0", "+0.00"),
    (3500, "+2.0", "-1.00"),
    (4000, "+1.0", "+0.00"),
    (4000, "+1.0", "-1.00"),
    (4000, "+2.0", "+0.00"),
    (4000, "+2.0", "-1.00"),
]


def _name(teff: int, logg: str, mh: str) -> str:
    return (
        f"bosz2024_ms_t{teff}_g{logg}_m{mh}_a+0.00_c+0.00_v2_r500"
        "_resam.txt.gz"
    )


def _trim_gz_lines(source: Path, target: Path, offset: int, rows: int) -> None:
    with gzip.open(source, "rt") as fh:
        lines = []
        for i, line in enumerate(fh):
            if i >= offset + rows:
                break
            if i >= offset:
                lines.append(line)
    with gzip.open(target, "wt", newline="") as fh:
        fh.writelines(lines)


def _trim_text_lines(source: Path, target: Path, offset: int, rows: int) -> None:
    with open(source, encoding="utf-8") as fh:
        lines = []
        for i, line in enumerate(fh):
            if i >= offset + rows:
                break
            if i >= offset:
                lines.append(line)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(lines)


def main(argv: list[str]) -> int:
    staging = Path(argv[1]) if len(argv) > 1 else Path(
        r"C:\Users\63479\AppData\Local\Temp\stellarlib\bosz-fetch"
    )
    rgf_raw = Path(
        r"D:\Documents\PythonCode\mylib\red_gaint_feedback\data\raw\bosz"
    )
    if not staging.is_dir():
        print(f"staging dir {staging} not found; pass it as argv[1]", file=sys.stderr)
        return 1

    for teff, logg, mh in WINDOW:
        src = staging / _name(teff, logg, mh)
        _trim_gz_lines(src, HERE / src.name, OFFSET, ROWS)

    # cool star from the rgf raw set (its optical rows still carry the
    # upstream-clipped zeros between lines)
    _trim_gz_lines(
        rgf_raw / _name(3000, "+1.5", "+0.00"),
        HERE / _name(3000, "+1.5", "+0.00"),
        OFFSET,
        ROWS,
    )
    _trim_text_lines(
        rgf_raw / "bosz2024_wave_r500.txt",
        HERE / "bosz2024_wave_r500.txt",
        OFFSET,
        ROWS,
    )

    # synthetic error-path fixtures
    with gzip.open(HERE / "three_columns.txt.gz", "wt", newline="") as fh:
        fh.write("1.0 2.0 3.0\n4.0 5.0 6.0\n")
    (HERE / "short_wave.txt").write_text("1.0\n2.0\n3.0\n", encoding="utf-8")
    print(f"wrote fixtures under {HERE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
