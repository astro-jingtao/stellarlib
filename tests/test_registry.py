"""Tests for the declarative unified-parameter registry."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from stellarlib.core import FieldOrigin, ParameterRole, ParameterSource
from stellarlib.provenance import Reference
from stellarlib.registry import (
    REGISTRY_FILE,
    backend_data_file,
    backend_names,
    backend_notes,
    field_spec_dict,
    field_specs,
    load_registry,
    product_names,
    product_notes,
    references,
    resolve_parameters,
    unified_field_names,
)

REPO_ROOT = Path(__file__).parent.parent


class TestRegistryData:
    def test_registry_file_ships_with_the_package(self):
        assert REGISTRY_FILE.exists()
        assert REGISTRY_FILE.suffix == ".json"

    def test_loads_and_lists_backends_and_products(self):
        reg = load_registry()
        assert reg["schema_version"] == 1
        assert set(backend_names()) == {"bosz", "tlusty", "miles"}
        assert product_names("bosz") == ("resampled_spectra",)
        assert set(product_names("tlusty")) == {"ostar2002", "bstar2006"}
        assert product_names("miles") == ("library",)

    def test_unified_vocabulary_present(self):
        names = unified_field_names()
        assert len(names) == len(set(names))
        for required in (
            "wavelength",
            "wavelength_um",
            "observed_flux",
            "eddington_flux",
            "surface_flux",
            "continuum",
        ):
            assert required in names

    def test_unknown_backend_fails_loudly(self):
        with pytest.raises(ValueError, match="no backend 'phoenix'"):
            field_specs("phoenix", "spectra")


class TestFieldSpecs:
    def test_bosz_specs_map_to_native_arrays(self):
        specs = field_spec_dict("bosz", "resampled_spectra")
        assert specs["eddington_flux"].native_name == "flux"
        assert specs["continuum"].native_name == "continuum"
        assert specs["wavelength"].native_name == "wavelength"
        assert specs["surface_flux"].origin is FieldOrigin.TRANSFORMED_NATIVE
        assert specs["surface_flux"].transformation == "4 pi * x"
        assert specs["wavelength_um"].transformation == "x * 1e-4"
        assert specs["surface_flux"].mapping_version == "1"

    def test_tlusty_continuum_is_missing(self):
        specs = field_spec_dict("tlusty", "ostar2002")
        assert specs["continuum"].origin is FieldOrigin.MISSING
        assert specs["eddington_flux"].native_name == "flux"

    def test_miles_maps_observed_flux_and_refuses_model_fluxes(self):
        specs = field_spec_dict("miles", "library")
        assert specs["observed_flux"].native_name == "flux"
        assert specs["observed_flux"].unit is None
        assert specs["eddington_flux"].origin is FieldOrigin.MISSING
        assert specs["surface_flux"].origin is FieldOrigin.MISSING
        assert specs["continuum"].origin is FieldOrigin.MISSING

    def test_transformed_fields_declare_implemented_transformation(self):
        for backend in backend_names():
            for product in product_names(backend):
                for spec in field_specs(backend, product):
                    if spec.origin is FieldOrigin.TRANSFORMED_NATIVE:
                        from stellarlib.unified import TRANSFORMATIONS

                        assert spec.transformation in TRANSFORMATIONS
                        assert spec.native_unit is not None


class TestResolveParametersBosz:
    DYNAMIC = {
        "teff": (3500.0, 4000.0, 4500.0),
        "logg": (1.0, 1.5, 2.0),
        "mh": (0.0,),
        "am": 0.0,
        "cm": 0.0,
        "vmicro": 2,
        "resolution": 500,
    }

    def test_roles_and_values(self):
        specs = {
            p.name: p
            for p in resolve_parameters("bosz", "resampled_spectra", self.DYNAMIC)
        }
        assert specs["teff"].role is ParameterRole.AXIS
        assert specs["teff"].values == (3500.0, 4000.0, 4500.0)
        assert specs["teff"].unit == "K"
        assert specs["log_surface_gravity"].values == (1.0, 1.5, 2.0)
        assert specs["m_h"].values == (0.0,)
        assert specs["alpha_m"].role is ParameterRole.FIXED
        assert specs["alpha_m"].value == 0.0
        assert specs["alpha_m"].source is ParameterSource.NATIVE
        assert specs["spectral_resolution"].value == 500
        assert specs["microturbulence"].value == 2

    def test_missing_required_dynamic_value_fails_loudly(self):
        dynamic = dict(self.DYNAMIC)
        del dynamic["resolution"]
        with pytest.raises(KeyError, match="resolution"):
            resolve_parameters("bosz", "resampled_spectra", dynamic)

    def test_omitted_vmicro_selects_mixed_family_fallback(self):
        dynamic = dict(self.DYNAMIC)
        del dynamic["vmicro"]
        specs = {
            p.name: p
            for p in resolve_parameters("bosz", "resampled_spectra", dynamic)
        }
        assert specs["microturbulence"].role is ParameterRole.IMPLICIT
        assert "varies by atmosphere family" in specs["microturbulence"].status


class TestResolveParametersTlusty:
    def test_ostar_axes_and_fixed_composition(self):
        specs = {
            p.name: p
            for p in resolve_parameters(
                "tlusty",
                "ostar2002",
                {
                    "teff": (27500.0, 30000.0),
                    "logg": (3.0, 3.25),
                    "composition": "G",
                    "vmicro": 1.0,
                },
            )
        }
        assert specs["teff"].role is ParameterRole.AXIS
        assert specs["log_surface_gravity"].values == (3.0, 3.25)
        assert specs["composition"].role is ParameterRole.FIXED
        assert specs["composition"].value == "G"
        assert specs["microturbulence"].value == 1.0
        assert specs["spectral_resolution"].role is ParameterRole.IMPLICIT

    def test_bstar_product(self):
        specs = {
            p.name: p
            for p in resolve_parameters(
                "tlusty",
                "bstar2006",
                {
                    "teff": (15000.0, 16000.0),
                    "logg": (1.75, 2.0),
                    "composition": "BG",
                    "vmicro": 2.0,
                },
            )
        }
        assert specs["teff"].values == (15000.0, 16000.0)
        assert specs["composition"].value == "BG"


class TestReferencesAndNotes:
    def test_references_are_reference_objects(self):
        for backend in backend_names():
            refs = references(backend)
            assert refs and all(isinstance(r, Reference) for r in refs)
        assert references("bosz")[0].bibcode == "2024A&A...688A.197M"
        assert references("tlusty")[0].bibcode == "2003ApJS..146..417L"

    def test_notes_exist(self):
        for backend in backend_names():
            assert backend_notes(backend)
            for product in product_names(backend):
                assert isinstance(product_notes(backend, product), tuple)

    def test_zero_clipping_stance_is_recorded(self):
        notes = " ".join(backend_notes("bosz")).lower()
        assert "verbatim" in notes
        assert "no clipping" in notes


class TestGeneratedDocument:
    def test_docs_unified_parameters_md_is_current(self):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "generate_parameter_docs.py"),
                "--check",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
