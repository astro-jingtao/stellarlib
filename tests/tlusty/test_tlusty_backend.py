"""Tests for the TLUSTY backend: set construction, release inference,
whole-model kind handling (7 + 17 together), vispec tar reading, and
bilinear interpolation on the synthetic fixtures.

The interpolation expectations are hand-computed from the fixture law
H(Teff, log g, lam) = (Teff/1e4) * log g * lam/1000 (the same law the
downstream application's own test suite used).
"""

from __future__ import annotations

from pathlib import Path
import shutil

import numpy as np
import pytest

from stellarlib.core import ParameterRole
from stellarlib.exceptions import (
    AmbiguousConventionError,
    MetadataConflictError,
)
from stellarlib.facade import TlustySpectra
from stellarlib.grid import open_grid
from stellarlib.models.tlusty.backend import read_tlusty

DATA = Path(__file__).parent.parent / "data" / "tlusty"
WAVE = np.array([5000.0, 5001.0, 5002.0])

OSTAR_NAMES = [
    "G30000g300v10.vis.7.gz",
    "G30000g325v10.vis.7.gz",
    "G32500g300v10.vis.7.gz",
    "G32500g325v10.vis.7.gz",
]
BSTAR_NAMES = [
    "BG15000g175v20.vis.7.gz",
    "BG15000g200v20.vis.7.gz",
    "BG16000g175v20.vis.7.gz",
    "BG16000g200v20.vis.7.gz",
]
TAR = DATA / "Gvispec_fixture.tar"


def _ostar_grid() -> TlustySpectra:
    return TlustySpectra([read_tlusty([DATA / n for n in OSTAR_NAMES])])


class TestSetConstruction:
    def test_ostar_identity_axes_parameters(self):
        grid = _ostar_grid()
        assert grid.identity.release == "OSTAR2002"
        assert grid.identity.product == "ostar2002"
        assert grid.identity.physics_configuration == "composition G"
        assert grid.selectable == ("teff", "log_surface_gravity")
        assert grid.axes["teff"] == (30000.0, 32500.0)
        assert grid.axes["log_surface_gravity"] == (3.0, 3.25)
        assert grid.holes == ()
        spec = grid.sets[0].parameter("composition")
        assert spec.role is ParameterRole.FIXED
        assert spec.value == "G"
        assert grid.sets[0].parameter("microturbulence").value == 1.0

    def test_bstar_release_inferred_from_teff(self):
        grid = TlustySpectra([read_tlusty([DATA / n for n in BSTAR_NAMES])])
        assert grid.identity.release == "BSTAR2006"
        assert grid.sets[0].parameter("microturbulence").value == 2.0

    def test_overlap_band_requires_explicit_release(self):
        names = ["G27500g300v10.vis.7.gz", "G30000g300v10.vis.7.gz"]
        with pytest.raises(AmbiguousConventionError, match="overlap"):
            read_tlusty([DATA / n for n in names])
        set_ = read_tlusty([DATA / n for n in names], release="OSTAR2002")
        assert set_.identity.release == "OSTAR2002"

    def test_explicit_release_validated_against_span(self):
        with pytest.raises(AmbiguousConventionError, match="spans Teff"):
            read_tlusty(
                [DATA / n for n in BSTAR_NAMES[:2]], release="OSTAR2002"
            )

    def test_mixed_composition_refused(self, tmp_path):
        files = [DATA / "G30000g300v10.vis.7.gz", DATA / "BG15000g175v20.vis.7.gz"]
        with pytest.raises(MetadataConflictError, match="composition"):
            read_tlusty(files)

    def test_kind17_held_with_record_no_warning(self, recwarn):
        # kind 7 + kind 17 are one model's two data: the continuum rides
        # along in every set, is recorded, and warns about nothing
        set_ = read_tlusty(
            [DATA / n for n in OSTAR_NAMES]
            + [DATA / n.replace(".vis.7.", ".vis.17.") for n in OSTAR_NAMES]
        )
        assert len(set_) == 4
        assert set_.notes["kind17_files"] == [
            n.replace(".vis.7.", ".vis.17.") for n in OSTAR_NAMES
        ]
        for node in set_:
            assert node.facts["continuum_member"].endswith(".vis.17.gz")
        assert not [w for w in recwarn.list if "kind-17" in str(w.message)]

    def test_only_kind17_refused(self):
        from stellarlib.exceptions import UnrecognizedFormatError

        with pytest.raises(UnrecognizedFormatError, match="kind-17 continuum"):
            read_tlusty([DATA / "G27500g325v10.vis.17.gz"])


class TestVispecTar:
    def test_tar_is_one_whole_product(self):
        set_ = read_tlusty(TAR)
        assert len(set_) == 4
        assert set_.identity.release == "OSTAR2002"
        assert set_.identity.physics_configuration == "composition G"
        assert set_.notes["tar"] == "Gvispec_fixture.tar"
        assert set_.notes["members"] == {"kind7": 4, "kind17": 4}
        for node in set_:
            assert node.facts["member"].endswith(".vis.7.gz")
            assert node.facts["continuum_member"] == (
                node.facts["member"].replace(".vis.7.", ".vis.17.")
            )

    def test_tar_loader_streams_members(self):
        grid = TlustySpectra([read_tlusty(TAR)])
        spectrum = grid.spectrum(teff=31250.0, log_surface_gravity=3.125)
        np.testing.assert_allclose(
            spectrum.native.flux, 3.125 * 3.125 * WAVE / 1000.0
        )
        assert spectrum.unified["wavelength"][0] == pytest.approx(5000.0)

    def test_tar_continuum_readable_from_member_name(self):
        # the recorded sibling is addressable: one member read, own grid
        import tarfile

        from stellarlib.models.tlusty.reader import read_vis_payload

        set_ = read_tlusty(TAR)
        node = next(iter(set_))
        with tarfile.open(TAR) as tf:
            payload = tf.extractfile(node.facts["continuum_member"]).read()
        continuum = read_vis_payload(payload, node.facts["continuum_member"])
        assert continuum.wavelength[0] == pytest.approx(5000.0)
        assert continuum.flux.size == 2  # the coarser kind-17 grid

    def test_open_grid_sniffs_tar(self):
        grid = open_grid(TAR, model="tlusty/OSTAR2002")
        assert len(grid) == 4

    def test_tar_plus_loose_members_refused(self):
        with pytest.raises(ValueError, match="one vispec tar"):
            read_tlusty([TAR, DATA / OSTAR_NAMES[0]])

    def test_kind17_only_tar_refused(self, tmp_path):
        import tarfile

        stray = tmp_path / "only17.tar"
        with tarfile.open(stray, "w") as tf:
            tf.add(DATA / "G27500g325v10.vis.17.gz", arcname="G27500g325v10.vis.17.gz")
        from stellarlib.exceptions import UnrecognizedFormatError

        with pytest.raises(UnrecognizedFormatError, match="kind-7"):
            read_tlusty(stray)


class TestOpenGrid:
    def test_open_grid_sniffs_tlusty(self, tmp_path):
        for name in OSTAR_NAMES:
            shutil.copy(DATA / name, tmp_path / name)
        grid = open_grid(tmp_path, model="tlusty/OSTAR2002")
        assert grid.identity.family == "tlusty"
        assert len(grid) == 4

    def test_open_grid_distinguishes_bosz(self, tmp_path):
        # a BOSZ file and a TLUSTY file in one tree: sniffing refuses
        bosz = (
            Path(__file__).parent.parent / "data" / "bosz"
            / "bosz2024_ms_t3500_g+1.0_m+0.00_a+0.00_c+0.00_v2_r500_resam.txt.gz"
        )
        shutil.copy(bosz, tmp_path / bosz.name)
        shutil.copy(DATA / OSTAR_NAMES[0], tmp_path / OSTAR_NAMES[0])
        from stellarlib.exceptions import AmbiguousFormatError

        with pytest.raises(AmbiguousFormatError, match="pass model="):
            open_grid(tmp_path)


class TestBilinearInterpolation:
    def test_hand_computed_bilinear_value(self):
        grid = _ostar_grid()
        # downstream parity case: query (32500, 4.0) on the law
        # (Teff/1e4) * logg * lam/1000 -> 3.25 * 4.0 * lam/1000 — but our
        # fixture loggs run 3.0-3.25, so query inside: (31250, 3.125)
        spectrum = grid.spectrum(teff=31250.0, log_surface_gravity=3.125)
        assert grid.last_record.method == "multilinear"
        np.testing.assert_allclose(
            spectrum.native.flux, 3.125 * 3.125 * WAVE / 1000.0
        )

    def test_midpoint_equals_corner_mean(self):
        grid = _ostar_grid()
        spectrum = grid.spectrum(teff=31250.0, log_surface_gravity=3.125)
        corners = grid.corner_spectrum(grid.last_record.node_indices[0])
        np.testing.assert_allclose(
            spectrum.native.flux, corners.mean(axis=0), rtol=1e-12
        )

    def test_node_and_edge_cases(self):
        grid = _ostar_grid()
        spectrum = grid.spectrum(teff=30000.0, log_surface_gravity=3.0)
        assert grid.last_record.method == "exact-node"
        np.testing.assert_allclose(
            spectrum.native.flux, 3.0 * 3.0 * WAVE / 1000.0
        )
        from stellarlib.exceptions import UnsafeInterpolationError

        with pytest.raises(UnsafeInterpolationError, match="outside"):
            grid.spectrum(teff=20000.0, log_surface_gravity=3.0)
        from stellarlib.warnings import ClampedInterpolationWarning

        with pytest.warns(ClampedInterpolationWarning):
            clamped = grid.spectrum(
                teff=20000.0, log_surface_gravity=3.0, range_policy="clamp"
            )
        np.testing.assert_allclose(
            clamped.native.flux, 3.0 * 3.0 * WAVE / 1000.0
        )

    def test_unified_fields(self):
        grid = _ostar_grid()
        spectrum = grid.spectrum(teff=31250.0, log_surface_gravity=3.125)
        np.testing.assert_allclose(
            spectrum.unified["surface_flux"],
            4.0 * np.pi * 3.125 * 3.125 * WAVE / 1000.0,
        )
        with pytest.raises(KeyError, match="not available"):
            spectrum.unified["continuum"]
