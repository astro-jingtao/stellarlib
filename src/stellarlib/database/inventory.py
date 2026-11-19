"""Root-level derived files: database.json, README, doc snapshots.

Everything here is regenerated from the directory contents alone — never
copied between machines, never merged.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from stellarlib import __version__
from stellarlib.database.core import Database
from stellarlib.database.scan import scan_database, write_status
from stellarlib.registry.docs import render_documentation

__all__ = ["refresh_inventory"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def refresh_inventory(root: Path) -> None:
    """Regenerate database.json, README.md, doc snapshot, and db_status."""

    root = Path(root)
    products = Database(root).products
    inventory = {
        "stellarlib_version": __version__,
        "generated_at": _now_iso(),
        "products": [
            {
                "family": p.family,
                "release": p.release,
                "label": p.label,
                "kind": p.manifest.get("kind"),
                "n_parts": len(p.manifest.get("parts", {})),
                "size_bytes": sum(
                    (entry or {}).get("size_bytes") or 0
                    for entry in p.manifest.get("parts", {}).values()
                ),
            }
            for p in products
        ],
    }
    _atomic_write_json(root / "database.json", inventory)

    lines = [
        "# stellarlib spectral-library database",
        "",
        f"Built by stellarlib {__version__} on {inventory['generated_at']}.",
        "",
        "Each `<family>/<collection>/<label>` directory is one product: its "
        "data files and a MANIFEST.json recording every part's source URL "
        "(where fetched), sha256, size, retrieval time, and the literature "
        "to cite. The blueprint (design.json) is a local record of the "
        "complete upstream library — whole official packages only; "
        "build_database completes what is missing and db_status.md reports "
        "the two-state scan against that blueprint.",
        "",
        "```python",
        "import stellarlib",
        f"db = stellarlib.open_database({str(root)!r})",
        "grid = db.grid('bosz/2024', label='...')",
        "```",
        "",
        "## Products",
        "",
        "| Product | Kind | Parts | Size (bytes) |",
        "|---|---|---|---|",
    ]
    for p in products:
        parts = p.manifest.get("parts", {})
        size = sum((entry or {}).get("size_bytes") or 0 for entry in parts.values())
        lines.append(
            f"| `{p.family}/{p.release}/{p.label}` | {p.manifest.get('kind')} "
            f"| {len(parts)} | {size:,} |"
        )
    lines += ["", "## Citations", ""]
    seen = set()
    for p in products:
        for citation in p.manifest.get("references", []):
            if citation not in seen:
                seen.add(citation)
                lines.append(f"- {citation}")
    with open(root / "README.md", "w", encoding="utf-8", newline="\n") as _f:
        _f.write("\n".join(lines) + "\n")
    with open(root / "unified-parameters.md", "w", encoding="utf-8", newline="\n") as _f:
        _f.write(render_documentation())
    write_status(scan_database(root))


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as _f:
        _f.write(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)
