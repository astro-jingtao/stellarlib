"""Tests for the BOSZ backend: set construction, wave pairing, identity.

The grid-level tests reuse the 8 real fixture nodes and check the
wave-pairing discovery rules, the loud error paths (row mismatch,
composition mixing, wave ambiguity), lazy loading, the unified view
(surface_flux = 4 pi * H), and real-data interpolation invariants
(midpoint = corner mean under trilinear weights).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from stellarlib.core import ParameterRole
from stellarlib.exceptions import (
    MetadataConflictError,
    WavelengthGridError,
)
from stellarlib.facade import BoszSpectra, construct
from stellarlib.grid import open_grid
from stellarlib.models.bosz.backend import read_bosz

DATA = Path(__file__).parent.parent / "data" / "bosz"

NODE_NAMES = [
    f"bosz2024_ms_t{t}_g{g}_m{m}_a+0.00_c+0.00_v2_r500_resam.txt.gz"
    for t in (3500, 4000)
    for g in ("+1.0", "+2.0")
    for m in ("+0.00", "-1.00")
]


def _read_fixture_set(**kwargs) -> object:
    return read_bosz([DATA / name for name in NODE_NAMES], **kwargs)


class TestSetConstruction:
    def test_nodes_axes_identity(self):
        set_ = _read_fixture_set()
        assert len(set_) == 8
        assert set_.selectable == ("teff", "log_surface_gravity", "m_h")
        grid = BoszSpectra([set_])
        assert grid.axes["teff"] == (3500.0, 4000.0)
        assert grid.axes["log_surface_gravity"] == (1.0, 2.0)
        assert grid.axes["m_h"] == (-1.0, 0.0)
        assert grid.identity.family == "bosz"
        assert grid.identity.release == "2024"
        assert grid.identity.physics_configuration == "r500"
        assert grid.holes == ()

    def test_uniform_vmicro_reported_fixed(self):
        set_ = _read_fixture_set()
        spec = set_.parameter("microturbulence")
        assert spec.role is ParameterRole.FIXED
        assert spec.value == 2

    def test_mixed_vmicro_reported_implicit(self, tmp_path):
        # mp carries v1 and ms carries v2: a set spanning both families
        # cannot state one microturbulence
        names = [
            "bosz2024_ms_t3500_g+1.0_m+0.00_a+0.00_c+0.00_v2_r500_resam.txt.gz",
            "bosz2024_ms_t3500_g+2.0_m+0.00_a+0.00_c+0.00_v2_r500_resam.txt.gz",
        ]
        extra = tmp_path / "bosz2024_mp_t3500_g+4.0_m+0.00_a+0.00_c+0.00_v1_r500_resam.txt.gz"
        shutil.copy(DATA / names[0], extra)
        set_ = read_bosz([DATA / names[0], DATA / names[1], extra])
        spec = set_.parameter("microturbulence")
        assert spec.role is ParameterRole.IMPLICIT
        assert "varies by atmosphere family" in spec.status

    def test_mixed_composition_refused(self, tmp_path):
        # same window but a different [a/M] sneaks in
        renegade = tmp_path / "bosz2024_ms_t3500_g+1.0_m+0.00_a+0.25_c+0.00_v2_r500_resam.txt.gz"
        shutil.copy(DATA / NODE_NAMES[0], renegade)
        with pytest.raises(MetadataConflictError, match="one set holds one am"):
            read_bosz([DATA / NODE_NAMES[1], renegade])

    def test_mixed_resolution_refused(self, tmp_path):
        renegade = tmp_path / "bosz2024_ms_t3500_g+1.0_m+0.00_a+0.00_c+0.00_v2_r1000_resam.txt.gz"
        shutil.copy(DATA / NODE_NAMES[0], renegade)
        with pytest.raises(MetadataConflictError, match="resolution"):
            read_bosz([DATA / NODE_NAMES[1], renegade])


class TestWavePairing:
    def test_wave_discovered_flat(self, tmp_path):
        for name in NODE_NAMES:
            shutil.copy(DATA / name, tmp_path / name)
        shutil.copy(DATA / "bosz2024_wave_r500.txt", tmp_path)
        set_ = read_bosz(tmp_path)
        assert set_.notes["wave_rows"] == 300

    def test_wave_discovered_in_wavelength_grids_subdir(self, tmp_path):
        sub = tmp_path / "spectra"
        sub.mkdir()
        for name in NODE_NAMES:
            shutil.copy(DATA / name, sub / name)
        wg = tmp_path / "wavelength_grids"
        wg.mkdir()
        shutil.copy(DATA / "bosz2024_wave_r500.txt", wg)
        set_ = read_bosz(tmp_path)
        assert set_.notes["wave_rows"] == 300

    def test_explicit_wave_path_wins(self, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        shutil.copy(DATA / "bosz2024_wave_r500.txt", target)
        set_ = _read_fixture_set(wave_path=target / "bosz2024_wave_r500.txt")
        assert set_.notes["wave_rows"] == 300

    def test_missing_wave_is_loud(self, tmp_path):
        for name in NODE_NAMES:
            shutil.copy(DATA / name, tmp_path / name)
        with pytest.raises(WavelengthGridError, match="wave_path"):
            read_bosz(tmp_path)

    def test_ambiguous_wave_lists_candidates(self, tmp_path):
        for name in NODE_NAMES:
            shutil.copy(DATA / name, tmp_path / name)
        shutil.copy(DATA / "bosz2024_wave_r500.txt", tmp_path)
        sub = tmp_path / "wavelength_grids"
        sub.mkdir()
        shutil.copy(DATA / "bosz2024_wave_r500.txt", sub)
        with pytest.raises(WavelengthGridError, match="multiple candidate"):
            read_bosz(tmp_path)

    def test_row_mismatch_is_loud(self, tmp_path):
        for name in NODE_NAMES:
            shutil.copy(DATA / name, tmp_path / name)
        set_ = read_bosz(tmp_path, wave_path=DATA / "short_wave.txt")
        grid = BoszSpectra([set_])
        with pytest.raises(WavelengthGridError, match="disagree"):
            grid.get_spectrum(teff=3500.0, log_surface_gravity=1.0, m_h=0.0)


class TestLoadingAndUnified:
    def test_lazy_loading_pairs_wave(self):
        grid = BoszSpectra([_read_fixture_set()])
        assert grid.load_cache_size() == 0
        spectrum = grid.get_spectrum(teff=3500.0, log_surface_gravity=1.0, m_h=0.0)
        assert grid.load_cache_size() == 1
        native = spectrum.native
        assert native.wavelength is not None
        assert native.wavelength.shape == native.flux.shape
        # rgf parity: wavelength in micron via the unified transformation
        np.testing.assert_allclose(
            spectrum.unified["wavelength_um"], native.wavelength * 1e-4
        )
        # post_processor parity: F_lambda = 4 pi H_lambda
        np.testing.assert_allclose(
            spectrum.unified["surface_flux"], 4.0 * np.pi * native.flux
        )
        np.testing.assert_allclose(
            spectrum.unified["continuum"], native.continuum
        )

    def test_provenance_carries_assets_and_references(self):
        grid = BoszSpectra([_read_fixture_set()])
        spectrum = grid.get_spectrum(teff=4000.0, log_surface_gravity=2.0, m_h=-1.0)
        prov = spectrum.provenance
        filenames = [a.filename for a in prov.assets]
        assert "bosz2024_wave_r500.txt" in filenames
        assert "bosz2024_ms_t4000_g+2.0_m-1.00_a+0.00_c+0.00_v2_r500_resam.txt.gz" in filenames
        url = next(
            a.source.url
            for a in prov.assets
            if a.filename
            == "bosz2024_ms_t4000_g+2.0_m-1.00_a+0.00_c+0.00_v2_r500_resam.txt.gz"
        )
        assert url == (
            "https://archive.stsci.edu/hlsps/bosz/bosz2024/r500/m-1.00/"
            "bosz2024_ms_t4000_g+2.0_m-1.00_a+0.00_c+0.00_v2_r500_resam.txt.gz"
        )
        assert prov.scientific_references[0].bibcode == "2024A&A...688A.197M"


class TestOpenGrid:
    def test_open_grid_with_model_from_directory(self, tmp_path):
        for name in NODE_NAMES:
            shutil.copy(DATA / name, tmp_path / name)
        shutil.copy(DATA / "bosz2024_wave_r500.txt", tmp_path)
        grid = open_grid(tmp_path, model="bosz/2024")
        assert grid.identity.release == "2024"
        assert len(grid) == 8

    def test_open_grid_sniffs_bosz(self, tmp_path):
        for name in NODE_NAMES:
            shutil.copy(DATA / name, tmp_path / name)
        shutil.copy(DATA / "bosz2024_wave_r500.txt", tmp_path)
        grid = open_grid(tmp_path)
        assert grid.identity.family == "bosz"

    def test_construct_validates_release(self, tmp_path):
        for name in NODE_NAMES:
            shutil.copy(DATA / name, tmp_path / name)
        shutil.copy(DATA / "bosz2024_wave_r500.txt", tmp_path)
        with pytest.raises(ValueError, match="does not match"):
            construct("bosz/2017", paths=tmp_path)


class TestRealDataInterpolation:
    def test_exact_node_returns_published_values(self):
        grid = BoszSpectra([_read_fixture_set()])
        spectrum = grid.spectrum(teff=3500.0, log_surface_gravity=1.0, m_h=0.0)
        assert grid.last_record.method == "exact-node"
        assert not spectrum.native.metadata.get("derived")

    def test_midpoint_equals_corner_mean(self):
        grid = BoszSpectra([_read_fixture_set()])
        spectrum = grid.spectrum(teff=3750.0, log_surface_gravity=1.5, m_h=-0.5)
        assert grid.last_record.method == "multilinear"
        corners = grid.corner_spectrum(grid.last_record.node_indices[0])
        np.testing.assert_allclose(
            spectrum.native.flux, corners.mean(axis=0), rtol=1e-12
        )

    def test_interpolated_between_two_teff_nodes(self):
        grid = BoszSpectra([_read_fixture_set()])
        spectrum = grid.spectrum(
            teff=3600.0, log_surface_gravity=1.0, m_h=0.0
        )
        lo = grid.get_spectrum(teff=3500.0, log_surface_gravity=1.0, m_h=0.0)
        hi = grid.get_spectrum(teff=4000.0, log_surface_gravity=1.0, m_h=0.0)
        np.testing.assert_allclose(
            spectrum.native.flux, 0.2 * hi.native.flux + 0.8 * lo.native.flux
        )
