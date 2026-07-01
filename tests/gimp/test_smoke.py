"""Tier-2 smoke test: proves the session-scoped headless GIMP fixture boots,
round-trips the bridge, tears down, and that the by-path group loader works.

This is the foundation check the per-group Tier-2 suites build on.
"""
import pytest

pytestmark = pytest.mark.gimp


def test_headless_gimp_boots(gimp):
    r = gimp.run("_result = {'v': Gimp.version()}", undo_group=False).to_dict()
    assert r["ok"], r["error"]
    # Gimp.version() returns a string like "3.2.4".
    v = r["result"]["v"]
    assert isinstance(v, str) and v.split(".")[0] == "3", f"unexpected version {v!r}"


def test_bridge_roundtrip_new_image(gimp):
    r = gimp.run(
        """
img = Gimp.Image.new(32, 16, Gimp.ImageBaseType.RGB)
_result = {"id": img.get_id(), "w": img.get_width(), "h": img.get_height()}
""",
        undo_group=False,
    ).to_dict()
    assert r["ok"], r["error"]
    assert r["result"]["w"] == 32
    assert r["result"]["h"] == 16
    assert isinstance(r["result"]["id"], int)


def test_load_group_by_path(load_group):
    grp = load_group("layers")
    # The by-path loader must expose the group's _impl fns and register().
    assert hasattr(grp, "_create_layer")
    assert hasattr(grp, "register")
