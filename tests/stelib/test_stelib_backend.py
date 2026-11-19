"""STELIB backend and facade tests: the empirical-scatter set, the
star-table join, the second-spectrum and table-only records, and the
nearest-star query semantics (real upstream bytes; see
tests/data/stelib/README.md)."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

import stellarlib
from stellarlib.exceptions import MetadataConflictError, UnrecognizedFormatError
from stellarlib.facade import StelibSpectra, construct
from stellarlib.models.stelib.backend import read_stelib
from stellarlib.models.stelib.design import STELIB_ADAPTER
from stellarlib.warnings import VoronoiInterpolationWarning

DATA = Path(__file__).parent.parent / "data" / "stelib"
TABLE = DATA / "table.dat"
COR = sorted((DATA / "cor_sp").glob("*.fits"))
OBS = sorted((DATA / "obs_sp").glob("*.fits"))


class TestReadSet:
    def test_from_catalog_tree(self):
        s = read_stelib(DATA)
        assert len(s) == 3  # the parameterized stars
        assert s.identity.release == "2003"
        assert s.selectable == ("teff", "log_surface_gravity", "m_h")
        assert s.notes["star_table"] == TABLE.name
        assert s.notes["spectra_held"] == 6
        assert s.notes["spectra_served"] == 3
        assert s.notes["observed_counterparts_held"] == 6
        assert s.notes["stars_without_parameters"] == [
            {"star": "HD 005820", "spectrum_file": "HD005820_V3.2.fits"},
            {"star": "HD 015318", "spectrum_file": "HD015318_V3.2.fits"},
        ]
        assert s.notes["second_corrected_spectra"] == [
            {"star": "HD 053929", "spectrum_file": "HD053929ss_V3.2.fits"}
        ]
        assert s.notes["rows_without_corrected_spectrum"] == [
            {"star": "LHS   235", "observed_only": []}
        ]

    def test_node_coordinates_and_facts(self):
        s = read_stelib(DATA)
        (node,) = [n for n in s if n.facts["star_name"] == "HD 053929"]
        assert node.coordinates == {
            "teff": 14011.0,
            "log_surface_gravity": 3.63,
            "m_h": 0.29,
        }
        # the table's own cSpFile1 order serves the star; the second
        # spectrum is held data recorded in notes, never a second node
        assert node.facts["spectrum_file"] == "HD053929lp_V3.2.fits"
        assert node.facts["observed_counterparts"] == [
            "HD53929_moy_sso.fits",
            "HD53929_moy_jkt.fits",
        ]

    def test_spectrum_loads_and_maps(self):
        s = read_stelib(DATA)
        node = s.nodes[0]
        spectrum = s.load(node)
        assert len(spectrum) == 6700
        assert spectrum.unified["observed_flux_density"].shape == (6700,)
        assert spectrum.unified["wavelength_um"][0] == pytest.approx(
            spectrum.unified["wavelength"][0] * 1e-4
        )
        for missing in ("observed_flux", "surface_flux", "eddington_flux"):
            with pytest.raises(KeyError, match="not available"):
                spectrum.unified[missing]

    def test_from_loose_files(self):
        s = read_stelib([*COR, TABLE])
        assert len(s) == 3
        assert s.notes["spectra_held"] == 6
        assert s.notes["observed_counterparts_held"] == 0

    def test_spectrum_without_table_row_fails(self, tmp_path):
        lonely = tmp_path / "HD271163_V3.2.fits"
        shutil.copy2(COR[0], lonely)
        with pytest.raises(MetadataConflictError, match="no star-table row"):
            read_stelib([lonely, *COR[1:], TABLE])

    def test_missing_table_fails(self):
        with pytest.raises(ValueError, match="star table"):
            read_stelib(COR)

    def test_observed_files_alone_are_not_a_set(self):
        with pytest.raises(UnrecognizedFormatError, match="corrected spectra"):
            read_stelib([*OBS, TABLE])


class TestFacade:
    def _spectra(self) -> StelibSpectra:
        return StelibSpectra.construct(paths=DATA)

    def test_nearest_star_query(self):
        stelib = self._spectra()
        with pytest.warns(VoronoiInterpolationWarning, match="nearest published node"):
            spectrum = stelib.spectrum(teff=4600.0, log_surface_gravity=1.0, m_h=-2.0)
        assert spectrum.selection_info["method"] == "voronoi"
        assert spectrum.selection_info["returned"] == {
            "teff": 4712.0,
            "log_surface_gravity": 1.13,
            "m_h": -2.41,
        }
        assert stelib.last_record.method == "voronoi"

    def test_exact_node_hit(self):
        stelib = self._spectra()
        spectrum = stelib.spectrum(teff=14011, log_surface_gravity=3.63, m_h=0.29)
        assert spectrum.selection_info["method"] == "exact-node"
        assert spectrum.native.metadata["corrected"] is True

    def test_multilinear_refused(self):
        stelib = self._spectra()
        with pytest.raises(ValueError, match="own wavelength"):
            stelib.spectrum(
                teff=4600.0,
                log_surface_gravity=1.0,
                m_h=-2.0,
                interp="multilinear",
            )

    def test_construct_from_paths_and_database_shape(self):
        stelib = construct("stelib/2003", paths=DATA)
        assert isinstance(stelib, StelibSpectra)
        assert stelib.identity.release == "2003"
        with pytest.raises(ValueError, match="database="):
            construct("stelib/2003")

    def test_open_grid_on_the_tree(self):

        grid = stellarlib.open_grid(DATA, model="stelib/2003")
        assert len(grid) == 3
        assert grid.axes["teff"] == (4712.0, 7611.0, 14011.0)


class TestAdapter:
    def test_designs_phase_one(self):
        designs = list(STELIB_ADAPTER.designs({"family": "stelib"}))
        assert len(designs) == 1
        (design,) = designs
        assert design.directory_name == "stelib/library/all"
        assert [p.role for p in design.parts] == [
            "provenance_snapshot",
            "bulk_manifest",
            "atlas",
        ]
        assert design.parts[1].url.endswith("/III/232/table.dat")
        assert design.facts["catalog"] == "III/232"
        assert design.facts["channel"] == "cds"  # canonical default

    def test_source_channel_selects_the_base(self):
        (design,) = STELIB_ADAPTER.designs({"family": "stelib", "source": "adac"})
        assert design.parts[0].url == (
            "http://vizier.nao.ac.jp/ftp/cats/III/232/ReadMe"
        )
        assert design.facts["channel"] == "adac"

    def test_unknown_source_refused(self):
        with pytest.raises(ValueError, match="known channels"):
            STELIB_ADAPTER.designs({"family": "stelib", "source": "ivoa"})

    def test_expand_design_enumerates_from_the_table(self):
        (design,) = STELIB_ADAPTER.designs({"family": "stelib", "source": "adac"})
        expanded = STELIB_ADAPTER.expand_design(design, DATA)
        assert len(expanded.parts) == 3 + 6 + 6  # top-level + cor + obs
        names = [p.name for p in expanded.parts]
        assert "cor_sp/HD053929ss_V3.2.fits" in names
        assert "obs_sp/HD53929_moy_sso.fits" in names
        cor_part = next(p for p in expanded.parts if p.name.endswith("lp_V3.2.fits"))
        assert cor_part.url == (
            "http://vizier.nao.ac.jp/ftp/cats/III/232/cor_sp/HD053929lp_V3.2.fits"
        )

    def test_expand_without_table_is_phase_one(self, tmp_path):
        (design,) = STELIB_ADAPTER.designs({"family": "stelib"})
        assert STELIB_ADAPTER.expand_design(design, tmp_path) == design

    def test_design_of_manifest_round_trip(self):
        design = STELIB_ADAPTER.designs({"family": "stelib", "source": "adac"})[0]
        manifest = {
            "family": "stelib",
            "label": "all",
            "family_section": {"mirror": dict(design.facts)},
        }
        assert STELIB_ADAPTER.design_of_manifest(manifest, Path()) == design
