"""STELIB reader tests: file recognition, the corrected-spectrum FITS
format, and the star table's fixed-width layout (real upstream bytes;
see tests/data/stelib/README.md)."""

from __future__ import annotations

from pathlib import Path

from astropy.io import fits, fits as astropy_fits
import pytest

from stellarlib.models.stelib.reader import (
    is_corrected_spectrum,
    parse_star_table,
    read_star_table,
    read_stelib_fits,
    recognize_stelib_fits,
    star_table_among,
)

DATA = Path(__file__).parent.parent / "data" / "stelib"
TABLE = DATA / "table.dat"
COR = DATA / "cor_sp" / "HD002857_V3.2.fits"
OBS = DATA / "obs_sp" / "HD53929_moy_sso.fits"


class TestRecognize:
    def test_corrected_names(self):
        assert recognize_stelib_fits(COR)
        assert is_corrected_spectrum(COR)
        assert is_corrected_spectrum("HD053929ss_V3.2.fits")

    def test_observed_names(self):
        assert recognize_stelib_fits("HD002857_moy.fits")
        assert recognize_stelib_fits(OBS)
        assert recognize_stelib_fits("LTT4364_moy_save.fits")
        assert not is_corrected_spectrum(OBS)

    def test_foreign_names_rejected(self):
        for name in ("s0001.fits", "table.dat", "ReadMe", "m11.fits"):
            assert not recognize_stelib_fits(name)


class TestReadFits:
    def test_verbatim_flux_and_own_wcs_axis(self):
        native = read_stelib_fits(COR, COR.name)
        assert len(native) == 6700
        assert native.metadata["bunit"] == "erg /s /cm2 /angstroms"
        assert native.metadata["corrected"] is True
        with fits.open(COR) as hdul:
            header = hdul[0].header
            flux = hdul[0].data
        assert (native.flux == flux).all()  # verbatim, negatives included
        expected_first = header["CRVAL1"] + header["CDELT1"] * (1 - header["CRPIX1"])
        assert native.wavelength[0] == pytest.approx(expected_first)
        expected_last = header["CRVAL1"] + header["CDELT1"] * (6700 - header["CRPIX1"])
        assert native.wavelength[-1] == pytest.approx(expected_last)

    def test_per_star_grids_differ(self):
        other = read_stelib_fits(DATA / "cor_sp" / "HD006268_V3.2.fits", "x")
        one = read_stelib_fits(COR, COR.name)
        assert other.wavelength[0] != pytest.approx(one.wavelength[0])

    def test_2d_image_refused(self, tmp_path):

        hdu = astropy_fits.PrimaryHDU(data=[[1.0, 2.0]])
        hdu.header["BUNIT"] = "erg /s /cm2 /angstroms"
        target = tmp_path / "fake_V3.2.fits"
        hdu.writeto(target)
        with pytest.raises(ValueError, match="NAXIS=1"):
            read_stelib_fits(target, target.name)

    def test_observed_file_marked(self):
        native = read_stelib_fits(OBS, OBS.name)
        assert native.metadata["corrected"] is False


class TestStarTable:
    def test_fixture_rows_parse(self):
        rows = read_star_table(TABLE)
        assert len(rows) == 6
        by_star = {row["star"]: row for row in rows}
        hd2857 = by_star["HD 002857"]
        assert hd2857["teff"] == 7611
        assert hd2857["log_surface_gravity"] == 2.67
        assert hd2857["m_h"] == -1.51
        assert hd2857["spectral_type"] == "A2"
        assert hd2857["cor_files"] == ["HD002857_V3.2.fits"]
        assert hd2857["obs_files"] == ["HD002857_moy.fits"]
        assert hd2857["table_file"] == TABLE.name

    def test_missing_values_are_none(self):
        rows = read_star_table(TABLE)
        by_star = {row["star"]: row for row in rows}
        hd5820 = by_star["HD 005820"]
        assert hd5820["teff"] is None
        assert hd5820["log_surface_gravity"] is None
        assert hd5820["m_h"] is None
        assert hd5820["cor_files"] == ["HD005820_V3.2.fits"]
        hd15318 = by_star["HD 015318"]
        assert hd15318["teff"] == 11300
        assert hd15318["log_surface_gravity"] is None

    def test_second_file_columns(self):
        rows = read_star_table(TABLE)
        by_star = {row["star"]: row for row in rows}
        hd53929 = by_star["HD 053929"]
        assert hd53929["cor_files"] == [
            "HD053929lp_V3.2.fits",
            "HD053929ss_V3.2.fits",
        ]
        assert hd53929["obs_files"] == [
            "HD53929_moy_sso.fits",
            "HD53929_moy_jkt.fits",
        ]
        lhs235 = by_star["LHS   235"]
        assert lhs235["cor_files"] == []
        assert lhs235["obs_files"] == []

    def test_short_line_refused(self):
        with pytest.raises(ValueError, match="shorter than the documented"):
            parse_star_table("HD 002857  00 31\n")

    def test_bad_parameter_field_refused(self):
        raw = TABLE.read_text(encoding="utf-8").splitlines()[0]
        corrupt = raw[:47] + "xx   " + raw[52:]  # Teff column made unparsable
        with pytest.raises(ValueError, match="layout changed"):
            parse_star_table(corrupt)

    def test_star_table_among(self):
        assert star_table_among([COR, TABLE]) == TABLE
        assert star_table_among([COR]) is None
