"""Database transfer: export selections, merge databases, verify plans.

Moving a stellarlib database between machines is three explicit steps:
``export_database`` writes a declarative selection (the machine-readable
plan, the rsync path tables, and the receiving procedure); the transport
itself is rsync (``rsync_command``/``run_rsync``) or any byte copy; and
``merge_database`` imports one local database — or any partial copy of
one, which is itself a legal database — into another. ``verify_transfer``
checks a database against an export plan without merging.

The merge never overwrites and never deletes: whole sound products land
as staged atomic directory copies, sound parts complete matching
products part by part, everything already present stays, and conflicts
(both sides archived, shas disagree) are recorded for a human, never
resolved. Root-level derived artifacts are regenerated from the merged
directories, never copied.

The signature cache is the one piece of machine-local state, and the
merge owns its migration rule: a no-verify merge (the default — the
fast path that copies bytes and records without hashing) invalidates
the cache entries of exactly the migrated files, so the next scan or
``verify_database`` re-hashes them once against their recorded evidence
— the forced full verify covers the migrated portion only, never the
whole database. A verifying merge hashes every copied part against the
source's recorded sha256 before it lands and seeds the cache with the
verified answers.
"""
