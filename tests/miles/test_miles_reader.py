"""MILES reader tests: the FITS grammar, the WCS wavelength axis, and the
fixed-width parameter table (real upstream bytes; see
tests/data/miles/README.md)."""

from __future__ import annotations

from pathlib import Path

from astropy.io import fits
import numpy as np
import pytest

from stellarlib.models.miles.reader import (
    parse_params_table,
    read_miles_fits,
    read_params_table,
    recognize_miles,
)

DATA = Path(__file__).parent.parent / "data" / "miles"
PARAMS = DATA / "paramsMILES_v9.1_excerpt.txt"


class TestRecognize:
    def test_grammar(self):
        assert recognize_miles("s0001.fits")
        assert recognize_miles(DATA / "s0985.fits")
        assert not recognize_miles("s0001.fits.gz")
        assert not recognize_miles("S0001.FITS")
        assert not recognize_miles("paramsMILES_v9.1.txt")
        assert not recognize_miles("MILES_library_v9.1_FITS.tar.gz")


class TestReadFits:
    def test_real_file_verbatim(self):
        native = read_miles_fits(DATA / "s0001.fits", "s0001.fits")
        assert len(native) == 4367
        assert native.flux.dtype == np.dtype(">f4")
        # negatives reach the reader untouched — no clipping anywhere
        assert float(native.flux.min()) < 0.0
        # the wavelength axis is the header's own linear WCS
        assert native.wavelength[0] == pytest.approx(3500.0)
        assert native.wavelength[-1] == pytest.approx(7429.4)
        assert native.wavelength[1] - native.wavelength[0] == pytest.approx(0.9)
        assert native.continuum is None
        assert native.metadata["object"] == "HD224930"
        assert native.metadata["timexpo"] == -999.0  # REDUCEME sentinel kept
        assert "CRVAL1" in native.provenance_keys["wavelength_from"]
        assert native.provenance_keys["flux_convention"].startswith(
            "relative flux"
        )

    def test_bytes_input_matches_path_input(self):
        by_path = read_miles_fits(DATA / "s0002.fits", "s0002.fits")
        by_bytes = read_miles_fits(
            (DATA / "s0002.fits").read_bytes(), "s0002.fits"
        )
        assert np.array_equal(by_path.flux, by_bytes.flux)
        assert np.array_equal(by_path.wavelength, by_bytes.wavelength)

    def test_multi_row_image_is_a_format_change(self, tmp_path):

        hdu = fits.PrimaryHDU(data=np.zeros((2, 10), dtype=">f4"))
        path = tmp_path / "s0001.fits"
        hdu.writeto(path)
        with pytest.raises(ValueError, match="single-row image HDU"):
            read_miles_fits(path, "s0001.fits")


class TestParamsTable:
    def test_real_excerpt(self):
        rows = read_params_table(PARAMS)
        assert sorted(rows) == [1, 2, 838, 985]
        s1 = rows[1]
        assert s1["name"] == "HD224930"
        assert s1["teff"] == 5305
        assert s1["log_surface_gravity"] == 4.49
        assert s1["m_h"] == -0.75
        assert s1["refs"] == "SKC"
        assert s1["libraries"] == "L     I E"
        # a name with spaces parses through the fixed-width columns
        assert rows[985]["name"] == "NGC 7789 971(946) Cl"
        assert rows[985]["spectral_type"] == "GB"
        # '--' is a not-reported value, kept as None
        assert rows[838]["m_h"] is None
        assert rows[838]["teff"] == 2748
        # the CaT column is optional and may be empty
        assert rows[2]["cat_number"] is None
        assert rows[1]["cat_number"] == "673"

    def test_missing_label_is_a_format_change(self):
        with pytest.raises(ValueError, match="expected columns"):
            parse_params_table("Name  Teff\n      5000\n")

    def test_non_number_miles_column_fails(self):
        text = (
            "Name                  MILES   CaT   SpT            Teff"
            "   log g   [Fe/H]  Refs   Libraries\n"
            "                      s????   \n"
            " ---------------------------------------\n"
            "HD224930               ???           G3 V    5305   4.49"
            "    -0.75   SKC   L\n"
        )
        with pytest.raises(ValueError, match="MILES column holds"):
            parse_params_table(text)

    def test_duplicate_s_number_fails(self):
        row = (
            "HD224930               0001         G3 V            5305"
            "   4.49    -0.75   SKC   L\n"
        )
        text = (
            "Name                  MILES   CaT   SpT            Teff"
            "   log g   [Fe/H]  Refs   Libraries\n"
            "                      s????   \n"
            " - - - - - - - - - -\n" + row + row
        )
        with pytest.raises(ValueError, match="states s0001 twice"):
            parse_params_table(text)
