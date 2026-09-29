import json
import re
from pathlib import Path

import gimp_mcp

REPO = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((REPO / "server.json").read_text(encoding="utf-8"))
PYPROJECT = (REPO / "pyproject.toml").read_text(encoding="utf-8")


def pyproject_version():
    return re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT, re.M).group(1)


def test_manifest_version_matches_the_package():
    assert MANIFEST["version"] == pyproject_version() == gimp_mcp.__version__
    for package in MANIFEST["packages"]:
        assert package["version"] == pyproject_version()


def test_manifest_description_fits_the_registry_limit():
    assert len(MANIFEST["description"]) <= 100


def test_readme_carries_the_registry_ownership_marker():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {MANIFEST['name']}" in readme


def test_the_registry_launch_command_exists():
    identifier = MANIFEST["packages"][0]["identifier"]
    assert re.search(rf'^{re.escape(identifier)}\s*=\s*"gimp_mcp\.cli:main"', PYPROJECT, re.M)
