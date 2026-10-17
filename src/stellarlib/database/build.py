"""build_database and register_local_product: the write paths.

The blueprint (design.json) is a local record of the complete upstream
library: every declared family expands to its whole official packages —
never a subset. The builder completes what the blueprint declares and
what is missing, one part per fetch, checksummed on arrival; for
directory-mirror products the first part is the upstream bulk manifest
itself (a BOSZ bulkdl script), whose arrival expands the design to the
full file list within the same build. Products containing nonconforming
parts are never touched (a checksum pass proves completeness, not design
membership); a failed fetch (e.g. an HTTP 404) is recorded as exactly
that failed attempt and the part stays missing — the next build retries
it. ``dry_run=True`` reports the plan without touching the disk.
Registration admits locally held files as products — copied in by
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
from stellarlib.database.design import (
    FamilyAdapter,
    PartVerdict,
    ProductDesign,
    VerdictKind,
)
from stellarlib.database.inventory import refresh_inventory
from stellarlib.database.manifest import (
    empty_manifest,
    read_manifest,
    write_manifest,
)
from stellarlib.database.scan import scan_database
from stellarlib.integrity import sha256_file
from stellarlib.models.bosz.reader import recognize_bosz, recognize_wave_grid
from stellarlib.models.tlusty.reader import recognize_tlusty
from stellarlib.registry import references

__all__ = ["build_database", "register_local_product", "DESIGN_SCHEMA"]

DESIGN_SCHEMA = "stellarlib-design/2"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _merge_designs(root: Path, declarations: Iterable[dict] | None) -> list[dict]:
    path = root / "design.json"
    existing: list[dict] = []
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        schema = payload.get("schema")
        if schema == "stellarlib-design/1":
            raise ValueError(
                f"{path}: schema 'stellarlib-design/1' (window declarations) "
                "is from the pre-blueprint design and is not migrated; a "
                "blueprint (schema 'stellarlib-design/2') declares the full "
                "upstream library per family — declare it anew with "
                "build_database(declarations=[{'family': ...}], ...) after "
                "removing the old design.json"
            )
        if schema != DESIGN_SCHEMA:
            raise ValueError(
                f"{path}: unknown design schema {schema!r} "
                f"(expected {DESIGN_SCHEMA!r})"
            )
        existing = [dict(e) for e in payload.get("blueprint", [])]
    if declarations is None:
        return existing
    added = [dict(d) for d in declarations]
    return existing + [d for d in added if d not in existing]


def _write_designs(root: Path, declarations: list[dict]) -> None:
    path = root / "design.json"
    payload = {
        "schema": DESIGN_SCHEMA,
        "note": "blueprint: a local record of the complete upstream "
        "library — every declared family is held in full, as whole "
        "official packages; build_database completes what is missing",
        "blueprint": declarations,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )


def _release_of(family: str, collection: str) -> str:
    if family == "bosz":
        return "2024"
    return collection


def _format_of(family: str) -> str:
    return {"bosz": "bosz_resam", "tlusty": "tlusty_vis"}[family]


def _build_product(
    design: ProductDesign,
    adapter: FamilyAdapter,
    root: Path,
    session,
    log,
) -> None:
    """Complete one product: expand the design, fetch missing parts, and
    re-expand once the upstream bulk manifest part has arrived."""
    directory = root / design.family / design.collection / design.label
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "MANIFEST.json"
    if manifest_path.exists():
        manifest = read_manifest(directory)
    else:
        manifest = empty_manifest(
            family=design.family,
            release=_release_of(design.family, design.collection),
            label=design.label,
            kind=design.kind,
            source_class="official_archive",
            note="whole official package: fetched exactly as the source "
            "publishes it (a directory mirror or an archive file)",
            format=_format_of(design.family),
            references=[r.citation for r in references(design.family)],
            family_section={"mirror": dict(design.facts)},
        )
    manifest.setdefault("family_section", {}).setdefault(
        "mirror", dict(design.facts)
    )

    design = adapter.expand_design(design, directory)

    def _missing() -> list[tuple[ProductDesign, PartVerdict, object]]:
        verdicts = [
            adapter.classify_part(design, part, directory, manifest)
            for part in design.parts
        ]
        bad = [v for v in verdicts if v.kind == VerdictKind.NONCONFORMING]
        if bad:
            # invariant: a product with nonconforming parts is never
            # completed by the builder — checksum validity proves
            # integrity, not design membership
            log(
                f"NOTICE {design.directory_name}: {len(bad)} nonconforming "
                f"part(s) ({'; '.join(v.summary for v in bad[:3])}) — "
                "product left for human review, nothing fetched"
            )
            write_manifest(directory, manifest)
            return []
        return [
            (design, v, p) for v, p in zip(verdicts, design.parts)
            if v.kind == VerdictKind.MISSING
        ]

    def _fetch_all(pending) -> bool:
        nonlocal design
        for current, _verdict, part in pending:
            log(f"  fetch {part.name}")
            entry = dict(
                adapter.fetch_part(
                    current, part, directory, session=session, log=log
                )
            )
            manifest["parts"][part.name] = entry
            if part.role == "bulk_manifest" and "sha256" in entry:
                # the upstream manifest of the directory has arrived:
                # the design now enumerates every file it lists
                design = adapter.expand_design(design, directory)
                return True
        return False

    while True:
        pending = _missing()
        if not pending:
            break
        # a pass ends early only when a bulk manifest part arrives and
        # the design expands; the next pass then covers the listed files
        if not _fetch_all(pending):
            break
    write_manifest(directory, manifest)


def build_database(
    database: str | Path,
    declarations: Iterable[dict] | None = None,
    session=None,
    dry_run: bool = False,
    log=print,
) -> Database | None:
    """Build (or complete) a local database against its blueprint.

    ``declarations`` adds blueprint entries (``{"family": "bosz"}``,
    ``{"family": "tlusty"}``) to the database's ``design.json``; a
    family entry declares the family's complete upstream library — every
    official package, whole. A ``None`` keeps the existing blueprint —
    completing a previous build. Missing parts are fetched through the
    family adapter (streamed, sha256 recorded in the MANIFEST at
    arrival); products with nonconforming parts are skipped with a
    notice, never patched. ``dry_run=True`` reports the plan and touches
    nothing.
    """
    root = Path(database)
    merged = _merge_designs(root, declarations)
    if not merged:
        raise ValueError(
            "no blueprint declared: pass declarations=[{'family': ...}] "
            "once; later builds reuse the database's design.json"
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
        adapter = ADAPTERS[product.design.family]
        _build_product(product.design, adapter, root, session, log)

    refresh_inventory(root)
    return open_database(root)


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
    projects' existing subsets, or the extracted contents of a TLUSTY
    vispec tar. With ``copy=True`` (default) the files are copied into
    the product directory (self-contained, portable; only relative paths
    recorded). With ``copy=False`` the files stay where they are and the
    MANIFEST records their absolute paths (``by_reference``) — right for
    large in-place sets, at the cost of portability.
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
        recognized = lambda p: recognize_tlusty(p) or p.name.endswith(".tar")
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
    """Infer the release from the kind-7 spectra held (files or tar
    members); kind-17 continuum files ride along in every set and carry
    no release-discriminating Teff the kind-7 files do not."""
    import tarfile

    from stellarlib.models.tlusty.backend import _infer_release
    from stellarlib.models.tlusty.reader import parse_filename, recognize_tlusty

    teffs = []
    for p in files:
        if p.name.endswith(".tar"):
            with tarfile.open(p, "r") as tf:
                names = [
                    m.name.rsplit("/", 1)[-1]
                    for m in tf.getmembers()
                    if m.isfile() and recognize_tlusty(m.name.rsplit("/", 1)[-1])
                ]
            for name in names:
                facts = parse_filename(name)
                if facts["kind"] == "7":
                    teffs.append(facts["teff"])
            continue
        facts = parse_filename(p.name if p.name.endswith(".gz") else p.name + ".gz")
        if facts["kind"] == "7":
            teffs.append(facts["teff"])
    if not teffs:
        raise ValueError(
            "no kind-7 detailed spectra among the sources (kind-17 "
            "continuum files alone are not a spectrum product)"
        )
    return _infer_release(tuple(teffs), None)


def _local_section(family: str, files: list[Path]) -> dict:
    if family == "tlusty":
        import tarfile

        from stellarlib.models.tlusty.reader import parse_filename, recognize_tlusty

        compositions = set()
        for p in files:
            if p.name.endswith(".tar"):
                with tarfile.open(p, "r") as tf:
                    names = [
                        m.name.rsplit("/", 1)[-1]
                        for m in tf.getmembers()
                        if m.isfile() and recognize_tlusty(m.name.rsplit("/", 1)[-1])
                    ]
                sources = names
            else:
                sources = [p.name if p.name.endswith(".gz") else p.name + ".gz"]
            for name in sources:
                compositions.add(parse_filename(name)["composition"])
        if len(compositions) == 1:
            return {"composition": compositions.pop()}
    return {}
