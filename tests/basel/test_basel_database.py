"""BaSeL database tests: registration, blueprint designs, and the
database-open path (offline, on tmp-path databases)."""

from __future__ import annotations

import pathlib
import shutil

import pytest

import stellarlib
from stellarlib.database.build import build_database, register_local_product
from stellarlib.database.core import open_database
from stellarlib.database.scan import enumerate_designs
from stellarlib.exceptions import AmbiguousConventionError

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "basel"


def _registered_db(tmp_path, tar_name, fixture, label="library"):
    """A tmp database holding one registered basel product (the fixture
    under the official archive name, so registration recognizes it)."""
    tarball = tmp_path / "held" / tar_name
    tarball.parent.mkdir()
    shutil.copy2(DATA / fixture, tarball)
    return register_local_product(
        tmp_path, "basel", label, files=[tarball], note="fixture registration"
    )


class TestBlueprintDesigns:
    def test_family_declares_the_three_published_files(self):
        designs = enumerate_designs(
            "/nonexistent", declarations=[{"family": "basel"}]
        )
        assert sorted(designs) == [
            "basel/2.2/library",
            "basel/3.1/library",
            "basel/format/readme",
        ]
        design, adapter = designs["basel/3.1/library"]
        assert adapter.family == "basel"
        assert design.parts[0].name == "BaSeL3_1.tar.gz"
        assert design.parts[0].url.endswith("BaSeL3_1.tar.gz")

    def test_design_of_manifest_matches_by_archive(self):
        designs = enumerate_designs(
            "/nonexistent", declarations=[{"family": "basel"}]
        )
        _, adapter = designs["basel/2.2/library"]
        design = adapter.design_of_manifest(
            {"family_section": {"mirror": {"archive": "BaSeL2_2.tar.gz"}}},
            pathlib.Path("."),
        )
        assert design is not None
        assert design.directory_name == "basel/2.2/library"
        assert adapter.design_of_manifest(
            {"family_section": {"mirror": {"archive": "other.tar"}}},
            pathlib.Path("."),
        ) is None

    def test_dry_run_reports_three_products(self, tmp_path, capsys):
        build_database(tmp_path, declarations=[{"family": "basel"}], dry_run=True)
        out = capsys.readouterr().out
        assert "basel/2.2/library" in out
        assert "basel/3.1/library" in out
        assert "basel/format/readme" in out


class TestRegistrationAndOpen:
    def test_register_31_tarball_and_open_grid(self, tmp_path):
        record = _registered_db(tmp_path, "BaSeL3_1.tar.gz", "basel3_1_fixture.tar.gz")
        assert record.directory == tmp_path / "basel" / "3.1" / "library"
        db = open_database(tmp_path)
        grid = db.grid("basel/3.1", variant="WLBC99")
        assert len(grid) == 8
        assert grid.identity.physics_configuration == "calibration WLBC99"

    def test_grid_open_demands_variant_on_both(self, tmp_path):
        _registered_db(tmp_path, "BaSeL3_1.tar.gz", "basel3_1_fixture.tar.gz")
        db = open_database(tmp_path)

        with pytest.raises(AmbiguousConventionError, match="variant"):
            db.grid("basel/3.1")

    def test_register_22_tarball_and_open_grid(self, tmp_path):
        _registered_db(tmp_path, "BaSeL2_2.tar.gz", "basel2_2_fixture.tar.gz")
        db = open_database(tmp_path)
        grid = db.grid("basel/2.2")
        assert len(grid) == 7
        assert grid.identity.release == "2.2"

    def test_readme_registers_as_its_own_product(self, tmp_path):
        # the readme product registers under the official file name
        stand_in = tmp_path / "held" / "BaSeL_ReadMe.txt"
        stand_in.parent.mkdir()
        stand_in.write_text("format description stand-in\n", encoding="utf-8")
        record = register_local_product(
            tmp_path, "basel", "readme", files=[stand_in], note="fixture"
        )
        assert record.directory == tmp_path / "basel" / "format" / "readme"

    def test_mixed_product_registration_refused(self, tmp_path):
        held = tmp_path / "held"
        held.mkdir()
        shutil.copy2(DATA / "basel3_1_fixture.tar.gz", held / "BaSeL3_1.tar.gz")
        shutil.copy2(DATA / "basel2_2_fixture.tar.gz", held / "BaSeL2_2.tar.gz")
        with pytest.raises(ValueError, match="one published file"):
            register_local_product(
                tmp_path, "basel", "library",
                files=[held / "BaSeL3_1.tar.gz", held / "BaSeL2_2.tar.gz"],
                note="",
            )

    def test_construct_from_database(self, tmp_path):

        _registered_db(tmp_path, "BaSeL3_1.tar.gz", "basel3_1_fixture.tar.gz")
        grid = stellarlib.construct(
            "basel/3.1", database=str(tmp_path), variant="PADOVA2000"
        )
        assert len(grid) == 8
        assert grid.identity.physics_configuration == "calibration PADOVA2000"
