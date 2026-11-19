"""BaSeL reader tests: filename grammar and the cor-format parser."""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from stellarlib.exceptions import DataIntegrityError
from stellarlib.models.basel.reader import (
    BLOCK_LINES,
    HEADER_LINES,
    N_WAVE,
    parse_cor,
    parse_cor_index,
    parse_filename,
    recognize_basel,
)

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "basel"


class TestFilenameGrammar:
    def test_parse_roundtrip(self):
        assert parse_filename("lbc96_m50.cor") == {
            "prefix": "lbc96",
            "release": "2.2",
            "m_h": -5.0,
        }
        assert parse_filename("wlbc99_p05.cor") == {
            "prefix": "wlbc99",
            "release": "3.1",
            "m_h": 0.5,
        }
        assert parse_filename("wlbc99_m15.cor")["m_h"] == -1.5

    def test_rejects_other_names(self):
        with pytest.raises(ValueError, match="not a BaSeL cor filename"):
            parse_filename("G27500g300v10.vis.7.gz")
        assert not recognize_basel("whatever.txt")
        assert recognize_basel(pathlib.Path("/a/b/wlbc99_p00.cor"))
        assert not recognize_basel(3.0)


def parseable(lines: list) -> str:
    return "\n".join(lines) + "\n"


class TestCorParsing:
    def test_index_matches_full_parse(self):
        text = (DATA / "wlbc99_p00.cor").read_text(encoding="utf-8")
        wave, blocks = parse_cor_index(text, "wlbc99_p00.cor")
        assert len(wave) == N_WAVE
        assert wave[0] == 9.1
        assert wave[-1] == 160000.0
        assert len(blocks) == 4
        assert [b.teff for b in blocks] == [4000.0, 4000.0, 4500.0, 4500.0]
        assert [b.logg for b in blocks] == [0.5, 1.0, 0.5, 1.0]
        # the parameter line's quirk columns are kept verbatim
        assert blocks[0].vturb_column == 2.0
        assert blocks[0].h_fraction_column == 1.25

        full = parse_cor(text, "wlbc99_p00.cor")
        assert full.blocks == blocks
        assert all(f.shape == (N_WAVE,) for f in full.fluxes)
        assert np.array_equal(full.wavelength, wave)

    def test_block_flux_verbatim(self):
        text = (DATA / "wlbc99_p00.cor").read_text(encoding="utf-8")
        full = parse_cor(text, "wlbc99_p00.cor")
        lines = text.splitlines()
        first_value = float(lines[HEADER_LINES + 1][:11])
        assert full.fluxes[0][0] == first_value
        last_line = lines[HEADER_LINES + BLOCK_LINES - 1]
        assert full.fluxes[0][-1] == float(last_line[-11:])

    def test_structure_violations_raise(self, tmp_path):
        good = (DATA / "wlbc99_p00.cor").read_text(encoding="utf-8")
        lines = good.splitlines()
        truncated = parseable(lines[:-10])
        with pytest.raises(DataIntegrityError, match="do not fit"):
            parse_cor_index(truncated, "x.cor")
        broken_param = parseable([
            *lines[:HEADER_LINES], "garbage", *lines[HEADER_LINES + 1 :],
        ])
        with pytest.raises(DataIntegrityError, match="does not follow"):
            parse_cor_index(broken_param, "x.cor")
        short_flux = parseable([
            *lines[: HEADER_LINES + 1],
            lines[HEADER_LINES + 1][:-11],
            *lines[HEADER_LINES + 2 :],
        ])
        with pytest.raises(DataIntegrityError, match="flux values"):
            parse_cor(short_flux, "x.cor")
        padded_header = parseable(
            [f"{9.1:10.1f}" * 8] * HEADER_LINES + lines[HEADER_LINES:]
        )
        with pytest.raises(DataIntegrityError, match="wavelength header"):
            parse_cor_index(padded_header, "x.cor")
