"""BaSeL family adapter: the author-page tarballs as whole products.

The upstream publishing surface (Pieter Westera's page at UFABC, the
one live channel after every VizieR route for J/A+A/381/524 died —
see docs/data-sources.md) publishes exactly three files: the BaSeL 2.2
tarball, the BaSeL 3.1 tarball, and the format ReadMe. A stellarlib
product is one of those files: fetched whole, stored verbatim as its
single part, spectra streamed from the tar members. The blueprint
declares all three; no finer fetch granularity exists upstream, and
none is invented here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import dataclasses
from pathlib import Path

import requests

from stellarlib.database.design import (
    PartDesign,
    PartVerdict,
    ProductDesign,
    classify_part_generic,
    fetch_streamed_part,
)
from stellarlib.database.signature_cache import SignatureCache
from stellarlib.exceptions import SourceUnavailableError

__all__ = [
    "AUTHOR_PAGE",
    "BASEL_ADAPTER",
    "BASEL_FILES",
    "BASEL_README",
    "BaselAdapter",
]

AUTHOR_PAGE = "http://professor.ufabc.edu.br/~pieter.westera/BaSeL.html"
BASEL_README = "BaSeL_ReadMe.txt"

# one product per published file: release -> (collection, label, file,
# role). The 3.1 tarball holds BOTH calibration variants — the variant
# is a grid-open selection on the read side, never a fetch distinction.
BASEL_FILES: dict[str, dict] = {
    "2.2": {
        "archive": "BaSeL2_2.tar.gz",
        "url": "http://professor.ufabc.edu.br/~pieter.westera/BaSeL2_2.tar.gz",
        "size_bytes": 27295538,
        "sha256": (
            "8bbc772dcbad4e62e38dd2b2a7c73543c2c0a3ad61d57766c1cf7d6ed39ce68b"
        ),
        "role": "archive",
    },
    "3.1": {
        "archive": "BaSeL3_1.tar.gz",
        "url": "http://professor.ufabc.edu.br/~pieter.westera/BaSeL3_1.tar.gz",
        "size_bytes": 18053781,
        "sha256": (
            "8e5f3fd597ab27143d508f50b15c205246b19d2c684bd77f37a88c3cbf87ea2a"
        ),
        "role": "archive",
    },
    "readme": {
        "archive": BASEL_README,
        "url": f"http://professor.ufabc.edu.br/~pieter.westera/{BASEL_README}",
        "size_bytes": 2377,
        "sha256": (
            "e43cd9de4bf21e52f1fdd2e1353b3da1315cf0e9bec24dc5b6a80e5479c576d6"
        ),
        "role": "provenance_snapshot",
    },
}


def _file_design(key: str, info: Mapping) -> ProductDesign:
    return ProductDesign(
        family="basel",
        collection=key if key != "readme" else "format",
        label="library" if key != "readme" else "readme",
        kind="official_mirror",
        parts=(
            PartDesign(
                name=info["archive"], url=info["url"], role=info["role"]
            ),
        ),
        facts={
            "release": key,
            "archive": info["archive"],
            "page": AUTHOR_PAGE,
            "size_bytes": info["size_bytes"],
            "sha256": info["sha256"],
        },
    )


class BaselAdapter:
    """The BaSeL family's database adapter: one whole published file
    per product, the generic classification and fetch."""

    family = "basel"

    def designs(self, declaration: Mapping) -> Iterable[ProductDesign]:
        """The complete upstream surface for a ``{"family": "basel"}``
        blueprint entry: the two version tarballs and the format
        ReadMe. There is no partial form."""
        return [_file_design(key, info) for key, info in BASEL_FILES.items()]

    def expand_design(
        self, design: ProductDesign, directory: Path
    ) -> ProductDesign:
        """Nothing to expand: one file is one part."""
        return design

    def design_of_manifest(
        self, manifest: Mapping, directory: Path
    ) -> ProductDesign | None:
        """The official design a manifest describes, matched by its
        recorded archive name (a registered product)."""
        section = manifest.get("family_section") or {}
        mirror = section.get("mirror")
        if mirror is None or "archive" not in mirror:
            return None
        for key, info in BASEL_FILES.items():
            if mirror["archive"] == info["archive"]:
                design = _file_design(key, info)
                if manifest.get("label") not in (None, design.label):
                    design = dataclasses.replace(
                        design, label=manifest["label"]
                    )
                return design
        return None

    def classify_part(
        self,
        design: ProductDesign,
        part: PartDesign,
        directory: Path,
        manifest: Mapping,
        cache: SignatureCache | None = None,
    ) -> PartVerdict:
        """The generic two-state classification (shared by every
        single-file family)."""
        return classify_part_generic(design, part, directory, manifest, cache)

    def fetch_part(
        self,
        design: ProductDesign,
        part: PartDesign,
        directory: Path,
        *,
        session: requests.Session | None = None,
        log: Callable[[str], None] = print,
    ) -> Mapping:
        """Fetch the one file over HTTP, streamed and hashed (the
        generic fetch)."""
        if part.url is None:
            raise SourceUnavailableError(
                f"{part.name}: no fetch URL in the design"
            )
        return fetch_streamed_part(part, directory, session=session, log=log)


BASEL_ADAPTER = BaselAdapter()
