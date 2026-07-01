"""Tier-1: the whole tool surface must REGISTER with no GIMP.

`pytest tests/` (the CI-runnable suite) otherwise never imports a single
`tools/*.py` — a syntax error, bad import, duplicate `@mcp.tool` name, or
malformed signature would sail through Tier-1 green and only fail under
`--run-gimp`. This closes that gap with a fake MCP that captures registrations.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from gimp_mcp.tools import register_all  # noqa: E402

EXPECTED_GROUPS = 13
EXPECTED_TOOLS = 117  # bump deliberately when adding/removing tools
#                      114 = 113 + list_shirt_presets (garment-colour knockout feature)
#                      117 = 114 + offset_content + seam_check + normalize (TTUpscale backlog)
#                      With GIMP_MCP_NO_EXEC=1 the count is 116 (gimp_exec opted out).

# Tools added in the TTUpscale backlog pass — pin their presence so a rename/drop is loud.
_TTUPSCALE_TOOLS = ("offset_content", "seam_check", "normalize")


class FakeMCP:
    """Captures `@mcp.tool(...)` registrations without a real FastMCP or GIMP."""

    def __init__(self) -> None:
        self.tools: list[str] = []

    def tool(self, *args, name=None, **kwargs):
        def deco(fn):
            self.tools.append(name or getattr(fn, "__name__", repr(fn)))
            return fn
        return deco

    def __getattr__(self, _name):
        # Any other registration method (resource/prompt/...) -> no-op decorator.
        def factory(*a, **k):
            def deco(fn):
                return fn
            return deco
        return factory


def _register():
    mcp = FakeMCP()
    report = register_all(mcp, ctx=None)  # registration must not touch the bridge
    return mcp, report


def test_all_groups_register_without_gimp():
    _mcp, report = _register()
    assert report["skipped"] == [], f"groups failed to register: {report['skipped']}"
    assert len(report["registered"]) == EXPECTED_GROUPS, report["registered"]


def test_no_duplicate_tool_names():
    mcp, _ = _register()
    dups = sorted({n for n in mcp.tools if mcp.tools.count(n) > 1})
    assert not dups, f"duplicate @mcp.tool names: {dups}"


def test_expected_tool_count(monkeypatch):
    monkeypatch.delenv("GIMP_MCP_NO_EXEC", raising=False)  # default surface: gimp_exec ON
    mcp, _ = _register()
    assert len(mcp.tools) == EXPECTED_TOOLS, (
        f"tool count is {len(mcp.tools)}, expected {EXPECTED_TOOLS} — "
        "update EXPECTED_TOOLS if this change is intentional")
    assert "gimp_exec" in mcp.tools


def test_ttupscale_tools_registered():
    """offset_content/seam_check (layers) + normalize (color_tone) must be present."""
    mcp, _ = _register()
    missing = [t for t in _TTUPSCALE_TOOLS if t not in mcp.tools]
    assert not missing, f"missing TTUpscale-backlog tools: {missing}"


def test_gimp_exec_opt_out(monkeypatch):
    """GIMP_MCP_NO_EXEC=1 skips registering the raw host-exec tool (D6), and only
    that tool — every other tool still registers."""
    monkeypatch.setenv("GIMP_MCP_NO_EXEC", "1")
    mcp, report = _register()
    assert report["skipped"] == [], report["skipped"]
    assert "gimp_exec" not in mcp.tools
    assert len(mcp.tools) == EXPECTED_TOOLS - 1
    dups = sorted({n for n in mcp.tools if mcp.tools.count(n) > 1})
    assert not dups, f"duplicate @mcp.tool names: {dups}"
