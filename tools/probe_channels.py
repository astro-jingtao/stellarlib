"""Probe the upstream channels from this machine (operational tooling).

Answers one question: which acquisition channels can THIS machine
reach, with exactly the HTTP client the builder itself uses (requests,
honoring the environment's proxy settings)? Run it on any candidate
download machine before choosing where to fetch from; a channel that
answers here is a channel ``build_database`` can use there.

Usage::

    python tools/probe_channels.py            # every URL below
    python tools/probe_channels.py --basel    # only the BaSeL channels

Interpretation: ``OK`` = the URL answered with the expected payload
(size in bytes); ``NOT-FOUND`` = reachable but the catalog is absent on
that mirror; ``BODY-ERROR`` = HTTP 200 with an error page (the CfA
failure mode: check the printed first line); ``FAIL`` = no answer
(transport error / timeout — the proxy-tunnel hang is this machine's
known CDS failure mode, see docs/data-sources.md).
"""

from __future__ import annotations

import argparse
import sys

import requests

TIMEOUT_S = 20

# (label, URL, note) — the acquisition channels that matter for the
# families still needing an egress decision, plus controls that
# separate "no egress at all" from "this host is blocked".
CHANNELS: list[tuple[str, str, str]] = [
    # -- controls ----------------------------------------------------------
    (
        "control-egress",
        "https://api.github.com",
        "any 2xx/3xx answer proves the machine has egress at all",
    ),
    (
        "control-adac-stelib",
        "http://vizier.nao.ac.jp/ftp/cats/III/232/ReadMe",
        (
            "the channel the whole STELIB tree was verified through "
            "(2026-11-18); its health here is the baseline"
        ),
    ),
    # -- BaSeL 3.1 = VizieR J/A+A/381/524 ----------------------------------
    (
        "basel-cds-https-ftp",
        "https://cdsarc.cds.unistra.fr/ftp/J/A+A/381/524/ReadMe",
        (
            "the canonical CDS tree over https (the documented form of "
            "the ftp tree; unpinned from the 2026-11-18 build site — "
            "this probe is where it gets verified)"
        ),
    ),
    (
        "basel-cds-https-pub",
        "https://cdsarc.cds.unistra.fr/pub/cats/J/A+A/381/524/ReadMe",
        "alternate https form of the same tree",
    ),
    (
        "basel-cds-http-bridge",
        "http://cdsarc.u-strasbg.fr/pub/cats/J/A+A/381/524/ReadMe",
        "the u-strasbg host over plain http",
    ),
    (
        "basel-adac",
        "http://vizier.nao.ac.jp/ftp/cats/J/A+A/381/524/ReadMe",
        (
            "the NAOJ mirror's native tree (no 524 under J/A+A/381 as "
            "of 2026-11-18 — NOT-FOUND here means the mirror still "
            "lacks it)"
        ),
    ),
    (
        "basel-cfa",
        (
            "https://vizier.cfa.harvard.edu/viz-bin/asu-txt"
            "?-source=J%2FA%2BA%2F381%2F524&out.max=1"
        ),
        (
            "the CfA mirror's query view (200 with 'Table or Catalog "
            "not found' in the body = the mirror's J/A+A tree is absent)"
        ),
    ),
    (
        "basel-cfa-control",
        (
            "https://vizier.cfa.harvard.edu/viz-bin/asu-txt"
            "?-source=III%2F232&out.max=1"
        ),
        (
            "the same mirror serving a known catalog — separates mirror "
            "outage from missing tree"
        ),
    ),
    (
        "basel-author-page",
        "http://professor.ufabc.edu.br/~pieter.westera/BaSeL_ReadMe.txt",
        (
            "the author's own distribution page (index at "
            "~pieter.westera/BaSeL.html): both BaSeL 2.2 and 3.1 "
            "tarballs came from here (2026-11-18), after every VizieR "
            "channel above proved dead"
        ),
    ),
    # -- STELIB (already mirrored; channels for a re-fetch) ----------------
    (
        "stelib-cds-https-ftp",
        "https://cdsarc.cds.unistra.fr/ftp/III/232/ReadMe",
        "the canonical CDS form used by the default source=\"cds\"",
    ),
]

MIRRORS = [
    "vizier.iucaa.in",
    "vizier.inasan.ru",
    "vizier.bao.ac.cn",
    "vizier.u-tokyo.ac.jp",
    "vizier.ast.cam.ac.uk",
]


def _probe(label: str, url: str, note: str) -> str:
    try:
        response = requests.get(url, timeout=TIMEOUT_S, stream=True)
    except requests.RequestException as exc:
        reason = type(exc).__name__
        if "timeout" in str(exc).lower() or "Timeout" in reason:
            reason = f"timeout ({TIMEOUT_S}s)"
        return f"FAIL     {label:<24} {reason}"
    size = 0
    chunks = []
    for chunk in response.iter_content(chunk_size=1 << 16):
        size += len(chunk)
        chunks.append(chunk)
        if size > 4096:
            break
    body = b"".join(chunks).decode("utf-8", errors="replace")
    first = body.splitlines()[0][:60] if body else ""
    if response.status_code == 404:
        return f"NOT-FOUND {label:<24} reachable, catalog absent"
    if response.status_code >= 400:
        return f"FAIL     {label:<24} HTTP {response.status_code}"
    if "not found" in body[:2000].lower():
        telling = next(
            (
                line.strip()[:70]
                for line in body.splitlines()
                if "not found" in line.lower()
            ),
            first,
        )
        return (
            f"BODY-ERROR {label:<24} HTTP 200 but the body says: "
            f"{telling!r}"
        )
    return f"OK       {label:<24} HTTP {response.status_code}, ~{size} B: {first!r}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--basel", action="store_true", help="only the BaSeL channels"
    )
    args = parser.parse_args(argv)

    channels = CHANNELS
    if args.basel:
        channels = [c for c in CHANNELS if c[0].startswith("basel")]
    print(f"probing {len(channels)} channel(s), timeout {TIMEOUT_S}s each")
    print("(requests honors http_proxy/https_proxy like the builder does)")
    print()
    for label, url, note in channels:
        print(f"# {note}")
        print(_probe(label, url, note))
        print()
    print(
        "ftp-only form (curl, not the builder's client): "
        "curl --max-time 20 -sI "
        "ftp://cdsarc.u-strasbg.fr/pub/cats/J/A+A/381/524/ReadMe"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
