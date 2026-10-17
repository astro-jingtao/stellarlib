"""Sanity checks of the public API surface."""

from __future__ import annotations

import stellarlib


def test_version():
    assert stellarlib.__version__ == "0.1.1"


def test_all_names_importable():
    for name in stellarlib.__all__:
        assert getattr(stellarlib, name) is not None, name


def test_error_hierarchy():
    for name in stellarlib.__all__:
        obj = getattr(stellarlib, name)
        if isinstance(obj, type) and name.endswith("Error"):
            assert issubclass(obj, stellarlib.StellarLibError), name
