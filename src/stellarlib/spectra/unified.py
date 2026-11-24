"""Unified layer: a traceable, lazily-computed view over native spectra.

The unified view renames a product's arrays to canonical field names and
computes documented transformations, but it is not a second copy of the
data: native arrays are referenced, transformed fields are computed on
demand, and every field carries its :class:`~stellarlib.spectra.core.FieldSpec`
describing exactly how it maps back to native data. File-metadata fields
read per-node constants stated outside the file's columns (for example a
filename's Teff token).
"""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
from typing import Any, Callable
import warnings

from astropy import units as u
from astropy.table import Table
import numpy as np

from stellarlib.exceptions import WavelengthGridError
from stellarlib.spectra.core import FieldOrigin, FieldSpec
from stellarlib.spectra.native import NativeSpectrum

__all__ = ["TRANSFORMATIONS", "UnifiedView"]

# Transformations are a fixed, named vocabulary — never free-form code.
# A FieldSpec's `transformation` string must be one of these. Every entry
# takes (values, native): constant conversions ignore the native spectrum,
# wavelength-dependent ones (a per-Hz to per-wavelength unit bridge) read
# the native wavelength array from it.
_SPEED_OF_LIGHT_NM_S = 2.99792458e17  # nm/s


def _fnu_per_hz_to_flambda_per_angstrom(
    values: np.ndarray, native: NativeSpectrum
) -> np.ndarray:
    """f_nu [per Hz] -> f_lambda [per Angstrom], lambda in nm natively.

    f_lambda = f_nu * c / lambda^2 with lambda in nm; the / 10 converts
    the per-nm density to the per-Angstrom density (1 nm = 10 Angstrom).
    """
    if native.wavelength is None:
        raise WavelengthGridError(
            "the f_nu -> f_lambda conversion needs the native wavelength "
            "array, which this product's files do not carry"
        )
    return values * _SPEED_OF_LIGHT_NM_S / np.asarray(native.wavelength) ** 2 / 10.0


TRANSFORMATIONS: dict[str, Callable[[np.ndarray, NativeSpectrum], np.ndarray]] = {
    "4 pi * x": lambda a, _native: 4.0 * np.pi * a,
    "x * 1e-4": lambda a, _native: a * 1.0e-4,
    "10 ** x": lambda a, _native: np.power(10.0, a),
    "x * 10": lambda a, _native: a * 10.0,
    "x * 1e-3": lambda a, _native: a * 1.0e-3,
    "x * c / lambda_nm^2 / 10": _fnu_per_hz_to_flambda_per_angstrom,
    "4 pi * x * c / lambda_nm^2 / 10": lambda a, native: (
        4.0 * np.pi * _fnu_per_hz_to_flambda_per_angstrom(a, native)
    ),
}


class UnifiedView:
    """Lazy unified access over one native spectrum.

    ``view[name]`` returns the unified field's values: the native array
    when the origin is native, the named transformation when it is
    transformed, the attached computation rule when it is derived, or the
    per-node constant when it is file metadata. ``to_table()`` materializes
    the unified layer as an astropy Table.
    """

    def __init__(
        self,
        native: NativeSpectrum,
        field_specs: Mapping[str, FieldSpec],
        computations: Mapping[str, Callable[[NativeSpectrum], np.ndarray]]
        | None = None,
        constants: Mapping[str, Any] | None = None,
    ) -> None:
        """Hold the specs over one native spectrum (nothing computed yet)."""
        self._native = native
        self._specs = dict(field_specs)
        self._computations = dict(computations or {})
        self._constants = dict(constants or {})

    def with_native(self, native: NativeSpectrum) -> UnifiedView:
        """The same view (specs, computations, constants) over a new native.

        Used by derived products (resampling, node interpolation) so the
        result exposes the identical unified vocabulary over its new
        arrays.
        """
        return UnifiedView(
            native,
            self._specs,
            computations=self._computations,
            constants=self._constants,
        )

    @property
    def field_names(self) -> list[str]:
        """The view's unified field names."""
        return list(self._specs)

    def field_spec(self, name: str) -> FieldSpec:
        """One field's spec, with a helpful error when absent."""
        try:
            return self._specs[name]
        except KeyError:
            raise KeyError(
                f"no unified field {name!r}; available: {self.field_names}"
            ) from None

    def has_field(self, name: str) -> bool:
        """Whether the view maps a unified field of this name."""
        return name in self._specs

    def __contains__(self, name: str) -> bool:
        """Field-membership test (``name in view``)."""
        return name in self._specs

    def _native_array(self, native_name: str) -> np.ndarray:
        if native_name == "flux":
            return np.asarray(self._native.flux)
        if native_name == "continuum":
            if self._native.continuum is None:
                raise KeyError(
                    "native field 'continuum' is not present in this "
                    "product's files"
                )
            return np.asarray(self._native.continuum)
        if native_name == "wavelength":
            if self._native.wavelength is None:
                raise WavelengthGridError(
                    "this product's files carry no wavelength column; pair "
                    "the spectrum with its companion wavelength grid "
                    "explicitly (see the backend reader) before reading "
                    "wavelength fields"
                )
            return np.asarray(self._native.wavelength)
        raise KeyError(
            f"unknown native array {native_name!r}; a native spectrum has "
            "only wavelength, flux, and continuum"
        )

    def __getitem__(self, name: str) -> np.ndarray:
        spec = self.field_spec(name)
        if spec.origin is FieldOrigin.MISSING:
            raise KeyError(f"unified field {name!r} is not available in this product")
        if spec.origin is FieldOrigin.FILE_METADATA:
            try:
                return np.asarray(self._constants[spec.native_name])
            except KeyError:
                raise KeyError(
                    f"unified field {name!r} is file metadata keyed "
                    f"{spec.native_name!r}, which the reader did not supply"
                ) from None
        if spec.origin is FieldOrigin.DERIVED:
            try:
                fn = self._computations[name]
            except KeyError:
                raise NotImplementedError(
                    f"derived field {name!r} has no computation rule attached"
                ) from None
            return fn(self._native)
        if spec.native_name is None:
            raise KeyError(
                f"unified field {name!r} has no native source declared"
            )
        values = self._native_array(spec.native_name)
        if spec.origin is FieldOrigin.NATIVE:
            return values
        if spec.origin is FieldOrigin.TRANSFORMED_NATIVE:
            try:
                fn = TRANSFORMATIONS[spec.transformation]
            except KeyError:
                raise NotImplementedError(
                    f"transformation {spec.transformation!r} of field {name!r} "
                    "is documented but not implemented"
                ) from None
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                return fn(values, self._native)
        raise NotImplementedError(
            f"origin {spec.origin!r} of field {name!r} is not implemented"
        )

    def to_table(self) -> Table:
        """The unified layer as an astropy Table (specs in the meta)."""
        cols = {}
        meta = {
            "stellarlib_layer": "unified",
            "field_specs": {n: s.to_dict() for n, s in self._specs.items()},
        }
        for name in self.field_names:
            spec = self._specs[name]
            if spec.origin is FieldOrigin.MISSING:
                continue
            cols[name] = self[name]
        table = Table(cols, meta=meta)
        for name, spec in self._specs.items():
            if name not in table.colnames or spec.unit is None:
                continue
            # keep the string in meta rather than inventing a unit
            with contextlib.suppress(ValueError):
                table[name].unit = u.Unit(spec.unit)
        return table
