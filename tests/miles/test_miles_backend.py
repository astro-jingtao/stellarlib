"""MILES backend and facade tests: the empirical-scatter set, the
s-number join, the unparameterized-star record, and the nearest-star
query semantics (real upstream bytes; see tests/data/miles/README.md)."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from stellarlib.exceptions import MetadataConflictError
from stellarlib.facade import MilesSpectra, construct
from stellarlib.models.miles.backend import read_miles
from stellarlib.models.miles.design import MILES_ADAPTER
from stellarlib.warnings import VoronoiInterpolationWarning

DATA = Path(__file__).parent.parent / "data" / "miles"
PARAMS = DATA / "paramsMILES_v9.1_excerpt.txt"
TAR = DATA / "miles_library_fixture.tar.gz"
FITSES = [
    DATA / "s0001.fits",
    DATA / "s0002.fits",
    DATA / "s0838.fits",
    DATA / "s0985.fits",
]


def _params_without(tmp_path: Path, s_number: int) -> Path:
    """The excerpt minus one star's row (a package made inconsistent)."""
    lines = PARAMS.read_text(encoding="utf-8").splitlines()
    drop = f"{s_number:04d}"
    header = lines[0]
    miles_at = header.index("MILES")
    kept = [
        ln
        for ln in lines[3:]
        if ln[miles_at : miles_at + 6].split()[0] != drop
    ]
    out = tmp_path / "paramsMILES_v9.1.txt"
    out.write_text("\n".join([*lines[:3], *kept]) + "\n", encoding="utf-8")
    return out


class TestReadSet:
    def test_from_tarball(self):
        s = read_miles([TAR], params_path=PARAMS)
        assert len(s) == 3  # the parameterized stars; s0838 has no [Fe/H]
        assert s.identity.release == "2011"
        assert s.identity.version == "v9.1"
        assert s.selectable == ("teff", "log_surface_gravity", "m_h")
        assert s.notes["spectra_held"] == 4
        assert s.notes["params_table"] == PARAMS.name
        assert s.notes["stars_without_parameters"] == [
            {"s_number": 838, "name": "G156-031"}
        ]
        (node,) = [n for n in s if n.facts["s_number"] == 985]
        assert node.coordinates == {
            "teff": 4020.0, "log_surface_gravity": 1.32, "m_h": -0.13,
        }
        assert node.facts["star_name"] == "NGC 7789 971(946) Cl"
        assert node.facts["member"] == "s0985.fits"

    def test_from_loose_files(self):
        s = read_miles([*FITSES, PARAMS])
        assert len(s) == 3
        assert s.notes["spectra_held"] == 4
        (node,) = [n for n in s if n.facts["s_number"] == 1]
        assert node.facts["file"] == "s0001.fits"
        assert node.path == DATA / "s0001.fits"

    def test_spectrum_loads_and_maps(self):
        s = read_miles([TAR], params_path=PARAMS)
        node = s.nodes[0]
        spectrum = s.load(node)
        assert len(spectrum) == 4367
        assert spectrum.unified["observed_flux"].shape == (4367,)
        assert spectrum.unified["wavelength"][0] == pytest.approx(3500.0)
        assert spectrum.unified["wavelength_um"][0] == pytest.approx(0.35)
        with pytest.raises(KeyError, match="not available"):
            spectrum.unified["surface_flux"]
        with pytest.raises(KeyError, match="not available"):
            spectrum.unified["eddington_flux"]

    def test_spectrum_without_table_row_fails(self, tmp_path):
        table = _params_without(tmp_path, 985)
        with pytest.raises(MetadataConflictError, match="s0985"):
            read_miles([TAR], params_path=table)

    def test_missing_table_fails(self, tmp_path):
        lonely = tmp_path / TAR.name
        shutil.copy2(TAR, lonely)
        with pytest.raises(ValueError, match="parameter table"):
            read_miles([lonely])


class TestGuards:
    def test_tarball_plus_loose_files_refused(self):
        with pytest.raises(ValueError, match="never both"):
            read_miles([TAR, *FITSES], params_path=PARAMS)

    def test_two_tarballs_refused(self):
        with pytest.raises(ValueError, match="one library tarball"):
            read_miles([TAR, TAR.with_name("other.tar.gz")], params_path=PARAMS)

    def test_duplicate_loose_s_number_refused(self, tmp_path):
        copy = tmp_path / "s0001.fits"
        shutil.copy2(DATA / "s0001.fits", copy)
        with pytest.raises(MetadataConflictError, match="s0001 twice"):
            read_miles([DATA / "s0001.fits", copy], params_path=PARAMS)


class TestFacade:
    def _spectra(self) -> MilesSpectra:
        return MilesSpectra.construct(paths=[TAR], params_path=PARAMS)

    def test_nearest_star_query(self):
        miles = self._spectra()
        with pytest.warns(VoronoiInterpolationWarning, match="nearest published node"):
            spectrum = miles.spectrum(teff=4100.0, log_surface_gravity=1.0, m_h=-0.1)
        assert spectrum.selection_info["method"] == "voronoi"
        assert spectrum.selection_info["returned"] == {
            "teff": 4020.0, "log_surface_gravity": 1.32, "m_h": -0.13,
        }
        assert miles.last_record.method == "voronoi"
        assert miles.last_record.node_values == (
            ({"teff": 4020.0, "log_surface_gravity": 1.32, "m_h": -0.13},),
        )

    def test_exact_node_hit(self):
        miles = self._spectra()
        spectrum = miles.spectrum(teff=5305, log_surface_gravity=4.49, m_h=-0.75)
        assert spectrum.selection_info["method"] == "exact-node"
        assert spectrum.selection_info["returned"]["teff"] == 5305

    def test_multilinear_refused(self):
        miles = self._spectra()
        with pytest.raises(ValueError, match="per-star normalizations"):
            miles.spectrum(
                teff=4100.0, log_surface_gravity=1.0, m_h=-0.1,
                interp="multilinear",
            )

    def test_construct_from_paths_and_database_shape(self, tmp_path):
        miles = construct("miles/2011", paths=[TAR], params_path=PARAMS)
        assert isinstance(miles, MilesSpectra)
        assert miles.identity.release == "2011"
        with pytest.raises(ValueError, match="database="):
            construct("miles/2011")

    def test_open_grid_on_a_product_layout(self, tmp_path):
        import stellarlib

        shutil.copy2(TAR, tmp_path / TAR.name)
        shutil.copy2(PARAMS, tmp_path / PARAMS.name)
        grid = stellarlib.open_grid(tmp_path, model="miles/2011")
        assert len(grid) == 3
        assert grid.axes["teff"] == (4020.0, 4167.0, 5305.0)


class TestAdapter:
    def test_designs_declare_the_whole_package(self):
        designs = list(MILES_ADAPTER.designs({"family": "miles"}))
        assert len(designs) == 1
        (design,) = designs
        assert design.directory_name == "miles/library/all"
        assert [p.role for p in design.parts] == [
            "archive", "parameters", "provenance_snapshot",
        ]
        assert design.facts["n_spectra"] == 985

    def test_design_of_manifest_round_trip(self):
        design = MILES_ADAPTER.designs({"family": "miles"})[0]
        manifest = {
            "family": "miles",
            "label": "all",
            "family_section": {"mirror": dict(design.facts)},
        }
        assert MILES_ADAPTER.design_of_manifest(manifest, Path()) == design
