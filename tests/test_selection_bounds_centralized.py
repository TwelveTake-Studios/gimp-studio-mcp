"""Tier-1: keep the GIMP-3 ``Gimp.Selection.bounds`` 6-tuple quirk centralized (#3).

The quirk normalizer lives in ``bridge/gimp_compat.py`` (``_normalize_bounds`` /
``_bounds_tail``, exposed as ``compat.selection_bbox`` / ``compat.selection_bounds``).
Every tool site must read selection bounds THROUGH compat — never by calling
``Gimp.Selection.bounds`` and hand-indexing the tuple. Two incompatible hand-rolled
conventions (selections.py off the tail, print_dtf.py off the head) were the bug.

Why a source guard and not just the behavioral tests: on today's 6-tuple the head and
tail conventions read the SAME elements (``b[-5] is b[1]``, ``b[-4:] is b[2:6]`` on a
length-6 sequence), so every structural test passes under BOTH conventions. A revert to
head-indexing would stay green. This guard fails fast if any tool re-introduces a direct
``Gimp.Selection.bounds(`` call, which is the only place head-vs-tail can diverge.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "gimp_mcp")

# Only the quirk owner may call Gimp.Selection.bounds directly.
_OWNER = "bridge/gimp_compat.py"
_CALL = re.compile(r"Gimp\.Selection\.bounds\s*\(")


def _shipped_py_files():
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def test_selection_bounds_only_called_in_compat():
    offenders: dict[str, int] = {}
    owner_seen = False
    for path in _shipped_py_files():
        rel = os.path.relpath(path, SRC).replace("\\", "/")
        text = open(path, encoding="utf-8").read()
        hits = len(_CALL.findall(text))
        if rel == _OWNER:
            owner_seen = True
            continue
        if hits:
            offenders[rel] = hits
    assert owner_seen, f"quirk owner {_OWNER} not found under {SRC}"
    assert not offenders, (
        "Gimp.Selection.bounds called outside compat (the hand-rolled 6-tuple quirk "
        "is the #3 bug) — route through compat.selection_bbox / compat.selection_bounds: "
        f"{offenders}")
