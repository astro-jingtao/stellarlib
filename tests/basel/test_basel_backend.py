"""BaSeL backend tests: set construction, variants, quirks, unified
mapping, and grid queries over the fixture excerpts."""

from __future__ import annotations

import pathlib
import warnings

from astropy import units as u
import numpy as np
import pytest
from scipy.integrate import trapezoid

from stellarlib.exceptions import (
    AmbiguousConventionError,
    DataIntegrityError,
    GridHoleError,
    MetadataConflictError,
    UnsafeInterpolationError,
)
from stellarlib.facade import BaselSpectra
from stellarlib.models.basel.backend import read_basel
from stellarlib.warnings import DataQuirkWarning, VoronoiInterpolationWarning

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "basel"

_STEFAN_BOLTZMANN = 5.670374419e-5

# integrate through scipy: numpy's trapz/trapezoid name moved with its
# major version (1.x ships only np.trapz, >= 2.3 only np.trapezoid)
# while scipy has carried trapezoid since 1.6 — one name valid across
# the whole supported numpy range.


def _fixture_files(*names):
    return [DATA / n for n in names]


class TestSetConstruction:
    def test_two_files_one_set(self):
        s = read_basel(_fixture_files("wlbc99_p00.cor", "wlbc99_m20.cor"),
                       variant="WLBC99")
        assert len(s) == 8
        assert s.identity.family == "basel"
        assert s.identity.release == "3.1"
        assert s.identity.physics_configuration == "calibration WLBC99"
        assert s.selectable == ("teff", "log_surface_gravity", "m_h")
        assert s.notes["files"] == {"wlbc99_m20.cor": 4, "wlbc99_p00.cor": 4}
        assert "feh_column_mismatches" not in s.notes

    def test_directory_source(self, tmp_path):
        target = tmp_path / "WLBC99"
        target.mkdir()
        for name in ("wlbc99_p00.cor", "wlbc99_m20.cor"):
            (target / name).write_bytes((DATA / name).read_bytes())
        s = read_basel(target)
        assert len(s) == 8
        assert s.identity.physics_configuration == "calibration WLBC99"

    def test_flat_31_set_requires_variant(self):
        with pytest.raises(AmbiguousConventionError, match="variant"):
            read_basel(_fixture_files("wlbc99_p00.cor", "wlbc99_m20.cor"))

    def test_mixed_releases_refused(self):
        with pytest.raises(MetadataConflictError, match="one BaSeL version"):
            read_basel(_fixture_files("wlbc99_p00.cor", "lbc96_p00.cor"),
                       variant="WLBC99")

    def test_22_rejects_variant(self):
        with pytest.raises(ValueError, match="no calibration variants"):
            read_basel(_fixture_files("lbc96_p00.cor"), variant="WLBC99")

    def test_unknown_variant_refused(self):
        with pytest.raises(ValueError, match="variant must be one of"):
            read_basel(_fixture_files("wlbc99_p00.cor"), variant="LEJEUNE")

    def test_duplicate_coordinates_within_file_refused(self, tmp_path):
        text = (DATA / "wlbc99_p00.cor").read_text(encoding="utf-8")
        lines = text.splitlines()
        doubled = lines[: HEADER_AND_FIRST_BLOCK_END] + lines[HEADER_LINES:]
        path = tmp_path / "wlbc99_p00.cor"
        path.write_text("\n".join(doubled) + "\n", encoding="utf-8")
        with pytest.raises(DataIntegrityError, match="both sit at"):
            read_basel([path], variant="WLBC99")


HEADER_LINES = 153
HEADER_AND_FIRST_BLOCK_END = HEADER_LINES + 176


class TestTarStreaming:
    def test_31_tar_demands_variant_then_serves_one(self):
        tar = DATA / "basel3_1_fixture.tar.gz"
        with pytest.raises(AmbiguousConventionError, match="both calibration"):
            read_basel(tar)
        for variant in ("WLBC99", "PADOVA2000"):
            s = read_basel(tar, variant=variant)
            assert len(s) == 8
            assert s.identity.physics_configuration == f"calibration {variant}"
            assert all(n.facts["member"].startswith(f"BaSeL3.1/{variant}/")
                       for n in s)

    def test_22_tar_streams(self):
        s = read_basel(DATA / "basel2_2_fixture.tar.gz")
        assert len(s) == 7
        assert s.identity.release == "2.2"
        assert s.identity.physics_configuration is None
        assert len(s.provenance.assets) == 1

    def test_wrong_variant_for_the_held_tree(self):
        with pytest.raises(ValueError, match="no calibration variants"):
            read_basel(DATA / "basel2_2_fixture.tar.gz", variant="PADOVA2000")


class TestTwo2QuirkRecording:
    def test_22_warns_about_recorded_quirks_at_set_construction(self):
        # every path that builds a 2.2 grid goes through read_basel, so
        # the warning fires at grid initialization (open_grid, construct,
        # db.grid alike)
        with pytest.warns(DataQuirkWarning, match="data-sources"):
            read_basel(_fixture_files("lbc96_p00.cor"))

    def test_31_does_not_warn(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DataQuirkWarning)
            read_basel(_fixture_files("wlbc99_p00.cor"), variant="WLBC99")

    def test_feh_mismatch_blocks_recorded_not_fixed(self):
        s = read_basel(_fixture_files("lbc96_p00.cor", "lbc96_p01.cor"))
        assert len(s) == 7
        mismatches = s.notes["feh_column_mismatches"]
        assert len(mismatches) == 2
        assert {m["file"] for m in mismatches} == {"lbc96_p01.cor"}
        assert {m["spectrum_number"] for m in mismatches} == {192, 193}
        assert {m["feh_column"] for m in mismatches} == {0.2}
        assert {m["file_m_h"] for m in mismatches} == {0.1}
        # the p01 nodes sit at the FILE's metallicity (+0.1), and both
        # files together hold exactly one node per coordinate
        keys = {n.key(s.selectable) for n in s}
        assert (6750.0, 1.0, 0.1) in keys
        assert (6750.0, 1.0, 0.0) in keys
        node = next(n for n in s if n.coordinates["m_h"] == 0.1)
        assert node.facts["feh_matches_file"] is False
        spectrum = s.load(node)
        assert spectrum.native.metadata["feh_column"] == 0.2
        assert spectrum.native.metadata["m_h"] == 0.1


class TestUnifiedMapping:
    def test_units_and_transformations(self):
        s = read_basel(_fixture_files("wlbc99_p00.cor"), variant="WLBC99")
        spectrum = s.load(s[0])
        native = spectrum.native
        assert native.wavelength[0] == 9.1  # nm, verbatim
        unified = spectrum.unified
        assert unified["wavelength"][0] == pytest.approx(91.0)  # Angstrom
        assert unified["wavelength_um"][0] == pytest.approx(0.0091)
        # f_nu -> f_lambda: F_lambda = f_nu * c / lambda^2 (per nm) / 10
        c_nm = 2.99792458e17
        expected = native.flux[0] * c_nm / native.wavelength[0] ** 2 / 10.0
        assert unified["eddington_flux"][0] == pytest.approx(expected, rel=1e-12)
        assert unified["surface_flux"][0] == pytest.approx(
            4.0 * np.pi * expected, rel=1e-12
        )
        assert "continuum" not in unified.field_names or spectrum.field_info(
            "continuum"
        ).origin.value == "missing"
        with pytest.raises(KeyError):
            unified["continuum"]

    def test_bolometric_closure_the_flux_unit_ruling(self):
        """The physics anchor: integrated surface flux = sigma T^4.

        The published f_nu integrates to sigma T^4 / 4 pi (the Eddington
        flux per steradian; the author's ReadMe flags the 4pi ambiguity
        and the numbers settle it), so the unified surface flux must
        close onto sigma T^4 directly.
        """
        s = read_basel(_fixture_files("wlbc99_p00.cor"), variant="WLBC99")
        for node in s:
            spectrum = s.load(node)
            wave_a = spectrum.unified["wavelength"]
            flux = spectrum.unified["surface_flux"]
            integral = abs(trapezoid(flux, wave_a))
            expected = _STEFAN_BOLTZMANN * node.coordinates["teff"] ** 4
            assert integral == pytest.approx(expected, rel=5e-3)

    def test_native_units_documented(self):
        s = read_basel(_fixture_files("wlbc99_p00.cor"), variant="WLBC99")
        native = s.load(s[0]).native
        # the reader states the published convention as astropy units,
        # not unit strings parsed downstream
        assert native.wavelength_unit is u.nm
        assert native.flux_unit == u.erg / (u.s * u.cm**2 * u.Hz * u.sr)
        table = native.to_table()
        assert table["wavelength"].unit.to_string() == "nm"
        assert "Hz" in table["flux"].unit.to_string()


class TestGridQueries:
    def _grid(self):
        return BaselSpectra.construct(
            paths=_fixture_files("wlbc99_p00.cor", "wlbc99_m20.cor"),
            variant="WLBC99",
        )

    def test_axes_and_holes(self):
        grid = self._grid()
        assert grid.axes["m_h"] == (-2.0, 0.0)
        assert grid.axes["teff"] == (4000.0, 4500.0)
        assert grid.axes["log_surface_gravity"] == (0.5, 1.0)
        assert not grid.has_holes()  # the fixture rectangle is complete

    def test_exact_node_hit(self):
        grid = self._grid()
        spec = grid.spectrum(teff=4500.0, log_surface_gravity=1.0, m_h=0.0)
        assert spec.selection_info["method"] == "exact-node"
        assert grid.last_record.method == "exact-node"

    def test_multilinear_across_all_three_axes(self):
        grid = self._grid()
        spec = grid.spectrum(
            teff=4200.0, log_surface_gravity=0.7, m_h=-0.6,
        )
        assert grid.last_record.method == "multilinear"
        assert len(grid.last_record.node_values[0]) == 8
        corners = [grid.load_node(k) for k in
                   [tuple(v[ax] for ax in grid.selectable)
                    for v in grid.last_record.node_values[0]]]
        weights = np.asarray(grid.last_record.weights[0])
        stack = np.asarray([c.native.flux for c in corners])
        assert np.allclose(spec.native.flux, weights @ stack, rtol=1e-12)

    def test_holed_bracket_cell_raises_and_voronoi_answers(self, tmp_path):
        """The m20 fixture minus its (4000, 0.5) block: the m_h axis
        still spans (-2.0, 0.0) but the bracket cell of an interior query
        is missing one corner — the honest hole the library publishes at
        full scale, reproduced on the fixture."""
        text = (DATA / "wlbc99_m20.cor").read_text(encoding="utf-8")
        lines = text.splitlines()
        holed = tmp_path / "wlbc99_m20.cor"
        holed.write_text(
            "\n".join(lines[: HEADER_LINES] + lines[HEADER_LINES + 176 :]) + "\n",
            encoding="utf-8",
        )
        grid = BaselSpectra.construct(
            paths=_fixture_files("wlbc99_p00.cor", holed), variant="WLBC99"
        )
        assert grid.has_holes()
        with pytest.raises(GridHoleError, match="missing node"):
            grid.spectrum(teff=4200.0, log_surface_gravity=0.7, m_h=-1.0)
        with pytest.warns(VoronoiInterpolationWarning):
            spec = grid.spectrum(
                teff=4200.0, log_surface_gravity=0.7, m_h=-1.0,
                interp="voronoi",
            )
        assert spec.selection_info["method"] == "voronoi"
        assert spec.selection_info["returned"]["m_h"] in (-2.0, 0.0)

    def test_out_of_range_strict(self):
        grid = self._grid()
        with pytest.raises(UnsafeInterpolationError, match="outside"):
            grid.spectrum(teff=6000.0, log_surface_gravity=0.7, m_h=0.0)

    def test_corner_machinery(self):
        grid = self._grid()
        idx, w, _clamped = grid.corner_weights(
            teff=[4200.0], log_surface_gravity=[0.7], m_h=[-0.6]
        )
        assert idx.shape == (1, 8)
        assert np.isclose(w.sum(axis=1)[0], 1.0)
        rows = grid.corner_spectrum(idx[0])
        assert rows.shape == (8, 1221)
