"""Sanity checks of the public API surface."""

from __future__ import annotations

from importlib.metadata import version as _dist_version

import stellarlib


def test_version():
    # the constant and the installed distribution must agree — the test
    # reads the version from metadata instead of a hardcoded string so a
    # release bump that misses one side fails here, loudly
    assert stellarlib.__version__ == _dist_version("stellarlib")


def test_all_names_importable():
    for name in stellarlib.__all__:
        assert getattr(stellarlib, name) is not None, name


def test_error_hierarchy():
    for name in stellarlib.__all__:
        obj = getattr(stellarlib, name)
        if isinstance(obj, type) and name.endswith("Error"):
            assert issubclass(obj, stellarlib.StellarLibError), name
