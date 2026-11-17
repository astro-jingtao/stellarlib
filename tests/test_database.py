"""Tests for the database package: blueprint, manifest, scan, build, verify,
register.

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
import threading
from pathlib import Path

import pytest

from stellarlib.database.build import (
    build_database,
    register_local_product,
    verify_database,
)
from stellarlib.database.core import open_database
from stellarlib.database.design import (
    PartDesign,
    VerdictKind,
    classify_part_generic,
)
from stellarlib.database.manifest import (
    MANIFEST_SCHEMA,
    empty_manifest,
    read_manifest,
    write_manifest,
)
from stellarlib.database.scan import scan_database
from stellarlib.database.signature_cache import SignatureCache
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

# an rorig directory additionally holds the lineid product line: two
# noresam spectra per node (v0 and the family vmicro), a lineid file per
# v0 spectrum, and the directory's own Apache index listing all of them
RORIG_SCRIPT_NAME = "hlsp_bosz_bosz2024_sim_rorig_m+0.00_v1_bulkdl.sh"
RORIG_SCRIPT_URL = f"{_MAST}/download_scripts/{RORIG_SCRIPT_NAME}"
RORIG_INDEX_URL = f"{_MAST}/rorig/m+0.00/"
RORIG_NODE_NAMES = [
    f"bosz2024_ms_t3500_g{g}_m+0.00_a+0.00_c+0.00_v{v}_rorig_noresam.txt.gz"
    for g in ("+1.0", "+2.0")
    for v in (0, 2)
]
LINEID_NAMES = [
    n.replace("_noresam.txt.gz", "_lineid.txt.gz")
    for n in RORIG_NODE_NAMES
    if "_v0_" in n
]


def _bulkdl_text(names) -> bytes:
    lines = ["#!/bin/sh"]
    for name in names:
        lines.append(
            f'curl -f --create-dirs --output "r500/m+0.00/{name}" '
            f'"{_MAST}/r500/m+0.00/{name}"'
        )
    return ("\n".join(lines) + "\n").encode()


def _rorig_script_text(names) -> bytes:
    lines = ["#!/bin/sh"]
    for name in names:
        lines.append(
            f'curl -f --create-dirs --output "rorig/m+0.00/{name}" '
            f'"{_MAST}/rorig/m+0.00/{name}"'
        )
    return ("\n".join(lines) + "\n").encode()


def _rorig_index_html(lineid_names, spectrum_names) -> bytes:
    links = ["/hlsps/bosz/bosz2024/rorig/"] + lineid_names + spectrum_names
    body = "\n".join(
        f'<li><a href="{name}"> {name.rsplit("/", 1)[-1]}</a></li>'
        for name in links
    )
    html = (
        "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 3.2 Final//EN\">\n"
        "<html><head><title>Index of /hlsps/bosz/bosz2024/rorig/m+0.00"
        "</title></head><body>\n"
        f"<ul>{body}</ul>\n</body></html>\n"
    )
    return html.encode()


def _gz_text(rows) -> bytes:
    import gzip

    return gzip.compress(("\n".join(rows) + "\n").encode())


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

    def test_rorig_design_also_declares_the_directory_index(self):
        from stellarlib.models.bosz.design import BOSZ_ADAPTER

        designs = {
            d.directory_name: d
            for d in BOSZ_ADAPTER.designs({"family": "bosz"})
        }
        design = designs["bosz/rorig/m+0.00"]
        assert [p.role for p in design.parts] == [
            "bulk_manifest",
            "directory_index",
        ]
        index = design.parts[1]
        assert index.name == "directory_index.html"
        assert index.url == f"{_MAST}/rorig/m+0.00/"

    def test_parse_directory_index(self):
        from stellarlib.models.bosz.design import parse_directory_index

        html = _rorig_index_html(LINEID_NAMES, RORIG_NODE_NAMES).decode()
        pairs = parse_directory_index(html, RORIG_INDEX_URL)
        assert pairs == tuple(
            (name, f"{RORIG_INDEX_URL}{name}") for name in LINEID_NAMES
        )
        with pytest.raises(ValueError, match="no known BOSZ product slot"):
            parse_directory_index(
                '<li><a href="unexpected.dat">x</a></li>', RORIG_INDEX_URL
            )
        with pytest.raises(ValueError, match="no lineid files"):
            parse_directory_index(
                '<li><a href="x_r500_resam.txt.gz">x</a></li>', RORIG_INDEX_URL
            )

    def test_expand_design_rorig_enumerates_lineid(self, tmp_path):
        from stellarlib.models.bosz.design import BOSZ_ADAPTER

        designs = {
            d.directory_name: d
            for d in BOSZ_ADAPTER.designs({"family": "bosz"})
        }
        design = designs["bosz/rorig/m+0.00"]
        (tmp_path / RORIG_SCRIPT_NAME).write_bytes(
            _rorig_script_text(RORIG_NODE_NAMES)
        )
        # the script alone enumerates the spectra; lineid wait for the index
        spectra_only = BOSZ_ADAPTER.expand_design(design, tmp_path)
        assert len(spectra_only.parts) == 2 + len(RORIG_NODE_NAMES)
        assert not any(p.role == "lineid" for p in spectra_only.parts)
        (tmp_path / "directory_index.html").write_bytes(
            _rorig_index_html(LINEID_NAMES, RORIG_NODE_NAMES)
        )
        expanded = BOSZ_ADAPTER.expand_design(design, tmp_path)
        assert len(expanded.parts) == (
            2 + len(RORIG_NODE_NAMES) + len(LINEID_NAMES)
        )
        lineid_parts = [p for p in expanded.parts if p.role == "lineid"]
        assert [p.name for p in lineid_parts] == LINEID_NAMES
        assert all(p.url == f"{RORIG_INDEX_URL}{p.name}" for p in lineid_parts)
        # re-expansion is idempotent
        again = BOSZ_ADAPTER.expand_design(expanded, tmp_path)
        assert again.parts == expanded.parts

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


class TestManifestIntegrityVerdict:
    """classify_part_generic judges a present part with recorded
    evidence against BOTH recorded quantities: a size mismatch is the
    verdict on its own (identical bytes cannot have different sizes);
    a matching size still pays the sha256 comparison — never skipped
    on size equality alone — served through the signature cache when
    one is given."""

    def _classify(self, tmp_path, *, size_entry, payload=None, part=None,
                  cache=None):
        part = part or SOLAR_NAMES[0]
        payload = payload if payload is not None else (
            DATA / part).read_bytes()
        (tmp_path / part).write_bytes(payload)
        entry = {"file": part, "sha256": _sha(DATA / part)}
        if size_entry == "match":
            entry["size_bytes"] = (DATA / part).stat().st_size
        elif size_entry == "stale":
            entry["size_bytes"] = (DATA / part).stat().st_size + 1
        elif size_entry is not None:
            raise AssertionError(size_entry)
        return classify_part_generic(
            design=None,
            part=PartDesign(name=part),
            directory=tmp_path,
            manifest={"parts": {part: entry}},
            cache=cache,
        )

    def test_size_mismatch_is_nonconforming_without_hashing(
            self, tmp_path, monkeypatch):
        import stellarlib.database.design as design_mod

        def _forbidden(*args, **kwargs):
            raise AssertionError("a size mismatch needs no hash read")

        monkeypatch.setattr(design_mod, "sha256_file", _forbidden)
        verdict = self._classify(tmp_path, size_entry="stale")
        assert verdict.kind == VerdictKind.NONCONFORMING
        assert "size" in verdict.reasons[0]

    def test_matching_size_still_pays_the_sha256_comparison(
            self, tmp_path, monkeypatch):
        import stellarlib.database.design as design_mod

        counted = {"n": 0}
        real = design_mod.sha256_file

        def _counting(path, *args, **kwargs):
            counted["n"] += 1
            return real(path, *args, **kwargs)

        monkeypatch.setattr(design_mod, "sha256_file", _counting)
        verdict = self._classify(tmp_path, size_entry="match")
        assert verdict.kind == VerdictKind.OK
        assert counted["n"] == 1

    def test_tampered_file_with_different_size(self, tmp_path):
        verdict = self._classify(tmp_path, size_entry="match",
                                 payload=b"tampered")
        assert verdict.kind == VerdictKind.NONCONFORMING
        assert "size" in verdict.reasons[0]

    def test_tampered_file_with_same_size(self, tmp_path):
        original = (DATA / SOLAR_NAMES[0]).read_bytes()
        verdict = self._classify(
            tmp_path, size_entry="match",
            payload=bytes([original[0] ^ 1]) + original[1:],
        )
        assert verdict.kind == VerdictKind.NONCONFORMING
        assert "sha256" in verdict.reasons[0]

    def test_record_without_size_hashes(self, tmp_path):
        # legacy entries (no size_bytes) keep the sha256-only semantics
        verdict = self._classify(tmp_path, size_entry=None)
        assert verdict.kind == VerdictKind.OK

    def test_cache_serves_the_hash_of_an_unchanged_file(
            self, tmp_path, monkeypatch):
        import stellarlib.database.signature_cache as sig_mod

        def _forbidden(*args, **kwargs):
            raise AssertionError("an unchanged file must not be re-read")

        monkeypatch.setattr(sig_mod, "sha256_file", _forbidden)
        part = SOLAR_NAMES[0]
        path = tmp_path / part
        path.write_bytes((DATA / part).read_bytes())
        entry = {
            "file": part,
            "sha256": _sha(DATA / part),
            "size_bytes": (DATA / part).stat().st_size,
        }
        cache = SignatureCache(tmp_path)
        cache.remember(path, _sha(DATA / part))
        verdict = classify_part_generic(
            design=None,
            part=PartDesign(name=part),
            directory=tmp_path,
            manifest={"parts": {part: entry}},
            cache=cache,
        )
        assert verdict.kind == VerdictKind.OK

    def test_absent_part_is_missing_regardless_of_records(
            self, tmp_path):
        part = SOLAR_NAMES[1]
        verdict = classify_part_generic(
            design=None,
            part=PartDesign(name=part),
            directory=tmp_path,
            manifest={"parts": {}},
        )
        assert verdict.kind == VerdictKind.MISSING


class TestSignatureCache:
    """The database's quick-signature sha256 cache: a file that did
    not change since its last hash is verified with one stat on every
    later scan. The store is derived data — delete it to force a full
    re-verify — and its keys are relative to the database root, so a
    within-machine move or alias keeps it valid."""

    def _built_db(self, tmp_path):
        build_database(
            tmp_path, declarations=[{"family": "bosz"}],
            session=_mirror_session(),
        )

    def _count_reads(self, monkeypatch):
        import stellarlib.database.signature_cache as sig_mod

        counted = {"n": 0}
        real = sig_mod.sha256_file

        def _counting(path, *args, **kwargs):
            counted["n"] += 1
            return real(path, *args, **kwargs)

        monkeypatch.setattr(sig_mod, "sha256_file", _counting)
        return counted

    def _mirror(self, report):
        return next(
            p for p in report.products
            if p.label_path == "bosz/r500/m+0.00"
        )

    def test_fetch_time_hashes_feed_the_cache(self, tmp_path, monkeypatch):
        self._built_db(tmp_path)
        assert (tmp_path / "signature_cache.json").is_file()
        reads = self._count_reads(monkeypatch)
        report = scan_database(tmp_path)
        assert reads["n"] == 0
        assert self._mirror(report).state == "complete"

    def test_cache_holds_keys_relative_to_the_root(self, tmp_path):
        self._built_db(tmp_path)
        part = tmp_path / "bosz" / "r500" / "m+0.00" / SOLAR_NAMES[0]
        st = part.stat()
        store = json.loads(
            (tmp_path / "signature_cache.json").read_text(encoding="utf-8")
        )
        expected_key = f"bosz/r500/m+0.00/{SOLAR_NAMES[0]}_{st.st_size}_{st.st_mtime_ns}"
        assert store[expected_key] == _sha(DATA / SOLAR_NAMES[0])
        assert not any(str(tmp_path) in key for key in store)

    def test_deleted_cache_is_rebuilt_by_a_scan(
            self, tmp_path, monkeypatch):
        self._built_db(tmp_path)
        (tmp_path / "signature_cache.json").unlink()
        reads = self._count_reads(monkeypatch)
        scan_database(tmp_path)
        assert reads["n"] > 0
        assert (tmp_path / "signature_cache.json").is_file()

    def test_moving_the_database_keeps_the_cache_valid(
            self, tmp_path, monkeypatch):
        self._built_db(tmp_path)
        moved = tmp_path.parent / (tmp_path.name + "-moved")
        tmp_path.rename(moved)
        reads = self._count_reads(monkeypatch)
        report = scan_database(moved)
        assert reads["n"] == 0
        assert self._mirror(report).state == "complete"

    def test_symlinked_database_alias_keeps_keys_stable(
            self, tmp_path, monkeypatch):
        import os

        self._built_db(tmp_path)
        alias = tmp_path.parent / (tmp_path.name + "-alias")
        os.symlink(tmp_path, alias)
        try:
            reads = self._count_reads(monkeypatch)
            report = scan_database(alias)
            assert reads["n"] == 0
            assert self._mirror(report).state == "complete"
        finally:
            alias.unlink()

    def test_dry_run_verifies_but_never_writes_the_cache(
            self, tmp_path, monkeypatch):
        self._built_db(tmp_path)
        (tmp_path / "signature_cache.json").unlink()
        reads = self._count_reads(monkeypatch)
        build_database(tmp_path, dry_run=True)
        assert reads["n"] > 0  # verdicts stay strict without a cache
        assert not (tmp_path / "signature_cache.json").exists()

    def test_quick_signature_trust_boundary(self, tmp_path, monkeypatch):
        # the documented boundary: the cache answers by quick
        # signature, so a same-size edit with a restored mtime is
        # beyond its detection — deleting the cache forces the full
        # re-verify that catches it
        import os

        self._built_db(tmp_path)
        part = tmp_path / "bosz" / "r500" / "m+0.00" / SOLAR_NAMES[0]
        st = part.stat()
        data = bytearray(part.read_bytes())
        data[0] ^= 1
        part.write_bytes(bytes(data))
        os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns))
        reads = self._count_reads(monkeypatch)
        assert self._mirror(scan_database(tmp_path)).state == "complete"
        assert reads["n"] == 0  # served from the cache: undetected
        (tmp_path / "signature_cache.json").unlink()
        report = scan_database(tmp_path)
        assert self._mirror(report).state == "incomplete"
        verdict = next(
            v for v in self._mirror(report).verdicts
            if v.part == SOLAR_NAMES[0]
        )
        assert verdict.kind == VerdictKind.NONCONFORMING

    def test_registration_feeds_the_cache(self, tmp_path, monkeypatch):
        register_local_product(
            tmp_path, "bosz", "reg",
            files=[DATA / name for name in SOLAR_NAMES[:2]],
            note="registered subset",
        )
        reads = self._count_reads(monkeypatch)
        report = scan_database(tmp_path)
        assert reads["n"] == 0
        product = next(
            p for p in report.products if p.label_path == "bosz/r500/reg"
        )
        assert product.state == "complete"


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

    def test_build_fetches_rorig_mirror_with_lineid(self, tmp_path):
        import hashlib

        lineid_bytes = _gz_text(["507.718 HeI 1.1", "522.213 HeI 6.1"])
        noresam_bytes = _gz_text(["1000.0 1.0 1.0", "1001.0 0.9 1.0"])
        session = FakeSession(
            {
                RORIG_SCRIPT_URL: _rorig_script_text(RORIG_NODE_NAMES),
                RORIG_INDEX_URL: _rorig_index_html(
                    LINEID_NAMES, RORIG_NODE_NAMES
                ),
                **{
                    f"{RORIG_INDEX_URL}{name}": lineid_bytes
                    for name in LINEID_NAMES
                },
                **{
                    f"{RORIG_INDEX_URL}{name}": noresam_bytes
                    for name in RORIG_NODE_NAMES
                },
            }
        )
        build_database(
            tmp_path, declarations=[{"family": "bosz"}], session=session
        )
        directory = tmp_path / "bosz" / "rorig" / "m+0.00"
        manifest = read_manifest(directory)
        # the directory index itself arrived and is checksum-recorded
        index_entry = manifest["parts"]["directory_index.html"]
        assert index_entry["url"] == RORIG_INDEX_URL
        assert (
            index_entry["sha256"]
            == hashlib.sha256(
                _rorig_index_html(LINEID_NAMES, RORIG_NODE_NAMES)
            ).hexdigest()
        )
        # every enumerated file arrived: spectra from the script, lineid
        # from the index
        assert (
            len(manifest["parts"])
            == 2 + len(RORIG_NODE_NAMES) + len(LINEID_NAMES)
        )
        for name in LINEID_NAMES:
            entry = manifest["parts"][name]
            assert entry["sha256"] == hashlib.sha256(lineid_bytes).hexdigest()
            assert (directory / name).read_bytes() == lineid_bytes
        # the scan judges the completed mirror against the full design
        report = scan_database(tmp_path)
        mirror = next(
            p for p in report.products if p.label_path == "bosz/rorig/m+0.00"
        )
        assert mirror.state == "complete"

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


class TestBuildSelect:
    def test_select_limits_one_round_to_whole_products(self, tmp_path):
        session = _mirror_session()
        build_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
            session=session,
            select=["bosz/r500/m+0.00", "bosz/wavelength_grids/*"],
        )
        # the selected products were built (r500 m+0.00 + the wave
        # product's r500 file; the other six wave files 404 by design
        # of the fake upstream)
        assert (tmp_path / "bosz" / "r500" / "m+0.00" / "MANIFEST.json").exists()
        assert (
            tmp_path / "bosz" / "wavelength_grids" / "all" / "MANIFEST.json"
        ).exists()
        # unselected products were not even attempted
        assert not (tmp_path / "bosz" / "r500" / "m+0.25").exists()
        assert not (tmp_path / "bosz" / "rorig").exists()
        # the blueprint still declares the full library
        design = json.loads((tmp_path / "design.json").read_text(encoding="utf-8"))
        assert design["blueprint"] == [{"family": "bosz"}]
        # a later unrestricted run completes the rest (retry semantics
        # unchanged by rounds)
        session2 = _mirror_session()
        build_database(tmp_path, session=session2)
        other = read_manifest(tmp_path / "bosz" / "r500" / "m+0.25")
        other_script = "hlsp_bosz_bosz2024_sim_r500_m+0.25_v1_bulkdl.sh"
        assert other["parts"][other_script]["http_status"] == 404

    def test_select_unknown_pattern_fails_loudly(self, tmp_path):
        with pytest.raises(ValueError, match="bosz/nope/\\*"):
            build_database(
                tmp_path,
                declarations=[{"family": "bosz"}],
                dry_run=True,
                select=["bosz/nope/*"],
            )

    def test_select_empty_list_refused(self, tmp_path):
        with pytest.raises(ValueError, match="empty"):
            build_database(
                tmp_path, declarations=[{"family": "bosz"}], select=[]
            )


class _RaisingSession(FakeSession):
    """Serves the mapped URLs; raises for the URLs in ``broken``."""

    def __init__(self, mapping, broken, calls):
        super().__init__(mapping)
        self._broken = set(broken)
        self._calls = calls  # shared across worker sessions

    def get(self, url, stream=True, timeout=None):
        self._calls.append(url)
        if url in self._broken:
            import requests

            raise requests.ConnectionError("simulated transport failure")
        if url in self.mapping:
            return FakeResponse(200, self.mapping[url])
        return FakeResponse(404, b"")


class _BlockingSession(_RaisingSession):
    """``get`` blocks for the URLs in ``block`` until ``release`` is set,
    and a broken URL raises only after a blocked fetch is provably inside
    ``get`` (``entered``) — pins one fetch in flight across a sibling
    failure, making the drain contract deterministically observable.
    ``order`` records the enter/fail/resume sequence for assertions."""

    def __init__(self, mapping, broken, calls, block, release, entered, order):
        super().__init__(mapping, broken, calls)
        self._block = set(block)
        self._release = release
        self._entered = entered
        self._order = order

    def get(self, url, stream=True, timeout=None):
        if url in self._block:
            self._order.append(("enter", url.rsplit("/", 1)[-1]))
            self._entered.set()
            self._release.wait(timeout=30)
            self._order.append(("resume", url.rsplit("/", 1)[-1]))
            return super().get(url, stream=stream, timeout=timeout)
        if url in self._broken:
            self._calls.append(url)
            # only fail once the blocked fetch is inside get(): the
            # failure then provably strikes a batch with work in flight
            self._entered.wait(timeout=30)
            self._release.set()
            self._order.append(("fail", url.rsplit("/", 1)[-1]))
            import requests

            raise requests.ConnectionError("simulated transport failure")
        return super().get(url, stream=stream, timeout=timeout)


class TestParallelFetch:
    def _factory(self, mapping, broken=None):
        calls: list = []

        def make():
            return _RaisingSession(mapping, broken or (), calls)

        return make, calls

    def test_parallel_build_completes_the_product(self, tmp_path):
        mapping = {SCRIPT_URL: _bulkdl_text(SOLAR_NAMES)}
        for name in SOLAR_NAMES:
            mapping[f"{_MAST}/r500/m+0.00/{name}"] = (DATA / name).read_bytes()
        mapping[WAVE_URL] = (DATA / WAVE_NAME).read_bytes()
        factory, _calls = self._factory(mapping)
        db = build_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
            select=["bosz/r500/m+0.00", "bosz/wavelength_grids/*"],
            workers=3,
            session_factory=factory,
        )
        directory = tmp_path / "bosz" / "r500" / "m+0.00"
        manifest = read_manifest(directory)
        assert len(manifest["parts"]) == 1 + len(SOLAR_NAMES)
        for name in SOLAR_NAMES:
            entry = manifest["parts"][name]
            assert entry["sha256"] == _sha(DATA / name)
            assert entry["url"].endswith(name)
        report = scan_database(tmp_path)
        mirror = next(
            p for p in report.products if p.label_path == "bosz/r500/m+0.00"
        )
        assert mirror.state == "complete"
        grid = db.grid("bosz/2024", collection="r500", label="m+0.00")
        assert len(grid) == len(SOLAR_NAMES)

    def test_parallel_rorig_expands_after_manifest_batch(self, tmp_path):
        import hashlib

        lineid_bytes = _gz_text(["507.718 HeI 1.1", "522.213 HeI 6.1"])
        noresam_bytes = _gz_text(["1000.0 1.0 1.0", "1001.0 0.9 1.0"])
        mapping = {
            RORIG_SCRIPT_URL: _rorig_script_text(RORIG_NODE_NAMES),
            RORIG_INDEX_URL: _rorig_index_html(LINEID_NAMES, RORIG_NODE_NAMES),
            **{
                f"{RORIG_INDEX_URL}{name}": lineid_bytes
                for name in LINEID_NAMES
            },
            **{
                f"{RORIG_INDEX_URL}{name}": noresam_bytes
                for name in RORIG_NODE_NAMES
            },
        }
        factory, _calls = self._factory(mapping)
        build_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
            select=["bosz/rorig/m+0.00"],
            workers=2,
            session_factory=factory,
        )
        directory = tmp_path / "bosz" / "rorig" / "m+0.00"
        manifest = read_manifest(directory)
        assert (
            len(manifest["parts"])
            == 2 + len(RORIG_NODE_NAMES) + len(LINEID_NAMES)
        )
        report = scan_database(tmp_path)
        mirror = next(
            p for p in report.products if p.label_path == "bosz/rorig/m+0.00"
        )
        assert mirror.state == "complete"

    def test_parallel_failure_drains_batch_and_raises(self, tmp_path):
        # half the spectra fail with a transport error: the run fails
        # loudly (same contract as the sequential path), the batch
        # drains, and every part that did arrive lies complete on disk
        good = SOLAR_NAMES[:2]
        broken_urls = [
            f"{_MAST}/r500/m+0.00/{name}" for name in SOLAR_NAMES[2:]
        ]
        mapping = {SCRIPT_URL: _bulkdl_text(SOLAR_NAMES)}
        for name in good:
            mapping[f"{_MAST}/r500/m+0.00/{name}"] = (DATA / name).read_bytes()
        factory, calls = self._factory(mapping, broken=broken_urls)
        with pytest.raises(Exception, match="simulated transport failure"):
            build_database(
                tmp_path,
                declarations=[{"family": "bosz"}],
                select=["bosz/r500/m+0.00"],
                workers=4,
                session_factory=factory,
            )
        directory = tmp_path / "bosz" / "r500" / "m+0.00"
        for name in good:
            assert (directory / name).is_file()
        assert not list(directory.glob("*.part"))
        # the good parts were attempted (they sit first in the batch) and
        # are recorded; the failure came from an attempted broken part.
        # Which broken parts get attempted is deliberately NOT asserted:
        # after the first failure the queued remainder is cancelled
        # before starting — that is the drain+cancel contract, so "every
        # broken url was attempted" is a race, not a guarantee
        good_urls = [f"{_MAST}/r500/m+0.00/{name}" for name in good]
        assert all(url in calls for url in good_urls)
        assert set(calls) & set(broken_urls)
        manifest = read_manifest(directory)
        for name in good:
            assert manifest["parts"][name]["sha256"] == _sha(DATA / name)

    def test_parallel_failure_waits_for_inflight_fetches(self, tmp_path):
        # a fetch IN FLIGHT when a sibling fails is drained to
        # completion, never abandoned: its file lands atomically and its
        # manifest entry is recorded even after the failure was seen
        release = threading.Event()
        entered = threading.Event()
        order: list = []
        calls: list = []
        fast, slow, broken = SOLAR_NAMES[0], SOLAR_NAMES[1], SOLAR_NAMES[2]
        slow_url = f"{_MAST}/r500/m+0.00/{slow}"
        broken_url = f"{_MAST}/r500/m+0.00/{broken}"
        mapping = {SCRIPT_URL: _bulkdl_text(SOLAR_NAMES[:3])}
        for name in (fast, slow):
            mapping[f"{_MAST}/r500/m+0.00/{name}"] = (DATA / name).read_bytes()

        def make():
            return _BlockingSession(
                mapping, (broken_url,), calls, (slow_url,),
                release, entered, order,
            )

        with pytest.raises(Exception, match="simulated transport failure"):
            build_database(
                tmp_path,
                declarations=[{"family": "bosz"}],
                select=["bosz/r500/m+0.00"],
                workers=2,
                session_factory=make,
            )
        # the slow fetch was provably inside get() before the failure
        # struck, and did complete afterwards (drain, not abandon)
        assert order.index(("enter", slow)) < order.index(("fail", broken))
        assert ("resume", slow) in order
        directory = tmp_path / "bosz" / "r500" / "m+0.00"
        for name in (fast, slow):
            assert (directory / name).is_file()
        assert not (directory / broken).exists()
        assert not list(directory.glob("*.part"))
        manifest = read_manifest(directory)
        for name in (fast, slow):
            assert manifest["parts"][name]["sha256"] == _sha(DATA / name)
        assert broken not in manifest["parts"]

    def test_workers_one_uses_the_sequential_session_path(self, tmp_path):
        mapping = {SCRIPT_URL: _bulkdl_text(SOLAR_NAMES[:1])}
        for name in SOLAR_NAMES[:1]:
            mapping[f"{_MAST}/r500/m+0.00/{name}"] = (DATA / name).read_bytes()
        build_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
            select=["bosz/r500/m+0.00"],
            session=FakeSession(mapping),
        )
        manifest = read_manifest(tmp_path / "bosz" / "r500" / "m+0.00")
        assert len(manifest["parts"]) == 2

    def test_session_with_workers_refused(self, tmp_path):
        with pytest.raises(ValueError, match="session_factory"):
            build_database(
                tmp_path,
                declarations=[{"family": "bosz"}],
                session=_mirror_session(),
                workers=2,
            )

    def test_session_factory_without_workers_refused(self, tmp_path):
        factory, _calls = self._factory({SCRIPT_URL: b""})
        with pytest.raises(ValueError, match="session_factory"):
            build_database(
                tmp_path,
                declarations=[{"family": "bosz"}],
                session_factory=factory,
            )

    def test_workers_must_be_positive(self, tmp_path):
        with pytest.raises(ValueError, match="workers"):
            build_database(tmp_path, workers=0)

    def test_select_dry_run_reports_the_subset(self, tmp_path, capsys):
        build_database(
            tmp_path,
            declarations=[{"family": "tlusty"}],
            dry_run=True,
            select=["tlusty/OSTAR2002/G"],
        )
        out = capsys.readouterr().out
        assert "tlusty/OSTAR2002/G" in out
        assert "tlusty/BSTAR2006/BG_v2" not in out
        assert "selection: 1 of 20" in out
        assert "total: 1 product(s)" in out


class TestManifestFlushDuringFetch:
    """A fetch pass persists arrivals before it ends: a mirror product
    fetches tens of thousands of parts in one pass, and a failure (or a
    hard kill) mid-pass must not leave the already-fetched files
    present-but-unrecorded — that state classifies ok with no integrity
    evidence and is never re-verified."""

    def _raising_factory(self, mapping, broken):
        def make():
            return _RaisingSession(mapping, broken, [])

        return make

    def test_parallel_failure_persists_arrived_parts(self, tmp_path):
        # the drained batch's manifest write happens before the loud
        # raise, so the parts that did arrive carry full records
        good = SOLAR_NAMES[:2]
        broken_urls = [
            f"{_MAST}/r500/m+0.00/{name}" for name in SOLAR_NAMES[2:]
        ]
        mapping = {SCRIPT_URL: _bulkdl_text(SOLAR_NAMES)}
        for name in good:
            mapping[f"{_MAST}/r500/m+0.00/{name}"] = (DATA / name).read_bytes()
        factory = self._raising_factory(mapping, broken_urls)
        with pytest.raises(Exception, match="simulated transport failure"):
            build_database(
                tmp_path,
                declarations=[{"family": "bosz"}],
                select=["bosz/r500/m+0.00"],
                workers=4,
                session_factory=factory,
            )
        manifest = read_manifest(tmp_path / "bosz" / "r500" / "m+0.00")
        assert SCRIPT_NAME in manifest["parts"]
        for name in good:
            entry = manifest["parts"][name]
            assert entry["sha256"] == _sha(DATA / name)
            assert entry["url"].endswith(name)

    def test_sequential_failure_persists_arrived_parts(self, tmp_path):
        good = SOLAR_NAMES[:2]
        mapping = {SCRIPT_URL: _bulkdl_text(SOLAR_NAMES[:3])}
        for name in good:
            mapping[f"{_MAST}/r500/m+0.00/{name}"] = (DATA / name).read_bytes()
        session = _RaisingSession(
            mapping,
            [f"{_MAST}/r500/m+0.00/{SOLAR_NAMES[2]}"],
            [],
        )
        with pytest.raises(Exception, match="simulated transport failure"):
            build_database(
                tmp_path,
                declarations=[{"family": "bosz"}],
                select=["bosz/r500/m+0.00"],
                session=session,
            )
        manifest = read_manifest(tmp_path / "bosz" / "r500" / "m+0.00")
        assert SCRIPT_NAME in manifest["parts"]
        for name in good:
            assert manifest["parts"][name]["sha256"] == _sha(DATA / name)

    def test_manifest_flushes_mid_pass(self, tmp_path, monkeypatch):
        # the chunked flush (every _MANIFEST_FLUSH_PARTS arrivals) fires
        # DURING the pass, not only at its end: probing the second
        # spectrum's fetch (with the threshold at 2: script + first
        # spectrum arrived) must find the first spectrum already
        # recorded on disk
        import stellarlib.database.build as build_mod

        monkeypatch.setattr(build_mod, "_MANIFEST_FLUSH_PARTS", 2)
        directory = tmp_path / "bosz" / "r500" / "m+0.00"
        probed = []

        class _ProbeSession(FakeSession):
            def get(self, url, stream=True, timeout=None):
                if url.endswith(SOLAR_NAMES[1]) and not probed:
                    probed.append(url)
                    manifest = read_manifest(directory)
                    entry = manifest["parts"][SOLAR_NAMES[0]]
                    assert entry["sha256"] == _sha(DATA / SOLAR_NAMES[0])
                return super().get(url, stream=stream, timeout=timeout)

        mapping = {SCRIPT_URL: _bulkdl_text(SOLAR_NAMES[:3])}
        for name in SOLAR_NAMES[:3]:
            mapping[f"{_MAST}/r500/m+0.00/{name}"] = (DATA / name).read_bytes()
        build_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
            select=["bosz/r500/m+0.00"],
            session=_ProbeSession(mapping),
        )
        assert probed  # the probe actually fired


class TestVerifyDatabase:
    """verify_database: re-check recorded evidence in place, backfill
    evidence for present-but-unrecorded parts (the lost-write state a
    transport-failed fetch pass leaves), and never fetch."""

    def _mirror_built(self, tmp_path) -> Path:
        build_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
            session=_mirror_session(),
            log=lambda *_: None,
        )
        return tmp_path / "bosz" / "r500" / "m+0.00"

    @staticmethod
    def _strip_records(directory: Path, names) -> None:
        manifest = read_manifest(directory)
        for name in names:
            del manifest["parts"][name]
        write_manifest(directory, manifest)

    def test_backfills_lost_manifest_records(self, tmp_path):
        directory = self._mirror_built(tmp_path)
        # one entry lost entirely; one downgraded to a bare failed-fetch
        # record (the shapes a transport-failed pass can leave behind)
        lost, downgraded = SOLAR_NAMES[0], SOLAR_NAMES[1]
        manifest = read_manifest(directory)
        del manifest["parts"][lost]
        manifest["parts"][downgraded] = {
            "file": downgraded,
            "http_status": 404,
            "error": "not found",
            "url": f"{_MAST}/r500/m+0.00/{downgraded}",
            "attempted_at": "2026-01-01T00:00:00Z",
        }
        write_manifest(directory, manifest)

        report = verify_database(
            tmp_path, select=["bosz/r500/m+0.00"], log=lambda *_: None
        )

        (v,) = report.products
        assert v.state == "complete"
        assert v.backfilled == 2
        assert v.recorded_ok == 3  # script + the two untouched spectra
        assert v.missing == 0 and v.nonconforming == ()
        restored = read_manifest(directory)["parts"]
        entry = restored[lost]
        assert entry["sha256"] == _sha(DATA / lost)
        assert entry["size_bytes"] == (DATA / lost).stat().st_size
        assert entry["evidence"] == "sha256_verified_in_place"
        assert entry["url"] == f"{_MAST}/r500/m+0.00/{lost}"
        kept = restored[downgraded]
        assert kept["http_status"] == 404  # the recorded history survives
        assert kept["sha256"] == _sha(DATA / downgraded)
        # the backfill is durable: a scan now sees full evidence
        scan = scan_database(tmp_path)
        (product,) = (p for p in scan.products if p.label_path == "bosz/r500/m+0.00")
        assert product.state == "complete"

    def test_parallel_workers_match_sequential_backfill(self, tmp_path):
        directory = self._mirror_built(tmp_path)
        self._strip_records(directory, SOLAR_NAMES)

        report = verify_database(
            tmp_path, select=["bosz/r500/m+0.00"], workers=4,
            log=lambda *_: None,
        )

        (v,) = report.products
        assert v.backfilled == 4
        assert v.state == "complete"
        manifest = read_manifest(directory)
        for name in SOLAR_NAMES:
            assert manifest["parts"][name]["sha256"] == _sha(DATA / name)

    def test_reports_nonconforming_and_missing_without_touching_them(
        self, tmp_path
    ):
        directory = self._mirror_built(tmp_path)
        # same-size corruption: the size check passes, sha256 catches it
        tampered = directory / SOLAR_NAMES[0]
        raw = tampered.read_bytes()
        tampered.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0xFF]))
        # size corruption: the verdict on its own, no read needed
        short = directory / SOLAR_NAMES[1]
        short.write_bytes(b"short")
        # a deleted part is missing — reported, never fetched
        (directory / SOLAR_NAMES[2]).unlink()

        report = verify_database(
            tmp_path, select=["bosz/r500/m+0.00"], log=lambda *_: None
        )

        (v,) = report.products
        assert v.state == "incomplete"
        assert v.recorded_ok == 2  # script + the untouched spectrum
        assert v.missing == 1
        assert len(v.nonconforming) == 2
        reasons = dict(v.nonconforming)
        assert "size 5 !=" in reasons[SOLAR_NAMES[1]]
        assert "sha256" in reasons[SOLAR_NAMES[0]]
        # nonconforming parts keep their recorded entries unchanged
        manifest = read_manifest(directory)
        assert "evidence" not in manifest["parts"][SOLAR_NAMES[0]]

    def test_absent_products_and_select_discipline(self, tmp_path):
        # a selected build leaves the other products without directories
        build_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
            session=_mirror_session(),
            select=["bosz/r500/m+0.00"],
            log=lambda *_: None,
        )
        report = verify_database(tmp_path, log=lambda *_: None)
        states = {v.label_path: v.state for v in report.products}
        assert len(states) == 113
        assert states["bosz/r500/m+0.00"] == "complete"
        assert states["bosz/r500/m+0.25"] == "absent"
        assert states["bosz/wavelength_grids/all"] == "absent"
        with pytest.raises(ValueError, match="match no declared product"):
            verify_database(tmp_path, select=["bosz/nope/*"], log=lambda *_: None)
        with pytest.raises(ValueError, match="select must not be an empty list"):
            verify_database(tmp_path, select=[], log=lambda *_: None)

    def test_learns_cache_and_second_pass_reads_nothing(
        self, tmp_path, monkeypatch
    ):
        self._mirror_built(tmp_path)
        # drop the builder's cache: the first verify reads every part
        (tmp_path / "signature_cache.json").unlink()

        import stellarlib.database.build as build_mod

        calls: list[Path] = []
        real = build_mod.sha256_file

        def counting(path):
            calls.append(path)
            return real(path)

        monkeypatch.setattr(build_mod, "sha256_file", counting)
        first = verify_database(
            tmp_path, select=["bosz/r500/m+0.00"], log=lambda *_: None
        )
        assert len(calls) == 5  # script + 4 spectra, each read exactly once
        calls.clear()
        second = verify_database(
            tmp_path, select=["bosz/r500/m+0.00"], log=lambda *_: None
        )
        assert calls == []  # every answer served by the learned cache
        assert first.products == second.products

    def test_manifest_flushes_mid_verify(self, tmp_path, monkeypatch):
        import stellarlib.database.build as build_mod

        directory = self._mirror_built(tmp_path)
        self._strip_records(directory, SOLAR_NAMES)
        monkeypatch.setattr(build_mod, "_MANIFEST_FLUSH_PARTS", 2)
        writes = []
        real_write = build_mod.write_manifest

        def counting_write(dir_path, manifest):
            writes.append(dir_path)
            return real_write(dir_path, manifest)

        monkeypatch.setattr(build_mod, "write_manifest", counting_write)

        verify_database(
            tmp_path, select=["bosz/r500/m+0.00"], log=lambda *_: None
        )

        # 4 backfills at a threshold of 2: two mid-pass flushes plus the
        # closing write — a kill mid-verify loses at most one chunk
        assert writes == [directory, directory, directory]

    def test_requires_a_blueprint(self, tmp_path):
        with pytest.raises(ValueError, match="no blueprint declared"):
            verify_database(tmp_path, log=lambda *_: None)

    def test_product_directory_without_manifest_fails_loudly(self, tmp_path):
        build_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
            session=_mirror_session(),
            select=["bosz/r500/m+0.00"],
            log=lambda *_: None,
        )
        (tmp_path / "bosz" / "r500" / "m+0.25").mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            verify_database(tmp_path, log=lambda *_: None)


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

    def test_facade_construct_from_database(self, tmp_path):
        # the family facades' database= path: construct() opens the
        # database and hands its sets to the unified query layer
        # (regression: the import inside _database_grid targeted a name
        # the database package never exported, so this path ImportError'd)
        from stellarlib.facade import BoszSpectra, TlustySpectra

        register_local_product(
            tmp_path, "bosz", "solar",
            [DATA / n for n in SOLAR_NAMES] + [DATA / WAVE_NAME],
            note="registered subset",
        )
        files = [
            TLUSTY_DATA / n
            for n in (
                "G30000g300v10.vis.7.gz",
                "G30000g325v10.vis.7.gz",
                "G32500g300v10.vis.7.gz",
                "G32500g325v10.vis.7.gz",
            )
        ]
        register_local_product(tmp_path, "tlusty", "ostar_g", files, note="")

        bosz = BoszSpectra.construct(database=tmp_path, label="solar")
        assert len(bosz) == len(SOLAR_NAMES)
        tlusty = TlustySpectra.construct(database=tmp_path, label="ostar_g")
        assert len(tlusty) == len(files)

    def test_construct_database_mirror_wave_sidecar(self, tmp_path):
        # the mirror layout: the wavelength sidecar lives in its own
        # product, never next to the mirror's spectra. construct's
        # database branch discovers it across products, and an explicit
        # wave_path pin reaches the reader instead of being dropped
        # (a bogus pin is a loud error, never silently replaced by
        # discovery)
        from stellarlib.exceptions import WavelengthGridError
        from stellarlib.facade import BoszSpectra

        build_database(
            tmp_path,
            declarations=[{"family": "bosz"}],
            select=["bosz/r500/m+0.00", "bosz/wavelength_grids/*"],
            session=_mirror_session(),
        )
        spectra = BoszSpectra.construct(
            database=tmp_path, collection="r500", label="m+0.00"
        )
        assert len(spectra) == len(SOLAR_NAMES)
        with pytest.raises(WavelengthGridError, match="not-there"):
            BoszSpectra.construct(
                database=tmp_path,
                collection="r500",
                label="m+0.00",
                wave_path=tmp_path / "not-there.txt",
            )
