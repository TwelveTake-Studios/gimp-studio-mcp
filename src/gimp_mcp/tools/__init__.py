"""Tool groups A..M.

`register_all` wires every implemented group onto the MCP server. Groups that
are still scaffold stubs (their `register` raises NotImplementedError) are
skipped, so the server runs with whatever is implemented — incremental build.
"""
from __future__ import annotations

from . import (
    session,        # A
    document,       # B
    layers,         # C
    masks_alpha,    # D
    selections,     # E
    paint,          # F
    text,           # G
    color_tone,     # H
    filters,        # I
    print_dtf,      # J
    color_mgmt,     # K
    analysis,       # L
    safety,         # M
)

_GROUPS = (
    session, document, layers, masks_alpha, selections, paint, text,
    color_tone, filters, print_dtf, color_mgmt, analysis, safety,
)


def register_all(mcp, ctx) -> dict:
    """Register every implemented tool group; skip scaffold stubs. Returns a report."""
    registered, skipped = [], []
    for group in _GROUPS:
        name = group.__name__.rsplit(".", 1)[-1]
        try:
            group.register(mcp, ctx)
            registered.append(name)
        except NotImplementedError:
            skipped.append(name)
    return {"registered": registered, "skipped": skipped}
