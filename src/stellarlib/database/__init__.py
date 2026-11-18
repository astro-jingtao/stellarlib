"""The local spectral-library database: explicit, self-contained, portable.

Layout (``database/`` package — generic management core, per-family
adapters under ``models/<family>/design.py``)::

    <database>/
        design.json                # the blueprint: the full upstream library
        database.json              # inventory of every product (regenerated)
        README.md                  # human summary (regenerated)
        unified-parameters.md      # mapping documentation snapshot
        db_status.md / .json       # two-state scan report (regenerated)
        signature_cache.json       # sha256-by-quick-signature cache
                                   # (derived accelerator; delete to
                                   # force a full re-verify)
        <family>/<collection>/<label>/
            MANIFEST.json          # parts, checksums, identity, format
            <data files>           # whole official packages: directory
                                   # mirrors (with their upstream bulk
                                   # manifest) or verbatim archive files

Concepts (mirroring the unified database architecture): a **Product** is
one directory holding one whole official package plus its MANIFEST; a
**Part** is one file, the unit of fetching and two-state judgment; the
**Blueprint** is a local record of the complete upstream library — every
family it names is held in full, as whole upstream units, never a
subset; the **Scanner** classifies every designed part (ok / missing /
nonconforming) into a two-state product report (complete / incomplete);
the **Builder** fetches missing parts through the family adapter and
never touches nonconforming ones; the **Verifier** (``verify_database``)
re-checks recorded evidence in place and backfills records for
present-but-unrecorded parts, never fetching; the **Transfer**
(``database/transfer/``) moves products between databases — export a
plan, transport the bytes, merge into the target, with the signature
cache invalidated for exactly the migrated portion so the next verify
re-hashes it once.
"""
