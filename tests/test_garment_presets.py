"""Tier-1: the garment-colour preset data ships and is well-formed.

Guards the knockout `shirt=` feature: if `data/garment_presets.json` ever goes
missing (e.g. not packaged) or corrupt, `shirt=` silently degrades to an empty
preset list. This catches that without GIMP.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from gimp_mcp.tools import print_dtf  # noqa: E402


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_presets_load_and_shape():
    presets = print_dtf._load_garment_presets()
    assert len(presets) >= 12, f"expected >=12 garment presets, got {len(presets)}"
    for name, p in presets.items():
        assert _HEX_RE.match(p.get("hex") or ""), ("bad/missing hex", name, p)
        assert p.get("mode") in ("subtract", "hard"), (name, p)
        assert isinstance(p.get("display"), str) and p["display"].strip(), (name, p)
        if "hex2" in p:                       # optional 2nd thread colour (heather)
            assert _HEX_RE.match(p["hex2"] or ""), ("bad hex2", name, p)
        if "tolerance" in p:                  # out-of-range silently clears the layer
            assert isinstance(p["tolerance"], (int, float)), ("tolerance not numeric", name, p)
            assert 0.0 <= p["tolerance"] <= 1.0, ("tolerance out of range", name, p)


def test_list_shirt_presets_envelope():
    r = print_dtf._list_shirt_presets()
    # full canonical 5-key envelope (ok/result/stdout/warnings/error)
    assert r["ok"] is True and r["error"] is None
    assert "stdout" in r and "warnings" in r
    assert r["result"]["count"] == len(print_dtf._load_garment_presets())
    # every listed preset carries its name key
    assert all("name" in p for p in r["result"]["presets"])


def test_unknown_preset_errors():
    r = print_dtf._knockout_background(None, shirt="chartreuse")
    # canonical structured error envelope (not a bare string) — frozen contract
    assert r["ok"] is False and r["error"]["type"] == "ValueError"
    assert "unknown shirt preset" in r["error"]["message"]
    assert "warnings" in r and r["result"] is None


def test_invalid_mode_errors():
    r = print_dtf._knockout_background(None, mode="soft")
    assert r["ok"] is False and r["error"]["type"] == "ValueError"
    assert "mode must be" in r["error"]["message"]
