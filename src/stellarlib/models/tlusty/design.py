"""TLUSTY family adapter: local products only, by design.

TLUSTY distributes per-composition tar archives with no per-file URLs
(the tar inventory still awaits live verification — see catalog.json), so
the fetch channel is deliberately closed: products enter a database
through ``register_local_product`` (files you hold, checksummed and
recorded) until the archive channel is verified. The adapter still
enumerates designs for scanning: a registered product's design is its
own manifest part list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from stellarlib.database.design import (
    PartDesign,
    PartVerdict,
    ProductDesign,
    classify_part_generic,
)
from stellarlib.exceptions import SourceUnavailableError

__all__ = ["TlustyAdapter", "TLUSTY_ADAPTER"]


class TlustyAdapter:
    family = "tlusty"

    def designs(self, declaration: Mapping) -> Iterable[ProductDesign]:
        raise SourceUnavailableError(
            "tlusty publishes no per-file download channel (per-composition "
            "vispec tars, inventory not yet verified live); declare tlusty "
            "products with register_local_product(files=...) instead — see "
            "docs/data-sources.md"
        )

    def design_of_manifest(self, manifest: Mapping) -> ProductDesign | None:
        parts = tuple(
            PartDesign(name=name, url=None)
            for name in (manifest.get("parts") or {})
        )
        if not parts:
            return None
        release = manifest["release"]
        return ProductDesign(
            family="tlusty",
            collection=release,
            label=manifest["label"],
            kind="local_user_files",
            parts=parts,
            facts={"composition": (manifest.get("family_section") or {}).get("composition")},
        )

    def classify_part(
        self, design: ProductDesign, part: PartDesign, directory: Path,
        manifest: Mapping,
    ) -> PartVerdict:
        return classify_part_generic(design, part, directory, manifest)

    def fetch_part(
        self,
        design: ProductDesign,
        part: PartDesign,
        directory: Path,
        *,
        session=None,
        log=print,
    ) -> Mapping:
        raise SourceUnavailableError(
            f"{part.name}: tlusty has no per-file fetch channel; obtain the "
            "vispec tar from tlusty.oca.eu, extract the kind-7 files, and "
            "register them with register_local_product"
        )


TLUSTY_ADAPTER = TlustyAdapter()
