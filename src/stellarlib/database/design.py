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
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from stellarlib.integrity import sha256_file

__all__ = [
    "PartDesign",
    "ProductDesign",
    "PartVerdict",
    "VerdictKind",
    "FamilyAdapter",
    "classify_part_generic",
]


@dataclasses.dataclass(frozen=True)
class PartDesign:
    """One file the design declares: a filename within its product.

    ``url`` is where the file is fetched from; ``None`` marks a
    local-only part (no official channel exists).
    """

    name: str
    url: str | None = None
    role: str = "spectrum"  # "spectrum" | "lineid" | "bulk_manifest" | "directory_index" | "wavelength_grid" | "archive"


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
) -> PartVerdict:
    """The generic part classification, shared by every family.

    missing: the file is absent (a recorded failed fetch keeps the part
    missing — nothing is inferred from an HTTP status, and the builder
    retries it on the next run). nonconforming: present but its sha256
    disagrees with the recorded one (integrity). ok: present and
    matching.
    """
    path = directory / part.name
    recorded = (manifest.get("parts") or {}).get(part.name)
    if not path.exists():
        return PartVerdict(part=part.name, kind=VerdictKind.MISSING)
    expected = (recorded or {}).get("sha256")
    if expected is None:
        return PartVerdict(part=part.name, kind=VerdictKind.OK)
    actual = sha256_file(path)
    if actual.lower() != str(expected).lower():
        return PartVerdict(
            part=part.name,
            kind=VerdictKind.NONCONFORMING,
            reasons=(f"integrity: sha256 {actual} != recorded {expected}",),
        )
    return PartVerdict(part=part.name, kind=VerdictKind.OK)


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
        manifest: Mapping,
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
