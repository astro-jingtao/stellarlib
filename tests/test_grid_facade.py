"""Tests for SpectrumGrid and the unified facade, on synthetic grids.

The synthetic spectra carry a linear flux law in the node coordinates, so
multilinear interpolation must reproduce the law exactly — the numerical
anchor the downstream parity tests reuse on real fixtures.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stellarlib.core import FieldOrigin, FieldSpec, ModelIdentity
from stellarlib.exceptions import (
    GridHoleError,
    GridPointNotFoundError,
    UnsafeInterpolationError,
)
from stellarlib.facade import UnifiedSpectra
from stellarlib.native import NativeSpectrum
from stellarlib.provenance import Provenance
from stellarlib.spectrum import GridNode, Spectrum, SpectrumSet
from stellarlib.unified import UnifiedView
from stellarlib.warnings import (
    ClampedInterpolationWarning,
    NearestGridPointWarning,
    VoronoiInterpolationWarning,
)

TEFFS = (3500.0, 3750.0, 4000.0)
LOGGS = (1.0, 1.5, 2.0)
MHS = (-0.5, 0.0)

FLUX_SPECS = {
    "eddington_flux": FieldSpec(
        name="eddington_flux", native_name="flux", unit="erg/(s cm^2 Angstrom)"
    ),
    "surface_flux": FieldSpec(
        name="surface_flux",
        native_name="flux",
        unit="erg/(s cm^2 Angstrom)",
        origin=FieldOrigin.TRANSFORMED_NATIVE,
        transformation="4 pi * x",
    ),
}

WAVE = np.linspace(1000.0, 2000.0, 51)


def _flux_law(teff, logg, mh):
    return 0.001 * teff + 2.0 * logg + 1.0 * mh


def _make_node(teff, logg, mh):
    return GridNode(
        coordinates={
            "teff": float(teff),
            "log_surface_gravity": float(logg),
            "m_h": float(mh),
        },
        path=Path(f"synthetic_t{teff}_g{logg}_m{mh}.txt"),
        facts={"family": "mp"},
    )


def _loader(node):
    c = node.coordinates
    flux = np.full(WAVE.size, _flux_law(c["teff"], c["log_surface_gravity"], c["m_h"]))
    native = NativeSpectrum(
        wavelength=WAVE.copy(),
        flux=flux,
        continuum=flux + 0.5,
        provenance_keys={"file": node.filename},
    )
    identity = ModelIdentity(family="synth", release="1", product="spectra")
    return Spectrum(
        native=native,
        identity=identity,
        field_specs=dict(FLUX_SPECS),
        provenance=Provenance(transformations=("parse synthetic",)),
        selection=dict(c),
        unified_view=UnifiedView(native, dict(FLUX_SPECS)),
    )


def _grid(nodes):
    identity = ModelIdentity(family="synth", release="1", product="spectra")
    set_ = SpectrumSet(
        nodes=nodes,
        identity=identity,
        loader=_loader,
        selectable=("teff", "log_surface_gravity", "m_h"),
    )
    return UnifiedSpectra([set_])


def _full_nodes():
    return [
        _make_node(t, g, m) for t in TEFFS for g in LOGGS for m in MHS
    ]


class TestGridIntrospection:
    def test_axes_report_truth(self):
        grid = _grid(_full_nodes())
        assert grid.axes["teff"] == TEFFS
        assert grid.axes["log_surface_gravity"] == LOGGS
        assert grid.axes["m_h"] == MHS
        assert len(grid) == 18

    def test_complete_rectangle_has_no_holes(self):
        grid = _grid(_full_nodes())
        assert grid.holes == ()
        assert not grid.has_holes()

    def test_holes_are_reported_not_filled(self):
        nodes = _full_nodes()
        holed = [n for n in nodes if n.coordinates["teff"] != 3750.0 or n.coordinates["log_surface_gravity"] != 1.5]
        grid = _grid(holed)
        assert grid.holes == ((3750.0, 1.5, -0.5), (3750.0, 1.5, 0.0))
        assert grid.has_holes()

    def test_duplicate_node_refused(self):
        nodes = [*_full_nodes(), _make_node(3500.0, 1.0, -0.5)]
        with pytest.raises(ValueError, match="duplicate"):
            _grid(nodes)

    def test_identity_must_be_unique(self):
        nodes = _full_nodes()
        identity = ModelIdentity(family="other", release="9", product="x")
        other = SpectrumSet(
            nodes=[],
            identity=identity,
            loader=_loader,
            selectable=("teff", "log_surface_gravity", "m_h"),
        )
        grid_set = _grid(nodes).sets[0]
        with pytest.raises(ValueError, match="mix library products"):
            UnifiedSpectra([grid_set, other])


class TestSelection:
    def test_exact_hits_node(self):
        grid = _grid(_full_nodes())
        spectrum = grid.get_spectrum(teff=3750.0, log_surface_gravity=1.5, m_h=0.0)
        assert spectrum.selection_info["method"] == "exact"
        np.testing.assert_allclose(spectrum.native.flux[0], _flux_law(3750.0, 1.5, 0.0))

    def test_exact_miss_raises(self):
        grid = _grid(_full_nodes())
        with pytest.raises(GridPointNotFoundError):
            grid.get_spectrum(teff=3600.0, log_surface_gravity=1.5, m_h=0.0)

    def test_hole_raises_grid_hole_error(self):
        nodes = [n for n in _full_nodes() if n.coordinates != {"teff": 3750.0, "log_surface_gravity": 1.5, "m_h": 0.0}]
        grid = _grid(nodes)
        with pytest.raises(GridHoleError, match="hole"):
            grid.get_spectrum(teff=3750.0, log_surface_gravity=1.5, m_h=0.0)

    def test_nearest_snaps_and_warns(self):
        grid = _grid(_full_nodes())
        with pytest.warns(NearestGridPointWarning):
            spectrum = grid.get_spectrum(
                method="nearest", teff=3760.0, log_surface_gravity=1.5, m_h=0.0
            )
        assert spectrum.selection_info["distances"]["teff"] == pytest.approx(10.0)

    def test_lazy_loading_and_cache(self):
        grid = _grid(_full_nodes())
        assert grid.load_cache_size() == 0
        grid.get_spectrum(teff=3500.0, log_surface_gravity=1.0, m_h=-0.5)
        grid.get_spectrum(teff=3500.0, log_surface_gravity=1.0, m_h=-0.5)
        assert grid.load_cache_size() == 1
        grid.clear_cache()
        assert grid.load_cache_size() == 0


class TestFacadePointQueries:
    def test_exact_node_needs_no_interpolation(self):
        grid = _grid(_full_nodes())
        spectrum = grid.spectrum(teff=4000.0, log_surface_gravity=2.0, m_h=-0.5)
        assert grid.last_record.method == "exact-node"
        np.testing.assert_allclose(
            spectrum.native.flux[0], _flux_law(4000.0, 2.0, -0.5)
        )

    def test_multilinear_reproduces_linear_law(self):
        grid = _grid(_full_nodes())
        for teff, logg, mh in [
            (3760.0, 1.3, -0.2),
            (3900.0, 1.75, 0.0),
            (3510.0, 1.95, -0.5),
        ]:
            spectrum = grid.spectrum(
                teff=teff, log_surface_gravity=logg, m_h=mh
            )
            np.testing.assert_allclose(
                spectrum.native.flux[0], _flux_law(teff, logg, mh)
            )
        assert grid.last_record.method == "multilinear"
        assert len(grid.last_record.weights[0]) == 8
        assert sum(grid.last_record.weights[0]) == pytest.approx(1.0)

    def test_strict_range_policy_refuses_outside(self):
        grid = _grid(_full_nodes())
        with pytest.raises(UnsafeInterpolationError, match="outside"):
            grid.spectrum(teff=3000.0, log_surface_gravity=1.5, m_h=0.0)

    def test_clamp_policy_warns_and_edge_interpolates(self):
        grid = _grid(_full_nodes())
        with pytest.warns(ClampedInterpolationWarning, match="clamped"):
            spectrum = grid.spectrum(
                teff=3000.0,
                log_surface_gravity=1.5,
                m_h=0.0,
                range_policy="clamp",
            )
        # clamps to the teff=3500 edge: flux of the edge plane at (1.5, 0)
        np.testing.assert_allclose(
            spectrum.native.flux[0], _flux_law(3500.0, 1.5, 0.0)
        )
        assert grid.last_record.clamped[0]["teff"] is True

    def test_holed_cell_refuses_multilinear(self):
        nodes = [
            n
            for n in _full_nodes()
            if n.coordinates
            not in (
                {"teff": 3750.0, "log_surface_gravity": 1.5, "m_h": 0.0},
                {"teff": 3750.0, "log_surface_gravity": 1.5, "m_h": -0.5},
            )
        ]
        grid = _grid(nodes)
        with pytest.raises(GridHoleError, match="voronoi"):
            grid.spectrum(teff=3760.0, log_surface_gravity=1.4, m_h=-0.25)

    def test_voronoi_returns_nearest_node(self):
        nodes = [
            n
            for n in _full_nodes()
            if n.coordinates
            not in (
                {"teff": 3750.0, "log_surface_gravity": 1.5, "m_h": 0.0},
                {"teff": 3750.0, "log_surface_gravity": 1.5, "m_h": -0.5},
            )
        ]
        grid = _grid(nodes)
        with pytest.warns(VoronoiInterpolationWarning):
            spectrum = grid.spectrum(
                teff=3760.0,
                log_surface_gravity=1.4,
                m_h=-0.25,
                interp="voronoi",
            )
        assert grid.last_record.method == "voronoi"
        # the exact 8 corners of that cell are the holes; nearest existing
        # node is one step away in log g
        returned = grid.last_record.node_values[0][0]
        assert returned["log_surface_gravity"] in (1.0, 2.0)
        np.testing.assert_allclose(spectrum.native.flux[0], _flux_law(returned["teff"], returned["log_surface_gravity"], returned["m_h"]))

    def test_interpolated_spectrum_is_marked_derived(self):
        grid = _grid(_full_nodes())
        spectrum = grid.spectrum(teff=3760.0, log_surface_gravity=1.3, m_h=-0.2)
        assert spectrum.native.metadata["derived"] is True
        assert spectrum.unified["surface_flux"][0] == pytest.approx(
            4.0 * np.pi * _flux_law(3760.0, 1.3, -0.2)
        )

    def test_get_flux_default_surface_flux(self):
        grid = _grid(_full_nodes())
        flux = grid.get_flux(teff=3760.0, log_surface_gravity=1.3, m_h=-0.2)
        np.testing.assert_allclose(
            flux, np.full(WAVE.size, 4.0 * np.pi * _flux_law(3760.0, 1.3, -0.2))
        )


class TestFacadeCornerMechanism:
    def test_corner_weights_and_corner_spectrum_pair(self):
        grid = _grid(_full_nodes())
        idx, w, _clamped = grid.corner_weights(
            teff=[3760.0, 3900.0],
            log_surface_gravity=[1.3, 1.7],
            m_h=[-0.2, -0.1],
        )
        assert idx.shape == (2, 8)
        rows = grid.corner_spectrum(idx[0])
        assert rows.shape == (8, WAVE.size)
        combined = w[0] @ rows
        np.testing.assert_allclose(combined, np.full(WAVE.size, _flux_law(3760.0, 1.3, -0.2)))

    def test_corner_weights_refuses_holed_cell(self):
        nodes = [
            n
            for n in _full_nodes()
            if n.coordinates != {"teff": 3750.0, "log_surface_gravity": 1.5, "m_h": 0.0}
        ]
        grid = _grid(nodes)
        with pytest.raises(GridHoleError, match="missing node"):
            grid.corner_weights(teff=3760.0, log_surface_gravity=1.3, m_h=-0.25)

    def test_single_valued_axes_fill_automatically(self):
        nodes = [_make_node(t, 1.5, 0.0) for t in TEFFS]
        grid = _grid(nodes)
        spectrum = grid.spectrum(teff=3800.0)
        np.testing.assert_allclose(spectrum.native.flux[0], _flux_law(3800.0, 1.5, 0.0))
        assert grid.last_record.method == "multilinear"
        assert len(grid.last_record.weights[0]) == 8


class TestDescribe:
    def test_describe_lists_axes_and_holes(self):
        grid = _grid(_full_nodes())
        text = grid.describe()
        assert "nodes: 18" in text
        assert "axis teff" in text
        assert "holes in axis product: 0" in text
