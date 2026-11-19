"""build_database, verify_database, and register_local_product: the write paths.

The blueprint (design.json) is a local record of the complete upstream
library: every declared family expands to its whole official packages —
never a subset. The builder completes what the blueprint declares and
what is missing, one part per fetch, checksummed on arrival; for
directory-mirror products the upstream manifests arrive first (a BOSZ
bulkdl script, plus for the rorig directories the Apache index that
carries the lineid product line), and their arrival expands the design
to the full file list within the same build. Products containing nonconforming
parts are never touched (a checksum pass proves completeness, not design
membership); a failed fetch (e.g. an HTTP 404) is recorded as exactly
that failed attempt and the part stays missing — the next build retries
it. ``dry_run=True`` reports the plan without touching the disk.
A pass may fetch its parts through bounded parallelism
(``build_database(workers=N)``) — a speed axis only: per-part semantics,
the two-state scan, and resume behavior are identical.
The verifier re-reads what the builder left: every recorded part is
checked against both recorded quantities, and a present part whose
manifest write was lost (a transport-failed pass) gets its evidence
backfilled — hashed in place and recorded. It never fetches.
Registration admits locally held files as products — copied in by
default (self-contained, portable), or referenced in place for sets that
already live where their project wants them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
import dataclasses
from datetime import datetime, timezone
from fnmatch import fnmatch
import json
from pathlib import Path
import shutil
import tarfile
import threading
from typing import NamedTuple

import requests

from stellarlib.database.core import Database, ProductRecord, open_database
from stellarlib.database.design import (
    FamilyAdapter,
    PartDesign,
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
from stellarlib.database.scan import (
    ADAPTERS,
    DatabaseReport,
    enumerate_designs,
    scan_database,
)
from stellarlib.database.signature_cache import SignatureCache
from stellarlib.exceptions import SourceUnavailableError
from stellarlib.integrity import sha256_file
from stellarlib.models.basel.design import BASEL_FILES
from stellarlib.models.bosz.reader import (
    parse_filename as parse_bosz_filename,
    recognize_bosz,
    recognize_wave_grid,
)
from stellarlib.models.miles.reader import PARAMS_NAME_RE as miles_params_name, recognize_miles
from stellarlib.models.stelib.reader import TABLE_NAME, recognize_stelib_fits
from stellarlib.models.tlusty.backend import _infer_release
from stellarlib.models.tlusty.reader import parse_filename, recognize_tlusty
from stellarlib.registry import references

__all__ = [
    "DESIGN_SCHEMA",
    "ProductVerification",
    "VerifyReport",
    "build_database",
    "register_local_product",
    "verify_database",
]

DESIGN_SCHEMA = "stellarlib-design/2"


def _new_fetch_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "stellarlib (spectral database builder)"
    return session


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
    with open(path, "w", encoding="utf-8", newline="\n") as _f:
        _f.write(json.dumps(payload, indent=2, sort_keys=True))


def _release_of(family: str, collection: str) -> str:
    if family == "bosz":
        return "2024"
    if family == "miles":
        return "2011"
    if family == "stelib":
        return "2003"
    return collection


def _format_of(family: str) -> str:
    return {
        "bosz": "bosz_resam",
        "tlusty": "tlusty_vis",
        "miles": "miles_fits",
        "stelib": "stelib_fits",
        "basel": "basel_cor",
    }[family]


# persist the in-memory manifest after this many arrivals within one
# fetch pass (see _record): bounds the unrecorded window of a
# transport-failed pass without a JSON rewrite per part
_MANIFEST_FLUSH_PARTS = 2000


def _product_manifest(design: ProductDesign, directory: Path) -> dict:
    """The product's manifest: what a previous pass left, or the fresh
    official-package record."""
    if (directory / "MANIFEST.json").exists():
        return read_manifest(directory)
    return empty_manifest(
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


def _expands_design(part: PartDesign, entry: dict) -> bool:
    """Whether one arrived part expands the design: an upstream manifest
    of the directory (the official bulkdl script; for rorig also the
    directory index) now enumerates every file it lists."""
    return part.role in ("bulk_manifest", "directory_index") and (
        "sha256" in entry
    )


class _ManifestRecorder:
    """Record part arrivals into a manifest, flushing in chunks.

    The streaming hash of a fetch is the file's sha256: recording it
    against the file's quick signature saves the first post-fetch
    classification a full read. A mirror product fetches tens of
    thousands of parts in ONE pass, and a transport failure mid-pass
    would otherwise leave every arrived file present-but-unrecorded
    (classified ok with no integrity evidence, never re-verified); a
    chunked flush bounds that loss window at _MANIFEST_FLUSH_PARTS
    arrivals without paying a JSON rewrite per part.
    """

    def __init__(
        self,
        directory: Path,
        manifest: dict,
        cache: SignatureCache | None,
    ) -> None:
        self.directory = directory
        self.manifest = manifest
        self.cache = cache
        self.arrived = 0

    def __call__(self, part: PartDesign, entry: dict) -> bool:
        """Record one arrival; True when it expands the design."""
        self.manifest["parts"][part.name] = entry
        if self.cache is not None and "sha256" in entry:
            self.cache.remember(self.directory / part.name, entry["sha256"])
        self.arrived += 1
        if self.arrived % _MANIFEST_FLUSH_PARTS == 0:
            write_manifest(self.directory, self.manifest)
            if self.cache is not None:
                self.cache.save_if_dirty()
        return _expands_design(part, entry)


def _classify_pass(
    design: ProductDesign,
    adapter: FamilyAdapter,
    directory: Path,
    manifest: dict,
    *,
    cache: SignatureCache | None,
    log: Callable[[str], None],
) -> list[tuple[ProductDesign, PartVerdict, object]]:
    """One scan/classify pass: the parts still missing, or [] when the
    product holds nonconforming parts.

    Invariant: a product with nonconforming parts is never completed by
    the builder — checksum validity proves integrity, not design
    membership.
    """
    verdicts = [
        adapter.classify_part(design, part, directory, manifest, cache)
        for part in design.parts
    ]
    bad = [v for v in verdicts if v.kind == VerdictKind.NONCONFORMING]
    if bad:
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


def _fetch_part(
    adapter: FamilyAdapter,
    directory: Path,
    log: Callable[[str], None],
    pending_item: tuple,
    session_of: Callable[[], requests.Session],
) -> tuple:
    """Fetch one pending part; returns (part, manifest entry)."""
    current, _verdict, part = pending_item
    log(f"  fetch {part.name}")
    entry = dict(
        adapter.fetch_part(
            current, part, directory, session=session_of(), log=log
        )
    )
    return part, entry


def _fetch_sequential(
    pending: list,
    session: requests.Session,
    adapter: FamilyAdapter,
    directory: Path,
    *,
    log: Callable[[str], None],
    record: _ManifestRecorder,
) -> bool:
    """One strictly sequential fetch pass; True when an arrival expanded
    the design — the pass then ends early, and the next pass covers the
    files the arrival enumerates."""
    for pending_item in pending:
        try:
            part, entry = _fetch_part(
                adapter, directory, log, pending_item, lambda: session
            )
        except Exception:
            # same contract as the parallel path: persist what did
            # arrive, then fail loudly
            write_manifest(directory, record.manifest)
            raise
        if record(part, entry):
            return True
    return False


class _SessionPool:
    """Per-worker-thread fetch sessions for one parallel pass."""

    def __init__(
        self, session_factory: Callable[[], requests.Session] | None
    ) -> None:
        self._factory = session_factory
        self._local = threading.local()
        self._created: list[requests.Session] = []

    def session(self) -> requests.Session:
        """This thread's session, created on first use."""
        own = getattr(self._local, "session", None)
        if own is None:
            own = (
                _new_fetch_session()
                if self._factory is None
                else self._factory()
            )
            self._created.append(own)
            self._local.session = own
        return own

    def close_all(self) -> None:
        """Close every session the pool created."""
        for own in self._created:
            closer = getattr(own, "close", None)
            if closer is not None:
                closer()


def _fetch_parallel(
    pending: list,
    workers: int,
    session_factory: Callable[[], requests.Session] | None,
    adapter: FamilyAdapter,
    directory: Path,
    *,
    log: Callable[[str], None],
    record: _ManifestRecorder,
) -> bool:
    """One parallel fetch pass (``workers`` threads, each holding its
    own session); True when an arrival expanded the design.

    The first failure fails the run loudly (the same contract as the
    sequential path); the rest of the batch drains — every part fetch
    is atomic, so a drained batch leaves only complete files. What did
    arrive is persisted before the raise: the next run resumes with
    full integrity records for every present file.
    """
    abort = threading.Event()
    pool = _SessionPool(session_factory)

    def fetch_or_abort(pending_item: tuple) -> tuple:
        if abort.is_set():
            raise SourceUnavailableError(
                f"{pending_item[2].name}: fetch batch aborted after an "
                "earlier fetch failure"
            )
        return _fetch_part(adapter, directory, log, pending_item, pool.session)

    expanded = False
    failure: BaseException | None = None
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch_or_abort, item) for item in pending]
            for future in as_completed(futures):
                if future.cancelled():
                    continue
                try:
                    part, entry = future.result()
                except Exception as exc:
                    if failure is None:
                        failure = exc
                        abort.set()
                        for other in futures:
                            other.cancel()
                    continue
                if record(part, entry):
                    expanded = True
    finally:
        pool.close_all()
    if failure is not None:
        write_manifest(directory, record.manifest)
        raise failure
    return expanded


def _build_product(
    design: ProductDesign,
    adapter: FamilyAdapter,
    root: Path,
    *,
    session: requests.Session | None,
    log: Callable[[str], None],
    workers: int = 1,
    session_factory: Callable[[], requests.Session] | None = None,
    cache: SignatureCache | None = None,
) -> None:
    """Complete one product: expand the design, fetch missing parts, and
    re-expand once the upstream bulk manifest part has arrived.

    ``workers`` bounds the parallelism of one fetch pass (1 = strictly
    sequential, the default); per-part semantics are identical either
    way (see :func:`build_database`). ``cache`` is the database's
    signature cache (see :func:`build_database` for what it does)."""
    directory = root / design.family / design.collection / design.label
    directory.mkdir(parents=True, exist_ok=True)
    manifest = _product_manifest(design, directory)
    manifest.setdefault("family_section", {}).setdefault(
        "mirror", dict(design.facts)
    )
    design = adapter.expand_design(design, directory)
    record = _ManifestRecorder(directory, manifest, cache)

    while True:
        pending = _classify_pass(
            design, adapter, directory, manifest, cache=cache, log=log
        )
        if not pending:
            break
        # a pass ends early only when a bulk manifest part arrives and
        # the design expands; the next pass then covers the listed files
        if workers <= 1:
            expanded = _fetch_sequential(
                pending, session, adapter, directory, log=log, record=record
            )
        else:
            expanded = _fetch_parallel(
                pending, workers, session_factory, adapter, directory,
                log=log, record=record,
            )
        if not expanded:
            break
        design = adapter.expand_design(design, directory)
    write_manifest(directory, manifest)
    if cache is not None:
        cache.save_if_dirty()


def _apply_select(names: Iterable[str], patterns: list[str] | None) -> list[str]:
    """Filter product names by glob patterns over their paths.

    A pattern that matches no declared product is an error — a typo'd
    selector must never masquerade as a completed round.
    """
    names = list(names)
    if patterns is None:
        return tuple(names)
    unmatched = [p for p in patterns if not any(fnmatch(n, p) for n in names)]
    if unmatched:
        raise ValueError(
            f"select pattern(s) match no declared product: {unmatched}; "
            f"declared: {names}"
        )
    return tuple(n for n in names if any(fnmatch(n, pat) for pat in patterns))


def _check_fetch_mode(
    session: requests.Session | None,
    session_factory: Callable[[], requests.Session] | None,
    workers: int,
) -> None:
    """Validate the fetch-mode argument combination (see
    :func:`build_database`)."""
    if workers < 1:
        raise ValueError(f"workers must be >= 1; got {workers}")
    if session is not None and workers > 1:
        raise ValueError(
            "session= serves the sequential path (workers=1); for "
            "workers > 1 pass session_factory= — each worker thread "
            "holds its own session"
        )
    if session_factory is not None and workers <= 1:
        raise ValueError(
            "session_factory= serves the parallel path (workers > 1); "
            "the sequential path injects its single session via session="
        )


def _report_dry_run(
    report: DatabaseReport,
    patterns: list[str] | None,
    log: Callable[[str], None],
) -> None:
    """Log the dry-run plan: per-product state plus totals. Touches
    nothing on disk."""
    chosen = _apply_select([p.label_path for p in report.products], patterns)
    by_name = {p.label_path: p for p in report.products}
    shown = [by_name[name] for name in chosen]
    for product in shown:
        missing = [
            v for v in product.verdicts if v.kind == VerdictKind.MISSING
        ]
        log(
            f"{product.label_path}: {product.state} "
            f"({len(missing)} part(s) to fetch)"
        )
    if patterns is not None:
        log(
            f"selection: {len(shown)} of {len(report.products)} "
            "declared product(s) match select"
        )
    missing_parts = sum(
        1
        for p in shown
        for v in p.verdicts
        if v.kind == VerdictKind.MISSING
    )
    nonconforming = sum(
        1
        for p in shown
        for v in p.verdicts
        if v.kind == VerdictKind.NONCONFORMING
    )
    log(
        f"total: {len(shown)} product(s), "
        f"{missing_parts} missing part(s), "
        f"{nonconforming} nonconforming (manual "
        "review, never auto-fetched)"
    )


def build_database(
    database: str | Path,
    *,
    declarations: Iterable[dict] | None = None,
    session: requests.Session | None = None,
    dry_run: bool = False,
    select: Iterable[str] | None = None,
    log: Callable[[str], None] = print,
    workers: int = 1,
    session_factory: Callable[[], requests.Session] | None = None,
) -> Database | None:
    """Build (or complete) a local database against its blueprint.

    ``declarations`` adds blueprint entries (``{"family": "bosz"}``,
    ``{"family": "tlusty"}``) to the database's ``design.json``; a
    family entry declares the family's complete upstream library — every
    official package, whole. A ``None`` keeps the existing blueprint —
    completing a previous build. Missing parts are fetched through the
    family adapter (streamed, sha256 recorded in the MANIFEST at
    arrival); every present part with recorded evidence is verified
    against both its size and its sha256 — a size mismatch is
    nonconforming on its own; a matching size still pays the sha256
    comparison, served from the database's quick-signature cache
    (``signature_cache.json``; unchanged files cost one stat — see
    :mod:`stellarlib.database.signature_cache`, also for the
    delete-to-re-verify rule). Products with nonconforming parts are
    skipped with a notice, never patched. ``dry_run=True`` reports the
    plan and touches nothing.

    ``select`` restricts what this one run fetches — a list of glob
    patterns matched against product paths (``"family/collection/
    label"``, e.g. ``"bosz/rorig/*"``, ``"tlusty/OSTAR2002/G"``). The
    blueprint is untouched: unselected products keep their state and a
    later run without ``select`` completes them. Fetch order is the
    builder's concern, never a design axis — ``select`` only chooses
    which whole products this round works on.

    ``workers`` bounds the parallelism of one product's fetch pass
    (default 1 = strictly sequential): with ``workers > 1`` the pending
    parts of one pass are fetched by that many worker threads, each
    holding its own session (created by ``session_factory``, or a plain
    ``requests.Session`` with the builder user agent when omitted).
    Per-part semantics are identical either way: every part is fetched
    whole or not at all (atomic tmp+replace, so an interrupted batch
    leaves no partial files), its manifest entry is recorded on arrival,
    a 404 stays a recorded failed fetch, and a transport error fails the
    run loudly after the in-flight batch drains — the next build resumes
    exactly where this one stopped. ``session`` (a single session) is
    the sequential path's injection point and cannot be combined with
    ``workers > 1``.
    """
    root = Path(database)
    _check_fetch_mode(session, session_factory, workers)
    merged = _merge_designs(root, declarations)
    if not merged:
        raise ValueError(
            "no blueprint declared: pass declarations=[{'family': ...}] "
            "once; later builds reuse the database's design.json"
        )
    patterns = None if select is None else [str(p) for p in select]
    if patterns is not None and not patterns:
        raise ValueError("select must not be an empty list")

    if dry_run:
        root.mkdir(parents=True, exist_ok=True)
        report = scan_database(
            root,
            declarations=list(declarations or []),
            signature_cache=SignatureCache(root, writable=False),
        )
        _report_dry_run(report, patterns, log)
        return None

    root.mkdir(parents=True, exist_ok=True)
    _write_designs(root, merged)
    cache = SignatureCache(root)
    report = scan_database(root, signature_cache=cache)

    own_session = False
    if session is None:
        # one session reused across the whole sequential build: a
        # session per part would pay connection setup on every file
        session = _new_fetch_session()
        own_session = True
    try:
        chosen = _apply_select(
            [p.label_path for p in report.products], patterns
        )
        by_name = {p.label_path: p for p in report.products}
        for name in chosen:
            product = by_name[name]
            adapter = ADAPTERS[product.design.family]
            _build_product(
                product.design, adapter, root,
                session=session, log=log,
                workers=workers, session_factory=session_factory,
                cache=cache,
            )

        refresh_inventory(root)
        cache.save_if_dirty()
        return open_database(root)
    finally:
        if own_session:
            session.close()


@dataclasses.dataclass(frozen=True)
class ProductVerification:
    """One product's outcome of a verify pass (see :func:`verify_database`)."""

    label_path: str
    directory: Path | None  # None: the product was never fetched
    recorded_ok: int  # present parts that passed their recorded evidence
    backfilled: int  # present parts whose evidence was added this run
    missing: int
    nonconforming: tuple[tuple[str, str], ...]  # (part name, reason)

    @property
    def state(self) -> str:
        """absent / incomplete / complete, from the part verdicts."""
        if self.directory is None:
            return "absent"
        if self.missing == 0 and not self.nonconforming:
            return "complete"
        return "incomplete"


@dataclasses.dataclass(frozen=True)
class VerifyReport:
    """The outcome of one :func:`verify_database` pass."""

    root: Path
    products: tuple[ProductVerification, ...]

    def to_dict(self) -> dict:
        """The report as a JSON-ready mapping."""
        return {
            "root": str(self.root),
            "products": [
                {
                    "product": v.label_path,
                    "state": v.state,
                    "recorded_ok": v.recorded_ok,
                    "backfilled": v.backfilled,
                    "missing": v.missing,
                    "nonconforming": [
                        {"part": name, "reason": reason}
                        for name, reason in v.nonconforming
                    ],
                }
                for v in self.products
            ],
        }


class _PartOutcome(NamedTuple):
    part: PartDesign
    kind: str  # "ok" | "missing" | "backfill" | "nonconforming"
    reason: str | None
    size: int | None
    digest: str | None  # the file's sha256 answer when it was needed
    computed: bool  # the digest was hashed this call, not served by peek


def _verify_part(
    part: PartDesign,
    directory: Path,
    recorded: dict | None,
    cache: SignatureCache,
) -> _PartOutcome:
    """Judge one part for the verifier — the same verdicts as
    ``classify_part_generic``, plus the backfill case (present on disk,
    no recorded sha256), with the digest returned so the caller records
    it. Never touches the manifest; safe to run on worker threads (the
    cache is only peeked, never mutated)."""
    path = directory / part.name
    try:
        st = path.stat()
    except FileNotFoundError:
        return _PartOutcome(part, "missing", None, None, None, False)
    expected = (recorded or {}).get("sha256")
    size_expected = (recorded or {}).get("size_bytes")
    if (
        expected is not None
        and size_expected is not None
        and st.st_size != size_expected
    ):
        # identical bytes cannot have different sizes: the verdict on its
        # own, nothing a full read could clear
        return _PartOutcome(
            part,
            "nonconforming",
            f"integrity: size {st.st_size} != recorded {size_expected} bytes",
            st.st_size,
            None,
            False,
        )
    digest = cache.peek(str(path), st.st_size, st.st_mtime_ns)
    computed = digest is None
    if computed:
        digest = sha256_file(path)
    if expected is None:
        return _PartOutcome(part, "backfill", None, st.st_size, digest, computed)
    if digest.lower() != str(expected).lower():
        return _PartOutcome(
            part,
            "nonconforming",
            f"integrity: sha256 {digest} != recorded {expected}",
            st.st_size,
            digest,
            computed,
        )
    return _PartOutcome(part, "ok", None, st.st_size, digest, computed)


def _judge_parts(
    design: ProductDesign,
    directory: Path,
    parts: dict,
    cache: SignatureCache,
    workers: int,
) -> list[_PartOutcome]:
    """Judge every designed part of one product.

    A hashing failure aborts before any manifest mutation: outcomes are
    collected whole first, so a failed product leaves no partial state
    (the cache holds nothing of it either — workers only peek).
    """

    def judge(part: PartDesign) -> _PartOutcome:
        return _verify_part(part, directory, parts.get(part.name), cache)

    if workers <= 1:
        return [judge(part) for part in design.parts]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(judge, design.parts))


def _apply_verify_outcomes(
    outcomes: list[_PartOutcome],
    parts: dict,
    directory: Path,
    manifest: dict,
    cache: SignatureCache,
) -> tuple[int, int, int, list[tuple[str, str]]]:
    """Fold judged outcomes into counts and manifest backfills.

    Returns (recorded_ok, missing, backfilled, nonconforming). The
    manifest is persisted in chunks as backfill evidence arrives — the
    cache piggybacks on the same flushes.
    """
    recorded_ok = missing = backfilled = 0
    nonconforming: list[tuple[str, str]] = []
    arrivals = 0
    for outcome in outcomes:
        if outcome.computed and outcome.digest is not None:
            cache.remember(directory / outcome.part.name, outcome.digest)
        if outcome.kind == "ok":
            recorded_ok += 1
        elif outcome.kind == "missing":
            missing += 1
        elif outcome.kind == "backfill":
            # present on disk, no recorded sha256: the file arrived in an
            # earlier fetch round whose manifest write was lost — hash in
            # place, keep whatever history the half-written entry holds
            entry = dict(parts.get(outcome.part.name) or {})
            entry.update(
                file=outcome.part.name,
                sha256=outcome.digest,
                size_bytes=outcome.size,
                verified_at=_now_iso(),
                url=outcome.part.url,
                evidence="sha256_verified_in_place",
            )
            parts[outcome.part.name] = entry
            backfilled += 1
            arrivals += 1
            if arrivals % _MANIFEST_FLUSH_PARTS == 0:
                write_manifest(directory, manifest)
                cache.save_if_dirty()
        else:
            nonconforming.append((outcome.part.name, outcome.reason or ""))
    return recorded_ok, missing, backfilled, nonconforming


def _verify_product(
    design: ProductDesign,
    adapter: FamilyAdapter,
    root: Path,
    *,
    log: Callable[[str], None],
    cache: SignatureCache,
    workers: int = 1,
) -> ProductVerification:
    """Verify one product in place: judge every designed part, backfill
    evidence for present-but-unrecorded ones, persist the manifest in
    chunks as evidence arrives (the cache piggybacks on the same
    flushes), and never fetch."""
    directory = root / design.family / design.collection / design.label
    if not directory.exists():
        return ProductVerification(design.directory_name, None, 0, 0, 0, ())
    manifest = read_manifest(directory)
    design = adapter.expand_design(design, directory)
    parts = manifest["parts"]
    outcomes = _judge_parts(design, directory, parts, cache, workers)
    recorded_ok, missing, backfilled, nonconforming = _apply_verify_outcomes(
        outcomes, parts, directory, manifest, cache
    )
    if backfilled:
        write_manifest(directory, manifest)
    cache.save_if_dirty()
    return ProductVerification(
        design.directory_name,
        directory,
        recorded_ok,
        backfilled,
        missing,
        tuple(nonconforming),
    )


def verify_database(
    database: str | Path,
    select: Iterable[str] | None = None,
    workers: int = 1,
    log: Callable[[str], None] = print,
) -> VerifyReport:
    """One full-integrity pass over a database, against its blueprint.

    Every product the blueprint declares is judged in place. Recorded
    parts are verified against both recorded quantities (size, then
    sha256 — served from the database's signature cache, so unchanged
    files cost one stat); present parts with no recorded sha256 — files
    whose fetch round completed but whose manifest write was lost — are
    hashed in place and their evidence recorded (``evidence:
    sha256_verified_in_place``), which is the backfill that turns
    present-ok-no-evidence into present-with-proof. Missing parts are
    reported, never fetched; nonconforming parts are reported for human
    decision and never touched. Products never fetched report as
    ``absent``. The signature cache is learned from and saved along the
    way (piggybacking the manifest's chunked flushes), so a re-run after
    an aborted pass re-reads only what it never reached.

    ``select`` restricts the pass to matching product paths (glob
    patterns, as in :func:`build_database`); the blueprint is untouched.
    ``workers`` bounds the hashing parallelism (default 1 = strictly
    sequential) — a speed axis only: per-part semantics are identical,
    worker threads only stat, peek, and hash, and a hashing error fails
    the run loudly having written nothing for the failing product.

    The pass covers blueprint products; registered local products
    (``register_local_product``) carry complete evidence by
    construction and are not its subject.
    """
    root = Path(database)
    if workers < 1:
        raise ValueError(f"workers must be >= 1; got {workers}")
    if not root.is_dir():
        raise NotADirectoryError(root)
    declarations = _merge_designs(root, None)
    if not declarations:
        raise ValueError(
            "no blueprint declared: verify_database judges parts against "
            "the database's design.json — declare it once with "
            "build_database(declarations=[{'family': ...}], ...)"
        )
    patterns = None if select is None else [str(p) for p in select]
    if patterns is not None and not patterns:
        raise ValueError("select must not be an empty list")

    designs = enumerate_designs(root)
    cache = SignatureCache(root)
    verifications: list[ProductVerification] = []
    for name in _apply_select(list(designs), patterns):
        design, adapter = designs[name]
        log(f"verify {name} ({len(design.parts)} designed part(s))")
        verification = _verify_product(
            design, adapter, root, log=log, cache=cache, workers=workers
        )
        verifications.append(verification)
        if verification.directory is None:
            log(f"{name}: absent (never fetched)")
        else:
            log(
                f"{name}: {verification.state} "
                f"({verification.recorded_ok} verified, "
                f"{verification.backfilled} backfilled, "
                f"{verification.missing} missing, "
                f"{len(verification.nonconforming)} nonconforming)"
            )
    total_backfilled = sum(v.backfilled for v in verifications)
    total_nonconforming = sum(len(v.nonconforming) for v in verifications)
    log(
        f"total: {len(verifications)} product(s) judged; "
        f"{total_backfilled} part(s) backfilled, {total_nonconforming} "
        "nonconforming (human decision, never touched)"
    )
    return VerifyReport(root=root, products=tuple(verifications))


def _recognize_bosz_local(path: Path) -> bool:
    """A local BOSZ source file: a spectrum or a wavelength-grid sidecar."""
    return recognize_bosz(path) or recognize_wave_grid(path)


def _recognize_tlusty_local(path: Path) -> bool:
    """A local TLUSTY source file: a spectrum or a vispec tar."""
    return recognize_tlusty(path) or path.name.endswith(".tar")


def _recognize_miles_local(path: Path) -> bool:
    """A local MILES source file: a spectrum FITS or the parameter table."""
    return recognize_miles(path) or miles_params_name.match(path.name) is not None


_STELIB_TREE_TOP = (TABLE_NAME, "ReadMe", "stelib.ps.gz")


def _recognize_stelib_local(path: Path) -> bool:
    """A local STELIB source file: a spectrum FITS or a catalog-tree top
    file."""
    return recognize_stelib_fits(path) or path.name in _STELIB_TREE_TOP


_BASEL_ARCHIVE_KEY = {info["archive"]: key for key, info in BASEL_FILES.items()}


def _recognize_basel_local(path: Path) -> bool:
    """A local BaSeL source file: one of the published archives or the
    ReadMe."""
    return path.name in _BASEL_ARCHIVE_KEY


_LOCAL_RECOGNIZERS: dict[str, Callable[[Path], bool]] = {
    "bosz": _recognize_bosz_local,
    "tlusty": _recognize_tlusty_local,
    "miles": _recognize_miles_local,
    "stelib": _recognize_stelib_local,
    "basel": _recognize_basel_local,
}


def _local_placement(
    family: str,
    path_list: list[Path],
) -> tuple[str, str]:
    """(collection, release) for a locally registered product — where
    the family's files land inside the database tree."""
    if family == "bosz":
        return _bosz_collection(path_list), "2024"
    if family == "tlusty":
        release = _tlusty_release(path_list)
        return release, release
    if family in ("miles", "stelib"):
        return "library", _release_of(family, "library")
    if family == "basel":
        keys = {
            _BASEL_ARCHIVE_KEY[p.name]
            for p in path_list
            if p.name in _BASEL_ARCHIVE_KEY
        }
        if len(keys) != 1:
            raise ValueError(
                "one basel product holds one published file (a version "
                f"tarball or the ReadMe); found {sorted(keys)}"
            )
        key = keys.pop()
        collection = key if key != "readme" else "format"
        return collection, collection
    raise ValueError(f"unknown family {family!r}")


def register_local_product(
    database: str | Path,
    family: str,
    label: str,
    files: Iterable[str | Path],
    *,
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

    recognized = _LOCAL_RECOGNIZERS.get(family)
    if recognized is None:
        raise ValueError(f"unknown family {family!r}")
    collection, release = _local_placement(family, path_list)

    for p in path_list:
        if not p.is_file():
            raise FileNotFoundError(p)
        if not recognized(p):
            raise ValueError(
                f"{p}: not recognized as a {family} source file"
            )

    target_dir = root / family / collection / label
    target_dir.mkdir(parents=True, exist_ok=True)
    cache = SignatureCache(root)
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
        digest = sha256_file(dest)
        cache.remember(dest, digest)
        parts[src.name] = {
            "file": src.name,
            "path": recorded_path,
            "sha256": digest,
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
    cache.save_if_dirty()
    refresh_inventory(root)
    return ProductRecord(directory=target_dir, manifest=manifest)


def _bosz_collection(files: list[Path]) -> str:
    resolutions = set()
    for p in files:
        if recognize_wave_grid(p):
            continue
        facts = parse_bosz_filename(p.name)
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
