"""MANIFEST reading, writing, and validation (schema stellarlib-manifest/1).

Every product directory carries one MANIFEST describing its parts (the
files), their checksums and sources, the identity triplet, and a family
section carrying the mirror declaration (which upstream unit this
product holds). Only relative paths are stored for copied products;
reference-registered local products record absolute paths and say so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from stellarlib.exceptions import DataIntegrityError

__all__ = [
    "MANIFEST_SCHEMA",
    "manifest_path",
    "read_manifest",
    "write_manifest",
    "empty_manifest",
]

MANIFEST_SCHEMA = "stellarlib-manifest/1"


def manifest_path(directory: Path) -> Path:
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
    tmp.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def empty_manifest(
    *,
    family: str,
    release: str,
    label: str,
    kind: str,
    source_class: str,
    note: str,
    format: str,
    references: list[str],
    family_section: Mapping | None = None,
    by_reference: bool = False,
) -> dict:
    return {
        "schema": MANIFEST_SCHEMA,
        "family": family,
        "release": release,
        "label": label,
        "kind": kind,
        "source_class": source_class,
        "note": note,
        "format": format,
        "references": list(references),
        "parts": {},
        "family_section": dict(family_section or {}),
        "by_reference": by_reference,
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
