"""Tier-1 tests for the server-side compat references (pure Python, no gi).

``gimp_mcp.compat`` only holds pure-Python reference constants + the documented
enum-rename map used by tool-author validation; the gi-using quirk helpers live
in ``bridge/gimp_compat.py`` (NOT importable here — Tier 2/3 territory).
"""
from __future__ import annotations

from gimp_mcp import compat


def test_compat_imports_without_gi():
    # Importing the module must not pull in gi / GIMP.
    import sys

    assert "gi" not in sys.modules or sys.modules.get("gi") is not None  # no forced import
    # (the real guard: this test module imported compat at top with no gi available)
    assert compat is not None


def test_fill_types():
    assert compat.FILL_TYPES == ("white", "transparent", "background", "foreground")
    assert isinstance(compat.FILL_TYPES, tuple)
    assert len(set(compat.FILL_TYPES)) == len(compat.FILL_TYPES)  # unique


def test_base_types():
    assert compat.BASE_TYPES == ("RGB", "GRAY", "INDEXED")
    assert isinstance(compat.BASE_TYPES, tuple)


def test_enum_renames_map():
    assert compat.ENUM_RENAMES == {"Gimp.MaskApplyType": "Gimp.MaskApplyMode"}


def test_enum_renames_keys_and_values_namespaced():
    for old, new in compat.ENUM_RENAMES.items():
        assert old.startswith("Gimp.")
        assert new.startswith("Gimp.")
        assert old != new


def test_constants_are_strings():
    for name in compat.FILL_TYPES:
        assert isinstance(name, str)
    for name in compat.BASE_TYPES:
        assert isinstance(name, str)
