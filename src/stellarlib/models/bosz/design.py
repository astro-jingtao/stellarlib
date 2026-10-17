"""BOSZ family adapter: window designs and MAST fetching.

A BOSZ product is one declared window — (resolution, [a/M], [C/M]) plus
the (Teff, log g, [M/H]) points to hold. The design enumerates one resam
file per point using the filename grammar and the atmosphere-family
policy proven downstream (ms for log g <= 3, vmicro 2; mp for log g >=
3.5 and Teff <= 8000 K, vmicro 1; ap for Teff >= 8250 K and for
7500-8000 K where mp is unavailable, vmicro 2), plus the per-resolution
wavelength grid. Fetching is per-file HTTP from the verified MAST tree; a
404 is recorded as a failed fetch attempt — the part stays missing and
is retried on the next build. No publication status is inferred from an
HTTP status; unpublished nodes are known only through the confirmed
family policy constants (family_for) at design time.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import requests

from stellarlib.database.design import (
    PartDesign,
    PartVerdict,
    ProductDesign,
    VerdictKind,
    classify_part_generic,
)
from stellarlib.exceptions import SourceUnavailableError
from stellarlib.models.bosz.reader import parse_filename

__all__ = ["BoszAdapter", "BOSZ_ADAPTER", "bosz_filename", "family_for"]

_CHUNK = 1 << 20
_TIMEOUT_GET = 1800
_RELEASE = "2024"
_BASE = "https://archive.stsci.edu/hlsps/bosz/bosz2024"


def family_for(teff: int, logg: float) -> tuple[str, int]:
    """Atmosphere family and vmicro for one (Teff, log g) point.

    The documented assignment (verified against the release tree): ms
    (MARCS spherical, v2) for log g <= 3; mp (MARCS plane-parallel, v1)
    for log g >= 3.5 and Teff <= 8000 K; ap (ATLAS9, v2) for Teff >= 8250
    and for 7500-8000 K where mp is unpublished. log g strictly between 3
    and 3.5 has no family (the published grids step over the seam);
    Teff strictly between 8000 and 8250 K with log g > 3 has none either.
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
    teff: int, logg: float, mh: float, am: float, cm: float, resolution: int
) -> str:
    atmos, vmicro = family_for(teff, logg)
    return (
        f"bosz2024_{atmos}_t{teff}_g{logg:+.1f}_m{mh:+.2f}_a{am:+.2f}"
        f"_c{cm:+.2f}_v{vmicro}_r{resolution}_resam.txt.gz"
    )


def _spectrum_url(filename: str, resolution: int, mh: float) -> str:
    return f"{_BASE}/r{resolution}/m{mh:+.2f}/{filename}"


def _window_facts(declaration: Mapping) -> dict:
    return {
        "resolution": int(declaration["resolution"]),
        "teffs": [int(t) for t in declaration["teffs"]],
        "loggs": [float(g) for g in declaration["loggs"]],
        "mhs": [float(m) for m in declaration["mhs"]],
        "am": float(declaration.get("am", 0.0)),
        "cm": float(declaration.get("cm", 0.0)),
    }


def _enumerate_parts(facts: Mapping) -> tuple[PartDesign, ...]:
    parts = []
    seen = set()
    for teff in facts["teffs"]:
        for logg in facts["loggs"]:
            for mh in facts["mhs"]:
                name = bosz_filename(
                    teff, logg, mh, facts["am"], facts["cm"], facts["resolution"]
                )
                if name in seen:
                    continue
                seen.add(name)
                parts.append(
                    PartDesign(
                        name=name,
                        url=_spectrum_url(name, facts["resolution"], mh),
                    )
                )
    parts.append(
        PartDesign(
            name=f"bosz2024_wave_r{facts['resolution']}.txt",
            url=f"{_BASE}/wavelength_grids/"
            f"bosz2024_wave_r{facts['resolution']}.txt",
            role="wavelength_grid",
        )
    )
    return tuple(parts)


def _window_design(declaration: Mapping) -> ProductDesign:
    facts = _window_facts(declaration)
    label = str(declaration["label"])
    return ProductDesign(
        family="bosz",
        collection=f"r{facts['resolution']}",
        label=label,
        kind="official_window",
        parts=_enumerate_parts(facts),
        facts=facts,
    )


class BoszAdapter:
    family = "bosz"

    def designs(self, declaration: Mapping) -> Iterable[ProductDesign]:
        return (_window_design(declaration),)

    def design_of_manifest(self, manifest: Mapping) -> ProductDesign | None:
        section = manifest.get("family_section") or {}
        window = section.get("window")
        if window is None:
            return None
        return _window_design(dict(window, label=manifest["label"]))

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
        if part.url is None:
            raise SourceUnavailableError(
                f"{part.name}: no fetch URL in the design"
            )
        own_session = session is None
        if own_session:
            session = requests.Session()
            session.headers["User-Agent"] = "stellarlib (spectral database builder)"
        try:
            target = directory / part.name
            digest = hashlib.sha256()
            tmp = target.with_suffix(target.suffix + ".part")
            response = session.get(part.url, stream=True, timeout=_TIMEOUT_GET)
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
                for chunk in response.iter_content(chunk_size=_CHUNK):
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


BOSZ_ADAPTER = BoszAdapter()
