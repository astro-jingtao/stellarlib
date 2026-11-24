"""The unified facade: family-specific grid objects and ``construct()``.

``UnifiedSpectra`` is the abstract face of a loaded library product: a
:class:`~stellarlib.grids.grid.SpectrumGrid` whose point queries
(``spectrum(teff=..., log_surface_gravity=..., m_h=...)``,
``node(...)``, ``corner_weights``/``corner_spectrum``) come from the grid
itself — every interpolating path leaving an
:class:`~stellarlib.grids.interp.InterpolationRecord` on ``last_record`` and on
the returned
:class:`~stellarlib.spectra.spectrum.InterpSpectrum`. What the subclasses add is
the family entry point: a ``construct()`` classmethod that loads the
right product from local paths or from a built database, plus the
family's own documentation of what the queries mean.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Iterable
import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from stellarlib.database.core import open_database
from stellarlib.exceptions import UnsupportedModelError
from stellarlib.grids.grid import SpectrumGrid
from stellarlib.models import parse_model_spec

if TYPE_CHECKING:
    from stellarlib.database.core import Database

__all__ = [
    "BaselSpectra",
    "BoszComposition",
    "BoszSpectra",
    "MilesSpectra",
    "StelibSpectra",
    "TlustySpectra",
    "UnifiedSpectra",
    "construct",
]


class UnifiedSpectra(SpectrumGrid, ABC):
    """Abstract unified interface over one library's spectral product.

    Concrete subclasses declare ``model_name`` and a ``construct()``
    entry point; the query machinery (``node``, ``spectrum``,
    ``corner_weights``, ``describe``, ``last_record``) is the grid's own
    — see :class:`~stellarlib.grids.grid.SpectrumGrid`. The per-family
    behavior lives in the backend the grid was built from.
    """

    model_name: str = ""


@dataclasses.dataclass(frozen=True)
class BoszComposition:
    """One composition slice of a BOSZ mirror directory.

    A mirror interleaves every composition and microturbulence variant
    of every (Teff, log g, [M/H]) node, so reading one is a selection.
    ``am`` is [a/M] — the alpha-element enhancement relative to the
    total metallicity (the filename's ``a`` token); ``cm`` is [C/M] —
    the carbon-to-metal ratio (the ``c`` token); both default 0,
    solar-scaled. ``vmicro`` is the microturbulent velocity in km/s
    (the ``v`` token), or ``None`` for the atmosphere-family policy
    (mp 1, ms/ap 2 km/s per node).
    """

    am: float = 0.0
    cm: float = 0.0
    vmicro: int | None = None


class BoszSpectra(UnifiedSpectra):
    """BOSZ 2024 resampled spectra under the unified interface."""

    model_name = "bosz"

    @classmethod
    def construct(
        cls,
        paths: str | Path | Iterable[str | Path] | None = None,
        *,
        wave_path: str | Path | None = None,
        database: str | Path | Database | None = None,
        label: str | None = None,
        collection: str | None = None,
        composition: BoszComposition | None = None,
        release: str | None = None,
    ) -> BoszSpectra:
        """Load BOSZ spectra from local files/directories or a database.

        ``paths`` may be a directory (a mirror is read by selecting one
        composition: ``composition`` defaults to solar-scaled with the
        atmosphere-family vmicro; the wavelength sidecar is discovered
        per the known layouts, or pinned with ``wave_path``), files, or
        both. ``database``/``label`` load a product from a database
        built with ``stellarlib.build_database`` — a mirror product
        needs its ``collection`` (e.g. ``"r500"``) when the metallicity
        label alone is ambiguous. ``wave_path`` pins the wavelength
        sidecar on either branch; left unset on the database branch, the
        sidecar is discovered across the database's products (mirror
        products hold no wave file of their own — the sidecars mirror as
        their own product).
        """
        selected = composition if composition is not None else BoszComposition()
        if database is not None:
            selection = {
                "am": selected.am,
                "cm": selected.cm,
                "vmicro": selected.vmicro,
            }
            # only a stated pin rides along: a None would occupy the
            # option and turn the database's cross-product sidecar
            # discovery off (grid treats a present key as a pin)
            if wave_path is not None:
                selection["wave_path"] = wave_path
            return cls(
                _database_grid(
                    "bosz/2024",
                    database,
                    label,
                    collection=collection,
                    **selection,
                ).sets
            )
        if paths is None:
            raise ValueError(
                "construct('bosz/2024') requires paths= of local files or "
                "database= of a built database"
            )
        grid = SpectrumGrid.from_paths(
            "bosz",
            _as_path_list(paths),
            wave_path=wave_path,
            am=selected.am,
            cm=selected.cm,
            vmicro=selected.vmicro,
        )
        return cls(grid.sets)


class TlustySpectra(UnifiedSpectra):
    """TLUSTY OSTAR2002/BSTAR2006 optical spectra under the unified interface."""

    model_name = "tlusty"

    @classmethod
    def construct(
        cls,
        paths: str | Path | Iterable[str | Path] | None = None,
        release: str | None = None,
        database: str | Path | Database | None = None,
        label: str | None = None,
    ) -> TlustySpectra:
        """Load TLUSTY spectra from local files/directories or a database.

        ``release`` (``"OSTAR2002"`` or ``"BSTAR2006"``) is inferred from
        the files' Teff span when omitted; a stated release is validated.
        """
        if database is not None:
            model = "tlusty" if release is None else f"tlusty/{release}"
            return cls(_database_grid(model, database, label).sets)
        if paths is None:
            raise ValueError(
                "construct('tlusty/<release>') requires paths= of local "
                "files or database= of a built database"
            )
        options = {} if release is None else {"release": release}
        grid = SpectrumGrid.from_paths("tlusty", _as_path_list(paths), **options)
        return cls(grid.sets)


class BaselSpectra(UnifiedSpectra):
    """BaSeL 2.2/3.1 semi-empirical SEDs under the unified interface.

    BaSeL is a theoretical grid (multilinear interpolation applies), but
    each metallicity publishes its own (Teff, log g) node set, so the
    3-axis grid carries large honest holes — a holed bracket cell
    refuses rectangular interpolation and says why;
    ``interp="voronoi"`` answers with the nearest published node.
    """

    model_name = "basel"

    @classmethod
    def construct(
        cls,
        paths: str | Path | Iterable[str | Path] | None = None,
        variant: str | None = None,
        database: str | Path | Database | None = None,
        label: str | None = None,
        release: str | None = None,
    ) -> BaselSpectra:
        """Load BaSeL spectra from the published tarball, an extracted
        tree, or a list of ``*.cor`` files, or from a database built
        with ``stellarlib.build_database``.

        ``variant`` states the calibration of a 3.1 set: required for a
        flat 3.1 file set and for sources holding both variants,
        inferred from a one-variant tree, rejected for 2.2. There is no
        default — the two calibrations serve different applications
        (WLBC99: empirical colour-temperature consistency;
        PADOVA2000: globular-cluster CMDs with Padova 2000 isochrones).
        """
        if database is not None:
            if release is None:
                releases = sorted(
                    {p.release for p in _basel_db(database).products_for("basel")}
                    - {"format"}
                )
                if len(releases) != 1:
                    raise ValueError(
                        f"basel release not stated and the database holds "
                        f"{releases}; qualify the model, e.g. "
                        f"'basel/{releases[0] if releases else '3.1'}'"
                    )
                release = releases[0]
            return cls(
                _database_grid(
                    f"basel/{release}", database, label, variant=variant
                ).sets
            )
        if paths is None:
            raise ValueError(
                "construct('basel/<release>') requires paths= of the "
                "tarball, extracted files, or database= of a built database"
            )
        options = {} if variant is None else {"variant": variant}
        grid = SpectrumGrid.from_paths("basel", _as_path_list(paths), **options)
        return cls(grid.sets)


def _basel_db(database: str | Path | Database) -> Database:

    return database if not isinstance(database, (str, Path)) else open_database(database)


class MilesSpectra(UnifiedSpectra):
    """MILES empirical stellar spectra under the unified interface.

    An empirical library is an observed scatter, not a model grid: each
    star's spectrum carries an arbitrary per-star normalization, so a
    weighted mix of two stars' fluxes is not a spectrum of anything.
    Point queries are therefore nearest-star only — the default
    ``interp="voronoi"`` returns the nearest parameterized star, warned
    and recorded; ``interp="multilinear"`` is refused outright (the
    family's backend entry carries the refusal).
    """

    model_name = "miles"

    @classmethod
    def construct(
        cls,
        paths: str | Path | Iterable[str | Path] | None = None,
        params_path: str | Path | None = None,
        database: str | Path | Database | None = None,
        label: str | None = None,
        release: str | None = None,
    ) -> MilesSpectra:
        """Load MILES spectra from the library tarball, an extracted
        directory or file list (``params_path=`` pins the parameter
        table; it is discovered among the sources when omitted), or from
        a database built with ``stellarlib.build_database`` (the library
        is one product; ``label`` is accepted for uniformity and is
        always ``"all"``)."""
        if database is not None:
            return cls(_database_grid("miles/2011", database, label).sets)
        if paths is None:
            raise ValueError(
                "construct('miles/2011') requires paths= of the library "
                "tarball, extracted files, or database= of a built database"
            )
        options = {} if params_path is None else {"params_path": params_path}
        grid = SpectrumGrid.from_paths(
            "miles", _as_path_list(paths), **options
        )
        return cls(grid.sets)


class StelibSpectra(UnifiedSpectra):
    """STELIB empirical stellar spectra under the unified interface.

    An empirical library is an observed scatter, not a model grid — and
    every star sits on its own wavelength grid — so a weighted mix of
    two stars' spectra is neither an observation of anything nor
    defined on a common sampling. Point queries are therefore
    nearest-star only — the default ``interp="voronoi"`` returns the
    nearest parameterized star, warned and recorded;
    ``interp="multilinear"`` is refused outright (the family's backend
    entry carries the refusal).
    """

    model_name = "stelib"

    @classmethod
    def construct(
        cls,
        paths: str | Path | Iterable[str | Path] | None = None,
        table_path: str | Path | None = None,
        database: str | Path | Database | None = None,
        label: str | None = None,
        release: str | None = None,
    ) -> StelibSpectra:
        """Load STELIB spectra from the catalog tree (or its cor_sp
        subdirectory), a file list (``table_path=`` pins the star table;
        it is discovered among the sources when omitted), or from a
        database built with ``stellarlib.build_database`` (the library
        is one product; ``label`` is accepted for uniformity and is
        always ``"all"``)."""
        if database is not None:
            return cls(_database_grid("stelib/2003", database, label).sets)
        if paths is None:
            raise ValueError(
                "construct('stelib/2003') requires paths= of the catalog "
                "tree or spectrum files, or database= of a built database"
            )
        options = {} if table_path is None else {"table_path": table_path}
        grid = SpectrumGrid.from_paths(
            "stelib", _as_path_list(paths), **options
        )
        return cls(grid.sets)


def _database_grid(
    model: str,
    database: str | Path | Database,
    label: str | None,
    collection: str | None = None,
    **selection,
) -> SpectrumGrid:

    db = database if not isinstance(database, (str, Path)) else open_database(database)
    if "/" in model:
        return db.grid(model, label=label, collection=collection, **selection)
    # an unqualified family ("tlusty", release left to inference): the
    # database states the release itself — the label must pin one
    # product across the family's releases, the same pinning
    # Database.grid applies within one release
    family = model
    releases = sorted(
        p.release for p in db.products_for(family) if label is None or p.label == label
    )
    if len(set(releases)) != 1:
        raise ValueError(
            f"model {model!r} is not version-qualified and label {label!r} "
            f"does not pin one release (products: {releases or 'none'}); "
            f"state the release, e.g. {family}/<release>"
        )
    return db.grid(
        f"{family}/{releases[0]}", label=label, collection=collection, **selection
    )


def _as_path_list(paths: str | Path | Iterable[str | Path]) -> list:
    if isinstance(paths, (str, Path)):
        return [paths]
    return list(paths)


_REGISTRY = {
    "basel": BaselSpectra,
    "bosz": BoszSpectra,
    "tlusty": TlustySpectra,
    "miles": MilesSpectra,
    "stelib": StelibSpectra,
}


def construct(
    model: str,
    paths: str | Path | Iterable[str | Path] | None = None,
    database: str | Path | Database | None = None,
    label: str | None = None,
    **family_options,
) -> UnifiedSpectra:
    """Build the unified point-query facade for one library product.

    ``model`` is version-qualified: ``"bosz/2024"``,
    ``"tlusty/OSTAR2002"``. The release part is validated against what
    the files state about themselves — a mismatch fails loudly.

    Data comes either from ``paths`` (files or directories you hold;
    ``family_options`` pass through, e.g. ``wave_path`` for BOSZ) or from
    a ``database`` built with ``stellarlib.build_database`` (use
    ``label`` to pick one product when a release publishes several);
    never from the network.
    """

    family, release = parse_model_spec(model)
    if release is None:
        raise ValueError(
            f"model must be version-qualified, e.g. 'bosz/2024'; got {model!r}"
        )
    try:
        cls = _REGISTRY[family]
    except KeyError:
        raise UnsupportedModelError(
            f"no unified class registered for model {model!r}; "
            f"registered families: {sorted(_REGISTRY)}"
        ) from None

    obj = cls.construct(
        paths=paths, database=database, label=label,
        release=release, **family_options
    )
    if obj.identity.release != release:
        raise ValueError(
            f"model {model!r} does not match the data: the files state "
            f"release {obj.identity.release!r}"
        )
    return obj
