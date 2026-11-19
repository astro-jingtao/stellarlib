"""Database and open_database: the read-side facade over one database root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stellarlib.database.manifest import read_manifest
from stellarlib.grid import SpectrumGrid
from stellarlib.models import parse_model_spec

__all__ = ["Database", "ProductRecord", "open_database"]


@dataclass(frozen=True)
class ProductRecord:
    """One product directory inside a database, described by its MANIFEST."""

    directory: Path
    manifest: dict

    @property
    def family(self) -> str:
        """The manifest's family name."""
        return self.manifest["family"]

    @property
    def release(self) -> str:
        """The manifest's release name."""
        return self.manifest["release"]

    @property
    def label(self) -> str:
        """The product's label within its release."""
        return self.manifest["label"]

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"ProductRecord({self.family}/{self.release}/{self.label})"


class Database:
    """An opened local database: the products present under one root."""

    def __init__(self, root: Path) -> None:
        """Open one database root: every product directory's manifest."""
        self.root = Path(root)
        if not self.root.is_dir():
            raise NotADirectoryError(
                f"{self.root} is not a database directory; build one first "
                "with stellarlib.build_database(database=...)"
            )
        self._products = tuple(
            ProductRecord(directory=manifest.parent, manifest=read_manifest(manifest.parent))
            for manifest in sorted(self.root.glob("*/*/*/MANIFEST.json"))
        )

    @property
    def products(self) -> tuple[ProductRecord, ...]:
        """Every product directory under the root."""
        return self._products

    def products_for(
        self, family: str, release: str | None = None
    ) -> tuple[ProductRecord, ...]:
        """The family's products, optionally one release."""
        return tuple(
            p
            for p in self._products
            if p.family == family and (release is None or p.release == release)
        )

    def grid(
        self,
        model: str,
        label: str | None = None,
        collection: str | None = None,
        **selection,
    ) -> SpectrumGrid:
        """Open one product as a grid.

        ``model`` is version-qualified (``"bosz/2024"``,
        ``"tlusty/OSTAR2002"``). When a release publishes several
        products (BOSZ: one per metallicity directory, so the same
        metallicity label exists in every broadening — pin both, e.g.
        ``collection="r500", label="m+0.00"``; TLUSTY: one per vispec
        tar, the release in ``model`` plus ``label`` suffices), the
        product is picked by ``collection`` and/or ``label``; what
        remains after pinning must be unique.

        ``selection`` passes read-side options through to the family
        reader. For BOSZ mirror products, which interleave every
        composition and vmicro variant of every node, this is where the
        grid's composition is chosen: ``am`` and ``cm`` (defaults 0,
        solar-scaled) and ``vmicro`` (default the family policy: mp 1,
        ms/ap 2 km/s) — a query over the mirror, never a fetch-side
        subset. The wavelength sidecar is located across the database's
        products and pinned automatically.
        """
        family, release = parse_model_spec(model)
        if release is None:
            raise ValueError(
                f"model must be version-qualified, e.g. 'bosz/2024'; "
                f"got {model!r}"
            )
        candidates = self.products_for(family, release)
        if not candidates:
            raise ValueError(
                f"database {self.root} holds no {family}/{release} product; "
                f"present: {[repr(p) for p in self._products]}"
            )
        if collection is not None:
            candidates = tuple(
                p for p in candidates
                if p.directory.parent.name == collection
            )
            if not candidates:
                raise ValueError(
                    f"no product under collection {collection!r} for "
                    f"{family}/{release}; available collections: "
                    f"{sorted(p.directory.parent.name for p in self.products_for(family, release))}"
                )
        if label is not None:
            candidates = tuple(p for p in candidates if p.label == label)
            if not candidates:
                raise ValueError(
                    f"no product labeled {label!r} for {family}/{release}; "
                    f"available: "
                    f"{[p.label for p in self.products_for(family, release)]}"
                )
        if len(candidates) > 1:
            raise ValueError(
                f"{family}/{release} has {len(candidates)} products; pin one "
                f"of {[p.label for p in candidates]} with label="
            )
        record = candidates[0]
        directory = self._data_directory(record)
        options = dict(selection)
        if family == "bosz" and "wave_path" not in options:
            wave = self._bosz_wave_path(record)
            if wave is not None:
                options["wave_path"] = wave
        return SpectrumGrid.from_directory(family, directory, **options)

    def _bosz_wave_path(self, record: ProductRecord) -> Path | None:
        """Locate the wavelength sidecar of one BOSZ product's resolution.

        Mirror products hold no wave file of their own — the sidecars
        mirror as their own upstream directory (``wavelength_grids``),
        so the file is searched across the database's products: first
        among physically present files, then among by-reference parts.
        """
        expected = f"bosz2024_wave_{record.directory.parent.name}.txt"
        for hit in sorted(self.root.glob(f"*/*/*/{expected}")):
            if hit.is_file():
                return hit
        for product in self._products:
            entry = (product.manifest.get("parts") or {}).get(expected)
            if entry and entry.get("path"):
                candidate = Path(entry["path"])
                if candidate.is_file():
                    return candidate
        return None

    def _data_directory(self, record: ProductRecord) -> Path:
        """Where a product's data files live.

        Copied and fetched products keep their files in the product
        directory. Reference-registered products record the original
        directory of their files; the files never move.
        """
        if record.manifest.get("by_reference"):
            sources = sorted(
                {
                    Path(entry["path"]).parent
                    for entry in record.manifest["parts"].values()
                    if entry.get("path")
                }
            )
            if len(sources) != 1:
                raise ValueError(
                    f"reference product {record!r} spans several source "
                    f"directories: {[str(s) for s in sources]}"
                )
            return sources[0]
        return record.directory

    def describe(self) -> str:
        """A human-readable inventory of the database."""
        lines = [
            f"stellarlib database at {self.root}",
            f"products: {len(self._products)}",
        ]
        for p in self._products:
            n_parts = len(p.manifest.get("parts", {}))
            lines.append(
                f"  {p.family}/{p.release}/{p.label} "
                f"({n_parts} parts, {p.manifest.get('kind')})"
            )
        return "\n".join(lines)


def open_database(database: str | Path) -> Database:
    """Open a local database built by ``build_database`` (or a partial copy)."""
    return Database(Path(database))
