"""Sanity checks of the public API surface."""

from __future__ import annotations

from importlib.metadata import version as _dist_version

import stellarlib
from stellarlib import exceptions


def test_version():
    # the constant and the installed distribution must agree — the test
    # reads the version from metadata instead of a hardcoded string so a
    # release bump that misses one side fails here, loudly
    assert stellarlib.__version__ == _dist_version("stellarlib")


def test_root_exports_only_the_entry_verbs():
    # the package root holds exactly the four whole-library entry verbs
    # plus the version constant; every other public name is imported
    # from its concrete module so callers see where it lives — any
    # addition here is an API-surface decision, not a convenience
    assert stellarlib.__all__ == [
        "__version__",
        "build_database",
        "construct",
        "open_database",
        "open_grid",
    ]


def test_error_hierarchy():
    for name in exceptions.__all__:
        obj = getattr(exceptions, name)
        if isinstance(obj, type) and name.endswith("Error"):
            assert issubclass(obj, exceptions.StellarLibError), name
