"""Database transfer tests: merge (whole-product, part-completion,
scoped signature-cache invalidation, conflicts, by-reference) and the
export/verify/rsync plan — offline, on registered stelib fixture
products (real upstream bytes; see tests/data/stelib/README.md)."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import shutil

from file_quick_signature.key import build_key
import pytest

import stellarlib
from stellarlib.database.build import register_local_product
from stellarlib.database.transfer import merge as merge_module
from stellarlib.database.transfer.export import (
    export_database,
    rsync_command,
    verify_transfer,
)
from stellarlib.database.transfer.merge import merge_database

DATA = Path(__file__).parent / "data" / "stelib"
COR_A = [
    DATA / "cor_sp" / "HD002857_V3.2.fits",
    DATA / "cor_sp" / "HD006268_V3.2.fits",
    DATA / "cor_sp" / "HD053929lp_V3.2.fits",
]
COR_B = [
    DATA / "cor_sp" / "HD015318_V3.2.fits",
    DATA / "cor_sp" / "HD005820_V3.2.fits",
]
TABLE = DATA / "table.dat"


def _db(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _register(db: Path, label: str, files: list[Path], **kwargs):
    return register_local_product(
        db, "stelib", label, [*files, TABLE], note="transfer fixture", **kwargs
    )


def _cache_entries(db: Path) -> dict:
    path = db / "signature_cache.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _plant_bogus_entry(db: Path, rel_path: str) -> str:
    """A signature-cache entry for rel_path claiming a wrong hash (the
    stale claim a no-verify merge must invalidate)."""

    key = build_key(rel_path, 1, 1)
    entries = _cache_entries(db)
    entries[key] = "0" * 64
    (db / "signature_cache.json").write_text(
        json.dumps(entries), encoding="utf-8"
    )
    return key


class TestMergeWholeProduct:
    def test_whole_product_into_empty_target(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "hand", COR_A)
        tgt = _db(tmp_path / "tgt")
        report = merge_database(src, tgt)
        assert report.merged_products == ["stelib/library/hand"]
        assert not report.conflicts
        assert (tgt / "stelib/library/hand/HD002857_V3.2.fits").is_file()
        assert (tgt / "stelib/library/hand/MANIFEST.json").is_file()
        assert (tgt / "db_status.md").is_file()  # regenerated, never copied

    def test_products_under_a_prefix_travel_together(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        _register(src, "b", COR_B)
        tgt = _db(tmp_path / "tgt")
        report = merge_database(src, tgt)
        assert sorted(report.merged_products) == [
            "stelib/library/a", "stelib/library/b",
        ]

    def test_already_present_is_kept_not_copied(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "hand", COR_A)
        tgt = _db(tmp_path / "tgt")
        merge_database(src, tgt)
        status = (tgt / "db_status.md").stat().st_mtime_ns
        again = merge_database(src, tgt)
        assert again.kept_products == ["stelib/library/hand"]
        assert not again.changed
        assert (tgt / "db_status.md").stat().st_mtime_ns == status

    def test_select_restricts_the_merge(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        _register(src, "b", COR_B)
        tgt = _db(tmp_path / "tgt")
        report = merge_database(src, tgt, select=["stelib/library/b"])
        assert report.merged_products == ["stelib/library/b"]
        assert not (tgt / "stelib/library/a").exists()

    def test_select_matching_nothing_is_an_error(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        with pytest.raises(ValueError, match="match no declared product"):
            merge_database(src, _db(tmp_path / "tgt"), select=["stelib/nope"])

    def test_source_needing_review_does_not_propagate(self, tmp_path):
        src = _db(tmp_path / "src")
        record = _register(src, "hand", COR_A)
        target = Path(record.directory) / "HD002857_V3.2.fits"
        target.write_bytes(b"corrupted")  # manifest still records the truth
        tgt = _db(tmp_path / "tgt")
        report = merge_database(src, tgt)
        assert report.source_needs_review == ["stelib/library/hand"]
        assert not report.changed

    def test_same_database_and_missing_target_refused(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "hand", COR_A)
        with pytest.raises(ValueError, match="same database"):
            merge_database(src, src)
        with pytest.raises(NotADirectoryError):
            merge_database(src, tmp_path / "not-there")

    def test_by_reference_product_never_merges(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "external", COR_A, copy=False)
        tgt = _db(tmp_path / "tgt")
        report = merge_database(src, tgt)
        assert report.source_by_reference == ["stelib/library/external"]
        assert not report.changed


class TestScopedCacheInvalidation:
    def test_no_verify_whole_product_invalidates_only_its_prefix(
        self, tmp_path
    ):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        _register(src, "b", COR_B)
        tgt = _db(tmp_path / "tgt")
        _register(tgt, "b", COR_B)  # target holds b; its cache learns b
        before_b = {
            k for k in _cache_entries(tgt) if "/library/b/" in k
        }
        assert before_b
        bogus_a = _plant_bogus_entry(
            tgt, "stelib/library/a/HD002857_V3.2.fits"
        )
        report = merge_database(src, tgt)
        assert report.merged_products == ["stelib/library/a"]
        assert report.invalidated_cache_entries == 1
        entries = _cache_entries(tgt)
        assert bogus_a not in entries  # the migrated portion re-verifies
        assert {
            k for k in entries if "/library/b/" in k
        } == before_b  # the untouched portion keeps its evidence

    def test_part_completion_invalidates_exactly_the_copied_part(
        self, tmp_path
    ):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        tgt = _db(tmp_path / "tgt")
        _register(tgt, "a", COR_A[:2])  # one part short of the source
        bogus = _plant_bogus_entry(
            tgt, "stelib/library/a/HD053929lp_V3.2.fits"
        )
        kept_before = {
            k for k in _cache_entries(tgt)
            if "HD002857" in k or "HD006268" in k
        }
        report = merge_database(src, tgt)
        assert report.completed_parts == [
            ("stelib/library/a", "HD053929lp_V3.2.fits")
        ]
        assert report.invalidated_cache_entries == 1
        assert bogus not in _cache_entries(tgt)
        assert {
            k for k in _cache_entries(tgt)
            if "HD002857" in k or "HD006268" in k
        } == kept_before
        # the completed part's evidence arrived with the manifest entry
        manifest = json.loads(
            (tgt / "stelib/library/a/MANIFEST.json").read_text(encoding="utf-8")
        )
        assert "HD053929lp_V3.2.fits" in manifest["parts"]

    def test_conflicting_part_records_target_untouched(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        tgt = _db(tmp_path / "tgt")
        tampered = tmp_path / "tampered"
        tampered.mkdir()
        for path in [*COR_A, TABLE]:
            shutil.copy2(path, tampered / path.name)
        (tampered / "HD002857_V3.2.fits").write_bytes(b"different bytes")
        _register(tgt, "a", [tampered / p.name for p in COR_A])
        manifest_before = (
            tgt / "stelib/library/a/MANIFEST.json"
        ).read_text(encoding="utf-8")
        report = merge_database(src, tgt)
        assert len(report.conflicts) == 1
        assert report.conflicts[0].part == "HD002857_V3.2.fits"
        assert not report.changed
        assert (
            tgt / "stelib/library/a/MANIFEST.json"
        ).read_text(encoding="utf-8") == manifest_before


class TestVerifyingMerge:
    def test_verify_hashes_and_seeds_the_cache(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "hand", COR_A)
        tgt = _db(tmp_path / "tgt")
        report = merge_database(src, tgt, verify=True)
        assert report.merged_products == ["stelib/library/hand"]
        assert report.invalidated_cache_entries == 0
        seeded = {
            k for k in _cache_entries(tgt) if "/library/hand/" in k
        }
        assert len(seeded) >= 3  # every hashed part, remembered

    def test_verify_aborts_on_sha_mismatch(self, tmp_path, monkeypatch):

        src = _db(tmp_path / "src")
        _register(src, "hand", COR_A)
        monkeypatch.setattr(
            merge_module, "sha256_file", lambda path: "f" * 64
        )
        with pytest.raises(ValueError, match="recorded sha256"):
            merge_database(_db(tmp_path / "src"), _db(tmp_path / "tgt"),
                           verify=True)
        assert not (_db(tmp_path / "tgt") / "stelib").exists()
        assert not (_db(tmp_path / "tgt") / ".staging").exists()


class TestExportVerifyRsync:
    def test_export_writes_the_plan_and_tables(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        result = export_database(src, select=["stelib/library/a"])
        outdir = src / "export" / "selection"
        for name in ("transfer.json", "files.rsync", "files.txt", "README.md"):
            assert (outdir / name).is_file()
        assert result.products == 1
        plan = json.loads(
            (outdir / "transfer.json").read_text(encoding="utf-8")
        )
        (entry,) = plan["products"]
        assert entry["product"] == "stelib/library/a"
        assert plan["totals"]["files"] == len(entry["files"])
        assert any(f["sha256"] for f in entry["files"])
        assert (outdir / "files.rsync").read_text().splitlines() == [
            "stelib/library/a", "export/selection/transfer.json",
        ]

    def test_export_name_collision_refused(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        export_database(src, select=["stelib/library/a"])
        with pytest.raises(FileExistsError):
            export_database(src, select=["stelib/library/a"])

    def test_verify_transfer_round_trip_and_corruption(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        export_database(src, select=["stelib/library/a"])
        intact = shutil.copytree(src, tmp_path / "intact")
        ok = verify_transfer(intact, intact / "export/selection/transfer.json")
        assert ok.ok
        assert ok.checked_files > 0
        corrupted = shutil.copytree(src, tmp_path / "corrupted")
        part = corrupted / "stelib/library/a/HD002857_V3.2.fits"
        part.write_bytes(part.read_bytes()[:-8] + b"deadbeef")
        bad = verify_transfer(
            corrupted, corrupted / "export/selection/transfer.json"
        )
        assert not bad.ok
        assert "stelib/library/a/HD002857_V3.2.fits" in bad.sha_mismatches
        part.unlink()
        gone = verify_transfer(
            corrupted, corrupted / "export/selection/transfer.json"
        )
        assert "stelib/library/a/HD002857_V3.2.fits" in gone.missing

    def test_rsync_command_assembles_the_transport(self, tmp_path):
        src = _db(tmp_path / "src")
        _register(src, "a", COR_A)
        result = export_database(src, select=["stelib/library/a"])
        command = rsync_command(src, result.outdir, "host:/dst")
        assert "--files-from=" in command
        assert "files.rsync" in command
        assert str(src) in command
        assert "host:/dst" in command

    def test_transfer_api_lives_in_concrete_modules(self):
        # the package root exports only the whole-library entry verbs;
        # the transfer verbs stay in their concrete modules, reached by
        # deep absolute import
        for module_name, names in (
            ("stellarlib.database.transfer.export",
             ("export_database", "verify_transfer",
              "rsync_command", "run_rsync")),
            ("stellarlib.database.transfer.merge", ("merge_database",)),
        ):
            module = importlib.import_module(module_name)
            for name in names:
                assert hasattr(module, name), (module_name, name)
                assert not hasattr(stellarlib, name), name
