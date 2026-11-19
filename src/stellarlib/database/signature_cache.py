"""The database's sha256 cache, keyed by file quick signatures.

Part classification verifies every present part with recorded
integrity evidence on every scan and build — both recorded quantities,
size and sha256 (see ``classify_part_generic``). The cache maps each
file's quick signature — the ``"{path}_{size}_{mtime_ns}"`` key of
``file_quick_signature``, built from the path RELATIVE to the database
root — to its sha256, so a file that did not change since its last
hash is verified with one ``stat`` instead of a full read.

The cache is an accelerator, never an authority: the manifest's
recorded sha256 stays what parts are judged against, and deleting
``signature_cache.json`` is always safe — the next scan re-hashes
everything and rebuilds it.

Keys are relative to the database root, so moving (or renaming, or
symlinking) the database within one machine keeps the cache valid.
There is deliberately no cross-machine invalidation: an
mtime-preserving copy (``cp -a``, ``scp -p``, ``tar``) leaves the keys
usable on the new machine while the content went unverified in
transit, so after migrating a database to another machine, delete
``signature_cache.json`` to force one full re-verify (a fresh rsync
transfer verifies content by checksum anyway).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import os
from pathlib import Path

from file_quick_signature.key import (
    apply_remappers,
    build_key,
    parse_key,
)
from file_quick_signature.remapper import AbsoluteToRelativeRemapper
from file_quick_signature.store import SignatureStore

from stellarlib.integrity import sha256_file

__all__ = ["SIGNATURE_CACHE_NAME", "SignatureCache"]

SIGNATURE_CACHE_NAME = "signature_cache.json"


class SignatureCache:
    """sha256 answers keyed by quick signature, stored in the database.

    The store file is ``<root>/signature_cache.json``; keys are built
    from paths relative to ``root`` (the root is canonicalized the same
    way ``file_quick_signature`` canonicalizes files, so every alias of
    the database produces the same keys). ``writable=False`` serves
    recorded answers but never records or saves — the read-only mode of
    dry-run planning. Instances are mutated only from the thread that
    owns the scan, build, or verify (worker threads never write; the
    hashing workers of a parallel verify consult the store read-only
    through :meth:`peek`).
    """

    def __init__(self, root: str | Path, writable: bool = True) -> None:
        """One database's quick-signature cache (a derived accelerator)."""
        self._root = Path(root)
        self._remapper = AbsoluteToRelativeRemapper(
            os.path.realpath(str(self._root))
        )
        self._store = SignatureStore(
            json_path=str(self._root / SIGNATURE_CACHE_NAME),
            file_remappers=self._remapper,
        ).load()
        self._writable = writable
        self._dirty = False

    def sha256(self, path: str | Path) -> str:
        """The file's sha256 — the recorded answer when the quick
        signature hits; a miss reads and hashes the file and records
        the answer (writable caches only)."""
        key = self._store.signature(str(path))
        recorded = self._store.data.get(key)
        if recorded is not None:
            return recorded
        digest = sha256_file(path)
        if self._writable:
            self._store.data[key] = digest
            self._dirty = True
        return digest

    def peek(self, path: str | Path, size: int, mtime_ns: int) -> str | None:
        """The recorded sha256 for exactly this file state, or None.

        Unlike :meth:`sha256` this never reads or hashes the file: the
        caller supplies the stat quantities it already holds, and the
        key is built from them byte-identically to the store's own
        pipeline. Read-only, so a hashing worker thread may call it
        while the owning thread alone records and saves.
        """
        key = build_key(
            apply_remappers(os.path.realpath(str(path)), self._remapper),
            size,
            mtime_ns,
        )
        return self._store.data.get(key)

    def remember(self, path: str | Path, digest: str) -> None:
        """Record a sha256 already computed elsewhere (the streaming
        hash of a fetch, the hash of a registration copy), so the
        file's first classification needs no read."""
        if not self._writable:
            return
        key = self._store.signature(str(path))
        self._store.data[key] = digest
        self._dirty = True

    def forget(self, paths: Iterable[Path]) -> int:
        """Drop every entry whose file path (relative to the root)
        matches one of ``paths`` exactly; returns how many went. The
        scoped form of deleting the whole store: the next scan re-hashes
        exactly these files."""
        return self._drop_if(
            lambda rel: rel in {str(p).replace("\\", "/") for p in paths}
        )

    def forget_prefixes(self, prefixes: Iterable[Path]) -> int:
        """Drop every entry whose file path (relative to the root) lies
        under one of ``prefixes`` (product directories, with or without
        the trailing slash); returns how many went."""
        cleaned = {
            str(p).replace("\\", "/").rstrip("/") + "/" for p in prefixes
        }
        return self._drop_if(lambda rel: rel.startswith(tuple(cleaned)))

    def _drop_if(self, matches: Callable[[str], bool]) -> int:
        if not self._writable:
            return 0
        doomed = [
            key
            for key in self._store.data
            if matches(parse_key(key).path)
        ]
        for key in doomed:
            del self._store.data[key]
        if doomed:
            self._dirty = True
        return len(doomed)

    def save_if_dirty(self) -> None:
        """Persist recorded answers (atomically); a clean cache writes
        nothing, so read-only scans never create the file."""
        if self._writable and self._dirty:
            self._store.save()
            self._dirty = False
