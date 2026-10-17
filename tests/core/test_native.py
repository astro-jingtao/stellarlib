"""Tests for the native spectrum object."""

from __future__ import annotations

import numpy as np
import pytest
from astropy import units as u
from astropy.table import Table

from stellarlib.native import NativeSpectrum


def _simple(**overrides) -> NativeSpectrum:
    kwargs = dict(
        wavelength=np.linspace(3000.0, 4000.0, 11),
        flux=np.full(11, 1.5),
        continuum=np.full(11, 1.6),
        header=("# a comment",),
        metadata={"teff": 5000.0},
    )
    kwargs.update(overrides)
    return NativeSpectrum(**kwargs)


class TestConstruction:
    def test_basic_construction(self):
        spec = _simple()
        assert len(spec) == 11
        assert spec.header == ("# a comment",)
        assert spec.metadata == {"teff": 5000.0}

    def test_wavelength_optional(self):
        spec = _simple(wavelength=None)
        assert spec.wavelength is None
        assert len(spec) == 11

    def test_continuum_optional(self):
        spec = _simple(continuum=None)
        assert spec.continuum is None

    def test_length_mismatch_wavelength_raises(self):
        with pytest.raises(ValueError, match="wavelength and flux"):
            _simple(wavelength=np.linspace(3000.0, 4000.0, 10))

    def test_length_mismatch_continuum_raises(self):
        with pytest.raises(ValueError, match="continuum and flux"):
            _simple(continuum=np.full(10, 1.6))

    def test_two_dimensional_flux_raises(self):
        with pytest.raises(ValueError, match="1-D"):
            _simple(flux=np.zeros((3, 4)))


class TestEquality:
    def test_equal_objects(self):
        assert _simple() == _simple()

    def test_flux_difference_detected(self):
        other = _simple(flux=np.full(11, 1.5000001))
        assert _simple() != other

    def test_wavelength_presence_difference_detected(self):
        assert _simple() != _simple(wavelength=None)


class TestToTable:
    def test_units_attached(self):
        table = _simple().to_table()
        assert isinstance(table, Table)
        assert table.colnames == ["wavelength", "flux", "continuum"]
        expected = u.erg / u.s / u.cm**2 / u.angstrom
        assert table["flux"].unit == expected
        assert table["wavelength"].unit == u.angstrom

    def test_columns_follow_native_layout(self):
        table = _simple(wavelength=None).to_table()
        assert table.colnames == ["flux", "continuum"]

    def test_values_unchanged(self):
        spec = _simple()
        table = spec.to_table()
        assert np.array_equal(table["flux"].value, spec.flux)
