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

    def test_bulkdl_scripts_live(self):
        # the official bulk packages: one script per upstream directory
        assert _head_ok(
            "https://archive.stsci.edu/hlsps/bosz/bosz2024/download_scripts/"
            "hlsp_bosz_bosz2024_sim_r500_m+0.00_v1_bulkdl.sh"
        )
        assert _head_ok(
            "https://archive.stsci.edu/hlsps/bosz/bosz2024/download_scripts/"
            "hlsp_bosz_bosz2024_sim_rorig_m-2.50_v1_bulkdl.sh"
        )

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

    def test_rorig_surface_live(self):
        # the rorig lineid channel: the directory's own Apache index (the
        # complete listing the official bulkdl scripts omit) and one
        # lineid file; plus the official README at its real URL (no
        # "2024" in the filename — the landing page's own link is broken)
        assert _head_ok(
            "https://archive.stsci.edu/hlsps/bosz/bosz2024/rorig/m+0.00/"
        )
        assert _head_ok(
            "https://archive.stsci.edu/hlsps/bosz/bosz2024/rorig/m+0.00/"
            "bosz2024_mp_t5000_g+5.0_m+0.00_a+0.00_c+0.00_v0_rorig_"
            "lineid.txt.gz"
        )
        assert _head_ok(
            "https://archive.stsci.edu/hlsps/bosz/bosz2024/"
            "hlsp_bosz_bosz_sim_all_all_v1_readme.txt"
        )


class TestTlustyCatalog:
    # the vispec tar inventory, live-verified 2026-10-18 (catalog
    # tar_inventory): the archives below were fully downloaded and their
    # exact byte sizes recorded; the rest are HEAD-checked for liveness
    _DATABASE = "https://tlusty.oca.eu/tlusty/Tlusty2002/database"

    def test_fully_verified_archives_unchanged(self):
        for archive, size in (
            ("Gvispec.tar", 89_733_120),
            ("BGvispec_v2.tar", 359_065_600),
        ):
            response = requests.head(
                f"{self._DATABASE}/{archive}",
                headers=HEADERS,
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            assert response.status_code == 200, archive
            reported = response.headers.get("Content-Length")
            if reported is not None:
                assert int(reported) == size, (
                    f"{archive}: upstream size {reported} != recorded {size}; "
                    "the archive may have been replaced — re-verify the catalog"
                )

    def test_tar_inventory_live(self):
        cat = catalog("tlusty")
        for release in ("ostar2002", "bstar2006"):
            for entry in cat["verified"]["tar_inventory"][release]["tars"].values():
                assert _head_ok(f"{self._DATABASE}/{entry['archive']}")
