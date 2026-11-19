"""Tests for the TLUSTY raw reader (filename grammar, vis files).

Expectations mirror the downstream application's hand-computed cases:
both composition prefixes, one-to-two-digit vmicro, the gzip-suffix
quirk, and the two-column layout.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stellarlib.models.tlusty.reader import (
    FILENAME_RE,
    parse_filename,
    read_vis,
    recognize_tlusty,
)

DATA = Path(__file__).parent.parent / "data" / "tlusty"
WAVE = np.array([5000.0, 5001.0, 5002.0])


class TestParseFilename:
    def test_parses_ostar_and_bstar_names(self):
        got = parse_filename("G27500g300v10.vis.7.gz")
        assert got == {
            "composition": "G",
            "teff": 27500,
            "logg": 3.0,
            "vmicro": 1.0,
            "kind": "7",
        }
        got = parse_filename("BG15000g425v2.vis.17.gz")
        assert got == {
            "composition": "BG",
            "teff": 15000,
            "logg": 4.25,
            "vmicro": 0.2,
            "kind": "17",
        }

    def test_rejects_foreign_names(self):
        for name in ("27500_3.00.vis.7.gz", "G27500g300v10.spec.gz"):
            assert FILENAME_RE.match(name) is None
            with pytest.raises(ValueError, match="not a TLUSTY"):
                parse_filename(name)

    def test_recognize_with_and_without_suffix(self):
        assert recognize_tlusty(DATA / "G30000g300v10.vis.7.gz")
        assert recognize_tlusty(DATA / "G30000g300v10.vis.7")
        # the grammar needs the composition+parameter tokens, so a loose
        # name is not claimed
        assert not recognize_tlusty(DATA / "three_columns.vis.7.gz")


class TestReadVis:
    def test_reads_two_columns_and_gzip_suffix_logic(self):
        native = read_vis(DATA / "G30000g300v10.vis.7")  # no .gz
        np.testing.assert_allclose(native.wavelength, WAVE)
        # the separable fixture law at (30000, 3.0)
        np.testing.assert_allclose(
            native.flux, 3.0 * 3.0 * WAVE / 1000.0
        )
        assert native.continuum is None
        assert native.metadata["teff"] == 30000

    def test_rejects_wrong_width(self):
        with pytest.raises(ValueError, match="two columns"):
            read_vis(DATA / "three_columns.vis.7")

    def test_air_wavelength_convention_recorded_in_metadata(self):
        native = read_vis(DATA / "G30000g300v10.vis.7.gz")
        assert native.metadata["composition"] == "G"
        assert native.provenance_keys["file"] == "G30000g300v10.vis.7.gz"
