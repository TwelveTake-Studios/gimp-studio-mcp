"""Tier-2 tests for the layers tool group (translated from spike/test_layers.py)."""
import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "layers"

# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes
# on real GIMP 3.0.4 + 3.2.4). 64x48 RGB image, two RGBA layers, both non-empty:
# l1 ("base") filled solid red, l2 ("top") transparent with a blue 20x20 square.
_FIXTURE_CODE = """
img = Gimp.Image.new(64, 48, Gimp.ImageBaseType.RGB)
l1 = Gimp.Layer.new(img, "base", 64, 48, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l1, None, 0)
Gimp.context_set_foreground(compat.color((200, 40, 40)))
l1.fill(Gimp.FillType.FOREGROUND)
l2 = Gimp.Layer.new(img, "top", 64, 48, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l2, None, 0)
l2.fill(Gimp.FillType.TRANSPARENT)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 8, 8, 20, 20)
Gimp.context_set_foreground(compat.color((40, 80, 220)))
l2.edit_fill(Gimp.FillType.FOREGROUND)
img.get_selection().none(img)
_result = {"image": img.get_id(), "l1": l1.get_id(), "l2": l2.get_id()}
"""

# --- helper read-back snippets (cheap EFFECT verification via the raw bridge) ---
# Top-level layer ids, top (index 0) -> bottom.
_TOP_LEVEL_IDS = """
img = find_image(args.get("image"))
_result = {"ids": [l.get_id() for l in img.get_layers()]}
"""

# A single item's live state: parent group id, mode, opacity, visibility, name.
_ITEM_STATE = """
it = Gimp.Item.get_by_id(args["layer"])
p = it.get_parent() if it else None
_result = {
    "exists": it is not None,
    "parent": (p.get_id() if p is not None else None),
    "mode": (int(it.get_mode()) if it else None),
    "opacity": (it.get_opacity() if it else None),
    "visible": (it.get_visible() if it else None),
    "name": (it.get_name() if it else None),
    "offsets": (list(it.get_offsets()) if it else None),
}
"""

# Prep for merge_down: drop bot to the bottom, top directly above it (spike VERBATIM).
_PRE_MERGE_DOWN = """
img = find_image(args.get("image"))
top = Gimp.Item.get_by_id(args["top"])
bot = Gimp.Item.get_by_id(args["bot"])
n = len(img.get_layers())
img.reorder_item(bot, None, n - 1)
img.reorder_item(top, None, n - 2)
_result = {"ok": True, "order": [l.get_id() for l in img.get_layers()]}
"""

# Flatten a duplicate and save it as a PNG so layer_from_file has a real file to load.
_MK_PNG = """
img = find_image(args.get("image"))
dup = img.duplicate(); dup.flatten()
f = Gio.File.new_for_path(args["path"])
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup, f)
dup.delete()
_result = {"saved": args["path"]}
"""


@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"image":.., "l1":.., "l2":..}
    # cleanup so the long-lived session doesn't leak images:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["image"]}, undo_group=False)


def _state(gimp, image, layer):
    """Read a layer's live state via the raw bridge (effect verification)."""
    r = gimp.run(_ITEM_STATE, args={"image": image, "layer": layer},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]


def _top_ids(gimp, image):
    r = gimp.run(_TOP_LEVEL_IDS, args={"image": image}, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["ids"]


def test_create_layer(gimp, grp, fx):
    r = grp._create_layer(gimp, "made", image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["layer"], int)
    assert res["name"] == "made"
    assert res["width"] == 64 and res["height"] == 48
    # EFFECT: the new layer is now the top-level layer at position 0.
    assert _top_ids(gimp, fx["image"])[0] == res["layer"]


def test_layer_from_file(gimp, grp, fx, tmp_path):
    png = str(tmp_path / "gimp-mcp-layers-fix.png")
    rexp = gimp.run(_MK_PNG, args={"image": fx["image"], "path": png},
                    undo_group=False).to_dict()
    assert rexp["ok"], rexp["error"]
    r = grp._layer_from_file(gimp, png, name="loaded", image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["layer"], int)
    assert res["name"] == "loaded"
    # The flattened fixture is 64x48, so the loaded layer matches.
    assert res["width"] == 64 and res["height"] == 48
    assert res["layer"] in _top_ids(gimp, fx["image"])


def test_duplicate_layer(gimp, grp, fx):
    r = grp._duplicate_layer(gimp, layer=fx["l1"], name="base-copy", image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["layer"], int)
    assert res["layer"] != fx["l1"]        # a genuine copy, distinct id
    assert res["name"] == "base-copy"
    # EFFECT: both the original and the copy are present.
    ids = _top_ids(gimp, fx["image"])
    assert fx["l1"] in ids and res["layer"] in ids


def test_set_opacity(gimp, grp, fx):
    r = grp._set_opacity(gimp, 55.0, layer=fx["l2"], image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["layer"] == fx["l2"]
    assert abs(res["opacity"] - 55.0) < 0.5
    # EFFECT: re-read opacity from the live layer.
    assert abs(_state(gimp, fx["image"], fx["l2"])["opacity"] - 55.0) < 0.5


def test_set_blend_mode(gimp, grp, fx):
    r = grp._set_blend_mode(gimp, "multiply", layer=fx["l2"], image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["layer"] == fx["l2"]
    assert res["mode"] == "MULTIPLY"
    assert isinstance(res["mode_value"], int)
    # EFFECT: the live layer mode matches the reported enum value.
    assert _state(gimp, fx["image"], fx["l2"])["mode"] == res["mode_value"]


def test_set_visible(gimp, grp, fx):
    r = grp._set_visible(gimp, False, layer=fx["l2"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["l2"]
    assert r["result"]["visible"] is False
    assert _state(gimp, fx["image"], fx["l2"])["visible"] is False
    # Toggle back on (spike exercises both directions).
    r2 = grp._set_visible(gimp, True, layer=fx["l2"], image=fx["image"])
    assert r2["ok"], r2["error"]
    assert r2["result"]["visible"] is True
    assert _state(gimp, fx["image"], fx["l2"])["visible"] is True


def test_rename_layer(gimp, grp, fx):
    r = grp._rename_layer(gimp, "renamed", layer=fx["l1"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["l1"]
    assert r["result"]["name"] == "renamed"
    assert _state(gimp, fx["image"], fx["l1"])["name"] == "renamed"


def test_reorder_layer(gimp, grp, fx):
    # Fixture stack is [l2 (top), l1 (bottom)]. Move l1 to position 0 (top).
    r = grp._reorder_layer(gimp, layer=fx["l1"], position=0, image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["l1"]
    assert r["result"]["position"] == 0
    # EFFECT: l1 is now the top-level layer at index 0.
    assert _top_ids(gimp, fx["image"])[0] == fx["l1"]


def test_move_layer(gimp, grp, fx):
    r = grp._move_layer(gimp, 5, 7, layer=fx["l2"], image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["layer"] == fx["l2"]
    assert res["x"] == 5 and res["y"] == 7
    # EFFECT: get_offsets() returns (success, x, y); confirm x/y landed.
    off = _state(gimp, fx["image"], fx["l2"])["offsets"]
    assert off[-2] == 5 and off[-1] == 7


def test_offset_layer(gimp, grp, fx):
    # l2 starts at canvas origin (0, 0); relative offset -> (3, -2).
    r = grp._offset_layer(gimp, dx=3, dy=-2, layer=fx["l2"], image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["layer"] == fx["l2"]
    assert res["x"] == 3 and res["y"] == -2
    off = _state(gimp, fx["image"], fx["l2"])["offsets"]
    assert off[-2] == 3 and off[-1] == -2


# --- offset_content / seam_check (pixel roll, for tileable textures) ---------
# A 4x1 RGBA row of distinct greys so a wrap-offset is exactly checkable.
_ROW4_CODE = """
img = Gimp.Image.new(4, 1, Gimp.ImageBaseType.RGB)
l = Gimp.Layer.new(img, "l", 4, 1, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l, None, 0)
Gimp.context_push()
for x, v in ((0, 10), (1, 20), (2, 30), (3, 40)):
    img.select_rectangle(Gimp.ChannelOps.REPLACE, x, 0, 1, 1)
    Gimp.context_set_foreground(compat.color((v, v, v)))
    l.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop()
Gimp.Selection.none(img)
_result = {"image": img.get_id(), "layer": l.get_id()}
"""


def _row(gimp, image):
    code = ("d = find_drawable(args.get('image'), None)\n"
            "_result = {'row': [list(compat.read_pixel(d, x, 0)) for x in range(4)]}")
    r = gimp.run(code, args={"image": image}, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["row"]


def _row4(gimp):
    r = gimp.run(_ROW4_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["image"], r["result"]["layer"]


def _del(gimp, iid):
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": iid}, undo_group=False)


def test_offset_content_wrap(gimp, grp):
    iid, lid = _row4(gimp)
    try:
        assert [p[0] for p in _row(gimp, iid)] == [10, 20, 30, 40]
        res = grp._offset_content(gimp, dx=1, dy=0, wrap=True, layer=lid, image=iid)
        assert res["ok"], res["error"]
        assert res["result"]["wrap"] is True and res["result"]["dx"] == 1
        # shift right by 1 with wrap: the last column wraps around to the front.
        assert [p[0] for p in _row(gimp, iid)] == [40, 10, 20, 30]
    finally:
        _del(gimp, iid)


def test_offset_content_no_wrap_transparent(gimp, grp):
    iid, lid = _row4(gimp)
    try:
        res = grp._offset_content(gimp, dx=1, dy=0, wrap=False, fill="transparent",
                                  layer=lid, image=iid)
        assert res["ok"], res["error"]
        assert res["result"]["wrap"] is False
        # vacated left column is transparent (alpha 0); the rest shifted in.
        row = _row(gimp, iid)
        assert row[0][3] == 0, row
        assert row[1][0] == 10 and row[3][0] == 30
    finally:
        _del(gimp, iid)


def test_seam_check(gimp, grp):
    iid, lid = _row4(gimp)
    try:
        res = grp._seam_check(gimp, axis="x", layer=lid, image=iid)
        assert res["ok"], res["error"]
        assert res["result"]["dx"] == 2 and res["result"]["dy"] == 0
        # w//2 = 2 with wrap: [10,20,30,40] -> [30,40,10,20].
        assert [p[0] for p in _row(gimp, iid)] == [30, 40, 10, 20]
    finally:
        _del(gimp, iid)


def test_group_create(gimp, grp, fx):
    r = grp._group_create(gimp, name="grp1", image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["layer"], int)
    assert res["name"] == "grp1"
    # EFFECT: the group exists as a top-level item.
    assert res["layer"] in _top_ids(gimp, fx["image"])


def test_group_add(gimp, grp, fx):
    g = grp._group_create(gimp, name="grp1", image=fx["image"])
    assert g["ok"], g["error"]
    gid = g["result"]["layer"]
    r = grp._group_add(gimp, layer=fx["l1"], group=gid, image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["l1"]
    assert r["result"]["group"] == gid
    # EFFECT: l1's parent is now the group (and no longer a top-level layer).
    assert _state(gimp, fx["image"], fx["l1"])["parent"] == gid
    assert fx["l1"] not in _top_ids(gimp, fx["image"])


def test_group_ungroup(gimp, grp, fx):
    # Ordered/stateful: build a populated group, then dissolve it (spike order).
    g = grp._group_create(gimp, name="grp1", image=fx["image"])
    assert g["ok"], g["error"]
    gid = g["result"]["layer"]
    add = grp._group_add(gimp, layer=fx["l1"], group=gid, image=fx["image"])
    assert add["ok"], add["error"]
    r = grp._group_ungroup(gimp, group=gid, image=fx["image"])
    assert r["ok"], r["error"]
    assert fx["l1"] in r["result"]["ungrouped"]
    # EFFECT: child lifted back to the root, empty group removed.
    ids = _top_ids(gimp, fx["image"])
    assert fx["l1"] in ids
    assert gid not in ids
    assert _state(gimp, fx["image"], fx["l1"])["parent"] is None


def test_merge_down(gimp, grp, fx):
    # Ordered/stateful: arrange l2 directly above l1 (spike VERBATIM), then merge.
    rmd = gimp.run(_PRE_MERGE_DOWN,
                   args={"image": fx["image"], "top": fx["l2"], "bot": fx["l1"]},
                   undo_group=False).to_dict()
    assert rmd["ok"], rmd["error"]
    r = grp._merge_down(gimp, layer=fx["l2"], image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["layer"], int)
    assert isinstance(res["name"], str)
    # EFFECT: the two fixture layers collapsed to one top-level layer.
    ids = _top_ids(gimp, fx["image"])
    assert len(ids) == 1
    assert ids[0] == res["layer"]


def test_delete_layer(gimp, grp, fx):
    # Create a spare layer, then delete it (don't disturb the fixture layers).
    made = grp._create_layer(gimp, "made", image=fx["image"])
    assert made["ok"], made["error"]
    mid = made["result"]["layer"]
    r = grp._delete_layer(gimp, layer=mid, image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["deleted"] == mid
    # EFFECT: the layer id is gone from the stack.
    assert mid not in _top_ids(gimp, fx["image"])


def test_flatten(gimp, grp, fx):
    r = grp._flatten(gimp, image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["layer"], int)
    assert isinstance(res["name"], str)
    assert res["num_layers"] == 1
    # EFFECT: exactly one top-level layer remains.
    assert _top_ids(gimp, fx["image"]) == [res["layer"]]
