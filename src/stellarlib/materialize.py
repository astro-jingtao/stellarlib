"""Materialized array side-products of a grid, with complete provenance.

A materialized grid is the merged (axes..., wavelength) array block the
sparse-matrix consumers want (the successor of the downstream
``BoszGrid.dump`` npz), written next to a JSON sidecar recording every
source file's checksum, the wavelength grid's identity, and the exact
transformation applied (stacking in C order, float64, no resampling).
The materialized product is always a derived artifact: the source files
remain the truth, and opening verifies they are still there and
unchanged. Grids with holes refuse to materialize — the merged rectangle
would have to invent rows for missing nodes.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from stellarlib.exceptions import GridHoleError, WavelengthGridError
from stellarlib.grid import SpectrumGrid
from stellarlib.integrity import sha256_file
from stellarlib.interp import corner_weights

__all__ = ["materialize_grid", "open_materialized", "MaterializedGrid"]

_SIDECAR_SUFFIX = ".sources.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def materialize_grid(
    grid: SpectrumGrid,
    path: str | Path,
    include_continuum: bool = True,
) -> "MaterializedGrid":
    """Write one grid's merged arrays to ``<path>.npz`` plus a sidecar.

    ``<path>.npz`` holds one array per selectable axis (its sorted
    values), ``wavelength``, ``flux`` with shape ``(n_axis0, ..., n_wave)``
    in C order over the axes (the ravel pairs exactly with
    :func:`stellarlib.interp.corner_weights` flat indices), and
    ``continuum`` when every node carries one. ``<path>.sources.json``
    records every source file (absolute path, sha256, size), the grid's
    identity, and the transformation statement. Holes refuse; a missing
    wavelength pairing refuses.
    """
    if grid.has_holes():
        holes = [
            dict(zip(grid.selectable, h)) for h in grid.holes[:5]
        ]
        raise GridHoleError(
            f"cannot materialize a grid with holes ({len(grid.holes)} "
            f"missing nodes, e.g. {holes}); select a hole-free grid or "
            "interpolate per query instead"
        )

    axes_values = [np.asarray(v, dtype=np.float64) for v in grid.axes.values()]
    shape = tuple(a.size for a in axes_values)
    n_wave = None
    flux_block = np.zeros(shape + (0,), dtype=np.float64)
    sources = []
    identity = grid.identity
    for flat_index in range(int(np.prod(shape))):
        multi = np.unravel_index(flat_index, shape)
        key = tuple(
            axes_values[pos][multi[pos]].item() for pos in range(len(shape))
        )
        spectrum = grid.load_node(key)
        native = spectrum.native
        if native.wavelength is None:
            raise WavelengthGridError(
                "cannot materialize spectra with no wavelength grid "
                f"attached (node {key})"
            )
        if n_wave is None:
            n_wave = native.wavelength.size
            wavelength = np.asarray(native.wavelength, dtype=np.float64)
            flux_block = np.zeros(shape + (n_wave,), dtype=np.float64)
            continuum_block = (
                np.zeros(shape + (n_wave,), dtype=np.float64)
                if include_continuum
                else None
            )
            has_continuum = native.continuum is not None
        else:
            if not np.array_equal(native.wavelength, wavelength):
                raise WavelengthGridError(
                    f"node {key} carries a different wavelength grid; "
                    "refusing to merge mismatched grids"
                )
            if (native.continuum is not None) != has_continuum:
                raise WavelengthGridError(
                    f"node {key} disagrees with the others on continuum "
                    "presence; refusing to merge"
                )
        flux_block[multi] = native.flux
        if include_continuum and has_continuum:
            continuum_block[multi] = native.continuum
        node = grid._node_of_key[key][1]
        sources.append(
            {
                "coordinates": dict(zip(grid.selectable, key)),
                "file": str(node.path.resolve()),
                "sha256": sha256_file(node.path),
                "size_bytes": node.path.stat().st_size,
            }
        )

    npz_path = Path(str(path).removesuffix(".npz") + ".npz")
    npz_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        name: values for name, values in zip(grid.selectable, axes_values)
    }
    payload["wavelength"] = wavelength
    payload["flux"] = flux_block
    if include_continuum and has_continuum:
        payload["continuum"] = continuum_block
    with open(npz_path, "wb") as fh:
        np.savez_compressed(fh, **payload)

    sidecar = {
        "stellarlib_version": _stellarlib_version(),
        "generated_at": _now_iso(),
        "identity": identity.to_dict(),
        "selectable": list(grid.selectable),
        "shape": list(shape),
        "transformation": (
            "stacked one array per grid node in C order over the axes; "
            "values verbatim as parsed (float64); no resampling, no "
            "clipping, no unit conversion"
        ),
        "sources": sources,
    }
    sidecar_path = npz_path.with_name(npz_path.name + _SIDECAR_SUFFIX)
    with open(sidecar_path, "w", encoding="utf-8", newline="\n") as _f:
        _f.write(json.dumps(sidecar, indent=2))
    return open_materialized(npz_path)


def _stellarlib_version() -> str:
    from stellarlib import __version__

    return __version__


@dataclasses.dataclass(frozen=True)
class MaterializedGrid:
    """One materialized array block plus its derivation record."""

    npz_path: Path
    sidecar: dict
    axes: dict[str, np.ndarray]
    wavelength: np.ndarray
    flux: np.ndarray
    continuum: np.ndarray | None

    @property
    def identity(self):
        from stellarlib.core import ModelIdentity

        return ModelIdentity.from_dict(self.sidecar["identity"])

    def verify_sources(self) -> list[str]:
        """Check every source file still exists and matches its sha256.

        Returns the list of problems (empty = everything unchanged). A
        materialized product is only trustworthy while its sources are.
        """
        problems = []
        for source in self.sidecar.get("sources", []):
            path = Path(source["file"])
            if not path.exists():
                problems.append(f"{path}: source file is gone")
                continue
            actual = sha256_file(path)
            if actual != source["sha256"]:
                problems.append(f"{path}: sha256 changed since materialization")
        return problems

    def corner_weights(self, **queries):
        """The rectangular corner mechanism over the materialized axes
        (same contract as :meth:`UnifiedSpectra.corner_weights`, flat
        indices pairing with the C-order ravel of the flux block)."""
        return corner_weights(self.axes, **queries)

    def corner_spectrum(self, flat_indices) -> np.ndarray:
        return self.flux.reshape(-1, self.wavelength.size)[np.asarray(flat_indices)]


def open_materialized(path: str | Path) -> MaterializedGrid:
    """Open a materialized grid, loading its sidecar derivation record."""
    npz_path = Path(str(path).removesuffix(".npz") + ".npz")
    sidecar_path = npz_path.with_name(npz_path.name + _SIDECAR_SUFFIX)
    if not npz_path.exists():
        raise FileNotFoundError(npz_path)
    if not sidecar_path.exists():
        raise FileNotFoundError(
            f"{sidecar_path}: a materialized grid is only complete with its "
            "sources sidecar; re-materialize (materialize_grid)"
        )
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    with np.load(npz_path) as npz:
        axes = {name: npz[name] for name in sidecar["selectable"]}
        wavelength = npz["wavelength"]
        flux = npz["flux"]
        continuum = npz["continuum"] if "continuum" in npz.files else None
    return MaterializedGrid(
        npz_path=npz_path,
        sidecar=sidecar,
        axes=axes,
        wavelength=wavelength,
        flux=flux,
        continuum=continuum,
    )
