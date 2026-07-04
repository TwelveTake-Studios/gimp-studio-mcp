"""Tier-1: keep the GIMP-3 get_offsets / get_resolution 3-tuple quirks centralized (#18).

``Gimp.Item.get_offsets`` and ``Gimp.Image.get_resolution`` both return (success, a, b) on
3.0.4 / 3.2.4 — not the two the 2.10 docs imply. ``compat.layer_offsets`` /
``compat.image_resolution`` (bridge/gimp_compat.py) are the sole owners; every tool site
reads through them. Same rationale as the selection-bounds guard: on a valid 3-tuple, head
and tail indexing read the same elements, so structural tests stay green under BOTH — a
revert to head-indexing would slip through. This source guard fails fast if any tool calls
``get_offsets`` / ``get_resolution`` directly (``set_offsets`` / ``set_resolution`` are fine).
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "gimp_mcp")

# Only the quirk owner may call these getters directly.
_OWNER = "bridge/gimp_compat.py"
_CALLS = {
    "get_offsets": re.compile(r"\.get_offsets\s*\("),
    "get_resolution": re.compile(r"\.get_resolution\s*\("),
}


def _shipped_py_files():
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def test_offsets_and_resolution_only_read_in_compat():
    offenders: dict[str, list[str]] = {}
    owner_seen = False
    for path in _shipped_py_files():
        rel = os.path.relpath(path, SRC).replace("\\", "/")
        text = open(path, encoding="utf-8").read()
        found = [name for name, rx in _CALLS.items() if rx.search(text)]
        if rel == _OWNER:
            owner_seen = True
            continue
        if found:
            offenders[rel] = found
    assert owner_seen, f"quirk owner {_OWNER} not found under {SRC}"
    assert not offenders, (
        "get_offsets / get_resolution called outside compat (the hand-rolled GIMP-3 "
        "3-tuple quirk — see #18) — route through compat.layer_offsets / "
        f"compat.image_resolution: {offenders}")
