"""build_database and register_local_product: the write paths.

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
Registration admits locally held files as products — copied in by
default (self-contained, portable), or referenced in place for sets that
already live where their project wants them.
"""

from __future__ import annotations

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

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
from stellarlib.database.scan import scan_database
from stellarlib.exceptions import SourceUnavailableError
from stellarlib.integrity import sha256_file
from stellarlib.models.bosz.reader import recognize_bosz, recognize_wave_grid
from stellarlib.models.tlusty.reader import recognize_tlusty
from stellarlib.registry import references

__all__ = ["build_database", "register_local_product", "DESIGN_SCHEMA"]

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
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )


def _release_of(family: str, collection: str) -> str:
    if family == "bosz":
        return "2024"
    return collection


def _format_of(family: str) -> str:
    return {"bosz": "bosz_resam", "tlusty": "tlusty_vis"}[family]


# persist the in-memory manifest after this many arrivals within one
# fetch pass (see _record): bounds the unrecorded window of a
# transport-failed pass without a JSON rewrite per part
_MANIFEST_FLUSH_PARTS = 2000


def _build_product(
    design: ProductDesign,
    adapter: FamilyAdapter,
    root: Path,
    session,
    log,
    workers: int = 1,
    session_factory=None,
) -> None:
    """Complete one product: expand the design, fetch missing parts, and
    re-expand once the upstream bulk manifest part has arrived.

    ``workers`` bounds the parallelism of one fetch pass (1 = strictly
    sequential, the default); per-part semantics are identical either
    way (see :func:`build_database`)."""
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

    def _expands_design(part: PartDesign, entry: dict) -> bool:
        return part.role in ("bulk_manifest", "directory_index") and (
            "sha256" in entry
        )

    def _record(part: PartDesign, entry: dict) -> bool:
        manifest["parts"][part.name] = entry
        # persist the manifest in chunks as parts arrive: a mirror
        # product fetches tens of thousands of parts in ONE pass, and a
        # transport failure mid-pass would otherwise leave every
        # arrived file present-but-unrecorded (classified ok with no
        # integrity evidence, never re-verified).  A chunked flush
        # bounds that loss window at _MANIFEST_FLUSH_PARTS arrivals
        # without paying a JSON rewrite per part.
        _record.arrived += 1
        if _record.arrived % _MANIFEST_FLUSH_PARTS == 0:
            write_manifest(directory, manifest)
        # an upstream manifest of the directory has arrived (the
        # official bulkdl script; for rorig also the directory index):
        # the design now enumerates every file they list
        return _expands_design(part, entry)

    _record.arrived = 0

    def _fetch_one(pending_item, session_of):
        current, _verdict, part = pending_item
        log(f"  fetch {part.name}")
        entry = dict(
            adapter.fetch_part(
                current, part, directory, session=session_of(), log=log
            )
        )
        return part, entry

    def _fetch_all(pending) -> bool:
        nonlocal design
        if workers <= 1:
            for pending_item in pending:
                try:
                    part, entry = _fetch_one(pending_item, lambda: session)
                except Exception:
                    # same contract as the parallel path: persist what
                    # did arrive, then fail loudly
                    write_manifest(directory, manifest)
                    raise
                if _record(part, entry):
                    design = adapter.expand_design(design, directory)
                    return True
            return False
        return _fetch_all_parallel(pending)

    def _fetch_all_parallel(pending) -> bool:
        nonlocal design
        abort = threading.Event()
        local = threading.local()
        created: list = []

        def worker_session():
            own = getattr(local, "session", None)
            if own is None:
                own = (
                    _new_fetch_session()
                    if session_factory is None
                    else session_factory()
                )
                created.append(own)
                local.session = own
            return own

        def fetch_or_abort(pending_item):
            if abort.is_set():
                raise SourceUnavailableError(
                    f"{pending_item[2].name}: fetch batch aborted after an "
                    "earlier fetch failure"
                )
            return _fetch_one(pending_item, worker_session)

        expanded = False
        failure: BaseException | None = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch_or_abort, item) for item in pending]
            for future in as_completed(futures):
                if future.cancelled():
                    continue
                try:
                    part, entry = future.result()
                except Exception as exc:
                    if failure is None:
                        # first failure fails the run loudly (the same
                        # contract as the sequential path); the rest of
                        # the batch drains — every part fetch is atomic,
                        # so a drained batch leaves only complete files
                        failure = exc
                        abort.set()
                        for other in futures:
                            other.cancel()
                    continue
                if _record(part, entry):
                    expanded = True
        for own in created:
            closer = getattr(own, "close", None)
            if closer is not None:
                closer()
        if failure is not None:
            # the batch has drained: persist what did arrive before the
            # loud failure — the next run resumes with full integrity
            # records for every present file
            write_manifest(directory, manifest)
            raise failure
        if expanded:
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


def _apply_select(products, patterns: list[str] | None):
    """Filter one run's products by glob patterns over their paths.

    A pattern that matches no declared product is an error — a typo'd
    selector must never masquerade as a completed round.
    """
    if patterns is None:
        return products
    names = [p.label_path for p in products]
    unmatched = [p for p in patterns if not any(fnmatch(n, p) for n in names)]
    if unmatched:
        raise ValueError(
            f"select pattern(s) match no declared product: {unmatched}; "
            f"declared: {names}"
        )
    return tuple(
        p for p in products if any(fnmatch(p.label_path, pat) for pat in patterns)
    )


def build_database(
    database: str | Path,
    declarations: Iterable[dict] | None = None,
    session=None,
    dry_run: bool = False,
    select: Iterable[str] | None = None,
    log=print,
    workers: int = 1,
    session_factory=None,
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
        report = scan_database(root, declarations=list(declarations or []))
        shown = _apply_select(report.products, patterns)
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
        return None

    root.mkdir(parents=True, exist_ok=True)
    _write_designs(root, merged)
    report = scan_database(root)

    from stellarlib.database.scan import ADAPTERS

    own_session = False
    if session is None:
        # one session reused across the whole sequential build: a
        # session per part would pay connection setup on every file
        session = _new_fetch_session()
        own_session = True
    try:
        for product in _apply_select(report.products, patterns):
            adapter = ADAPTERS[product.design.family]
            _build_product(
                product.design, adapter, root, session, log,
                workers=workers, session_factory=session_factory,
            )

        refresh_inventory(root)
        return open_database(root)
    finally:
        if own_session:
            session.close()


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
