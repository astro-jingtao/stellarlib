"""scan_database: the two-state report of a database against its design.

Every designed product is judged complete (all parts ok) or incomplete
(any part missing or nonconforming, with per-part verdicts and reasons).
Missing parts are the builder's automatic work; nonconforming parts
(integrity failures) never are — the report
lists them for human decision. Directories that map to no design are
reported as unregistered, never silently adopted.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from stellarlib.database.design import (
    FamilyAdapter,
    PartDesign,
    PartVerdict,
    ProductDesign,
    VerdictKind,
)
from stellarlib.database.signature_cache import SignatureCache
from stellarlib.models.bosz.design import BOSZ_ADAPTER
from stellarlib.models.miles.design import MILES_ADAPTER
from stellarlib.models.stelib.design import STELIB_ADAPTER
from stellarlib.models.tlusty.design import TLUSTY_ADAPTER

__all__ = [
    "ADAPTERS",
    "DatabaseReport",
    "ProductReport",
    "enumerate_designs",
    "scan_database",
]

ADAPTERS: dict[str, FamilyAdapter] = {
    "bosz": BOSZ_ADAPTER,
    "tlusty": TLUSTY_ADAPTER,
    "miles": MILES_ADAPTER,
    "stelib": STELIB_ADAPTER,
}


@dataclasses.dataclass(frozen=True)
class ProductReport:
    design: ProductDesign
    state: str  # "complete" | "incomplete"
    verdicts: tuple[PartVerdict, ...]
    directory: Path | None

    @property
    def label_path(self) -> str:
        return self.design.directory_name

    def incomplete_summary(self) -> str:
        bad = [v for v in self.verdicts if v.kind != VerdictKind.OK]
        return "; ".join(v.summary for v in bad[:6]) + (
            f" (+{len(bad) - 6} more)" if len(bad) > 6 else ""
        )


@dataclasses.dataclass(frozen=True)
class DatabaseReport:
    root: Path
    products: tuple[ProductReport, ...]
    unregistered: tuple[str, ...]

    def products_by_state(self, state: str) -> tuple[ProductReport, ...]:
        return tuple(p for p in self.products if p.state == state)

    def missing_part_count(self) -> int:
        return sum(
            1
            for p in self.products
            for v in p.verdicts
            if v.kind == VerdictKind.MISSING
        )

    def nonconforming_parts(self) -> list[PartVerdict]:
        return [
            v
            for p in self.products
            for v in p.verdicts
            if v.kind == VerdictKind.NONCONFORMING
        ]

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "products": [
                {
                    "product": p.label_path,
                    "state": p.state,
                    "parts": [
                        {"part": v.part, "kind": v.kind, "reasons": list(v.reasons)}
                        for v in p.verdicts
                    ],
                }
                for p in self.products
            ],
            "unregistered": list(self.unregistered),
        }

    def render_markdown(self) -> str:
        lines = [
            "# stellarlib database status",
            "",
            f"Two-state scan of `{self.root}` against its design.",
            "",
        ]
        for report in self.products:
            marker = "complete" if report.state == "complete" else "incomplete"
            lines.append(f"- `{report.label_path}`: **{marker}**")
            if report.state == "incomplete":
                lines.append(f"  - {report.incomplete_summary()}")
        if self.unregistered:
            lines += ["", "## Unregistered directories", ""]
            lines += [f"- `{u}`" for u in self.unregistered]
        counts = {
            state: len(self.products_by_state(state))
            for state in ("complete", "incomplete")
        }
        lines += [
            "",
            "## Summary",
            "",
            f"- products: {len(self.products)} "
            f"({counts['complete']} complete, {counts['incomplete']} incomplete)",
            f"- missing parts (builder's automatic work): {self.missing_part_count()}",
            f"- nonconforming parts (human decision): "
            f"{len(self.nonconforming_parts())}",
        ]
        return "\n".join(lines) + "\n"


def _design_file(root: Path) -> Path:
    return root / "design.json"


def _read_declarations(root: Path) -> list[dict]:
    path = _design_file(root)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = payload.get("schema")
    if schema == "stellarlib-design/1":
        raise ValueError(
            f"{path}: schema 'stellarlib-design/1' (window declarations) is "
            "from the pre-blueprint design and is not migrated; a blueprint "
            "(schema 'stellarlib-design/2') declares the full upstream "
            "library per family — declare it anew after removing the old "
            "design.json"
        )
    if schema != "stellarlib-design/2":
        raise ValueError(
            f"{path}: unknown design schema {schema!r} "
            "(expected 'stellarlib-design/2')"
        )
    return list(payload.get("blueprint", []))


def _adapter_for(family: str) -> FamilyAdapter:
    try:
        return ADAPTERS[family]
    except KeyError:
        raise ValueError(
            f"no database adapter for family {family!r}; known: "
            f"{sorted(ADAPTERS)}"
        ) from None


def _manifest_of(directory: Path) -> dict | None:
    path = directory / "MANIFEST.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def enumerate_designs(
    database: str | Path,
    declarations: list[dict] | None = None,
) -> dict[str, tuple[ProductDesign, FamilyAdapter]]:
    """Every designed product of a database: its blueprint's entries (plus
    any ``declarations`` passed for this call only), each expanded through
    its family adapter, keyed by directory name. Duplicate designed
    products are an error — one directory holds one product."""
    root = Path(database)
    designs: dict[str, tuple[ProductDesign, FamilyAdapter]] = {}
    for declaration in [*_read_declarations(root), *(declarations or [])]:
        family = declaration.get("family")
        if family is None:
            raise ValueError(f"blueprint declaration without family: {declaration!r}")
        adapter = _adapter_for(family)
        for design in adapter.designs(declaration):
            if design.directory_name in designs:
                raise ValueError(
                    f"duplicate designed product {design.directory_name}"
                )
            designs[design.directory_name] = (design, adapter)
    return designs


def scan_database(
    database: str | Path,
    declarations: list[dict] | None = None,
    signature_cache: SignatureCache | None = None,
) -> DatabaseReport:
    """Scan one database root: every designed product, every part judged.

    ``declarations`` adds blueprint entries for this scan only (used by
    ``build_database(dry_run=True)`` to plan without writing design.json).
    ``signature_cache`` accelerates the per-part integrity check (see
    :class:`~stellarlib.database.signature_cache.SignatureCache`); one is
    created, learned from, and saved when omitted — pass
    ``SignatureCache(root, writable=False)`` for a scan that must write
    nothing (dry-run planning).
    """
    from stellarlib.database.manifest import read_manifest

    root = Path(database)
    if not root.is_dir():
        raise NotADirectoryError(root)
    own_cache = signature_cache is None
    if own_cache:
        signature_cache = SignatureCache(root)

    designs = enumerate_designs(root, declarations)

    reports: list[ProductReport] = []
    registered: set[str] = set()
    product_dirs = sorted(
        p for p in root.glob("*/*/*") if p.is_dir() and not p.name.startswith(".")
    )
    for directory in product_dirs:
        relative = str(directory.relative_to(root)).replace("\\", "/")
        try:
            manifest = read_manifest(directory)
        except Exception:
            continue  # not a product directory; reported below if unmapped
        if relative in designs:
            design, adapter = designs[relative]
            design = adapter.expand_design(design, directory)
            designs[relative] = (design, adapter)
        else:
            adapter = _adapter_for(manifest["family"])
            design = adapter.design_of_manifest(manifest, directory)
            if design is None or design.directory_name != relative:
                # registered products: the design is the product's own
                # recorded part list
                design = ProductDesign(
                    family=manifest["family"],
                    collection=relative.split("/")[1],
                    label=manifest["label"],
                    kind="local_user_files",
                    parts=tuple(
                        PartDesign(name=name, url=None)
                        for name in (manifest.get("parts") or {})
                    ),
                )
        verdicts = tuple(
            adapter.classify_part(design, part, directory, manifest, signature_cache)
            for part in design.parts
        )
        state = (
            "complete"
            if verdicts and all(v.kind == VerdictKind.OK for v in verdicts)
            else "incomplete"
        )
        reports.append(
            ProductReport(
                design=design,
                state=state,
                verdicts=verdicts,
                directory=directory,
            )
        )
        registered.add(relative)

    for name, (design, adapter) in designs.items():
        if name in registered:
            continue
        verdicts = tuple(
            PartVerdict(part=part.name, kind=VerdictKind.MISSING)
            for part in design.parts
        )
        reports.append(
            ProductReport(
                design=design, state="incomplete", verdicts=verdicts, directory=None
            )
        )

    unregistered = []
    for directory in product_dirs:
        relative = str(directory.relative_to(root)).replace("\\", "/")
        if relative not in registered and relative not in designs:
            unregistered.append(relative)

    if own_cache:
        signature_cache.save_if_dirty()
    return DatabaseReport(
        root=root, products=tuple(reports), unregistered=tuple(unregistered)
    )


def write_status(report: DatabaseReport) -> None:
    """Write db_status.md and db_status.json (regenerated, never copied)."""
    with open(report.root / "db_status.json", "w", encoding="utf-8", newline="\n") as _f:
        _f.write(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    with open(report.root / "db_status.md", "w", encoding="utf-8", newline="\n") as _f:
        _f.write(report.render_markdown())
