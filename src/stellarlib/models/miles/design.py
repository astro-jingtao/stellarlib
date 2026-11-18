"""MILES family adapter: the whole official library as one product.

The upstream publishing unit is the library itself: the official FITS
tarball (``MILES_library_v9.1_FITS.tar.gz`` — exactly 985 flat
single-star members, live-verified 2026-11-17), the official parameter
table it is joined by, and the library's own web page stating the
coverage facts (3525-7500 Å, 2.5 Å FWHM, relative flux with the
absolutely-calibrated subset available only upon request). A stellarlib
product is that whole package: three designed parts, fetched verbatim,
no subsets. The site also publishes an ASCII re-encoding of the same 985
spectra and the CaT library — same data in another format and a
different library respectively, both out of this product (see
docs/data-sources.md).
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

__all__ = [
    "LIBRARY_PAGE_NAME",
    "LIBRARY_PAGE_URL",
    "LIBRARY_TAR_NAME",
    "LIBRARY_TAR_URL",
    "MILES_ADAPTER",
    "MILES_SPECTRA",
    "PARAMS_NAME",
    "PARAMS_URL",
    "MilesAdapter",
]

_BASE = "https://research.iac.es/proyecto/miles"
_TARBALLS = f"{_BASE}/media/tarfiles/Stellar_Libraries"

LIBRARY_TAR_NAME = "MILES_library_v9.1_FITS.tar.gz"
LIBRARY_TAR_URL = f"{_TARBALLS}/{LIBRARY_TAR_NAME}"
PARAMS_NAME = "paramsMILES_v9.1.txt"
PARAMS_URL = f"{_TARBALLS}/{PARAMS_NAME}"
LIBRARY_PAGE_NAME = "miles-library.html"
LIBRARY_PAGE_URL = (
    f"{_BASE}/pages/stellar-libraries/miles-library.php"
)

# the official tarball's member count (live-verified 2026-11-17: the
# archive holds exactly the flat s0001.fits..s0985.fits); the reader
# joins these against the parameter table and fails loudly on any
# count mismatch
MILES_SPECTRA = 985


def _library_design() -> ProductDesign:
    return ProductDesign(
        family="miles",
        collection="library",
        label="all",
        kind="official_mirror",
        parts=(
            PartDesign(
                name=LIBRARY_TAR_NAME,
                url=LIBRARY_TAR_URL,
                role="archive",
            ),
            PartDesign(
                name=PARAMS_NAME,
                url=PARAMS_URL,
                role="parameters",
            ),
            PartDesign(
                name=LIBRARY_PAGE_NAME,
                url=LIBRARY_PAGE_URL,
                role="provenance_snapshot",
            ),
        ),
        facts={
            "package": LIBRARY_TAR_NAME,
            "version": "v9.1",
            "n_spectra": MILES_SPECTRA,
        },
    )


class MilesAdapter:
    family = "miles"

    def designs(self, declaration: Mapping) -> Iterable[ProductDesign]:
        """The complete upstream surface for a ``{"family": "miles"}``
        blueprint entry: the one spectral library product. There is no
        partial form."""
        return [_library_design()]

    def expand_design(
        self, design: ProductDesign, directory: Path
    ) -> ProductDesign:
        return design  # whole files; nothing expands inside a build

    def design_of_manifest(
        self, manifest: Mapping, directory: Path
    ) -> ProductDesign | None:
        section = manifest.get("family_section") or {}
        mirror = section.get("mirror")
        if mirror is None or mirror.get("package") != LIBRARY_TAR_NAME:
            return None
        design = _library_design()
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
            raise SourceUnavailableError(
                f"{part.name}: no fetch URL in the design"
            )
        return fetch_streamed_part(part, directory, session=session, log=log)


MILES_ADAPTER = MilesAdapter()
