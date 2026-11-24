"""Native layer: a faithful representation of one upstream spectrum file.

The native spectrum preserves exactly what the file contains — numeric
values as parsed, the comment/header block, and any metadata the backend
extracted from the filename or header. Nothing is renamed, converted,
clipped, or filled in. The unified layer (``stellarlib.spectra.unified``) is built
on top of this, never instead of it.

Unlike an isochrone table, a spectrum is a small number of equal-length
numeric columns; the arrays are held directly (no heterogenous table
container). Where the published format omits a column — BOSZ resampled
files carry no wavelength column — the corresponding attribute is ``None``
rather than a synthesized array, and the missing grid is supplied only by
an explicit, recorded pairing with the companion wavelength file.
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
from typing import Any

from astropy import units as u
from astropy.table import Column, Table
import numpy as np

__all__ = ["NativeSpectrum"]


def _as_unit(value: str | u.UnitBase | None, field_name: str) -> u.UnitBase | None:
    """Normalize a unit declaration to an astropy unit (or ``None``).

    An astropy unit passes through; a string must parse as a valid
    astropy unit string — an invalid one raises, because a wrong label
    is as bad as a wrong number in a provenance-first library (a silent
    ``None`` would silently drop the statement); ``None`` stays ``None``
    as the explicit statement that the format declares no unit.
    """
    if value is None or isinstance(value, u.UnitBase):
        return value
    try:
        return u.Unit(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name}={value!r} is not a valid astropy unit string "
            "(pass an astropy unit, a parsable unit string, or None)"
        ) from exc


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
    wavelength_unit, flux_unit
        The published convention of the two columns, as astropy units —
        documentation for ``to_table``, never a conversion. Accepted:
        an astropy unit (``u.Angstrom`` — the defaults are astropy's own
        ``u.Angstrom`` and ``u.erg / (u.s * u.cm**2 * u.Angstrom)``), a
        string that parses as one (validated at construction; an
        invalid string raises — a wrong label is as bad as a wrong
        number), or ``None`` for the explicit statement that the format
        declares no unit. A family publishing something else (BaSeL: nm;
        erg / (s cm2 Hz sr)) states its own.
    """

    # unhashable by design: the arrays inside make equality a
    # content comparison, not an identity one
    __hash__ = None

    wavelength: np.ndarray | None
    flux: np.ndarray
    continuum: np.ndarray | None = None
    header: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    provenance_keys: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    wavelength_unit: str | u.UnitBase | None = u.Angstrom
    flux_unit: str | u.UnitBase | None = u.erg / (u.s * u.cm**2 * u.Angstrom)

    def __post_init__(self) -> None:
        object.__setattr__(self, "header", tuple(self.header))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "provenance_keys", dict(self.provenance_keys))
        object.__setattr__(
            self, "wavelength_unit", _as_unit(self.wavelength_unit, "wavelength_unit")
        )
        object.__setattr__(self, "flux_unit", _as_unit(self.flux_unit, "flux_unit"))
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
        return (
            self._same_optional(self.wavelength, other.wavelength)
            and np.array_equal(self.flux, other.flux)
            and self._same_optional(self.continuum, other.continuum)
            and self.header == other.header
            and dict(self.metadata) == dict(other.metadata)
        )

    @staticmethod
    def _same_optional(
        ours: np.ndarray | None, theirs: np.ndarray | None
    ) -> bool:
        """Both absent, or both present and array-equal."""
        if (ours is None) != (theirs is None):
            return False
        return ours is None or np.array_equal(ours, theirs)

    def to_table(self) -> Table:
        """The spectrum as an astropy Table with physical units attached.

        Column order follows the native layout: wavelength (when present),
        flux, then continuum. Units are documentation of the published
        convention, not a conversion: the numbers are identical to the
        parsed arrays. ``wavelength_unit``/``flux_unit`` state that
        convention per product (BaSeL publishes wavelength in nm and flux
        per Hz per steradian); families on the common Angstrom/per-
        Angstrom convention keep the astropy-unit defaults. A ``None``
        unit leaves its column unitless.
        """
        flux_unit = self.flux_unit
        columns = []
        names = []
        if self.wavelength is not None:
            columns.append(Column(self.wavelength, unit=self.wavelength_unit))
            names.append("wavelength")
        columns.append(Column(self.flux, unit=flux_unit))
        names.append("flux")
        if self.continuum is not None:
            columns.append(Column(self.continuum, unit=flux_unit))
            names.append("continuum")
        return Table(columns, names=names)
