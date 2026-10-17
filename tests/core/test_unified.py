"""Tests for the unified view over native spectra."""

from __future__ import annotations

import numpy as np
import pytest

from stellarlib.core import FieldOrigin, FieldSpec
from stellarlib.exceptions import WavelengthGridError
from stellarlib.native import NativeSpectrum
from stellarlib.unified import TRANSFORMATIONS, UnifiedView


def _native(**overrides) -> NativeSpectrum:
    kwargs = dict(
        wavelength=np.linspace(1000.0, 2000.0, 6),
        flux=np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        continuum=np.array([1.1, 2.1, 3.1, 4.1, 5.1, 6.1]),
    )
    kwargs.update(overrides)
    return NativeSpectrum(**kwargs)


def _specs(**overrides) -> dict:
    base = {
        "wavelength": FieldSpec(
            name="wavelength", native_name="wavelength", unit="Angstrom"
        ),
        "wavelength_um": FieldSpec(
            name="wavelength_um",
            native_name="wavelength",
            unit="um",
            origin=FieldOrigin.TRANSFORMED_NATIVE,
            transformation="x * 1e-4",
        ),
        "eddington_flux": FieldSpec(
            name="eddington_flux",
            native_name="flux",
            unit="erg/s/cm^2/Angstrom",
        ),
        "surface_flux": FieldSpec(
            name="surface_flux",
            native_name="flux",
            unit="erg/s/cm^2/Angstrom",
            origin=FieldOrigin.TRANSFORMED_NATIVE,
            transformation="4 pi * x",
        ),
        "continuum": FieldSpec(
            name="continuum", native_name="continuum", unit="erg/s/cm^2/Angstrom"
        ),
    }
    base.update(overrides)
    return base


class TestTransformations:
    def test_fixed_vocabulary_values(self):
        np.testing.assert_allclose(
            TRANSFORMATIONS["4 pi * x"](np.array([1.0, 2.0])),
            4.0 * np.pi * np.array([1.0, 2.0]),
        )
        np.testing.assert_allclose(
            TRANSFORMATIONS["x * 1e-4"](np.array([1000.0])),
            [0.1],
        )
        np.testing.assert_allclose(
            TRANSFORMATIONS["10 ** x"](np.array([2.0])),
            [100.0],
        )


class TestUnifiedView:
    def test_native_fields_returned_verbatim(self):
        view = UnifiedView(_native(), _specs())
        np.testing.assert_array_equal(view["eddington_flux"], [1, 2, 3, 4, 5, 6])
        np.testing.assert_array_equal(
            view["wavelength"], np.linspace(1000.0, 2000.0, 6)
        )

    def test_transformed_fields_computed(self):
        view = UnifiedView(_native(), _specs())
        np.testing.assert_allclose(
            view["surface_flux"], 4.0 * np.pi * np.array([1, 2, 3, 4, 5, 6])
        )
        np.testing.assert_allclose(
            view["wavelength_um"], np.linspace(1000.0, 2000.0, 6) * 1e-4
        )

    def test_file_metadata_constant(self):
        specs = _specs(
            teff=FieldSpec(
                name="teff",
                native_name="teff",
                unit="K",
                origin=FieldOrigin.FILE_METADATA,
            )
        )
        view = UnifiedView(_native(), specs, constants={"teff": 5000.0})
        assert view["teff"] == 5000.0

    def test_missing_field_raises(self):
        specs = _specs(
            continuum=FieldSpec(name="continuum", origin=FieldOrigin.MISSING)
        )
        view = UnifiedView(_native(), specs)
        with pytest.raises(KeyError, match="not available"):
            view["continuum"]

    def test_absent_wavelength_raises_wavelength_error(self):
        view = UnifiedView(_native(wavelength=None), _specs())
        with pytest.raises(WavelengthGridError, match="no wavelength column"):
            view["wavelength"]

    def test_absent_continuum_raises(self):
        view = UnifiedView(_native(continuum=None), _specs())
        with pytest.raises(KeyError, match="continuum"):
            view["continuum"]

    def test_unknown_field_raises_with_available(self):
        view = UnifiedView(_native(), _specs())
        with pytest.raises(KeyError, match="available"):
            view["photon_flux"]

    def test_field_spec_and_contains(self):
        view = UnifiedView(_native(), _specs())
        assert "surface_flux" in view
        assert view.has_field("surface_flux")
        assert view.field_spec("surface_flux").unit == "erg/s/cm^2/Angstrom"
        assert not view.has_field("photon_flux")


class TestToTable:
    def test_materializes_with_units_and_meta(self):
        view = UnifiedView(_native(), _specs())
        table = view.to_table()
        from astropy import units as u

        assert "surface_flux" in table.colnames
        assert "continuum" in table.colnames
        assert table["surface_flux"].unit == u.erg / u.s / u.cm**2 / u.angstrom
        assert table.meta["stellarlib_layer"] == "unified"
        np.testing.assert_allclose(
            table["surface_flux"].value, 4.0 * np.pi * np.array([1, 2, 3, 4, 5, 6])
        )

    def test_missing_fields_are_skipped(self):
        specs = _specs(
            continuum=FieldSpec(name="continuum", origin=FieldOrigin.MISSING)
        )
        view = UnifiedView(_native(), specs)
        assert "continuum" not in view.to_table().colnames
