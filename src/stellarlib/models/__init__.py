"""Backend registry: the set of supported spectral library families.

Each backend contributes a cheap ``recognize`` sniffer and a ``read``
function, plus a release ``catalog`` (the published products a local
database is built from). Core code dispatches through this registry — no
``if family == "bosz"`` branches anywhere else.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from stellarlib.exceptions import SourceUnavailableError, UnsupportedModelError
from stellarlib.models.basel.backend import read_basel
from stellarlib.models.basel.reader import recognize_basel
from stellarlib.models.bosz.backend import read_bosz
from stellarlib.models.bosz.reader import recognize_bosz
from stellarlib.models.miles.backend import read_miles
from stellarlib.models.miles.reader import recognize_miles
from stellarlib.models.stelib.backend import read_stelib, recognize_stelib
from stellarlib.models.tlusty.backend import read_tlusty
from stellarlib.models.tlusty.reader import recognize_tlusty

__all__ = ["BACKENDS", "catalog", "get_backend", "parse_model_spec", "register_backend"]


class Backend:
    """One library family's entry in the registry."""

    def __init__(
        self,
        name: str,
        recognize: Callable,
        read: Callable,
        empirical: bool = False,
        empirical_note: str | None = None,
    ) -> None:
        """Hold one family's sniffer, reader, and library class.

        ``empirical`` marks a library of observed stars: its spectra
        carry arbitrary per-star normalizations (and may each sit on
        their own wavelength grid), so cross-node interpolation is
        refused at the grid level and point queries default to the
        nearest-star answer. ``empirical_note`` is the one-clause reason
        the refusal message quotes (required when ``empirical`` is set).
        """
        self.name = name
        self.recognize = recognize
        self.read = read
        self.empirical = empirical
        self.empirical_note = empirical_note


BACKENDS: dict[str, Backend] = {
    "basel": Backend(name="basel", recognize=recognize_basel, read=read_basel),
    "bosz": Backend(name="bosz", recognize=recognize_bosz, read=read_bosz),
    "tlusty": Backend(name="tlusty", recognize=recognize_tlusty, read=read_tlusty),
    "miles": Backend(
        name="miles",
        recognize=recognize_miles,
        read=read_miles,
        empirical=True,
        empirical_note=(
            "the stars are an empirical scatter whose spectra carry "
            "arbitrary per-star normalizations — a weighted mix is not a "
            "spectrum of anything"
        ),
    ),
    "stelib": Backend(
        name="stelib",
        recognize=recognize_stelib,
        read=read_stelib,
        empirical=True,
        empirical_note=(
            "the stars are an empirical scatter, each on its own wavelength "
            "grid — a weighted mix is neither an observation of anything "
            "nor defined on a common sampling"
        ),
    ),
}


def get_backend(name: str) -> Backend:
    """One family's registry entry; unknown names list the known families."""
    try:
        return BACKENDS[name.lower()]
    except KeyError:
        raise UnsupportedModelError(
            f"no backend registered for library {name!r}; "
            f"registered families: {sorted(BACKENDS)}"
        ) from None


def register_backend(backend: Backend) -> None:
    """Register a third-party backend (extension point for new libraries)."""
    BACKENDS[backend.name.lower()] = backend


def catalog(family: str) -> dict:
    """The family's release catalog: the published products to acquire.

    The catalog is data (``models/<family>/catalog.json``) so the database
    builder stays family-agnostic. Entries state the exact URL templates,
    product structure, and format knowledge of each official product.
    """
    get_backend(family)  # validates the family name
    path = Path(__file__).parent / family.lower() / "catalog.json"
    if not path.exists():
        raise SourceUnavailableError(
            f"library family {family!r} declares no release catalog"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def parse_model_spec(model: str) -> tuple[str, str | None]:
    """Split ``"bosz/2024"`` into ``("bosz", "2024")``.

    A bare family (``"bosz"``) yields ``(family, None)`` — accepted only
    where autodetection is possible (open_grid sniffing); APIs that must
    know the release reject ``None``.
    """
    parts = [p.strip() for p in model.split("/")]
    if len(parts) == 1 and parts[0]:
        return parts[0].lower(), None
    if len(parts) == 2 and all(parts):
        return parts[0].lower(), parts[1]
    raise ValueError(
        f"model must be '<family>' or '<family>/<release>', "
        f"e.g. 'bosz/2024'; got {model!r}"
    )
