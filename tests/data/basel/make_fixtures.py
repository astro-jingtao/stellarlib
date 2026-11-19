"""Build the BaSeL test fixtures from the acquisition tarballs.

Trimmed excerpts of the real upstream files (the repo convention; never
hand-edit — regenerate with this script). Each fixture keeps the
complete 153-line wavelength header verbatim plus whole 176-line blocks
at chosen nodes:

- ``wlbc99_p00.cor`` / ``wlbc99_m20.cor`` (BaSeL 3.1, WLBC99 variant):
  the 2x2 (Teff, log g) rectangle (4000, 4500) x (0.5, 1.0) at both
  metallicities — a hole-free bracket cell for multilinear tests.
- ``lbc96_p00.cor`` / ``lbc96_p01.cor`` (BaSeL 2.2): the same rectangle
  plus the (6750, log g) row whose p01-file blocks declare [Fe/H] = 0.2
  against the filename's +0.1 — the real published mismatch quirk.
- ``basel3_1_fixture.tar.gz``: the two 3.1 files under both variant
  directories (WLBC99/, PADOVA2000/) — a synthetic wrapper around the
  verbatim trimmed members that reproduces the published tarball's
  layout for the variant-selection and tar-streaming tests.
- ``basel2_2_fixture.tar.gz``: the two 2.2 files under BaSeL2.2/, the
  published tarball's layout.

Run from the repository root with the acquisition tree present:

    python tests/data/basel/make_fixtures.py \
        /work/jingtao/database/stellarlib/acquisition/basel
"""

from __future__ import annotations

import io
import pathlib
import re
import sys
import tarfile

HERE = pathlib.Path(__file__).parent

# fixture node plan: file -> ((teff, logg) blocks to keep, ...)
BLOCK_LINES = 176
HEADER_LINES = 153

SELECTION = {
    ("BaSeL3_1.tar.gz", "BaSeL3.1/WLBC99/wlbc99_p00.cor", "wlbc99_p00.cor"): [
        (4000.0, 0.5), (4000.0, 1.0), (4500.0, 0.5), (4500.0, 1.0),
    ],
    ("BaSeL3_1.tar.gz", "BaSeL3.1/WLBC99/wlbc99_m20.cor", "wlbc99_m20.cor"): [
        (4000.0, 0.5), (4000.0, 1.0), (4500.0, 0.5), (4500.0, 1.0),
    ],
    ("BaSeL2_2.tar.gz", "BaSeL2.2/lbc96_p00.cor", "lbc96_p00.cor"): [
        (4000.0, 0.5), (4000.0, 1.0), (4500.0, 0.5), (4500.0, 1.0),
        (6750.0, 1.0),
    ],
    ("BaSeL2_2.tar.gz", "BaSeL2.2/lbc96_p01.cor", "lbc96_p01.cor"): [
        (6750.0, 0.5), (6750.0, 1.0),
    ],
}


def _blocks_by_coordinate(lines: list[str]) -> dict[tuple[float, float], list[str]]:
    param_re = re.compile(r"\s*(\d+)\s+([\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)")
    out = {}
    for k, ln in enumerate(lines[HEADER_LINES:]):
        if k % BLOCK_LINES == 0:
            m = param_re.match(ln)
            out[(float(m.group(2)), float(m.group(3)))] = lines[
                HEADER_LINES + k : HEADER_LINES + k + BLOCK_LINES
            ]
    return out


def main(acquisition: pathlib.Path) -> None:
    trimmed: dict[str, str] = {}
    for (tar_name, member, out_name), nodes in SELECTION.items():
        with tarfile.open(acquisition / tar_name, "r") as tf:
            lines = tf.extractfile(member).read().decode().splitlines()
        header, blocks = lines[:HEADER_LINES], _blocks_by_coordinate(lines)
        kept = [header]
        kept.extend(blocks[node] for node in nodes)
        text = "\n".join("\n".join(part) for part in kept) + "\n"
        trimmed[out_name] = text
        with open(HERE / out_name, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"{out_name}: {len(nodes)} blocks")

    with tarfile.open(HERE / "basel3_1_fixture.tar.gz", "w:gz") as tf:
        for variant in ("WLBC99", "PADOVA2000"):
            for name in ("wlbc99_p00.cor", "wlbc99_m20.cor"):
                data = trimmed[name].encode()
                info = tarfile.TarInfo(f"BaSeL3.1/{variant}/{name}")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    print("basel3_1_fixture.tar.gz written")

    with tarfile.open(HERE / "basel2_2_fixture.tar.gz", "w:gz") as tf:
        for name in ("lbc96_p00.cor", "lbc96_p01.cor"):
            data = trimmed[name].encode()
            info = tarfile.TarInfo(f"BaSeL2.2/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    print("basel2_2_fixture.tar.gz written")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "."))
