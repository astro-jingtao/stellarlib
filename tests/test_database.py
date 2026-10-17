"""Tests for the database package: blueprint, manifest, scan, build, register.

The build tests run offline against a fake HTTP session: the fake upstream
serves one real-format bulkdl script (listing the fixture spectra of one
metallicity directory), those files, and the r500 wavelength grid — every
other URL of the full blueprint answers 404. That exercises the whole
mirror path (script fetch, in-build expansion, per-file fetch, streaming
sha256, 404 fetch errors, keep verified, nonconforming products never
completed) without the network.
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
from stellarlib.exceptions import DataIntegrityError

DATA = Path(__file__).parent / "data" / "bosz"
TLUSTY_DATA = Path(__file__).parent / "data" / "tlusty"

NODE_NAMES = [
    f"bosz2024_ms_t{t}_g{g}_m{m}_a+0.00_c+0.00_v2_r500_resam.txt.gz"
    for t in (3500, 4000)
    for g in ("+1.0", "+2.0")
    for m in ("+0.00", "-1.00")
]
# one mirror directory holds one metallicity: the m+0.00 half of the set
SOLAR_NAMES = [NODE_NAMES[i] for i in (0, 2, 4, 6)]
WAVE_NAME = "bosz2024_wave_r500.txt"

_MAST = "https://archive.stsci.edu/hlsps/bosz/bosz2024"
SCRIPT_NAME = "hlsp_bosz_bosz2024_sim_r500_m+0.00_v1_bulkdl.sh"
SCRIPT_URL = f"{_MAST}/download_scripts/{SCRIPT_NAME}"
SOLAR_URLS = {
    f"{_MAST}/r500/m+0.00/{name}": (DATA / name).read_bytes()
    for name in SOLAR_NAMES
}
WAVE_URL = f"{_MAST}/wavelength_grids/{WAVE_NAME}"


def _bulkdl_text(names) -> bytes:
    lines = ["#!/bin/sh"]
    for name in names:
        lines.append(
            f'curl -f --create-dirs --output "r500/m+0.00/{name}" '
            f'"{_MAST}/r500/m+0.00/{name}"'
        )
    return ("\n".join(lines) + "\n").encode()


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
    """Serves known URLs; answers 404 for everything else and counts calls."""

    def __init__(self, mapping: dict[str, bytes]):
        self.mapping = mapping
        self.calls: list[str] = []

    def get(self, url: str, stream=True, timeout=None):
        self.calls.append(url)
        if url in self.mapping:
            return FakeResponse(200, self.mapping[url])
        return FakeResponse(404, b"")


def _mirror_session() -> FakeSession:
    mapping = {SCRIPT_URL: _bulkdl_text(SOLAR_NAMES)}
    for name in SOLAR_NAMES:
        mapping[f"{_MAST}/r500/m+0.00/{name}"] = (DATA / name).read_bytes()
    mapping[WAVE_URL] = (DATA / WAVE_NAME).read_bytes()
    return FakeSession(mapping)


class TestBlueprintDesign:
    def test_full_library_declared(self):
        from stellarlib.models.bosz.design import BOSZ_ADAPTER

        designs = list(BOSZ_ADAPTER.designs({"family": "bosz"}))
        assert len(designs) == 113  # 8 broadenings x 14 metallicities + wave
        dirs = [d for d in designs if d.collection != "wavelength_grids"]
        assert len(dirs) == 112
        assert {d.kind for d in designs} == {"official_mirror"}
        assert "bosz/r500/m+0.00" in {d.directory_name for d in designs}
        assert "bosz/rorig/m-2.50" in {d.directory_name for d in designs}

    def test_directory_design_is_phase_one_script(self):
        from stellarlib.models.bosz.design import BOSZ_ADAPTER

        designs = {
            d.directory_name: d
            for d in BOSZ_ADAPTER.designs({"family": "bosz"})
        }
        design = designs["bosz/r500/m+0.00"]
        (part,) = design.parts
        assert part.name == SCRIPT_NAME
        assert part.role == "bulk_manifest"
        assert part.url == SCRIPT_URL

    def test_wave_product_lists_all_seven_grids(self):
        from stellarlib.models.bosz.design import BOSZ_ADAPTER

        designs = {
            d.directory_name: d
            for d in BOSZ_ADAPTER.designs({"family": "bosz"})
        }
        design = designs["bosz/wavelength_grids/all"]
        assert design.collection == "wavelength_grids"
        names = [p.name for p in design.parts]
        assert names == [
            f"bosz2024_wave_r{r}.txt"
            for r in (500, 1000, 2000, 5000, 10000, 20000, 50000)
        ]
        assert {p.role for p in design.parts} == {"wavelength_grid"}

    def test_parse_bulkdl_script(self):
        from stellarlib.models.bosz.design import parse_bulkdl_script

        pairs = parse_bulkdl_script(_bulkdl_text(SOLAR_NAMES).decode())
        assert pairs == tuple(
            (name, f"{_MAST}/r500/m+0.00/{name}") for name in SOLAR_NAMES
        )
        with pytest.raises(ValueError, match="unparseable"):
            parse_bulkdl_script('curl -f --weird "x"\n')
        with pytest.raises(ValueError, match="no files"):
            parse_bulkdl_script("#!/bin/sh\n")

    def test_expand_design_needs_cached_script(self, tmp_path):
        from stellarlib.models.bosz.design import BOSZ_ADAPTER

        designs = {
            d.directory_name: d
            for d in BOSZ_ADAPTER.designs({"family": "bosz"})
        }
        design = designs["bosz/r500/m+0.00"]
        assert BOSZ_ADAPTER.expand_design(design, tmp_path) is design
        (tmp_path / SCRIPT_NAME).write_bytes(_bulkdl_text(SOLAR_NAMES))
        expanded = BOSZ_ADAPTER.expand_design(design, tmp_path)
        assert len(expanded.parts) == 1 + len(SOLAR_NAMES)
        assert expanded.parts[0].name == SCRIPT_NAME
        assert [p.name for p in expanded.parts[1:]] == SOLAR_NAMES
        # the wavelength product expands to itself
        wave = designs["bosz/wavelength_grids/all"]
        assert BOSZ_ADAPTER.expand_design(wave, tmp_path) is wave


class TestManifest:
    def test_roundtrip_and_validation(self, tmp_path):
        manifest = empty_manifest(
            family="bosz",
            release="2024",
            label="x",
            kind="official_mirror",
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
        with pytest.raises(DataIntegrityError, match="schema"):
            read_manifest(tmp_path)


class TestScan:
    def _product(self, tmp_path, name: str) -> Path:
        directory = tmp_path / "bosz" / "r500" / name
        directory.mkdir(parents=True)
        return directory

    def _mirror_manifest(self, label: str) -> dict:
        return empty_manifest(
            family="bosz",
            release="2024",
            label=label,
            kind="official_mirror",
            source_class="official_archive",
            note="",
            format="bosz_resam",
            references=[],
            family_section={
                "mirror": {"broadening": "r500", "metallicity": 0.0}
            },
        )

    def test_two_state_verdicts(self, tmp_path):
        directory = self._product(tmp_path, "m+0.00")
        (directory / SCRIPT_NAME).write_bytes(_bulkdl_text(SOLAR_NAMES))
        good = SOLAR_NAMES[0]
        (directory / good).write_bytes((DATA / good).read_bytes())
        tampered = SOLAR_NAMES[1]
        (directory / tampered).write_bytes(b"tampered")
        manifest = self._mirror_manifest("m+0.00")
        manifest["parts"] = {
            SCRIPT_NAME: {"file": SCRIPT_NAME, "sha256": _sha_str(_bulkdl_text(SOLAR_NAMES))},
            good: {"file": good, "sha256": _sha(DATA / good)},
            tampered: {"file": tampered, "sha256": "0" * 64},
            SOLAR_NAMES[2]: {
                # a recorded failed fetch: an HTTP status is a fact
                # about one attempt, never a publication claim
                "file": SOLAR_NAMES[2],
                "http_status": 404,
                "error": "not found",
            },
        }
        write_manifest(directory, manifest)
        report = scan_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
        )
        by_path = {p.label_path: p for p in report.products}
        product = by_path["bosz/r500/m+0.00"]
        assert product.state == "incomplete"
        kinds = {v.part: v.kind for v in product.verdicts}
        assert kinds[SCRIPT_NAME] == VerdictKind.OK
        assert kinds[good] == VerdictKind.OK
        assert kinds[tampered] == VerdictKind.NONCONFORMING
        assert kinds[SOLAR_NAMES[2]] == VerdictKind.MISSING
        assert kinds[SOLAR_NAMES[3]] == VerdictKind.MISSING
        assert report.missing_part_count() >= 2
        assert len(report.nonconforming_parts()) == 1

    def test_designed_but_absent_product_reports_missing(self, tmp_path):
        report = scan_database(tmp_path, declarations=[{"family": "bosz"}])
        by_path = {p.label_path: p for p in report.products}
        product = by_path["bosz/r500/m+0.00"]
        assert product.state == "incomplete"
        assert all(v.kind == VerdictKind.MISSING for v in product.verdicts)
        assert product.verdicts[0].part == SCRIPT_NAME
        assert product.directory is None

    def test_old_window_schema_rejected(self, tmp_path):
        (tmp_path / "design.json").write_text(
            json.dumps({"schema": "stellarlib-design/1", "windows": []}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="stellarlib-design/1"):
            scan_database(tmp_path)
        with pytest.raises(ValueError, match="stellarlib-design/1"):
            build_database(tmp_path, declarations=[{"family": "bosz"}])

    def test_unregistered_directory_reported(self, tmp_path):
        stray = tmp_path / "stray" / "a" / "b"
        stray.mkdir(parents=True)
        report = scan_database(tmp_path)
        assert "stray/a/b" in report.unregistered


def _sha(path: Path) -> str:
    from stellarlib.integrity import sha256_file

    return sha256_file(path)


def _sha_str(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


class TestBuild:
    def test_dry_run_touches_nothing(self, tmp_path, capsys):
        build_database(
            tmp_path, declarations=[{"family": "bosz"}], dry_run=True
        )
        assert not (tmp_path / "design.json").exists()
        out = capsys.readouterr().out
        assert "bosz/r500/m+0.00" in out
        assert "113 product(s)" in out

    def test_fetches_script_expands_and_completes(self, tmp_path):
        session = _mirror_session()
        db = build_database(
            tmp_path, declarations=[{"family": "bosz"}], session=session
        )
        # the blueprint was written whole
        design = json.loads(
            (tmp_path / "design.json").read_text(encoding="utf-8")
        )
        assert design["schema"] == "stellarlib-design/2"
        assert design["blueprint"] == [{"family": "bosz"}]
        # the mirror product completed: script + 4 spectra
        directory = tmp_path / "bosz" / "r500" / "m+0.00"
        manifest = read_manifest(directory)
        assert manifest["kind"] == "official_mirror"
        assert manifest["family_section"]["mirror"] == {
            "broadening": "r500",
            "metallicity": 0.0,
        }
        assert len(manifest["parts"]) == 1 + len(SOLAR_NAMES)
        entry = manifest["parts"][SOLAR_NAMES[0]]
        assert entry["sha256"] == _sha(DATA / SOLAR_NAMES[0])
        assert entry["url"].endswith(SOLAR_NAMES[0])
        # every other blueprint product recorded its failed script fetch
        other = read_manifest(tmp_path / "bosz" / "r500" / "m+0.25")
        other_script = "hlsp_bosz_bosz2024_sim_r500_m+0.25_v1_bulkdl.sh"
        assert other["parts"][other_script]["http_status"] == 404
        # the wavelength product completed for r500, 404 for the rest
        wave = read_manifest(tmp_path / "bosz" / "wavelength_grids" / "all")
        assert wave["parts"][WAVE_NAME]["sha256"] == _sha(DATA / WAVE_NAME)
        assert len(wave["parts"]) == 7
        # inventory and status were regenerated
        assert (tmp_path / "database.json").exists()
        assert (tmp_path / "README.md").exists()
        assert (tmp_path / "db_status.md").exists()
        # the built mirror opens as a grid: composition selection and
        # cross-product wave pairing included (the metallicity label
        # exists in every broadening, so the collection is pinned too)
        grid = db.grid("bosz/2024", collection="r500", label="m+0.00")
        assert len(grid) == 4
        assert grid.axes["m_h"] == (0.0,)
        notes = grid.sets[0].notes
        assert notes["selection"]["files_total"] == 4
        assert notes["selection"]["vmicro"] == "family"
        assert notes["wave_file"].endswith(WAVE_NAME)

    def test_second_build_keeps_verified(self, tmp_path):
        session = _mirror_session()
        build_database(tmp_path, declarations=[{"family": "bosz"}], session=session)
        session2 = _mirror_session()
        build_database(tmp_path, session=session2)
        assert SCRIPT_URL not in session2.calls
        for name in SOLAR_NAMES:
            assert f"{_MAST}/r500/m+0.00/{name}" not in session2.calls

    def test_404_records_fetch_error_and_retries(self, tmp_path):
        # the script lists a spectrum the fake upstream does not serve yet
        broken = FakeSession(
            {
                SCRIPT_URL: _bulkdl_text(SOLAR_NAMES),
                WAVE_URL: (DATA / WAVE_NAME).read_bytes(),
                **{
                    f"{_MAST}/r500/m+0.00/{n}": (DATA / n).read_bytes()
                    for n in SOLAR_NAMES[:3]
                },
            }
        )
        build_database(
            tmp_path, declarations=[{"family": "bosz"}], session=broken
        )
        missing_name = SOLAR_NAMES[3]
        directory = tmp_path / "bosz" / "r500" / "m+0.00"
        manifest = read_manifest(directory)
        entry = manifest["parts"][missing_name]
        assert entry["http_status"] == 404
        assert "unavailable_upstream" not in entry
        assert "sha256" not in entry
        assert not (directory / missing_name).exists()
        # scan reports the part as missing (the builder's automatic
        # work); no nonconforming verdict is invented from the 404
        report = scan_database(tmp_path)
        mirror = next(
            p for p in report.products if p.label_path == "bosz/r500/m+0.00"
        )
        assert mirror.state == "incomplete"
        kinds = {v.part: v.kind for v in mirror.verdicts}
        assert kinds[missing_name] == VerdictKind.MISSING
        assert all(
            v.kind != VerdictKind.NONCONFORMING for v in mirror.verdicts
        )
        # a rebuild retries the failed part — a 404 says nothing about
        # whether the upstream will serve it tomorrow
        build_database(tmp_path, session=_mirror_session())
        manifest = read_manifest(directory)
        assert (
            manifest["parts"][missing_name]["sha256"]
            == _sha(DATA / missing_name)
        )
        assert (directory / missing_name).exists()


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
        # registered products scan against their own recorded parts
        report = scan_database(tmp_path)
        registered = next(
            p for p in report.products if p.label_path == "bosz/r500/my_window"
        )
        assert registered.state == "complete"

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

    def test_tlusty_registration_accepts_whole_tar(self, tmp_path):
        record = register_local_product(
            tmp_path,
            "tlusty",
            "g_tar",
            [TLUSTY_DATA / "Gvispec_fixture.tar"],
            note="hand-held vispec tar",
        )
        assert record.manifest["release"] == "OSTAR2002"
        assert record.manifest["family_section"]["composition"] == "G"
        db = open_database(tmp_path)
        grid = db.grid("tlusty/OSTAR2002", label="g_tar")
        assert len(grid) == 4


class TestTlustyBlueprint:
    def test_full_tar_inventory_declared(self):
        from stellarlib.models.tlusty.design import TLUSTY_ADAPTER

        designs = list(TLUSTY_ADAPTER.designs({"family": "tlusty"}))
        assert len(designs) == 20  # OSTAR 10 compositions + BSTAR 5 x 2 vmicro
        by_name = {d.directory_name: d for d in designs}
        assert {d.kind for d in designs} == {"official_mirror"}
        (part,) = by_name["tlusty/OSTAR2002/G"].parts
        assert part.name == "Gvispec.tar"
        assert part.role == "archive"
        assert part.url == (
            "https://tlusty.oca.eu/tlusty/Tlusty2002/database/Gvispec.tar"
        )
        assert "tlusty/BSTAR2006/BG_v2" in by_name
        assert "tlusty/BSTAR2006/BT_v10" in by_name

    def test_build_fetches_whole_tars(self, tmp_path):
        session = FakeSession(
            {
                "https://tlusty.oca.eu/tlusty/Tlusty2002/database/Gvispec.tar": (
                    TLUSTY_DATA / "Gvispec_fixture.tar"
                ).read_bytes(),
            }
        )
        db = build_database(
            tmp_path, declarations=[{"family": "tlusty"}], session=session
        )
        manifest = read_manifest(tmp_path / "tlusty" / "OSTAR2002" / "G")
        assert list(manifest["parts"]) == ["Gvispec.tar"]
        assert (
            manifest["parts"]["Gvispec.tar"]["sha256"]
            == _sha(TLUSTY_DATA / "Gvispec_fixture.tar")
        )
        grid = db.grid("tlusty/OSTAR2002", label="G")
        assert len(grid) == 4
        assert grid.sets[0].notes["members"] == {"kind7": 4, "kind17": 4}


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
