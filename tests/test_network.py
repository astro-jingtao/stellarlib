"""Network tests: upstream catalog liveness and recorded-size checks.

Run explicitly with ``pytest -m network``; excluded from the default
suite. These verify that the URL templates and verified facts in the
catalogs still describe reality — a drift here means the catalog (and
possibly the fetch designs built on it) needs re-verification, recorded
in docs/data-sources.md.
"""

from __future__ import annotations

import pytest
import requests

from stellarlib.models import catalog

requests = pytest.importorskip("requests")

TIMEOUT = 60
HEADERS = {"User-Agent": "stellarlib (catalog verification)"}

pytestmark = pytest.mark.network


def _head_ok(url: str) -> bool:
    response = requests.head(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    if response.status_code == 405:  # some hosts reject HEAD
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        response.close()
    return response.status_code == 200


class TestBoszCatalog:
    def test_release_tree_live(self):
        assert _head_ok("https://archive.stsci.edu/hlsps/bosz/bosz2024/")

    def test_verified_sample_files_unchanged(self):
        cat = catalog("bosz")
        for name, size in cat["verified"]["sample_files_verified"].items():
            metallicity = "m+0.00" if "_m+0.00_" in name else "m-1.00"
            url = (
                f"https://archive.stsci.edu/hlsps/bosz/bosz2024/r500/"
                f"{metallicity}/{name}"
            )
            response = requests.head(
                url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True
            )
            assert response.status_code == 200, url
            reported = response.headers.get("Content-Length")
            if reported is not None:
                assert int(reported) == size, (
                    f"{name}: upstream size {reported} != recorded {size}; "
                    "the file may have been replaced — re-verify the catalog"
                )

    def test_wavelength_grid_live(self):
        assert _head_ok(
            "https://archive.stsci.edu/hlsps/bosz/bosz2024/"
            "wavelength_grids/bosz2024_wave_r500.txt"
        )


class TestTlustyCatalog:
    def test_distribution_host_live(self):
        assert _head_ok("https://tlusty.oca.eu/")
