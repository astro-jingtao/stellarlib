"""Export a database selection as a transferable plan.

``export_database`` writes a declarative selection to
``<db>/export/<name>/``: the machine-readable ``transfer.json`` carrying
every file of every selected product with its recorded sha256 (the
MANIFEST's own evidence; parts fetched but unrecorded carry null —
``checksums="all"`` hashes them too at the cost of reading every
selected byte), the ``files.rsync`` and ``files.txt`` path tables, and
a README with the receiving procedure. The export travels with the
database (rsync from the database root carries it along); any subset of
product directories that lands on the other side is itself a legal
database for :func:`merge_database`.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

from stellarlib import __version__
from stellarlib.database.build import _apply_select
from stellarlib.database.manifest import read_manifest
from stellarlib.database.scan import scan_database
from stellarlib.database.signature_cache import SignatureCache
from stellarlib.integrity import sha256_file

__all__ = [
    "ExportResult",
    "VerifyReport",
    "export_database",
    "rsync_command",
    "run_rsync",
    "verify_transfer",
]


def _now_iso() -> str:

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclasses.dataclass
class ExportResult:
    """One written export plan."""

    database: Path
    outdir: Path
    plan: dict

    @property
    def products(self) -> int:
        """How many products the plan carries."""
        return len(self.plan["products"])


def _recorded_shas(directory: Path) -> dict[str, str]:
    """The manifest's recorded sha256 per part, keyed by part name (a
    part name is its path relative to the product directory)."""
    manifest = read_manifest(directory)
    return {
        name: entry["sha256"]
        for name, entry in (manifest.get("parts") or {}).items()
        if isinstance(entry, dict) and entry.get("sha256")
    }


def export_database(
    database: str | Path,
    select: list[str] | None = None,
    name: str = "selection",
    *,
    checksums: str = "recorded",
    log: Callable[[str], None] = print,
) -> ExportResult:
    """Write a selection of one database to ``<db>/export/<name>/``.

    ``select`` takes glob patterns over product paths (as in
    :func:`stellarlib.build_database`); a pattern matching no product is
    an error. Every file of every selected product is listed — data,
    manifests, and extracted content alike: extraction is local
    materialization state, never a selection condition. Shas come from
    the MANIFESTs as recorded; ``checksums="all"`` hashes the
    unrecorded files too, making the plan end-to-end verifiable at the
    cost of reading every selected byte.
    """

    root = Path(database)
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a database directory")
    if checksums not in ("recorded", "all"):
        raise ValueError(
            f"checksums must be 'recorded' or 'all'; got {checksums!r}"
        )
    report = scan_database(
        root, signature_cache=SignatureCache(root, writable=False)
    )
    present = [p for p in report.products if p.directory is not None]
    selected = _apply_select(
        [p.design.directory_name for p in present], select
    )
    outdir = root / "export" / name
    if outdir.exists():
        raise FileExistsError(
            f"{outdir} already exists; choose another name or remove it"
        )

    selected_set = set(selected)
    entries = []
    total_bytes = 0
    total_files = 0
    for product in present:
        if product.design.directory_name not in selected_set:
            continue
        recorded = _recorded_shas(product.directory)
        files = []
        for path in sorted(product.directory.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            sha = recorded.get(path.relative_to(product.directory).as_posix())
            if sha is None and checksums == "all":
                sha = sha256_file(path)
            files.append(
                {"path": rel, "size_bytes": path.stat().st_size, "sha256": sha}
            )
            total_bytes += path.stat().st_size
            total_files += 1
        entries.append(
            {
                "product": product.design.directory_name,
                "family": product.design.family,
                "collection": product.design.collection,
                "label": product.design.label,
                "kind": product.design.kind,
                "state": product.state,
                "files": files,
            }
        )

    plan = {
        "stellarlib_version": __version__,
        "generated_at": _now_iso(),
        "products": entries,
        "totals": {
            "products": len(entries),
            "files": total_files,
            "bytes": total_bytes,
            "files_without_sha": sum(
                1 for e in entries for f in e["files"] if f["sha256"] is None
            ),
        },
    }

    outdir.mkdir(parents=True)
    plan_rel_dir = f"export/{name}"
    with open(outdir / "transfer.json", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(plan, indent=2, ensure_ascii=True) + "\n")
    rsync_lines = [e["product"] for e in entries]
    rsync_lines.append(f"{plan_rel_dir}/transfer.json")
    with open(outdir / "files.rsync", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(rsync_lines) + "\n")
    txt_lines = [f["path"] for e in entries for f in e["files"]]
    txt_lines.append(f"{plan_rel_dir}/transfer.json")
    with open(outdir / "files.txt", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(txt_lines) + "\n")
    with open(outdir / "README.md", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_export_readme(root, name, plan))
    log(
        f"exported {len(entries)} product(s), {total_files} file(s), "
        f"{total_bytes:,} bytes to {outdir}"
    )
    return ExportResult(database=root, outdir=outdir, plan=plan)


def _export_readme(root: Path, name: str, plan: dict) -> str:
    return (
        f"# stellarlib database transfer: {name}\n"
        "\n"
        f"{plan['totals']['products']} product(s), "
        f"{plan['totals']['files']} file(s), "
        f"{plan['totals']['bytes']:,} bytes "
        f"({plan['totals']['files_without_sha']} file(s) without a recorded "
        "sha).\n"
        "\n"
        "## Sending\n"
        "\n"
        "```bash\n"
        f"rsync -a --info=progress2 --files-from={root / 'export' / name / 'files.rsync'} "
        f"{root}/ user@host:/path/to/target-db/\n"
        "```\n"
        "\n"
        "Without rsync: copy every path listed in files.txt from a matching "
        "tree into the target database root, or use the merge channel "
        "(stellarlib.merge_database).\n"
        "\n"
        "## Receiving\n"
        "\n"
        "1. the data lands under the target database root — any subset of "
        "product directories is itself a legal database;\n"
        "2. verify the copy: "
        "`stellarlib.verify_transfer(<target-db>, <target-db>/export/"
        f"{name}/transfer.json)`;\n"
        "3. import: `stellarlib.merge_database(<source-db>, <target-db>)` "
        "— the default no-verify merge invalidates exactly the migrated "
        "files' signature-cache entries, so the next verify_database "
        "re-hashes them once; `verify=True` hashes every copied part "
        "against its recorded sha256 during the merge instead.\n"
    )


# -- verify ---------------------------------------------------------------------


@dataclasses.dataclass
class VerifyReport:
    """The outcome of checking a database against an export plan."""

    database: Path
    plan_path: Path
    checked_files: int = 0
    missing: list[str] = dataclasses.field(default_factory=list)
    size_mismatches: list[str] = dataclasses.field(default_factory=list)
    sha_mismatches: list[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every checked file verified."""
        return not (self.missing or self.size_mismatches or self.sha_mismatches)

    def summary(self) -> str:
        """The verification outcome as one human-readable block."""
        status = "OK" if self.ok else "FAILED"
        return (
            f"verify {self.database} against {self.plan_path}: {status} — "
            f"{self.checked_files} file(s) checked, {len(self.missing)} "
            f"missing, {len(self.size_mismatches)} size mismatches, "
            f"{len(self.sha_mismatches)} sha mismatches"
        )


def verify_transfer(database: str | Path, plan_path: str | Path) -> VerifyReport:
    """Check every recorded file of an export plan against a database.

    Files the plan carries a sha for are hashed; the rest (exported
    without ``checksums="all"`` from unrecorded files) are checked for
    presence and size.
    """
    root = Path(database)
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    report = VerifyReport(database=root, plan_path=Path(plan_path))
    for entry in plan["products"]:
        for file_entry in entry["files"]:
            rel = file_entry["path"]
            path = root / rel
            report.checked_files += 1
            if not path.is_file():
                report.missing.append(rel)
                continue
            if file_entry["size_bytes"] != path.stat().st_size:
                report.size_mismatches.append(rel)
            if file_entry["sha256"] and sha256_file(path) != file_entry["sha256"]:
                report.sha_mismatches.append(rel)
    return report


# -- rsync -----------------------------------------------------------------------


def rsync_command(database: str | Path, export_dir: str | Path, destination: str) -> str:
    """The transport command for one export (assembled, not run)."""
    database = Path(database)
    export_dir = Path(export_dir)
    return (
        f"rsync -a --info=progress2 "
        f"--files-from={export_dir / 'files.rsync'} {database}/ {destination}"
    )


def run_rsync(
    database: str | Path,
    export_dir: str | Path,
    destination: str,
    *,
    log: Callable[[str], None] = print,
) -> None:
    """Execute the rsync transport (the command above, run as-is)."""
    if shutil.which("rsync") is None:
        raise FileNotFoundError(
            "rsync is not on PATH; use the copy channel (merge_database) "
            "instead"
        )
    database = Path(database)
    export_dir = Path(export_dir)
    command = [
        "rsync",
        "-a",
        "--info=progress2",
        f"--files-from={export_dir / 'files.rsync'}",
        f"{database}/",
        destination,
    ]
    log(" ".join(command))
    subprocess.run(command, check=True)
