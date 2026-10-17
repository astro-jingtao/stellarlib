"""build_database and register_local_product: the write paths.

The builder completes what the design declares and what is missing — one
file per part, checksummed on arrival. Products containing nonconforming
parts are never touched (a checksum pass proves completeness, not design
membership); a failed fetch (e.g. an HTTP 404) is recorded as exactly
that failed attempt and the part stays missing — the next build retries
it. ``dry_run=True`` reports the plan without touching the
disk. Registration admits locally held files as products — copied in by
default (self-contained, portable), or referenced in place for sets that
already live where their project wants them.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from stellarlib.database.core import Database, ProductRecord, open_database
from stellarlib.database.design import VerdictKind
from stellarlib.database.inventory import refresh_inventory
from stellarlib.database.manifest import (
    empty_manifest,
    read_manifest,
    write_manifest,
)
from stellarlib.database.scan import scan_database
from stellarlib.integrity import sha256_file
from stellarlib.models import catalog
from stellarlib.models.bosz.reader import recognize_bosz, recognize_wave_grid
from stellarlib.models.tlusty.reader import recognize_tlusty
from stellarlib.registry import references

__all__ = ["build_database", "register_local_product"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _merge_designs(root: Path, declarations: Iterable[dict] | None) -> list[dict]:
    path = root / "design.json"
    existing = []
    if path.exists():
        existing = list(json.loads(path.read_text(encoding="utf-8")).get("windows", []))
    if declarations is None:
        return existing
    added = [dict(d) for d in declarations]
    merged = existing + [
        d for d in added if d not in existing
    ]
    return merged


def _write_designs(root: Path, declarations: list[dict]) -> None:
    path = root / "design.json"
    payload = {
        "schema": "stellarlib-design/1",
        "note": "declared fetchable windows; the design is the complete "
        "target, fetching is on demand (build_database)",
        "windows": declarations,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )


def build_database(
    database: str | Path,
    declarations: Iterable[dict] | None = None,
    session=None,
    dry_run: bool = False,
    log=print,
) -> Database | None:
    """Build (or complete) a local database against its declared design.

    ``declarations`` adds window declarations to the database's
    ``design.json`` (a ``None`` keeps the existing design — completing a
    previous build). Each declared product is scanned first; missing
    parts are fetched through the family adapter (streamed, sha256
    recorded in the MANIFEST at arrival); products with nonconforming
    parts are skipped with a notice, never patched. ``dry_run=True``
    reports the plan and touches nothing.
    """
    root = Path(database)
    merged = _merge_designs(root, declarations)
    if not merged:
        raise ValueError(
            "no design declared: pass declarations=[{family, ...}] once; "
            "later builds reuse the database's design.json"
        )

    if dry_run:
        root.mkdir(parents=True, exist_ok=True)
        report = scan_database(root, declarations=list(declarations or []))
        for product in report.products:
            missing = [
                v for v in product.verdicts if v.kind == VerdictKind.MISSING
            ]
            log(
                f"{product.label_path}: {product.state} "
                f"({len(missing)} part(s) to fetch)"
            )
        log(
            f"total: {len(report.products)} product(s), "
            f"{report.missing_part_count()} missing part(s), "
            f"{len(report.nonconforming_parts())} nonconforming (manual "
            "review, never auto-fetched)"
        )
        return None

    root.mkdir(parents=True, exist_ok=True)
    _write_designs(root, merged)
    report = scan_database(root)

    from stellarlib.database.scan import ADAPTERS

    for product in report.products:
        directory = (
            Path(product.directory)
            if product.directory is not None
            else root
            / product.design.family
            / product.design.collection
            / product.design.label
        )
        adapter = ADAPTERS[product.design.family]
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / "MANIFEST.json"
        manifest = (
            read_manifest(directory)
            if manifest_path.exists()
            else empty_manifest(
                family=product.design.family,
                release=_release_of(product.design.family, product.design.collection),
                label=product.design.label,
                kind=product.design.kind,
                source_class="official_archive",
                note="fetched per-file from the official release tree",
                format=_format_of(product.design.family),
                references=[r.citation for r in references(product.design.family)],
                family_section={"window": dict(product.design.facts)},
            )
        )
        manifest.setdefault("family_section", {}).setdefault(
            "window", dict(product.design.facts)
        )
        bad = [
            v
            for v in product.verdicts
            if v.kind == VerdictKind.NONCONFORMING
        ]
        if bad:
            # invariant: a product with nonconforming parts is never
            # completed by the builder — checksum validity proves
            # integrity, not design membership
            log(
                f"NOTICE {product.label_path}: {len(bad)} nonconforming "
                f"part(s) ({'; '.join(v.summary for v in bad[:3])}) — "
                "product left for human review, nothing fetched"
            )
            continue
        for verdict, part in zip(product.verdicts, product.design.parts):
            if verdict.kind == VerdictKind.MISSING:
                log(f"  fetch {part.name}")
                entry = dict(
                    adapter.fetch_part(
                        product.design, part, directory, session=session, log=log
                    )
                )
                manifest["parts"][part.name] = entry
        write_manifest(directory, manifest)

    refresh_inventory(root)
    return open_database(root)


def _release_of(family: str, collection: str) -> str:
    if family == "bosz":
        return "2024"
    return collection


def _format_of(family: str) -> str:
    return {"bosz": "bosz_resam", "tlusty": "tlusty_vis"}[family]


def register_local_product(
    database: str | Path,
    family: str,
    label: str,
    files: Iterable[str | Path],
    note: str,
    copy: bool = True,
) -> ProductRecord:
    """Register locally held files as a product of the database.

    This is the entry channel for hand-obtained sets — the two downstream
    projects' existing subsets, or a TLUSTY vispec tar's extracted files.
    With ``copy=True`` (default) the files are copied into the product
    directory (self-contained, portable; only relative paths recorded).
    With ``copy=False`` the files stay where they are and the MANIFEST
    records their absolute paths (``by_reference``) — right for large
    in-place sets, at the cost of portability.
    """
    root = Path(database)
    root.mkdir(parents=True, exist_ok=True)
    path_list = [Path(f) for f in files]
    if not path_list:
        raise ValueError("files must not be empty")

    if family == "bosz":
        recognized = lambda p: recognize_bosz(p) or recognize_wave_grid(p)
        collection = _bosz_collection(path_list)
        release = "2024"
    elif family == "tlusty":
        recognized = recognize_tlusty
        collection = _tlusty_release(path_list)
        release = collection
    else:
        raise ValueError(f"unknown family {family!r}")

    for p in path_list:
        if not p.is_file():
            raise FileNotFoundError(p)
        if not recognized(p):
            raise ValueError(
                f"{p}: not recognized as a {family} source file"
            )

    target_dir = root / family / collection / label
    target_dir.mkdir(parents=True, exist_ok=True)
    parts: dict[str, dict] = {}
    for src in path_list:
        if copy:
            dest = target_dir / src.name
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
            recorded_path = src.name
        else:
            dest = src
            recorded_path = str(src.resolve())
        parts[src.name] = {
            "file": src.name,
            "path": recorded_path,
            "sha256": sha256_file(dest),
            "size_bytes": dest.stat().st_size,
            "retrieved_at": _now_iso(),
            "url": None,
        }
    manifest = empty_manifest(
        family=family,
        release=release,
        label=label,
        kind="local_user_files",
        source_class="local_user_file",
        note=note,
        format=_format_of(family),
        references=[r.citation for r in references(family)],
        family_section=_local_section(family, path_list),
        by_reference=not copy,
    )
    manifest["parts"] = parts
    write_manifest(target_dir, manifest)
    refresh_inventory(root)
    return ProductRecord(directory=target_dir, manifest=manifest)


def _bosz_collection(files: list[Path]) -> str:
    resolutions = set()
    for p in files:
        if recognize_wave_grid(p):
            continue
        from stellarlib.models.bosz.reader import parse_filename, WAVE_FILENAME_RE

        facts = parse_filename(p.name)
        resolutions.add(facts["resolution"])
    if len(resolutions) != 1:
        raise ValueError(
            f"one product holds one resolution; found {sorted(resolutions)}"
        )
    return f"r{resolutions.pop()}"


def _tlusty_release(files: list[Path]) -> str:
    from stellarlib.models.tlusty.backend import _infer_release

    teffs = []
    for p in files:
        from stellarlib.models.tlusty.reader import parse_filename

        facts = parse_filename(p.name if p.name.endswith(".gz") else p.name + ".gz")
        if facts["kind"] != "7":
            raise ValueError(
                f"{p.name}: kind-17 continuum files are not part of a "
                "spectrum product"
            )
        teffs.append(facts["teff"])
    return _infer_release(tuple(teffs), None)


def _local_section(family: str, files: list[Path]) -> dict:
    if family == "tlusty":
        from stellarlib.models.tlusty.reader import parse_filename

        compositions = set()
        for p in files:
            name = p.name if p.name.endswith(".gz") else p.name + ".gz"
            compositions.add(parse_filename(name)["composition"])
        if len(compositions) == 1:
            return {"composition": compositions.pop()}
    return {}
