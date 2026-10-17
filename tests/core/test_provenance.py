"""Tests for the provenance schema and integrity helpers."""

from __future__ import annotations

import pytest

from stellarlib.exceptions import DataIntegrityError
from stellarlib.integrity import sha256_bytes, verify_checksum
from stellarlib.provenance import (
    DataAsset,
    Provenance,
    Reference,
    SourceChain,
    SourceClass,
    SourceRecord,
    SourceStatus,
)


class TestSourceRecord:
    def test_roundtrip(self):
        record = SourceRecord(
            source_class=SourceClass.OFFICIAL_ARCHIVE,
            url="https://archive.stsci.edu/hlsps/bosz/",
            status=SourceStatus.VERIFIED,
            accessed_at="2026-10-17T00:00:00",
        )
        again = SourceRecord.from_dict(record.to_dict())
        assert again == record

    def test_type_checks(self):
        with pytest.raises(TypeError):
            SourceRecord(source_class="official_archive")
        with pytest.raises(TypeError):
            SourceRecord(source_class=SourceClass.OFFICIAL_ORIGIN, status="verified")


class TestProvenanceBundle:
    def test_roundtrip(self):
        provenance = Provenance(
            data_sources=(
                SourceRecord(
                    source_class=SourceClass.OFFICIAL_ARCHIVE,
                    url="https://example.org/spectrum.txt.gz",
                    status=SourceStatus.VERIFIED,
                ),
            ),
            assets=(
                DataAsset(
                    filename="spectrum.txt.gz",
                    size_bytes=1024,
                    checksum=sha256_bytes(b"payload"),
                ),
            ),
            source_chains=(
                SourceChain(
                    transformations=("decompress gzip", "parse two columns"),
                ),
            ),
            scientific_references=(
                Reference(citation="Meszaros et al. (2024), A&A, 688, A197"),
            ),
            transformations=("parse",),
        )
        again = Provenance.from_dict(provenance.to_dict())
        assert again == provenance

    def test_defaults(self):
        provenance = Provenance()
        assert provenance.data_sources == ()
        assert provenance.transformations == ()


class TestIntegrity:
    def test_sha256_bytes_stable(self):
        assert sha256_bytes(b"data") == sha256_bytes(b"data")

    def test_verify_checksum_pass(self, tmp_path):
        target = tmp_path / "file.bin"
        target.write_bytes(b"payload")
        digest = sha256_bytes(b"payload")
        assert verify_checksum(target, digest) == digest

    def test_verify_checksum_mismatch_raises(self, tmp_path):
        target = tmp_path / "file.bin"
        target.write_bytes(b"payload")
        with pytest.raises(DataIntegrityError, match="sha256"):
            verify_checksum(target, "0" * 64)

    def test_verify_checksum_without_expectation(self, tmp_path):
        target = tmp_path / "file.bin"
        target.write_bytes(b"payload")
        assert verify_checksum(target, None) == sha256_bytes(b"payload")
