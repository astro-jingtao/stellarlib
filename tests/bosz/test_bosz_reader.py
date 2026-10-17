"""Tests for the BOSZ raw reader (filename grammar, resam, wave grids).

Expectations mirror the downstream applications' hand-computed cases:
the 8-token filename grammar, the two-column resam layout with no
wavelength column, and the one-column wave grid.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stellarlib.models.bosz.reader import (
    FILENAME_RE,
    WAVE_FILENAME_RE,
    parse_filename,
    read_resam,
    read_wave_grid,
    recognize_bosz,
    recognize_wave_grid,
)

DATA = Path(__file__).parent.parent / "data" / "bosz"


class TestParseFilename:
    def test_parses_all_fields(self):
        params = parse_filename(
            "bosz2024_mp_t5000_g+4.5_m+0.00_a+0.00_c+0.00_v1_r10000"
            "_resam.txt.gz"
        )
        assert params == {
            "family": "mp",
            "teff": 5000,
            "logg": 4.5,
            "mh": 0.0,
            "am": 0.0,
            "cm": 0.0,
            "vmicro": 1,
            "resolution": 10000,
        }

    def test_parses_negative_values(self):
        params = parse_filename(
            "bosz2024_ms_t3500_g-0.5_m-0.25_a+0.25_c-0.75_v2_r500"
            "_resam.txt.gz"
        )
        assert params["logg"] == -0.5
        assert params["mh"] == -0.25
        assert params["cm"] == -0.75
        assert params["family"] == "ms"

    def test_rejects_foreign_names(self):
        for bad in (
            "bosz2017_mp_t5000.txt.gz",
            "something.eep",
            "bosz2024_mp_t5000_g+4.5_m+0.00_resam.txt.gz",
        ):
            assert FILENAME_RE.match(bad) is None
            with pytest.raises(ValueError):
                parse_filename(bad)

    def test_recognize_by_name(self):
        assert recognize_bosz(DATA / "bosz2024_ms_t3500_g+1.0_m+0.00_a+0.00_c+0.00_v2_r500_resam.txt.gz")
        assert not recognize_bosz(DATA / "bosz2024_wave_r500.txt")
        assert recognize_wave_grid(DATA / "bosz2024_wave_r500.txt")
        assert WAVE_FILENAME_RE.match("bosz2024_wave_r10000.txt")


class TestReadResam:
    def test_reads_two_columns_no_wavelength(self):
        native = read_resam(
            DATA / "bosz2024_ms_t3500_g+1.0_m+0.00_a+0.00_c+0.00_v2_r500_resam.txt.gz"
        )
        assert native.wavelength is None
        assert native.flux.shape == (300,)
        assert native.continuum.shape == (300,)
        assert native.metadata["teff"] == 3500
        assert native.metadata["family"] == "ms"
        assert native.provenance_keys["file"].startswith("bosz2024_ms_")

    def test_rejects_wrong_width(self):
        with pytest.raises(ValueError, match="two columns"):
            read_resam(DATA / "three_columns.txt.gz")

    def test_values_are_verbatim_optical_signal(self):
        native = read_resam(
            DATA / "bosz2024_ms_t3500_g+1.0_m+0.00_a+0.00_c+0.00_v2_r500_resam.txt.gz"
        )
        # real optical values from the upstream file (no clipping applied
        # by us; this window happens to carry no clipped zeros)
        assert native.flux.min() > 0.0
        assert native.flux.max() > 4000.0

    def test_synthetic_two_column_file_reads(self, tmp_path):
        path = tmp_path / "spec.txt.gz"
        np.savetxt(path, np.array([[1e5, 2e5], [3e5, 4e5]]))
        native = read_resam(path)
        np.testing.assert_allclose(native.flux, [1e5, 3e5])
        np.testing.assert_allclose(native.continuum, [2e5, 4e5])
        assert native.metadata == {}


class TestReadWaveGrid:
    def test_reads_one_column(self):
        wave = read_wave_grid(DATA / "bosz2024_wave_r500.txt")
        assert wave.shape == (300,)
        assert np.all(np.diff(wave) > 0)
        assert wave[0] == pytest.approx(4988.5011, abs=1e-3)

    def test_rejects_multi_column(self, tmp_path):
        bad = tmp_path / "bad_wave.txt"
        np.savetxt(bad, np.array([[1.0, 2.0], [3.0, 4.0]]))
        with pytest.raises(ValueError, match="one column"):
            read_wave_grid(bad)
