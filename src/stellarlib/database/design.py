"""Design protocol: products, parts, verdicts, and the family adapter.

The design is the authoritative statement of "what the database is meant
to hold": the blueprint — a local record of the complete upstream
library. It is split by ownership exactly like the registry: this module
defines the generic protocol and the generic part classification; each
family's adapter (``models/<family>/design.py``) enumerates the upstream
publishing units (whole official packages — directory mirrors or archive
files, never subsets) and implements fetching at exactly the granularity
the source publishes.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Protocol

import requests

from stellarlib.database.signature_cache import SignatureCache
from stellarlib.integrity import sha256_file

__all__ = [
    "PartDesign",
    "ProductDesign",
    "PartVerdict",
    "VerdictKind",
    "FamilyAdapter",
    "classify_part_generic",
    "fetch_streamed_part",
    "FETCH_USER_AGENT",
]


@dataclasses.dataclass(frozen=True)
class PartDesign:
    """One file the design declares: a filename within its product.

    ``url`` is where the file is fetched from; ``None`` marks a
    local-only part (no official channel exists).
    """

    name: str
    url: str | None = None
    role: str = "spectrum"  # "spectrum" | "lineid" | "bulk_manifest" | "directory_index" | "wavelength_grid" | "archive" | "parameters" | "provenance_snapshot" | "atlas"


@dataclasses.dataclass(frozen=True)
class ProductDesign:
    """One declared product directory and its designed parts."""

    family: str
    collection: str  # directory level 2: bosz "r500"; tlusty "OSTAR2002"
    label: str
    kind: str  # "official_mirror" | "local_user_files"
    parts: tuple[PartDesign, ...]
    facts: Mapping[str, object] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", tuple(self.parts))
        object.__setattr__(self, "facts", dict(self.facts))

    @property
    def directory_name(self) -> str:
        return f"{self.family}/{self.collection}/{self.label}"


class VerdictKind:
    OK = "ok"
    MISSING = "missing"
    NONCONFORMING = "nonconforming"


@dataclasses.dataclass(frozen=True)
class PartVerdict:
    """The two-state judgment of one designed part."""

    part: str
    kind: str  # VerdictKind
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))

    @property
    def summary(self) -> str:
        if self.kind == VerdictKind.OK:
            return "ok"
        detail = "; ".join(self.reasons)
        return f"{self.kind} ({detail})" if detail else self.kind


def classify_part_generic(
    design: ProductDesign,
    part: PartDesign,
    directory: Path,
    manifest: Mapping,
    cache: SignatureCache | None = None,
) -> PartVerdict:
    """The generic part classification, shared by every family.

    missing: the file is absent (a recorded failed fetch keeps the part
    missing — nothing is inferred from an HTTP status, and the builder
    retries it on the next run). nonconforming: present but failing the
    recorded integrity evidence. ok: present and passing.

    A present part with a recorded sha256 is verified against BOTH
    recorded quantities: a size mismatch is the verdict on its own —
    identical bytes cannot have different sizes, so there is nothing a
    full read could clear — and a matching size proceeds to the sha256
    comparison, never skipped on size equality alone. ``cache`` (the
    database's quick-signature cache) serves that sha256 answer
    without reading an unchanged file; without one, every check is a
    full read. A record without sha256 (a 404 attempt, a checksum-less
    registration) leaves the part ok with no evidence to check.
    """
    path = directory / part.name
    if not path.exists():
        return PartVerdict(part=part.name, kind=VerdictKind.MISSING)
    recorded = (manifest.get("parts") or {}).get(part.name)
    expected = (recorded or {}).get("sha256")
    if expected is None:
        return PartVerdict(part=part.name, kind=VerdictKind.OK)
    size_expected = (recorded or {}).get("size_bytes")
    if size_expected is not None:
        size_actual = path.stat().st_size
        if size_actual != size_expected:
            return PartVerdict(
                part=part.name,
                kind=VerdictKind.NONCONFORMING,
                reasons=(
                    f"integrity: size {size_actual} != recorded "
                    f"{size_expected} bytes",
                ),
            )
    actual = cache.sha256(path) if cache is not None else sha256_file(path)
    if actual.lower() != str(expected).lower():
        return PartVerdict(
            part=part.name,
            kind=VerdictKind.NONCONFORMING,
            reasons=(f"integrity: sha256 {actual} != recorded {expected}",),
        )
    return PartVerdict(part=part.name, kind=VerdictKind.OK)


FETCH_USER_AGENT = "stellarlib (spectral database builder)"
_FETCH_CHUNK = 1 << 20
_FETCH_TIMEOUT_GET = 1800


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_streamed_part(
    part: PartDesign,
    directory: Path,
    *,
    session=None,
    log=print,
) -> Mapping:
    """Fetch one designed part over HTTP into its product directory.

    The body is streamed into an atomic temporary (whole file or
    nothing), the sha256 is computed on the fly, and the returned
    mapping is the part's MANIFEST entry. An HTTP 404 is returned as a
    recorded failed attempt (the part stays missing — no publication
    claim is inferred from a status); every other transport error
    propagates. A ``None`` session creates a plain one.
    """
    own_session = session is None
    if own_session:
        session = requests.Session()
        session.headers["User-Agent"] = FETCH_USER_AGENT
    try:
        target = directory / part.name
        digest = hashlib.sha256()
        tmp = target.with_suffix(target.suffix + ".part")
        response = session.get(part.url, stream=True, timeout=_FETCH_TIMEOUT_GET)
        if response.status_code == 404:
            log(f"  404 {part.name} (fetch failed; part stays missing)")
            return {
                "file": part.name,
                "http_status": 404,
                "error": "not found",
                "url": part.url,
                "attempted_at": _now_iso(),
            }
        response.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in response.iter_content(chunk_size=_FETCH_CHUNK):
                fh.write(chunk)
                digest.update(chunk)
        tmp.replace(target)
        return {
            "file": part.name,
            "sha256": digest.hexdigest(),
            "size_bytes": target.stat().st_size,
            "retrieved_at": _now_iso(),
            "url": part.url,
        }
    finally:
        if own_session:
            session.close()


class FamilyAdapter(Protocol):
    """What a family contributes to the generic database machinery."""

    family: str

    def designs(self, declaration: Mapping) -> Iterable[ProductDesign]:
        """Enumerate the declared products for one blueprint entry (a
        ``{"family": ...}`` declaration) — the family's complete
        upstream publishing surface, whole units only."""
        ...

    def expand_design(
        self, design: ProductDesign, directory: Path
    ) -> ProductDesign:
        """Replace a phase-1 design with the full part list once the
        product directory holds the upstream manifest part (a BOSZ
        bulkdl script). Families whose units are single files return
        the design unchanged."""
        ...

    def design_of_manifest(
        self, manifest: Mapping, directory: Path
    ) -> ProductDesign | None:
        """The design a manifest describes (for registered products)."""
        ...

    def classify_part(
        self, design: ProductDesign, part: PartDesign, directory: Path,
        manifest: Mapping, cache: SignatureCache | None = None,
    ) -> PartVerdict:
        ...

    def fetch_part(
        self,
        design: ProductDesign,
        part: PartDesign,
        directory: Path,
        *,
        session=None,
        log=print,
    ) -> Mapping:
        """Fetch one missing part; returns its manifest parts entry.

        Raises :class:`~stellarlib.exceptions.SourceUnavailableError` for
        parts with no fetchable channel. A 404 from the upstream records
        the failed attempt (status, url, time) and leaves the part
        missing — a failed fetch is never promoted to a claim about what
        the upstream publishes.
        """
        ...
