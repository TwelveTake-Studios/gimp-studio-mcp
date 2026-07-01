"""Tier-1: enforce the hard rule that the GIMP-side bridge
imports ONLY stdlib + gi.repository (+ its sibling `protocol`).

The bridge loads inside GIMP's own Python with NO venv on `sys.path`, so a
third-party import would break it at plug-in load. This was a CI-lint grep in the
plan; an AST check is precise (ignores strings/comments) and runs everywhere.
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(ROOT, "src", "gimp_mcp", "bridge")

# Files that run INSIDE GIMP (installed into the plug-ins dir by install.py): must
# stay pure. Keep in sync with install._BRIDGE_FILES.
BRIDGE_FILES = ["protocol.py", "gimp_side.py", "gimp_compat.py"]

# gi = PyGObject (ships with GIMP); the bridge files may import each OTHER (all are
# copied side-by-side into the plug-in dir).
ALLOWED_NONSTDLIB = {"gi"} | {os.path.splitext(f)[0] for f in BRIDGE_FILES}


def _top_level_import_roots(path: str) -> set[str]:
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import (from . import x) — in-package, fine
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_bridge_imports_are_stdlib_or_gi_only():
    stdlib = set(sys.stdlib_module_names)  # py3.10+
    offenders: dict[str, list[str]] = {}
    for fn in BRIDGE_FILES:
        path = os.path.join(BRIDGE, fn)
        assert os.path.exists(path), f"missing bridge file: {path}"
        bad = sorted(
            m for m in _top_level_import_roots(path)
            if m not in stdlib and m not in ALLOWED_NONSTDLIB
        )
        if bad:
            offenders[fn] = bad
    assert not offenders, (
        f"bridge files import non-stdlib/non-gi modules (breaks load inside GIMP): {offenders}")
