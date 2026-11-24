"""Tests for the core schema objects."""

from __future__ import annotations

import pytest

from stellarlib.spectra.core import (
    FieldOrigin,
    FieldSpec,
    ModelIdentity,
    ParameterRole,
    ParameterSource,
    ParameterSpec,
)


class TestParameterSpec:
    def test_axis_requires_values(self):
        with pytest.raises(ValueError, match="requires 'values'"):
            ParameterSpec(name="teff", role=ParameterRole.AXIS)

    def test_fixed_requires_value(self):
        with pytest.raises(ValueError, match="requires 'value'"):
            ParameterSpec(name="microturbulence", role=ParameterRole.FIXED)

    def test_derived_requires_relation(self):
        with pytest.raises(ValueError, match="requires"):
            ParameterSpec(name="surface_flux_scale", role=ParameterRole.DERIVED)

    def test_value_and_values_are_exclusive(self):
        with pytest.raises(ValueError, match="both"):
            ParameterSpec(
                name="x",
                role=ParameterRole.FIXED,
                value=2.0,
                values=(1.0, 2.0),
            )

    def test_axis_spec_roundtrip(self):
        spec = ParameterSpec(
            name="teff",
            role=ParameterRole.AXIS,
            values=(3500.0, 4000.0, 5000.0),
            unit="K",
            convention="effective temperature",
            source=ParameterSource.NATIVE,
        )
        d = spec.to_dict()
        assert d["role"] == "axis"
        again = ParameterSpec.from_dict(d)
        assert again == spec

    def test_values_coerced_to_tuple(self):
        spec = ParameterSpec(
            name="m_h", role=ParameterRole.AXIS, values=[-1.0, 0.0, 0.5]
        )
        assert isinstance(spec.values, tuple)

    def test_role_type_checked(self):
        with pytest.raises(TypeError, match="ParameterRole"):
            ParameterSpec(name="teff", role="axis", values=(3500.0,))


class TestFieldSpec:
    def test_native_requires_native_name(self):
        with pytest.raises(ValueError, match="native_name"):
            FieldSpec(name="eddington_flux", origin=FieldOrigin.NATIVE)

    def test_transformed_native_requires_transformation(self):
        with pytest.raises(ValueError, match="transformed_native"):
            FieldSpec(
                name="surface_flux",
                native_name="flux",
                origin=FieldOrigin.TRANSFORMED_NATIVE,
            )

    def test_derived_requires_transformation(self):
        with pytest.raises(ValueError, match="derived"):
            FieldSpec(name="wavelength_um", origin=FieldOrigin.DERIVED)

    def test_file_metadata_requires_native_name(self):
        with pytest.raises(ValueError, match="file_metadata"):
            FieldSpec(name="teff", origin=FieldOrigin.FILE_METADATA)

    def test_roundtrip(self):
        spec = FieldSpec(
            name="surface_flux",
            native_name="flux",
            unit="erg/s/cm^2/Angstrom",
            native_unit="erg/s/cm^2/Angstrom",
            origin=FieldOrigin.TRANSFORMED_NATIVE,
            transformation="4 pi * x",
            description="F_lambda = 4 pi H_lambda",
        )
        again = FieldSpec.from_dict(spec.to_dict())
        assert again == spec


class TestModelIdentity:
    def test_label_includes_physics_configuration(self):
        identity = ModelIdentity(
            family="bosz",
            release="2024",
            product="resampled_spectra",
            physics_configuration="r10000",
        )
        assert identity.label == "bosz/2024/r10000"

    def test_label_without_physics_configuration(self):
        identity = ModelIdentity(
            family="tlusty", release="OSTAR2002", product="optical_spectra"
        )
        assert identity.label == "tlusty/OSTAR2002"

    def test_roundtrip(self):
        identity = ModelIdentity(
            family="bosz", release="2024", product="resampled_spectra"
        )
        assert ModelIdentity.from_dict(identity.to_dict()) == identity
