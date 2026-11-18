"""STELIB family adapter: the whole VizieR III/232 catalog tree as one
product.

The upstream publishing unit is the catalog tree: the star table
``table.dat``, the corrected spectra ``cor_sp/`` (255 files — the
published library), their raw counterparts ``obs_sp/`` (258 files), the
catalog's ``ReadMe``, and the published atlas ``stelib.ps.gz``. A
stellarlib product mirrors that whole tree, no subsets.

The tree is published by CDS (the canonical source) and mirrored whole
by the NAOJ ADAC VizieR mirror; the blueprint entry picks the channel
explicitly — ``{"family": "stelib", "source": "cds"}`` (default) or
``{"family": "stelib", "source": "adac"}`` — and each fetched part's
manifest entry records the URL it actually came from. There is no
fallback between channels: a failing channel fails loudly (see
docs/data-sources.md for what was verified where).

The design starts as the three top-level files; ``table.dat`` is the
upstream's own manifest of the tree (its spectrum-file columns name
every ``cor_sp``/``obs_sp`` member), so once it is held the design
expands to the complete 516-part list — the BOSZ bulkdl expansion
pattern applied to the catalog's own table.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from stellarlib.database.design import (
    PartDesign,
    PartVerdict,
    ProductDesign,
    classify_part_generic,
    fetch_streamed_part,
)
from stellarlib.database.signature_cache import SignatureCache
from stellarlib.exceptions import SourceUnavailableError
from stellarlib.models.stelib.reader import (
    parse_star_table,
    star_table_among,
)

__all__ = [
    "ATLAS_NAME",
    "CATALOG_BASES",
    "DEFAULT_SOURCE",
    "README_NAME",
    "STELIB_ADAPTER",
    "TABLE_NAME",
    "StelibAdapter",
]

# the catalog tree's own bases: CDS canonical (the documented https form
# of the ftp tree — not verifiable from the 2026-11-18 build site, whose
# egress cannot reach cdsarc at all) and the NAOJ ADAC mirror (verified
# 2026-11-18 by fetching the entire tree through it)
CATALOG_BASES = {
    "cds": "https://cdsarc.cds.unistra.fr/ftp/III/232",
    "adac": "http://vizier.nao.ac.jp/ftp/cats/III/232",
}
DEFAULT_SOURCE = "cds"

TABLE_NAME = "table.dat"
README_NAME = "ReadMe"
ATLAS_NAME = "stelib.ps.gz"


def _phase_one(source: str) -> ProductDesign:
    base = CATALOG_BASES[source]
    return ProductDesign(
        family="stelib",
        collection="library",
        label="all",
        kind="official_mirror",
        parts=(
            PartDesign(
                name=README_NAME, url=f"{base}/ReadMe", role="provenance_snapshot"
            ),
            PartDesign(name=TABLE_NAME, url=f"{base}/table.dat", role="bulk_manifest"),
            PartDesign(name=ATLAS_NAME, url=f"{base}/stelib.ps.gz", role="atlas"),
        ),
        facts={
            "catalog": "III/232",
            "channel": source,
            "channels": dict(CATALOG_BASES),
        },
    )


def _expanded(source: str, table: Path) -> ProductDesign:
    """The complete design, its spectrum parts enumerated from the held
    star table (the upstream's own manifest of the tree)."""
    design = _phase_one(source)
    rows = parse_star_table(table.read_text(encoding="utf-8"))
    cor_names = sorted({n for row in rows for n in row["cor_files"] if n})
    obs_names = sorted({n for row in rows for n in row["obs_files"] if n})
    base = CATALOG_BASES[source]
    parts = [
        PartDesign(name=f"cor_sp/{name}", url=f"{base}/cor_sp/{name}")
        for name in cor_names
    ]
    parts += [
        PartDesign(name=f"obs_sp/{name}", url=f"{base}/obs_sp/{name}")
        for name in obs_names
    ]
    return ProductDesign(
        family=design.family,
        collection=design.collection,
        label=design.label,
        kind=design.kind,
        parts=design.parts + tuple(parts),
        facts=dict(design.facts),
    )


class StelibAdapter:
    family = "stelib"

    def designs(self, declaration: Mapping) -> Iterable[ProductDesign]:
        """The complete upstream surface for a ``{"family": "stelib"}``
        blueprint entry: the one catalog-tree product. ``source`` picks
        the fetch channel (see the module docstring); there is no
        partial form."""
        source = declaration.get("source", DEFAULT_SOURCE)
        if source not in CATALOG_BASES:
            raise ValueError(
                f"stelib declaration source={source!r}; known channels: "
                f"{sorted(CATALOG_BASES)}"
            )
        return [_phase_one(source)]

    def expand_design(self, design: ProductDesign, directory: Path) -> ProductDesign:
        table = star_table_among([directory / TABLE_NAME])
        if table is None or not table.exists():
            return design  # phase one: the tree's top-level files only
        source = design.facts.get("channel", DEFAULT_SOURCE)
        return _expanded(source, table)

    def design_of_manifest(
        self, manifest: Mapping, directory: Path
    ) -> ProductDesign | None:
        section = manifest.get("family_section") or {}
        mirror = section.get("mirror")
        if mirror is None or mirror.get("catalog") != "III/232":
            return None
        source = mirror.get("channel", DEFAULT_SOURCE)
        if source not in CATALOG_BASES:
            return None
        design = _phase_one(source)
        if manifest.get("label") != design.label:
            import dataclasses

            design = dataclasses.replace(design, label=manifest["label"])
        return design

    def classify_part(
        self,
        design: ProductDesign,
        part: PartDesign,
        directory: Path,
        manifest: Mapping,
        cache: SignatureCache | None = None,
    ) -> PartVerdict:
        return classify_part_generic(design, part, directory, manifest, cache)

    def fetch_part(
        self,
        design: ProductDesign,
        part: PartDesign,
        directory: Path,
        *,
        session=None,
        log=print,
    ) -> Mapping:
        if part.url is None:
            raise SourceUnavailableError(f"{part.name}: no fetch URL in the design")
        # spectrum parts live in the tree's cor_sp/ and obs_sp/
        # subdirectories; a part name with a parent creates it (the
        # flat families never need this)
        (directory / part.name).parent.mkdir(parents=True, exist_ok=True)
        return fetch_streamed_part(part, directory, session=session, log=log)


STELIB_ADAPTER = StelibAdapter()
