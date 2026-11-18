"""Tests for materialized array side-products."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stellarlib.exceptions import GridHoleError
from stellarlib.facade import BoszSpectra
from stellarlib.materialize import materialize_grid, open_materialized
from stellarlib.models.bosz.backend import read_bosz

DATA = Path(__file__).parent / "data" / "bosz"

NODE_NAMES = [
    f"bosz2024_ms_t{t}_g{g}_m{m}_a+0.00_c+0.00_v2_r500_resam.txt.gz"
    for t in (3500, 4000)
    for g in ("+1.0", "+2.0")
    for m in ("+0.00", "-1.00")
]


def _grid() -> BoszSpectra:
    return BoszSpectra([read_bosz([DATA / n for n in NODE_NAMES])])


def _holed_grid() -> BoszSpectra:
    files = [DATA / n for n in NODE_NAMES if n != NODE_NAMES[0]]
    return BoszSpectra([read_bosz(files)])


class TestMaterialize:
    def test_roundtrip_arrays_match_loaded_nodes(self, tmp_path):
        grid = _grid()
        out = materialize_grid(grid, tmp_path / "block")
        assert out.flux.shape == (2, 2, 2, 300)
        assert out.continuum.shape == (2, 2, 2, 300)
        # axes are sorted ascending: [0, 0, 0] is (3500 K, 1.0, [M/H] -1.0)
        node = grid.get_spectrum(teff=3500.0, log_surface_gravity=1.0, m_h=-1.0)
        np.testing.assert_array_equal(out.flux[0, 0, 0], node.native.flux)
        np.testing.assert_array_equal(out.wavelength, node.native.wavelength)
        assert out.axes["teff"].tolist() == [3500.0, 4000.0]
        assert out.axes["m_h"].tolist() == [-1.0, 0.0]

    def test_sidecar_records_sources_and_transformation(self, tmp_path):
        out = materialize_grid(_grid(), tmp_path / "block")
        assert len(out.sidecar["sources"]) == 8
        first = out.sidecar["sources"][0]
        assert Path(first["file"]).exists()
        assert len(first["sha256"]) == 64
        assert "no resampling" in out.sidecar["transformation"]
        assert out.sidecar["identity"]["family"] == "bosz"

    def test_verify_sources_detects_change_and_loss(self, tmp_path):
        grid = _grid()
        # copy sources to a scratch dir so mutating them is safe
        scratch = tmp_path / "src"
        scratch.mkdir()
        local_names = [
            n for n in NODE_NAMES
        ] + ["bosz2024_wave_r500.txt"]
        for name in local_names:
            (scratch / name).write_bytes((DATA / name).read_bytes())
        local_grid = BoszSpectra(
            [read_bosz([scratch / n for n in NODE_NAMES])]
        )
        out = materialize_grid(local_grid, tmp_path / "block")
        assert out.verify_sources() == []
        (scratch / NODE_NAMES[0]).write_bytes(b"tampered")
        problems = out.verify_sources()
        assert any("sha256 changed" in p for p in problems)
        (scratch / NODE_NAMES[1]).unlink()
        problems = out.verify_sources()
        assert any("is gone" in p for p in problems)

    def test_corner_mechanism_pairs_with_flux_block(self, tmp_path):
        grid = _grid()
        out = materialize_grid(grid, tmp_path / "block")
        idx, w, clamped = out.corner_weights(
            teff=[3760.0], log_surface_gravity=[1.3], m_h=[-0.25]
        )
        rows = out.corner_spectrum(idx[0])
        combined = w[0] @ rows
        spectrum = grid.spectrum(teff=3760.0, log_surface_gravity=1.3, m_h=-0.25)
        np.testing.assert_allclose(combined, spectrum.native.flux, rtol=1e-12)

    def test_holes_refuse_to_materialize(self, tmp_path):
        grid = _holed_grid()
        with pytest.raises(GridHoleError, match="holes"):
            materialize_grid(grid, tmp_path / "block")

    def test_open_requires_sidecar(self, tmp_path):
        materialize_grid(_grid(), tmp_path / "block")
        (tmp_path / "block.npz.sources.json").unlink()

        with pytest.raises(FileNotFoundError, match="sidecar"):
            open_materialized(tmp_path / "block.npz")

    def test_degenerate_one_axis_ladder(self, tmp_path):
        # the rgf-style Teff ladder at fixed log g and [M/H]: the block
        # keeps all three axes, degenerate dimensions included
        files = [
            DATA / n
            for n in NODE_NAMES
            if "_g+1.0_m+0.00_" in n
        ]
        grid = BoszSpectra([read_bosz(files)])
        out = materialize_grid(grid, tmp_path / "ladder")
        assert out.flux.shape == (2, 1, 1, 300)
        idx, w, _ = out.corner_weights(
            teff=[3600.0], log_surface_gravity=[1.0], m_h=[0.0]
        )
        rows = out.corner_spectrum(idx[0])
        np.testing.assert_allclose(
            (w[0] @ rows),
            grid.spectrum(teff=3600.0, log_surface_gravity=1.0, m_h=0.0).native.flux,
        )
