"""MANIFEST reading, writing, and validation (schema stellarlib-manifest/1).

Every product directory carries one MANIFEST describing its parts (the
files), their checksums and sources, the identity triplet, and a family
section carrying the mirror declaration (which upstream unit this
product holds). Only relative paths are stored for copied products;
reference-registered local products record absolute paths and say so.
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import json
from pathlib import Path

from stellarlib.exceptions import DataIntegrityError

__all__ = [
    "MANIFEST_SCHEMA",
    "ManifestHeader",
    "manifest_path",
    "read_manifest",
    "write_manifest",
]

MANIFEST_SCHEMA = "stellarlib-manifest/1"


def manifest_path(directory: Path) -> Path:
    """The MANIFEST.json path of one product directory."""
    return Path(directory) / "MANIFEST.json"


def read_manifest(directory: Path) -> dict:
    """Read and validate one product's MANIFEST."""
    path = manifest_path(directory)
    if not path.exists():
        raise FileNotFoundError(f"{path}: no MANIFEST (not a product?)")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    _validate(manifest, path)
    return manifest


def write_manifest(directory: Path, manifest: Mapping) -> None:
    """Write one product's MANIFEST atomically (always current schema)."""
    _validate(manifest, manifest_path(directory))
    path = manifest_path(directory)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as _f:
        _f.write(json.dumps(manifest, indent=2, sort_keys=True))
    tmp.replace(path)


@dataclasses.dataclass(frozen=True)
class ManifestHeader:
    """The identity half of a product manifest: everything but parts.

    One attribute per top-level manifest key (the schema itself lives
    here as fields); ``to_manifest`` stamps the schema version and
    appends the empty parts table.
    """

    family: str
    release: str
    label: str
    kind: str
    source_class: str
    note: str
    format: str
    references: tuple[str, ...] = ()
    family_section: Mapping | None = None
    by_reference: bool = False

    def to_manifest(self) -> dict:
        """A fresh manifest skeleton with no parts recorded yet."""
        return {
            "schema": MANIFEST_SCHEMA,
            "family": self.family,
            "release": self.release,
            "label": self.label,
            "kind": self.kind,
            "source_class": self.source_class,
            "note": self.note,
            "format": self.format,
            "references": list(self.references),
            "parts": {},
            "family_section": dict(self.family_section or {}),
            "by_reference": self.by_reference,
        }


def _validate(manifest: Mapping, path: Path) -> None:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise DataIntegrityError(
            f"{path}: schema {manifest.get('schema')!r} is not "
            f"{MANIFEST_SCHEMA!r}"
        )
    for key in ("family", "release", "label", "kind", "source_class", "parts"):
        if key not in manifest:
            raise DataIntegrityError(f"{path}: missing required key {key!r}")
    if not isinstance(manifest["parts"], dict):
        raise DataIntegrityError(f"{path}: 'parts' must be an object")
