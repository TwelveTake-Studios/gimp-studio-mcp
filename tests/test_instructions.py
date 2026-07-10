"""Tier-1: the server ships a non-empty AI ``instructions`` string, and
``build_server()`` wires it onto the FastMCP server.

Runs without GIMP: ``build_server`` constructs a FastMCP + a lazy GimpContext
and only registers tool closures (no bridge connection at build time).
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from gimp_mcp.instructions import INSTRUCTIONS  # noqa: E402

# Cross-cutting guidance that must survive edits (each line was validated by the
# cold-read pressure-test as load-bearing). A rename/drop should fail loudly.
_MARKERS = (
    "MENTAL MODEL",
    "VERIFY, DON'T ASSUME",
    "checkpoint -> work -> restore",
    "print_geometry RESAMPLES",
    "export_dtf_png",
    "fill fills the WHOLE drawable",
    "trim_to_content trims to the FULL alpha bbox",
)


def test_instructions_nonempty_and_substantive():
    assert isinstance(INSTRUCTIONS, str)
    assert len(INSTRUCTIONS) > 500


def test_instructions_key_markers_present():
    missing = [m for m in _MARKERS if m not in INSTRUCTIONS]
    assert not missing, f"instructions missing expected guidance: {missing}"


def test_build_server_wires_instructions():
    from gimp_mcp.server import build_server

    mcp, ctx = build_server()
    try:
        assert getattr(mcp, "instructions", None) == INSTRUCTIONS
    finally:
        ctx.close()
