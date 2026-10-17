"""Unified layer: a traceable, lazily-computed view over native spectra.

The unified view renames a product's arrays to canonical field names and
computes documented transformations, but it is not a second copy of the
data: native arrays are referenced, transformed fields are computed on
demand, and every field carries its :class:`~stellarlib.core.FieldSpec`
describing exactly how it maps back to native data. File-metadata fields
read per-node constants stated outside the file's columns (for example a
filename's Teff token).
"""

from __future__ import annotations

import warnings
from typing import Any, Callable, Mapping

import numpy as np
from astropy import units as u
from astropy.table import Table

from stellarlib.core import FieldOrigin, FieldSpec
from stellarlib.exceptions import WavelengthGridError
from stellarlib.native import NativeSpectrum

__all__ = ["UnifiedView", "TRANSFORMATIONS"]

# Transformations are a fixed, named vocabulary — never free-form code.
# A FieldSpec's `transformation` string must be one of these.
TRANSFORMATIONS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "4 pi * x": lambda a: 4.0 * np.pi * a,
    "x * 1e-4": lambda a: a * 1.0e-4,
    "10 ** x": lambda a: np.power(10.0, a),
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
    ):
        self._native = native
        self._specs = dict(field_specs)
        self._computations = dict(computations or {})
        self._constants = dict(constants or {})

    def with_native(self, native: NativeSpectrum) -> "UnifiedView":
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
        return list(self._specs)

    def field_spec(self, name: str) -> FieldSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise KeyError(
                f"no unified field {name!r}; available: {self.field_names}"
            ) from None

    def has_field(self, name: str) -> bool:
        return name in self._specs

    def __contains__(self, name: str) -> bool:
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
                return fn(values)
        raise NotImplementedError(
            f"origin {spec.origin!r} of field {name!r} is not implemented"
        )

    def to_table(self) -> Table:
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
            try:
                table[name].unit = u.Unit(spec.unit)
            except ValueError:
                # keep the string in meta rather than inventing a unit
                pass
        return table
