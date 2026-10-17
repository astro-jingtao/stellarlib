"""Native layer: a faithful representation of one upstream spectrum file.

The native spectrum preserves exactly what the file contains — numeric
values as parsed, the comment/header block, and any metadata the backend
extracted from the filename or header. Nothing is renamed, converted,
clipped, or filled in. The unified layer (``stellarlib.unified``) is built
on top of this, never instead of it.

Unlike an isochrone table, a spectrum is a small number of equal-length
numeric columns; the arrays are held directly (no heterogenous table
container). Where the published format omits a column — BOSZ resampled
files carry no wavelength column — the corresponding attribute is ``None``
rather than a synthesized array, and the missing grid is supplied only by
an explicit, recorded pairing with the companion wavelength file.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping

import numpy as np
from astropy import units as u
from astropy.table import Column, Table

__all__ = ["NativeSpectrum"]


@dataclasses.dataclass(frozen=True)
class NativeSpectrum:
    """One parsed upstream spectrum plus its verbatim header material.

    Attributes
    ----------
    wavelength
        Wavelength array as parsed, or ``None`` when the file itself carries
        no wavelength column (BOSZ resampled products); the companion
        wavelength grid is a separate file and a separate decision.
    flux
        The file's flux column, verbatim (BOSZ: Eddington flux H_lambda;
        TLUSTY: Eddington flux H_lambda). No unit conversion, no clipping.
    continuum
        The file's continuum column verbatim, or ``None`` when the format
        has none (TLUSTY ``*.vis`` files).
    header
        Verbatim header/comment lines of the file, in order, including their
        comment markers.
    metadata
        Structured key/value pairs the backend extracted from the filename
        or header (effective temperature, surface gravity, metallicity,
        resolution, ...). Keys are the backend's own; values are what the
        file states — never inferred.
    provenance_keys
        Free-form notes keyed by the backend identifying which source files
        fed this object (for example the flux filename and the wavelength
        sidecar filename); the object layer turns these into a provenance
        bundle.
    """

    wavelength: np.ndarray | None
    flux: np.ndarray
    continuum: np.ndarray | None = None
    header: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    provenance_keys: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "header", tuple(self.header))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "provenance_keys", dict(self.provenance_keys))
        self._require_1d("flux", self.flux)
        if self.wavelength is not None:
            self._require_1d("wavelength", self.wavelength)
            if len(self.wavelength) != len(self.flux):
                raise ValueError(
                    "wavelength and flux must have equal length "
                    f"({len(self.wavelength)} vs {len(self.flux)})"
                )
        if self.continuum is not None:
            self._require_1d("continuum", self.continuum)
            if len(self.continuum) != len(self.flux):
                raise ValueError(
                    "continuum and flux must have equal length "
                    f"({len(self.continuum)} vs {len(self.flux)})"
                )

    @staticmethod
    def _require_1d(name: str, array: np.ndarray) -> None:
        arr = np.asarray(array)
        if arr.ndim != 1:
            raise ValueError(f"{name} must be a 1-D array, got shape {arr.shape}")

    def __len__(self) -> int:
        return len(self.flux)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, NativeSpectrum):
            return NotImplemented
        if (self.wavelength is None) != (other.wavelength is None):
            return False
        if self.wavelength is not None and not np.array_equal(
            self.wavelength, other.wavelength
        ):
            return False
        if not np.array_equal(self.flux, other.flux):
            return False
        if (self.continuum is None) != (other.continuum is None):
            return False
        if self.continuum is not None and not np.array_equal(
            self.continuum, other.continuum
        ):
            return False
        return (
            self.header == other.header
            and dict(self.metadata) == dict(other.metadata)
        )

    def to_table(self) -> Table:
        """The spectrum as an astropy Table with physical units attached.

        Column order follows the native layout: wavelength (when present),
        flux, then continuum. Units are documentation of the published
        convention, not a conversion: the numbers are identical to the
        parsed arrays.
        """
        flux_unit = u.erg / u.s / u.cm**2 / u.angstrom
        columns = []
        names = []
        if self.wavelength is not None:
            columns.append(Column(self.wavelength, unit=u.angstrom))
            names.append("wavelength")
        columns.append(Column(self.flux, unit=flux_unit))
        names.append("flux")
        if self.continuum is not None:
            columns.append(Column(self.continuum, unit=flux_unit))
            names.append("continuum")
        return Table(columns, names=names)
