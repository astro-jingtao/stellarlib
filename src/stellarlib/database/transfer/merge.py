"""Merge one stellarlib database (or partial copy) into another.

Invariants (shared with the builder and the verifier):

1. Archived data is never overwritten and never deleted; a merge only
   adds what is missing.
2. A product holding any nonconforming part is never auto-completed by
   a merge, on either side: the source's unsound parts do not propagate,
   and a target needing review is left untouched.
3. A conflicting part (both sides archived, recorded shas disagree) is
   never resolved automatically: both values are recorded and a human
   decides.
4. Root-level derived artifacts (database.json, README, db_status) are
   regenerated from the merged directories, never copied or merged.
5. The signature cache never certifies bytes it did not hash or that
   arrived unverified: a no-verify merge invalidates the migrated
   files' entries (scoped — exactly the migrated portion), a verifying
   merge seeds entries it hashed itself.

Products registered ``by reference`` hold no data of their own (their
parts point outside the source database) and are never merged.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
import os
from pathlib import Path
import shutil
import uuid

from stellarlib.database.build import _apply_select
from stellarlib.database.design import PartDesign, ProductDesign, VerdictKind
from stellarlib.database.inventory import refresh_inventory
from stellarlib.database.manifest import read_manifest, write_manifest
from stellarlib.database.scan import ProductReport, scan_database
from stellarlib.database.signature_cache import SignatureCache
from stellarlib.integrity import sha256_file

__all__ = ["MergeConflict", "MergeReport", "merge_database"]


@dataclasses.dataclass(frozen=True)
class MergeConflict:
    """One part the merge refused to overwrite (recorded, not resolved)."""
    product: str
    part: str
    source_sha: str | None
    target_sha: str | None


@dataclasses.dataclass
class MergeReport:
    """What one merge did, product by product."""

    source: Path
    target: Path
    verify: bool = False
    merged_products: list[str] = dataclasses.field(default_factory=list)
    completed_parts: list[tuple[str, str]] = dataclasses.field(
        default_factory=list
    )
    kept_products: list[str] = dataclasses.field(default_factory=list)
    conflicts: list[MergeConflict] = dataclasses.field(default_factory=list)
    source_needs_review: list[str] = dataclasses.field(default_factory=list)
    target_needs_review: list[str] = dataclasses.field(default_factory=list)
    source_by_reference: list[str] = dataclasses.field(default_factory=list)
    identity_mismatches: list[str] = dataclasses.field(default_factory=list)
    invalidated_cache_entries: int = 0

    @property
    def changed(self) -> bool:
        """Whether the merge added or completed anything."""
        return bool(self.merged_products or self.completed_parts)

    def summary(self) -> str:
        """The merge outcome as one human-readable block."""
        mode = " (verify)" if self.verify else (
            " (no verify — migrated cache entries invalidated)"
        )
        lines = [
            (
                f"merge {self.source} -> {self.target}{mode}: "
                f"{len(self.merged_products)} product(s) merged whole, "
                f"{len(self.completed_parts)} part(s) completed into "
                f"existing products, {len(self.kept_products)} already "
                "present"
            ),
        ]
        lines.append(
            f"conflicts {len(self.conflicts)}; source review-skipped "
            f"{len(self.source_needs_review)}; target review-skipped "
            f"{len(self.target_needs_review)}; by-reference skipped "
            f"{len(self.source_by_reference)}; identity mismatches "
            f"{len(self.identity_mismatches)}; cache entries invalidated "
            f"{self.invalidated_cache_entries}"
        )
        lines.extend(
            f"  conflict {conflict.product}/{conflict.part}: source "
            f"{conflict.source_sha} vs target {conflict.target_sha} "
            "(target untouched)"
            for conflict in self.conflicts
        )
        return "\n".join(lines)


def _part_sha(manifest: dict, part_name: str) -> str | None:
    entry = (manifest.get("parts") or {}).get(part_name) or {}
    return entry.get("sha256")


def _copy_part_file(source_dir: Path, target_dir: Path, name: str) -> None:
    """Copy one part file into place atomically (.part then replace)."""
    src = source_dir / name
    if not src.exists():
        return
    dst = target_dir / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


@dataclasses.dataclass(frozen=True)
class _MergeCtx:
    """The per-merge state the product policies share."""

    target_root: Path
    staging: Path
    report: MergeReport
    cache: SignatureCache
    log: Callable[[str], None]


def _ok_parts_of(
    design: ProductDesign,
    s_report: ProductReport,
) -> list[PartDesign]:
    """The source product's parts verified OK — the only parts a merge
    migrates."""
    return [
        p for p in design.parts
        if any(
            v.part == p.name and v.kind == VerdictKind.OK
            for v in s_report.verdicts
        )
    ]


def _product_conflicts(
    key: str,
    ok_parts: list[PartDesign],
    s_manifest: dict,
    t_manifest: dict,
    t_report: ProductReport,
) -> list[MergeConflict]:
    """The product's conflicting parts: an overlapping part archived on
    both sides with disagreeing recorded shas — any one voids the whole
    product for this merge (recorded, never resolved)."""
    conflicts = []
    for part in ok_parts:
        t_verdict = next(
            (v for v in t_report.verdicts if v.part == part.name), None
        )
        if t_verdict is None or t_verdict.kind == VerdictKind.MISSING:
            continue
        s_sha = _part_sha(s_manifest, part.name)
        t_sha = _part_sha(t_manifest, part.name)
        if s_sha and t_sha and s_sha != t_sha:
            conflicts.append(MergeConflict(key, part.name, s_sha, t_sha))
    return conflicts


def _landed_part(
    part_name: str,
    key: str,
    s_manifest: dict,
    ctx: _MergeCtx,
) -> None:
    """One migrated part landed: unverify any cached claim about it, or
    (verify mode) hash it against the source's recorded evidence. A
    verify failure leaves the copied file in place unrecorded —
    present-but-suspect, the scan's two-state judgment owns it."""
    rel_path = f"{key}/{part_name}"
    if ctx.report.verify:
        digest = sha256_file(ctx.target_root / rel_path)
        recorded = _part_sha(s_manifest, part_name)
        if recorded and digest != recorded:
            raise ValueError(
                f"migrated {rel_path} fails its recorded sha256; "
                "merge aborted for this product"
            )
        ctx.cache.remember(ctx.target_root / rel_path, digest)
    else:
        ctx.report.invalidated_cache_entries += ctx.cache.forget({rel_path})


def _stage_whole_product(
    s_report: ProductReport,
    ok_parts: list[PartDesign],
    s_manifest: dict,
    ctx: _MergeCtx,
) -> None:
    """Land the product by staged whole-directory copy: its entire
    recorded state (files + MANIFEST) arrives as one atomic unit."""
    design = s_report.design
    key = design.directory_name
    stage = ctx.staging / uuid.uuid4().hex
    stage.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(s_report.directory, stage)
    staged_manifest = read_manifest(stage)
    identity = (
        staged_manifest.get("family"),
        staged_manifest.get("release"),
        staged_manifest.get("label"),
    )
    if identity != (design.family, s_manifest["release"], design.label):
        ctx.report.identity_mismatches.append(key)
        ctx.log(
            f"skip {key}: staged manifest identity {identity} does "
            "not match the source manifest"
        )
        return
    if ctx.report.verify:
        # hash the staged copy against the source's recorded evidence
        # before it counts as merged
        for part in ok_parts:
            recorded = _part_sha(s_manifest, part.name)
            if recorded and sha256_file(stage / part.name) != recorded:
                raise ValueError(
                    f"staged copy of {key}/{part.name} fails its "
                    "recorded sha256; merge aborted for this product"
                )
    target_dir = ctx.target_root / key
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(stage, target_dir)
    if ctx.report.verify:
        for part in ok_parts:
            recorded = _part_sha(s_manifest, part.name)
            if recorded:
                ctx.cache.remember(target_dir / part.name, recorded)
    else:
        # every file the copy brought: cache entries under this
        # product's prefix are unverified claims until re-hashed here
        ctx.report.invalidated_cache_entries += ctx.cache.forget_prefixes(
            {f"{key}/"}
        )
    ctx.report.merged_products.append(key)
    ctx.log(f"merged {key} (whole directory)")


def _complete_missing_parts(
    s_report: ProductReport,
    t_report: ProductReport,
    ok_parts: list[PartDesign],
    s_manifest: dict,
    ctx: _MergeCtx,
) -> None:
    """Complete the existing target product's misses part by part.

    A part the target's own design does not know (its manifest holds
    no entry) with no file on disk is a miss like any other — the copy
    adds both; a part whose file sits there unrecorded is the
    verifier's backfill territory, left alone.
    """
    design = s_report.design
    key = design.directory_name
    s_dir = s_report.directory
    t_dir = t_report.directory
    t_manifest = read_manifest(t_dir)
    added = []
    for part in ok_parts:
        t_verdict = next(
            (v for v in t_report.verdicts if v.part == part.name), None
        )
        if t_verdict is not None and t_verdict.kind != VerdictKind.MISSING:
            continue
        if t_verdict is None and (t_dir / part.name).exists():
            continue
        _copy_part_file(s_dir, t_dir, part.name)
        _landed_part(part.name, key, s_manifest, ctx)
        added.append(part.name)
        ctx.report.completed_parts.append((key, part.name))
    if added:
        parts = dict(t_manifest.get("parts") or {})
        for name in added:
            parts[name] = (s_manifest.get("parts") or {}).get(name)
        payload = dict(t_manifest)
        payload["parts"] = parts
        if (
            "family_section" not in payload
            and s_manifest.get("family_section")
        ):
            payload["family_section"] = s_manifest["family_section"]
        write_manifest(t_dir, payload)
        ctx.log(f"completed {key}: {len(added)} part(s)")
    else:
        ctx.report.kept_products.append(key)


def _merge_product(
    target_root: Path,
    staging: Path,
    *,
    s_report: ProductReport,
    t_report: ProductReport | None,
    report: MergeReport,
    cache: SignatureCache,
    log: Callable[[str], None],
) -> None:
    """Merge one product: judge the source and the target's state, then
    apply the one policy that state allows — a staged whole-directory
    copy when the target holds nothing, part-level completion of the
    target's misses otherwise (invariants: module docstring)."""
    design = s_report.design
    key = design.directory_name
    s_manifest = read_manifest(s_report.directory)

    if s_manifest.get("by_reference"):
        report.source_by_reference.append(key)
        log(f"skip {key}: registered by reference (its files are not in the source database)")
        return

    ok_parts = _ok_parts_of(design, s_report)
    if any(
        v.kind == VerdictKind.NONCONFORMING for v in s_report.verdicts
    ) or not ok_parts:
        report.source_needs_review.append(key)
        log(
            f"skip {key}: source needs review ({s_report.incomplete_summary()})"
        )
        return

    t_dir = t_report.directory if t_report is not None else None
    if t_dir is not None and any(
        v.kind == VerdictKind.NONCONFORMING for v in t_report.verdicts
    ):
        report.target_needs_review.append(key)
        log(f"skip {key}: target needs review, left untouched")
        return

    if t_dir is not None:
        conflicts = _product_conflicts(
            key, ok_parts, s_manifest, read_manifest(t_dir), t_report
        )
        if conflicts:
            report.conflicts.extend(conflicts)
            log(f"skip {key}: sha conflict (recorded; target untouched)")
            return

    ctx = _MergeCtx(
        target_root=target_root,
        staging=staging,
        report=report,
        cache=cache,
        log=log,
    )
    if t_dir is None:
        _stage_whole_product(s_report, ok_parts, s_manifest, ctx)
    else:
        _complete_missing_parts(s_report, t_report, ok_parts, s_manifest, ctx)


def merge_database(
    source: str | Path,
    target: str | Path,
    select: list[str] | None = None,
    *,
    verify: bool = False,
    log: Callable[[str], None] = print,
) -> MergeReport:
    """Import one local database (or any partial copy of one) into
    another, adding what is missing and touching nothing else.

    ``select`` takes glob patterns over product paths (as in
    :func:`stellarlib.build_database`) and restricts this one merge;
    a pattern matching no source product is an error. ``verify=False``
    (the default) is the fast path: parts are copied and recorded
    without hashing, and the migrated files' signature-cache entries
    are invalidated — scoped to exactly the migrated portion — so the
    next scan or ``verify_database`` re-hashes them once against their
    recorded evidence. ``verify=True`` hashes every copied part against
    the source's recorded sha256 before it counts as merged (a failure
    aborts the merge loudly) and seeds the cache with the verified
    answers instead.
    """

    source = Path(source)
    target = Path(target)
    if not source.is_dir():
        raise NotADirectoryError(f"{source} is not a database directory")
    if not target.is_dir():
        raise NotADirectoryError(
            f"{target} is not a database directory; create it (or build "
            "its blueprint) first"
        )
    if source.resolve() == target.resolve():
        raise ValueError("source and target are the same database")

    staging = target / ".staging"
    if staging.exists():
        shutil.rmtree(staging)  # sweep leftovers of an interrupted run
    staging.mkdir(parents=True, exist_ok=True)

    # read-only scans: a merge judges, it does not re-verify in place
    source_report = scan_database(
        source, signature_cache=SignatureCache(source, writable=False)
    )
    target_report = scan_database(
        target, signature_cache=SignatureCache(target, writable=False)
    )
    report = MergeReport(source=source, target=target, verify=verify)

    present = [
        p for p in source_report.products if p.directory is not None
    ]
    selected = set(
        _apply_select([p.design.directory_name for p in present], select)
    )
    t_index = {p.design.directory_name: p for p in target_report.products}

    cache = SignatureCache(target)
    try:
        for s_product in present:
            if s_product.design.directory_name not in selected:
                continue
            _merge_product(
                target,
                staging,
                s_report=s_product,
                t_report=t_index.get(s_product.design.directory_name),
                report=report,
                cache=cache,
                log=log,
            )
        cache.save_if_dirty()
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if report.changed:
        refresh_inventory(target)
    else:
        log("nothing merged; derived artifacts left as they are")
    return report
