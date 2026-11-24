"""Tests for the interpolation machinery.

The corner-weight tests replicate the hand-computed expectations of the
downstream applications' own test suites (node = single corner with
weight 1, cell midpoint = all-1/8, asymmetric fractions, clamp masks) and
compare against a literal re-implementation of their bracket algorithm,
pinning the exact contract (corner order ``c = 4*dt + 2*dg + d*m``, flat
C-order indices).
"""

from __future__ import annotations

import numpy as np
import pytest

from stellarlib.core import ModelIdentity
from stellarlib.exceptions import UnsafeInterpolationError
from stellarlib.interp import (
    InterpolationRecord,
    corner_weights,
    resample_wavelengths,
    voronoi_nearest,
)
from stellarlib.native import NativeSpectrum
from stellarlib.spectrum import InterpSpectrum, NodeSpectrum, Spectrum
from stellarlib.unified import UnifiedView

TEFFS = np.array([3500.0, 3750.0, 4000.0, 4250.0])
LOGGS = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
MHS = np.array([-1.0, -0.5, 0.0, 0.5])


def _downstream_corner_weights(teffs, loggs, mhs, teff, logg, mh):
    """The downstream BoszGrid.corner_weights algorithm, transplanted."""
    teff = np.atleast_1d(np.asarray(teff, dtype=np.float64))
    logg = np.atleast_1d(np.asarray(logg, dtype=np.float64))
    mh = np.atleast_1d(np.asarray(mh, dtype=np.float64))
    n_g, n_m = loggs.size, mhs.size

    def out_of(x, axis):
        return (x < axis[0]) | (x > axis[-1])

    clamped = (out_of(teff, teffs), out_of(logg, loggs), out_of(mh, mhs))

    def bracket(axis, x):
        i = np.clip(np.searchsorted(axis, x) - 1, 0, axis.size - 2)
        w = np.clip((x - axis[i]) / (axis[i + 1] - axis[i]), 0.0, 1.0)
        return i, w

    i_t, w_t = bracket(teffs, teff)
    i_g, w_g = bracket(loggs, logg)
    i_m, w_m = bracket(mhs, mh)

    idx = np.empty((teff.size, 8), dtype=np.int64)
    w = np.empty((teff.size, 8), dtype=np.float64)
    corner = 0
    for dt, wt in ((0, 1.0 - w_t), (1, w_t)):
        for dg, wg in ((0, 1.0 - w_g), (1, w_g)):
            for dm, wm in ((0, 1.0 - w_m), (1, w_m)):
                idx[:, corner] = ((i_t + dt) * n_g + i_g + dg) * n_m + i_m + dm
                w[:, corner] = wt * wg * wm
                corner += 1
    return idx, w, clamped


class TestCornerWeightsContract:
    def test_matches_downstream_algorithm_on_random_queries(self):
        rng = np.random.default_rng(42)
        teff = rng.uniform(3000.0, 5000.0, 40)
        logg = rng.uniform(-0.5, 2.5, 40)
        mh = rng.uniform(-1.5, 1.0, 40)
        idx, w, clamped = corner_weights(
            {"teff": TEFFS, "logg": LOGGS, "mh": MHS}, teff=teff, logg=logg, mh=mh
        )
        ref_idx, ref_w, ref_clamped = _downstream_corner_weights(
            TEFFS, LOGGS, MHS, teff, logg, mh
        )
        np.testing.assert_array_equal(idx, ref_idx)
        np.testing.assert_allclose(w, ref_w)
        for got, ref in zip(clamped, ref_clamped):
            np.testing.assert_array_equal(got, ref)

    def test_node_query_gives_single_corner_weight_one(self):
        idx, w, _ = corner_weights(
            {"teff": TEFFS, "logg": LOGGS, "mh": MHS},
            teff=3750.0,
            logg=1.0,
            mh=-0.5,
        )
        assert idx.shape == (1, 8)
        assert w.shape == (1, 8)
        np.testing.assert_allclose(w.sum(), 1.0)
        assert np.count_nonzero(w) == 1
        # node (1, 2, 1): flat = (1*5 + 2)*4 + 1 = 29
        assert idx[0, int(np.argmax(w))] == 29
        assert w[0, int(np.argmax(w))] == 1.0

    def test_cell_midpoint_all_eighth(self):
        _, w, _ = corner_weights(
            {"teff": TEFFS, "logg": LOGGS, "mh": MHS},
            teff=3875.0,
            logg=1.25,
            mh=-0.25,
        )
        np.testing.assert_allclose(w[0], np.full(8, 0.125))

    def test_asymmetric_fractions_hand_computed(self):
        # between TEFFS[1]=3750 and [2]=4000 at f=0.4; LOGGS[2]=1.0..1.5 at
        # f=0.2; MHS[2]=0.0..0.5 at f=0.6
        _, w, _ = corner_weights(
            {"teff": TEFFS, "logg": LOGGS, "mh": MHS},
            teff=3850.0,
            logg=1.1,
            mh=0.3,
        )
        expected = [
            0.6 * 0.8 * 0.4,
            0.6 * 0.8 * 0.6,
            0.6 * 0.2 * 0.4,
            0.6 * 0.2 * 0.6,
            0.4 * 0.8 * 0.4,
            0.4 * 0.8 * 0.6,
            0.4 * 0.2 * 0.4,
            0.4 * 0.2 * 0.6,
        ]
        np.testing.assert_allclose(w[0], expected)
        np.testing.assert_allclose(w.sum(), 1.0)

    def test_clamp_masks_report_out_of_range(self):
        _, _, clamped = corner_weights(
            {"teff": TEFFS, "logg": LOGGS, "mh": MHS},
            teff=[3000.0, 4000.0, 4500.0],
            logg=[1.0, 3.0, 1.0],
            mh=[0.0, 0.0, -2.0],
        )
        clamped_t, clamped_g, clamped_m = clamped
        np.testing.assert_array_equal(clamped_t, [True, False, True])
        np.testing.assert_array_equal(clamped_g, [False, True, False])
        np.testing.assert_array_equal(clamped_m, [False, False, True])

    def test_two_axis_case_matches_tlusty_order(self):
        # corner order (dt, dg) = (0,0), (0,1), (1,0), (1,1)
        teffs = np.array([15000.0, 16000.0, 17000.0])
        loggs = np.array([3.0, 3.25, 3.5])
        idx, w, _ = corner_weights(
            {"teff": teffs, "logg": loggs}, teff=15500.0, logg=3.375
        )
        assert idx.shape == (1, 4)
        np.testing.assert_allclose(w[0], np.full(4, 0.25))
        # cell (0, 1): corners (0,1), (0,2), (1,1), (1,2)
        np.testing.assert_array_equal(idx[0], [1, 2, 4, 5])

    def test_axis_validation(self):
        with pytest.raises(ValueError, match="strictly increasing"):
            corner_weights(
                {"a": [1.0, 3.0, 2.0], "b": [0.0, 1.0]}, a=1.5, b=0.5
            )
        with pytest.raises(ValueError, match="non-empty"):
            corner_weights({"a": [], "b": [0.0, 1.0]}, a=1.0, b=0.5)

    def test_degenerate_axis_interpolates_the_remaining_axes(self):
        # a single-value axis (the Teff-ladder case) pins both corner
        # digits on its one value; the other axes interpolate normally
        idx, w, _ = corner_weights(
            {"a": [2.0, 4.0], "b": [7.0]}, a=3.0, b=7.0
        )
        assert idx.shape == (1, 4)
        np.testing.assert_allclose(w[0], [0.5, 0.0, 0.5, 0.0])
        np.testing.assert_array_equal(idx[0], [0, 0, 1, 1])

    def test_queries_must_name_exactly_the_axes(self):
        with pytest.raises(ValueError, match="exactly the axes"):
            corner_weights({"a": [0.0, 1.0]}, a=0.5, b=0.5)


class TestVoronoiNearest:
    def test_nearest_node_wins(self):
        axes = {"teff": [3500.0, 3750.0, 4000.0], "logg": [1.0, 1.5, 2.0]}
        keys = [(3500.0, 1.0), (3500.0, 1.5), (3750.0, 1.0), (4000.0, 2.0)]
        indices, distances, scales = voronoi_nearest(
            axes, keys, [(3510.0, 1.05), (3980.0, 1.9)]
        )
        assert list(indices) == [0, 3]
        assert distances.shape == (2,)
        assert scales["teff"] == 250.0
        assert scales["logg"] == 0.5

    def test_holes_never_selected(self):
        # the teff=3750 column does not exist (holes); the query sits in
        # that column's cell, and voronoi must land on an available node
        axes = {"teff": [3500.0, 3750.0], "logg": [1.0, 1.5]}
        keys = [(3500.0, 1.0), (3500.0, 1.5)]  # (3750, *) are holes
        indices, _, _ = voronoi_nearest(axes, keys, [(3740.0, 1.45)])
        assert list(indices) == [1]

    def test_step_normalization_makes_axes_comparable(self):
        # 1 raw unit of logg = 2 grid steps (median step 0.5); 1 K of teff
        # = 1/250 steps — a small teff offset must not lose to a full logg
        # step
        axes = {"teff": [3500.0, 3750.0], "logg": [1.0, 1.5]}
        keys = [(3500.0, 1.0), (3750.0, 1.0)]
        indices, _, _ = voronoi_nearest(axes, keys, [(3510.0, 1.4)])
        assert list(indices) == [0]


class TestResampleWavelengths:
    def _make_spectrum(self):

        wave = np.linspace(1000.0, 2000.0, 101)
        native = NativeSpectrum(
            wavelength=wave,
            flux=2.0 * wave + 1.0,
            continuum=3.0 * wave,
        )
        identity = ModelIdentity(family="t", release="1", product="p")
        return NodeSpectrum(
            native=native,
            identity=identity,
            unified_view=UnifiedView(native, {}),
        )

    def test_resample_preserves_the_spectrum_class(self):
        node_spectrum = self._make_spectrum()
        out = resample_wavelengths(node_spectrum, [1100.0, 1500.0])
        assert isinstance(out, NodeSpectrum)
        record = InterpolationRecord(
            method="multilinear",
            axes=("teff",),
            requested=({"teff": 3760.0},),
            node_indices=((0,),),
            node_values=({"teff": 3750.0},),
            weights=((1.0,),),
            clamped=({"teff": False},),
        )
        interp_spectrum = InterpSpectrum(
            native=node_spectrum.native,
            identity=node_spectrum.identity,
            unified_view=UnifiedView(node_spectrum.native, {}),
            record=record,
        )
        out = resample_wavelengths(interp_spectrum, [1100.0, 1500.0])
        assert isinstance(out, InterpSpectrum)
        assert out.record is record

    def test_linear_function_reproduced_exactly(self):
        spectrum = self._make_spectrum()
        out = resample_wavelengths(spectrum, [1100.0, 1500.0, 1900.0])
        np.testing.assert_allclose(out.native.flux, [2201.0, 3001.0, 3801.0])
        np.testing.assert_allclose(out.native.continuum, [3300.0, 4500.0, 5700.0])

    def test_transformation_recorded_not_native(self):
        spectrum = self._make_spectrum()
        out = resample_wavelengths(spectrum, [1100.0, 1500.0])
        assert out.provenance.transformations
        assert "resample" in out.provenance.transformations[-1]
        assert out.native.metadata.get("derived") is not True

    def test_extrapolation_refused(self):
        spectrum = self._make_spectrum()
        with pytest.raises(UnsafeInterpolationError, match="outside"):
            resample_wavelengths(spectrum, [900.0, 1500.0])

    def test_unsorted_target_refused(self):
        spectrum = self._make_spectrum()
        with pytest.raises(UnsafeInterpolationError, match="strictly increasing"):
            resample_wavelengths(spectrum, [1500.0, 1100.0])

    def test_requires_wavelength(self):

        native = NativeSpectrum(wavelength=None, flux=np.ones(5))
        spectrum = Spectrum(
            native=native,
            identity=ModelIdentity(family="t", release="1", product="p"),
        )
        with pytest.raises(UnsafeInterpolationError, match="no wavelength"):
            resample_wavelengths(spectrum, [1.0, 2.0])
