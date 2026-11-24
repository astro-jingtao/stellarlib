"""The spectrum data model: the vocabulary, the two layers, the classes.

One library node's machinery lives here: the identity and spec
vocabulary (``core``), the verbatim native layer (``native``), the
traceable unified layer (``unified``), and the spectrum class family
plus the lightweight node index (``spectrum``).
"""

from stellarlib.spectra.core import (
    FieldOrigin,
    FieldSpec,
    ModelIdentity,
    ParameterRole,
    ParameterSource,
    ParameterSpec,
)
from stellarlib.spectra.native import NativeSpectrum
from stellarlib.spectra.spectrum import (
    GridNode,
    InterpSpectrum,
    NodeSpectrum,
    Spectrum,
    SpectrumSet,
)
from stellarlib.spectra.unified import TRANSFORMATIONS, UnifiedView

__all__ = [
    "TRANSFORMATIONS",
    "FieldOrigin",
    "FieldSpec",
    "GridNode",
    "InterpSpectrum",
    "ModelIdentity",
    "NativeSpectrum",
    "NodeSpectrum",
    "ParameterRole",
    "ParameterSource",
    "ParameterSpec",
    "Spectrum",
    "SpectrumSet",
    "UnifiedView",
]
