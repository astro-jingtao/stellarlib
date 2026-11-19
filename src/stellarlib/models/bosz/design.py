"""BOSZ family adapter: whole-directory mirrors of the MAST release tree.

The upstream publishing unit is one (broadening, metallicity) directory,
and its official bulk package is the per-directory ``bulkdl.sh`` script —
upstream's own manifest listing every spectrum file of that directory.
A stellarlib product is therefore one directory's complete mirror: the
design fetches the official script first (a designed part in its own
right, cached in the product directory under its upstream name), then
every file the script lists. The rorig directories additionally hold
line-identification files that no bulkdl script covers (the one official
``bulkdl-lineids.sh`` names "all" but lists m-2.50 only — an upstream
defect); their complete authoritative listing is the directory's own
Apache index, which the rorig mirrors therefore also fetch and cache as
a designed part (``directory_index.html``), and the lineid files enter
the blueprint from it. The blueprint declares all 112 directory mirrors
plus the ``wavelength_grids`` directory; no subset enumeration exists
anywhere on the fetch path.

Query-side publication knowledge stays in ``family_for`` (the confirmed
atmosphere-family and vmicro assignment): grids opened over a mirror
select one composition and the family-policy vmicro per node, and the
seams (log g strictly between 3 and 3.5; 8000-8250 K at log g > 3) are
known only there — never inferred from an HTTP status. A fetch 404 is
recorded as a failed fetch attempt; the part stays missing and is
retried on the next build.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import dataclasses
from pathlib import Path
import re

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
    "BOSZ_ADAPTER",
    "BOSZ_BROADENINGS",
    "BOSZ_METALLICITIES",
    "BoszAdapter",
    "bosz_filename",
    "bulkdl_script_name",
    "family_for",
    "parse_directory_index",
]

_BASE = "https://archive.stsci.edu/hlsps/bosz/bosz2024"

# The verified upstream inventory (docs/data-sources.md, catalog.json):
# 8 broadenings x 14 metallicity directories, each holding
# 43,204-45,724 spectrum files depending on metallicity (628,620 in
# total per broadening) as enumerated by its official bulkdl script.
_BROADENING_NAMES = {
    500: "r500",
    1000: "r1000",
    2000: "r2000",
    5000: "r5000",
    10000: "r10000",
    20000: "r20000",
    50000: "r50000",
    "orig": "rorig",
}
BOSZ_BROADENINGS: tuple[int | str, ...] = tuple(_BROADENING_NAMES)
BOSZ_METALLICITIES: tuple[float, ...] = (
    0.75, 0.5, 0.25, 0.0, -0.25, -0.5, -0.75,
    -1.0, -1.25, -1.5, -1.75, -2.0, -2.25, -2.5,
)
_WAVE_PRODUCT_LABEL = "all"
_WAVE_FILES = tuple(
    f"bosz2024_wave_r{r}.txt"
    for r in (500, 1000, 2000, 5000, 10000, 20000, 50000)
)

_CURL_LINE_RE = re.compile(
    r'^curl\s+.*--output\s+"([^"]+)"\s+"(https://[^"]+)"'
)

# The cached copy of one directory's Apache index (upstream serves the
# listing at the directory URL itself, under no filename of its own).
_DIRECTORY_INDEX_NAME = "directory_index.html"
_INDEX_HREF_RE = re.compile(r'<a href="([^"]+)">')
_INDEX_PRODUCT_RE = re.compile(r"_(resam|noresam|lineid)\.txt\.gz$")


def family_for(teff: int, logg: float) -> tuple[str, int]:
    """Atmosphere family and vmicro for one (Teff, log g) point.

    The documented assignment (verified against the release tree): ms
    (MARCS spherical, v2) for log g <= 3; mp (MARCS plane-parallel, v1)
    for log g >= 3.5 and Teff <= 8000 K; ap (ATLAS9, v2) for Teff >= 8250
    and for 7500-8000 K where mp is unpublished. log g strictly between 3
    and 3.5 has no family (the published grids step over the seam);
    Teff strictly between 8000 and 8250 K with log g > 3 has none either.

    This is query-side knowledge — how a grid picks each node's file
    inside a mirror. The fetch path never consults it: what is
    published is what the official bulkdl script lists.
    """
    if logg <= 3.0:
        return "ms", 2
    if logg >= 3.5:
        if teff <= 7500:
            return "mp", 1
        if teff <= 8000:
            return "ap", 2  # mp unpublished in 7500-8000 for log g > 3
        if teff >= 8250:
            return "ap", 2
        raise ValueError(
            f"no atmosphere family covers Teff {teff} K at log g {logg} "
            "(8000-8250 K gap between the MARCS and ATLAS9 domains)"
        )
    raise ValueError(
        f"log g {logg} lies strictly between the MARCS spherical (<= 3) "
        "and plane-parallel (>= 3.5) domains; the published grid steps "
        "over this seam — declare grid points at 3.0 or 3.5"
    )


def bosz_filename(
    *,
    teff: int,
    logg: float,
    mh: float,
    am: float,
    cm: float,
    resolution: int,
) -> str:
    """The canonical filename of one node's spectrum (query-side naming)."""
    atmos, vmicro = family_for(teff, logg)
    return (
        f"bosz2024_{atmos}_t{teff}_g{logg:+.1f}_m{mh:+.2f}_a{am:+.2f}"
        f"_c{cm:+.2f}_v{vmicro}_r{resolution}_resam.txt.gz"
    )


def bulkdl_script_name(broadening: int | str, mh: float) -> str:
    """The upstream name of one directory's official bulk-download script."""
    return (
        f"hlsp_bosz_bosz2024_sim_{_BROADENING_NAMES[broadening]}_"
        f"m{mh:+.2f}_v1_bulkdl.sh"
    )


def _metallicity_directory(mh: float) -> str:
    return f"m{mh:+.2f}"


def parse_bulkdl_script(text: str) -> tuple[tuple[str, str], ...]:
    """Extract every (filename, url) pair from an official bulkdl script.

    The script is upstream's manifest of one directory; its lines are
    ``curl ... --output "<relative path>" "<absolute url>"``. Only
    well-formed lines are accepted — a script stellarlib cannot parse
    verbatim is a hard error, never a partially-adopted file list.
    """
    pairs = []
    for line in text.splitlines():
        if not line.startswith("curl "):
            continue
        match = _CURL_LINE_RE.match(line)
        if match is None:
            raise ValueError(
                f"unparseable bulkdl script line: {line!r}; the upstream "
                "script format changed — re-verify the catalog"
            )
        output_path, url = match.groups()
        filename = output_path.rsplit("/", 1)[-1]
        pairs.append((filename, url))
    if not pairs:
        raise ValueError("bulkdl script lists no files")
    return tuple(pairs)


def parse_directory_index(
    text: str, directory_url: str
) -> tuple[tuple[str, str], ...]:
    """Extract every line-identification (filename, url) pair from the
    Apache index of one rorig directory.

    The index is upstream's own complete listing of the directory — the
    only official channel that includes the lineid files. Every listed
    file must carry a known product slot (resam, noresam, lineid);
    anything else means the release changed and the catalog needs
    re-verification (a hard error, never a partially-adopted list). Only
    the lineid files are returned: the spectra come from the official
    bulkdl script (the package definition), so the script stays the
    spectra manifest and the index supplies what the package omits.
    """
    directory_url = directory_url if directory_url.endswith("/") else directory_url + "/"
    pairs = []
    kinds_seen = set()
    for match in _INDEX_HREF_RE.finditer(text):
        href = match.group(1)
        if href.endswith("/"):
            continue  # parent/subdirectory navigation
        name = href.rsplit("/", 1)[-1]
        slot_match = _INDEX_PRODUCT_RE.search(name)
        if slot_match is None:
            raise ValueError(
                f"directory index lists {name!r}, which carries no known "
                "BOSZ product slot (resam/noresam/lineid) — the release "
                "changed; re-verify the catalog"
            )
        kinds_seen.add(slot_match.group(1))
        if slot_match.group(1) == "lineid":
            pairs.append((name, directory_url + name))
    if "lineid" not in kinds_seen:
        raise ValueError(
            "directory index lists no lineid files — this is not an rorig "
            "directory index; re-verify the catalog"
        )
    return tuple(pairs)


def _directory_design(broadening: int | str, mh: float) -> ProductDesign:
    name = _BROADENING_NAMES[broadening]
    script = bulkdl_script_name(broadening, mh)
    parts = [
        PartDesign(
            name=script,
            url=f"{_BASE}/download_scripts/{script}",
            role="bulk_manifest",
        )
    ]
    if name == "rorig":
        # the rorig directories hold the lineid product line, which the
        # official bulkdl scripts omit; the directory's own Apache index
        # is its complete authoritative listing
        parts.append(
            PartDesign(
                name=_DIRECTORY_INDEX_NAME,
                url=f"{_BASE}/rorig/{_metallicity_directory(mh)}/",
                role="directory_index",
            )
        )
    return ProductDesign(
        family="bosz",
        collection=name,
        label=_metallicity_directory(mh),
        kind="official_mirror",
        parts=tuple(parts),
        facts={"broadening": name, "metallicity": mh},
    )


def _wave_design() -> ProductDesign:
    return ProductDesign(
        family="bosz",
        collection="wavelength_grids",
        label=_WAVE_PRODUCT_LABEL,
        kind="official_mirror",
        parts=tuple(
            PartDesign(
                name=name,
                url=f"{_BASE}/wavelength_grids/{name}",
                role="wavelength_grid",
            )
            for name in _WAVE_FILES
        ),
        facts={"directory": "wavelength_grids"},
    )


class BoszAdapter:
    """The BOSZ family's database adapter: the 112 directory mirrors plus the wavelength grids."""
    family = "bosz"

    def designs(self, declaration: Mapping) -> Iterable[ProductDesign]:
        """The complete upstream surface for a ``{"family": "bosz"}``
        blueprint entry: all 112 directory mirrors plus the
        wavelength_grids directory. There is no partial form."""
        designs = [
            _directory_design(b, mh)
            for b in BOSZ_BROADENINGS
            for mh in BOSZ_METALLICITIES
        ]
        designs.append(_wave_design())
        return designs

    def expand_design(
        self, design: ProductDesign, directory: Path
    ) -> ProductDesign:
        """Replace the phase-1 parts (the bulkdl script, and for rorig
        directories the Apache index) with the phase-1 parts plus every
        file they enumerate, once each is cached in the product
        directory."""
        if design.facts.get("metallicity") is None:
            return design  # wavelength_grids product needs no expansion
        parts = list(design.parts)
        script_part = next(p for p in parts if p.role == "bulk_manifest")
        script = directory / script_part.name
        if script.is_file():
            listed = parse_bulkdl_script(script.read_text(encoding="utf-8"))
            known = {p.name for p in parts}
            parts += [
                PartDesign(name=name, url=url)
                for name, url in listed
                if name not in known
            ]
        index_part = next(
            (p for p in parts if p.role == "directory_index"), None
        )
        if index_part is not None and index_part.url:
            index = directory / index_part.name
            if index.is_file():
                listed = parse_directory_index(
                    index.read_text(encoding="utf-8", errors="replace"),
                    index_part.url,
                )
                known = {p.name for p in parts}
                parts += [
                    PartDesign(name=name, url=url, role="lineid")
                    for name, url in listed
                    if name not in known
                ]
        if len(parts) == len(design.parts):
            return design
        return dataclasses.replace(design, parts=tuple(parts))

    def design_of_manifest(
        self, manifest: Mapping, directory: Path
    ) -> ProductDesign | None:
        """The official design a manifest describes (for registered products)."""
        section = manifest.get("family_section") or {}
        mirror = section.get("mirror")
        if mirror is None:
            return None
        broadening = mirror["broadening"]
        key = int(broadening[1:]) if broadening != "rorig" else "orig"
        design = _directory_design(key, float(mirror["metallicity"]))
        design = self.expand_design(design, Path(directory))
        if manifest.get("label") != design.label:
            design = dataclasses.replace(design, label=manifest["label"])
        return design

    def classify_part(
        self, design: ProductDesign, part: PartDesign, directory: Path,
        manifest: Mapping, cache: SignatureCache | None = None,
    ) -> PartVerdict:
        """The generic two-state classification, shared by every family."""
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
        """Fetch one missing part over HTTP (streamed, hashed)."""
        if part.url is None:
            raise SourceUnavailableError(
                f"{part.name}: no fetch URL in the design"
            )
        return fetch_streamed_part(part, directory, session=session, log=log)


BOSZ_ADAPTER = BoszAdapter()
