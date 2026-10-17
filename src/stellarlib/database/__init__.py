"""The local spectral-library database: explicit, self-contained, portable.

Layout (``database/`` package — generic management core, per-family
adapters under ``models/<family>/design.py``)::

    <database>/
        design.json                # the declared target: fetchable windows
        database.json              # inventory of every product (regenerated)
        README.md                  # human summary (regenerated)
        unified-parameters.md      # mapping documentation snapshot
        db_status.md / .json       # two-state scan report (regenerated)
        <family>/<collection>/<label>/
            MANIFEST.json          # parts, checksums, identity, format
            <data files>           # resam/vis spectra + wavelength grids

Concepts (mirroring the unified database architecture): a **Product** is
one directory holding one coherent set of files plus its MANIFEST; a
**Part** is one file, the unit of fetching and two-state judgment; the
**Design** is the declared target — the complete set of products and
parts the database is meant to hold; the **Scanner** classifies every
designed part (ok / missing / nonconforming) into a two-state product
report (complete / incomplete); the **Builder** fetches missing parts
through the family adapter and never touches nonconforming ones. Fetching
is on demand: the design is complete, what is actually downloaded is the
user's choice (``dry_run`` first, then per-window builds).
"""
