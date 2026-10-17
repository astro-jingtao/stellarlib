"""Tests for the database package: design, manifest, scan, build, register.

The build tests run offline against a fake HTTP session serving the real
fixture bytes, so the fetch path (streaming sha256, 404 holes, keep
verified, nonconforming products never completed) is exercised without
the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stellarlib.database.build import build_database, register_local_product
from stellarlib.database.core import open_database
from stellarlib.database.design import VerdictKind
from stellarlib.database.manifest import (
    MANIFEST_SCHEMA,
    empty_manifest,
    read_manifest,
    write_manifest,
)
from stellarlib.database.scan import scan_database
from stellarlib.exceptions import SourceUnavailableError

DATA = Path(__file__).parent / "data" / "bosz"
TLUSTY_DATA = Path(__file__).parent / "data" / "tlusty"

WINDOW = {
    "family": "bosz",
    "label": "test_window",
    "resolution": 500,
    "teffs": [3500, 4000],
    "loggs": [1.0, 2.0],
    "mhs": [0.0, -1.0],
}

NODE_NAMES = [
    f"bosz2024_ms_t{t}_g{g}_m{m}_a+0.00_c+0.00_v2_r500_resam.txt.gz"
    for t in (3500, 4000)
    for g in ("+1.0", "+2.0")
    for m in ("+0.00", "-1.00")
]
WAVE_NAME = "bosz2024_wave_r500.txt"


class FakeResponse:
    def __init__(self, status_code: int, content: bytes):
        self.status_code = status_code
        self._content = content

    def raise_for_status(self) -> None:
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, chunk_size: int):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]


class FakeSession:
    """Serves fixture bytes for known URLs; counts every call."""

    def __init__(self, mapping: dict[str, bytes], missing_status: int = 404):
        self.mapping = mapping
        self.missing_status = missing_status
        self.calls: list[str] = []

    def get(self, url: str, stream=True, timeout=None):
        self.calls.append(url)
        if url in self.mapping:
            return FakeResponse(200, self.mapping[url])
        return FakeResponse(self.missing_status, b"")


def _fixture_session() -> FakeSession:
    mapping = {}
    for name in NODE_NAMES:
        url = (
            "https://archive.stsci.edu/hlsps/bosz/bosz2024/r500/"
            + ("m+0.00" if "_m+0.00_" in name else "m-1.00")
            + f"/{name}"
        )
        mapping[url] = (DATA / name).read_bytes()
    mapping[
        "https://archive.stsci.edu/hlsps/bosz/bosz2024/wavelength_grids/"
        "bosz2024_wave_r500.txt"
    ] = (DATA / WAVE_NAME).read_bytes()
    return FakeSession(mapping)


class TestBoszDesign:
    def test_window_enumerates_family_policy_files(self):
        from stellarlib.models.bosz.design import BOSZ_ADAPTER, family_for

        (design,) = BOSZ_ADAPTER.designs(WINDOW)
        names = {p.name for p in design.parts}
        assert NODE_NAMES[0] in names
        assert WAVE_NAME in names
        assert len(design.parts) == 9
        assert design.collection == "r500"
        assert design.kind == "official_window"
        # family policy: low log g is spherical MARCS v2
        assert family_for(3500, 1.0) == ("ms", 2)
        assert family_for(5000, 4.0) == ("mp", 1)
        assert family_for(9000, 4.0) == ("ap", 2)
        assert family_for(7750, 4.0) == ("ap", 2)
        with pytest.raises(ValueError, match="seam"):
            family_for(3500, 3.25)

    def test_url_uses_metallicity_directory(self):
        from stellarlib.models.bosz.design import BOSZ_ADAPTER

        (design,) = BOSZ_ADAPTER.designs(WINDOW)
        part = next(p for p in design.parts if "_m-1.00_" in p.name)
        assert part.url == (
            "https://archive.stsci.edu/hlsps/bosz/bosz2024/r500/m-1.00/"
            + part.name
        )


class TestManifest:
    def test_roundtrip_and_validation(self, tmp_path):
        manifest = empty_manifest(
            family="bosz",
            release="2024",
            label="x",
            kind="official_window",
            source_class="official_archive",
            note="n",
            format="bosz_resam",
            references=["ref"],
        )
        manifest["parts"]["f.txt"] = {"file": "f.txt", "sha256": "0" * 64}
        write_manifest(tmp_path, manifest)
        again = read_manifest(tmp_path)
        assert again == manifest

    def test_wrong_schema_rejected(self, tmp_path):
        (tmp_path / "MANIFEST.json").write_text(
            json.dumps({"schema": "other", "family": "bosz"}), encoding="utf-8"
        )
        from stellarlib.exceptions import DataIntegrityError

        with pytest.raises(DataIntegrityError, match="schema"):
            read_manifest(tmp_path)


class TestScan:
    def _product(self, tmp_path, name: str) -> Path:
        directory = tmp_path / "bosz" / "r500" / name
        directory.mkdir(parents=True)
        return directory

    def test_two_state_verdicts(self, tmp_path):
        directory = self._product(tmp_path, "w")
        good = NODE_NAMES[0]
        (directory / good).write_bytes((DATA / good).read_bytes())
        tampered = NODE_NAMES[1]
        (directory / tampered).write_bytes(b"tampered")
        manifest = empty_manifest(
            family="bosz",
            release="2024",
            label="w",
            kind="official_window",
            source_class="official_archive",
            note="",
            format="bosz_resam",
            references=[],
            family_section={"window": dict(WINDOW)},
        )
        manifest["parts"] = {
            good: {"file": good, "sha256": _sha(DATA / good)},
            tampered: {
                "file": tampered,
                "sha256": "0" * 64,
            },
            NODE_NAMES[2]: {
                "file": NODE_NAMES[2],
                "unavailable_upstream": True,
                "http_status": 404,
            },
        }
        write_manifest(directory, manifest)
        report = scan_database(tmp_path, declarations=[dict(WINDOW)])
        by_path = {p.label_path: p for p in report.products}
        product = by_path["bosz/r500/w"]
        assert product.state == "incomplete"
        kinds = {v.part: v.kind for v in product.verdicts}
        assert kinds[good] == VerdictKind.OK
        assert kinds[NODE_NAMES[1]] == VerdictKind.NONCONFORMING
        assert kinds[NODE_NAMES[2]] == VerdictKind.NONCONFORMING
        assert kinds[NODE_NAMES[3]] == VerdictKind.MISSING
        assert report.missing_part_count() >= 1
        assert len(report.nonconforming_parts()) == 2

    def test_designed_but_absent_product_reports_all_missing(self, tmp_path):
        report = scan_database(tmp_path, declarations=[dict(WINDOW)])
        product = report.products[0]
        assert product.state == "incomplete"
        assert all(v.kind == VerdictKind.MISSING for v in product.verdicts)
        assert product.directory is None

    def test_unregistered_directory_reported(self, tmp_path):
        stray = tmp_path / "stray" / "a" / "b"
        stray.mkdir(parents=True)
        report = scan_database(tmp_path)
        assert "stray/a/b" in report.unregistered


def _sha(path: Path) -> str:
    from stellarlib.integrity import sha256_file

    return sha256_file(path)


class TestBuild:
    def test_dry_run_touches_nothing(self, tmp_path, capsys):
        build_database(tmp_path, declarations=[dict(WINDOW)], dry_run=True)
        assert not (tmp_path / "design.json").exists()
        assert "test_window" in capsys.readouterr().out

    def test_fetches_records_and_opens(self, tmp_path):
        session = _fixture_session()
        db = build_database(tmp_path, declarations=[dict(WINDOW)], session=session)
        assert len(session.calls) == 9
        product = db.products[0]
        assert product.label == "test_window"
        directory = tmp_path / "bosz" / "r500" / "test_window"
        assert (directory / WAVE_NAME).exists()
        entry = product.manifest["parts"][NODE_NAMES[0]]
        assert entry["sha256"] == _sha(DATA / NODE_NAMES[0])
        assert entry["url"].endswith(NODE_NAMES[0])
        # inventory and status were regenerated
        assert (tmp_path / "database.json").exists()
        assert (tmp_path / "README.md").exists()
        assert (tmp_path / "db_status.md").exists()
        status = (tmp_path / "db_status.json").read_text(encoding="utf-8")
        assert '"complete"' in status
        # the built product opens as a grid
        grid = db.grid("bosz/2024", label="test_window")
        assert len(grid) == 8

    def test_second_build_keeps_verified(self, tmp_path):
        session = _fixture_session()
        build_database(tmp_path, declarations=[dict(WINDOW)], session=session)
        session2 = _fixture_session()
        build_database(tmp_path, session=session2)
        assert session2.calls == []

    def test_404_records_hole_and_blocks_rebuild(self, tmp_path):
        session = _fixture_session()
        build_database(tmp_path, declarations=[dict(WINDOW)], session=session)
        # new declaration adds a point the fake upstream does not serve
        holed = dict(
            WINDOW,
            label="holed",
            teffs=[3500],
            loggs=[1.0],
            mhs=[0.0, -0.5],
        )
        session2 = _fixture_session()
        build_database(tmp_path, declarations=[dict(holed)], session=session2)
        directory = tmp_path / "bosz" / "r500" / "holed"
        manifest = read_manifest(directory)
        hole_entry = next(
            v
            for k, v in manifest["parts"].items()
            if v.get("unavailable_upstream")
        )
        assert hole_entry["http_status"] == 404
        # scan reports the hole as nonconforming (human decision), and a
        # rebuild refuses to complete that product further
        report = scan_database(tmp_path)
        holed_report = next(
            p for p in report.products if p.design.label == "holed"
        )
        assert holed_report.state == "incomplete"
        assert any(
            v.kind == VerdictKind.NONCONFORMING for v in holed_report.verdicts
        )
        session3 = _fixture_session()
        build_database(tmp_path, session=session3)
        assert session3.calls == []


class TestRegisterLocalProduct:
    def test_copy_mode_self_contained(self, tmp_path):
        files = [DATA / n for n in NODE_NAMES] + [DATA / WAVE_NAME]
        record = register_local_product(
            tmp_path,
            "bosz",
            "my_window",
            files,
            note="hand-downloaded subset",
        )
        directory = tmp_path / "bosz" / "r500" / "my_window"
        assert (directory / WAVE_NAME).exists()
        assert not record.manifest["by_reference"]
        entry = record.manifest["parts"][NODE_NAMES[0]]
        assert entry["path"] == NODE_NAMES[0]
        assert entry["sha256"] == _sha(directory / NODE_NAMES[0])
        db = open_database(tmp_path)
        grid = db.grid("bosz/2024", label="my_window")
        assert len(grid) == 8

    def test_reference_mode_keeps_files_in_place(self, tmp_path):
        original = tmp_path / "original"
        original.mkdir()
        files = []
        for n in NODE_NAMES:
            target = original / n
            target.write_bytes((DATA / n).read_bytes())
            files.append(target)
        record = register_local_product(
            tmp_path,
            "bosz",
            "inplace",
            files,
            note="in-place set",
            copy=False,
        )
        assert record.manifest["by_reference"]
        entry = record.manifest["parts"][NODE_NAMES[0]]
        assert Path(entry["path"]).is_absolute()
        db = open_database(tmp_path)
        # wave missing in the reference set: opening the grid must fail
        # loudly (the pairing decision is explicit, never guessed)
        from stellarlib.exceptions import WavelengthGridError

        with pytest.raises(WavelengthGridError, match="wave_path"):
            db.grid("bosz/2024", label="inplace")

    def test_mixed_resolution_refused(self, tmp_path):
        renegade = tmp_path / NODE_NAMES[0].replace("_r500_", "_r1000_")
        renegade.write_bytes((DATA / NODE_NAMES[0]).read_bytes())
        files = [DATA / NODE_NAMES[0], renegade]
        with pytest.raises(ValueError, match="one resolution"):
            register_local_product(tmp_path, "bosz", "bad", files, note="")

    def test_tlusty_registration_infers_release(self, tmp_path):
        files = [
            TLUSTY_DATA / n
            for n in (
                "G30000g300v10.vis.7.gz",
                "G30000g325v10.vis.7.gz",
                "G32500g300v10.vis.7.gz",
                "G32500g325v10.vis.7.gz",
            )
        ]
        record = register_local_product(
            tmp_path, "tlusty", "ostar_g", files, note="extracted tar"
        )
        assert record.manifest["release"] == "OSTAR2002"
        assert record.manifest["family_section"]["composition"] == "G"
        db = open_database(tmp_path)
        grid = db.grid("tlusty/OSTAR2002", label="ostar_g")
        assert len(grid) == 4

    def test_tlusty_fetch_channel_closed(self, tmp_path):
        from stellarlib.models.tlusty.design import TLUSTY_ADAPTER
        from stellarlib.database.design import PartDesign, ProductDesign

        design = ProductDesign(
            family="tlusty",
            collection="OSTAR2002",
            label="x",
            kind="official_window",
            parts=(PartDesign(name="G30000g300v10.vis.7.gz", url=None),),
        )
        with pytest.raises(SourceUnavailableError, match="register_local_product"):
            TLUSTY_ADAPTER.fetch_part(design, design.parts[0], tmp_path)


class TestDatabaseOpen:
    def test_requires_directory(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            open_database(tmp_path / "missing")

    def test_label_pinning(self, tmp_path):
        files = [DATA / NODE_NAMES[0], DATA / WAVE_NAME]
        register_local_product(tmp_path, "bosz", "a", files, note="")
        register_local_product(tmp_path, "bosz", "b", files, note="")
        db = open_database(tmp_path)
        with pytest.raises(ValueError, match="pin one"):
            db.grid("bosz/2024")
        assert db.grid("bosz/2024", label="a") is not None
