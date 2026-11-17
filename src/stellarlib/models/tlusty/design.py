"""TLUSTY family adapter: whole vispec tar mirrors.

The upstream publishing unit is the per-composition vispec tar archive
(OSTAR2002: one tar per composition; BSTAR2006: one tar per composition
x vmicro), and every tar holds BOTH kinds of every atmosphere of its
composition — kind 7 (detailed spectrum) and kind 17 (continuum), one
model's two data, always together. A stellarlib product is one tar:
fetched whole, stored verbatim as its single part, spectra streamed from
its members. The blueprint declares all 20 tars (inventory live-verified
2026-10-18, catalog ``tar_inventory``); no finer fetch granularity
exists upstream, and none is invented here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from stellarlib.database.design import (
    PartDesign,
    PartVerdict,
    ProductDesign,
    classify_part_generic,
    fetch_streamed_part,
)
from stellarlib.database.signature_cache import SignatureCache
from stellarlib.exceptions import SourceUnavailableError

__all__ = ["TlustyAdapter", "TLUSTY_ADAPTER", "TLUSTY_TARS"]

_BASE = "https://tlusty.oca.eu/tlusty/Tlusty2002/database"

# The verified upstream inventory (catalog.json tar_inventory,
# live-verified 2026-10-18). label -> (release, archive, facts).
_OSTAR_Z = {
    "C": 2.0, "G": 1.0, "L": 0.5, "S": 0.2, "T": 0.1,
    "V": 1.0 / 30, "W": 0.02, "X": 0.01, "Y": 0.001, "Z": 0.0,
}
_BSTAR_Z = {"BC": 2.0, "BG": 1.0, "BL": 0.5, "BS": 0.2, "BT": 0.1}
TLUSTY_TARS: dict[str, dict] = {}
for _letter, _z in _OSTAR_Z.items():
    TLUSTY_TARS[_letter] = {
        "release": "OSTAR2002",
        "archive": f"{_letter}vispec.tar",
        "composition": _letter,
        "z_over_zsun": _z,
        "vmicro_km_s": 1,
    }
for _letters, _z in _BSTAR_Z.items():
    for _v in (2, 10):
        TLUSTY_TARS[f"{_letters}_v{_v}"] = {
            "release": "BSTAR2006",
            "archive": f"{_letters}vispec_v{_v}.tar",
            "composition": _letters,
            "z_over_zsun": _z,
            "vmicro_km_s": _v,
        }


def _tar_design(label: str, tar: Mapping) -> ProductDesign:
    return ProductDesign(
        family="tlusty",
        collection=tar["release"],
        label=label,
        kind="official_mirror",
        parts=(
            PartDesign(
                name=tar["archive"],
                url=f"{_BASE}/{tar['archive']}",
                role="archive",
            ),
        ),
        facts=dict(tar),
    )


class TlustyAdapter:
    family = "tlusty"

    def designs(self, declaration: Mapping) -> Iterable[ProductDesign]:
        """The complete upstream surface for a ``{"family": "tlusty"}``
        blueprint entry: all 20 vispec tars. There is no partial form."""
        return [_tar_design(label, tar) for label, tar in TLUSTY_TARS.items()]

    def expand_design(
        self, design: ProductDesign, directory: Path
    ) -> ProductDesign:
        return design  # one tar is one part; nothing to expand

    def design_of_manifest(
        self, manifest: Mapping, directory: Path
    ) -> ProductDesign | None:
        section = manifest.get("family_section") or {}
        mirror = section.get("mirror")
        if mirror is None or "archive" not in mirror:
            return None
        archive = mirror["archive"]
        for label, tar in TLUSTY_TARS.items():
            if tar["archive"] == archive:
                design = _tar_design(label, tar)
                if manifest.get("label") != design.label:
                    import dataclasses

                    design = dataclasses.replace(design, label=manifest["label"])
                return design
        return None

    def classify_part(
        self, design: ProductDesign, part: PartDesign, directory: Path,
        manifest: Mapping, cache: SignatureCache | None = None,
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
            raise SourceUnavailableError(
                f"{part.name}: no fetch URL in the design"
            )
        return fetch_streamed_part(part, directory, session=session, log=log)


TLUSTY_ADAPTER = TlustyAdapter()
