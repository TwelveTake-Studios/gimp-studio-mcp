"""Tier-2 tests for the analysis tool group (translated from spike/test_analysis.py)."""
import os

import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "analysis"

# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes
# on real GIMP 3.0.4 + 3.2.4). 64x48 RGBA image, whole layer red (255,0,0,255),
# with a blue (0,0,255,255) 6x6 rectangle painted at (10,10) so color_at /
# read_region / histogram have known values. Do not invent new GIMP calls.
_FIXTURE_CODE = """
img = Gimp.Image.new(64, 48, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "fixture", 64, 48, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
# Fill red.
Gimp.context_push()
Gimp.context_set_foreground(compat.color((255, 0, 0)))
layer.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop()
# Paint a blue rectangle region (10,10) 6x6.
img.select_rectangle(Gimp.ChannelOps.REPLACE, 10, 10, 6, 6)
Gimp.context_push()
Gimp.context_set_foreground(compat.color((0, 0, 255)))
layer.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop()
Gimp.Selection.none(img)
Gimp.displays_flush()
_result = {"id": img.get_id(), "layer": layer.get_id()}
"""


@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"id": <image>, "layer": <layer>}
    # cleanup so the long-lived session doesn't leak images:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["id"]}, undo_group=False)


# --- pixel-sampling tools (need the painted fixture) -----------------------

def test_color_at(gimp, grp, fx):
    img_id = fx["id"]
    # Blue pixel inside the painted rect.
    r = grp._color_at(gimp, 12, 12, image=img_id)
    assert r["ok"], r["error"]
    rgba = r["result"]["rgba"]
    assert isinstance(rgba, list) and len(rgba) == 4
    assert rgba[:3] == [0, 0, 255]
    # Red pixel outside the rect.
    r2 = grp._color_at(gimp, 0, 0, image=img_id)
    assert r2["ok"], r2["error"]
    assert r2["result"]["rgba"][:3] == [255, 0, 0]


def test_read_region(gimp, grp, fx):
    # 4x4 grid covering the top-left of the blue rect.
    r = grp._read_region(gimp, 10, 10, 4, 4, image=fx["id"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["width"] == 4 and res["height"] == 4
    pixels = res["pixels"]
    assert len(pixels) == 4 and len(pixels[0]) == 4
    # Every sampled pixel sits inside the 6x6 blue rect.
    assert pixels[0][0][:3] == [0, 0, 255]
    assert pixels[3][3][:3] == [0, 0, 255]


def test_get_bitmap(gimp, grp, fx):
    img_id = fx["id"]
    # plain export
    r = grp._get_bitmap(gimp, image=img_id)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["width"] == 64 and res["height"] == 48
    assert res["fmt"] == "png"
    assert isinstance(res["base64"], str) and res["base64"]
    assert isinstance(res["bytes"], int) and res["bytes"] > 0
    # background flatten (drops alpha by compositing over white)
    r = grp._get_bitmap(gimp, image=img_id, background="white")
    assert r["ok"], r["error"]
    assert r["result"]["base64"]
    assert r["result"]["width"] == 64 and r["result"]["height"] == 48
    # region crop + downscale: longest side must be <= max_dim
    r = grp._get_bitmap(gimp, image=img_id, region=[10, 10, 20, 20], max_dim=8)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["base64"]
    assert max(res["width"], res["height"]) <= 8


def test_get_bitmap_returns_viewable_content(gimp, grp, fx):
    """The #1 fix: the registered tool wraps the render as a VIEWABLE Image block +
    metadata (not a base64 text field). Exercises the full _get_bitmap->_bitmap_content
    path against a real headless render."""
    from mcp.server.fastmcp import Image

    out = grp._bitmap_content(grp._get_bitmap(gimp, image=fx["id"], max_dim=16))
    assert isinstance(out, list) and len(out) == 2
    img, meta = out
    assert isinstance(img, Image)
    assert isinstance(meta, dict) and "base64" not in meta
    assert meta["inline"] is True
    assert img.to_image_content().mimeType == "image/png"


def test_get_bitmap_max_dim_clamped_to_4096(gimp, grp):
    """max_dim is clamped to <=4096 even if a much larger value is requested."""
    code = """
img = Gimp.Image.new(8000, 1, Gimp.ImageBaseType.RGB)
l = Gimp.Layer.new(img, "l", 8000, 1, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l, None, 0); l.fill(Gimp.FillType.TRANSPARENT)
_result = {"id": img.get_id()}
"""
    r = gimp.run(code, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    iid = r["result"]["id"]
    try:
        res = grp._get_bitmap(gimp, image=iid, max_dim=99999)
        assert res["ok"], res["error"]
        d = res["result"]
        assert d["max_dim_requested"] == 99999
        # 8000-wide source, clamped to 4096 -> output longest side is exactly 4096.
        assert max(d["width"], d["height"]) == 4096
    finally:
        gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
                 args={"i": iid}, undo_group=False)


def test_get_bitmap_byte_budget_downscales(gimp, grp):
    """A tiny max_bytes forces the auto-step-down loop to shrink the longest side."""
    code = """
img = Gimp.Image.new(256, 256, Gimp.ImageBaseType.RGB)
l = Gimp.Layer.new(img, "l", 256, 256, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l, None, 0)
Gimp.context_push(); Gimp.context_set_foreground(compat.color((200, 50, 50)))
l.fill(Gimp.FillType.TRANSPARENT); l.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop()
_result = {"id": img.get_id()}
"""
    r = gimp.run(code, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    iid = r["result"]["id"]
    try:
        res = grp._get_bitmap(gimp, image=iid, max_dim=256, max_bytes=100)
        assert res["ok"], res["error"]
        d = res["result"]
        assert d["downscaled_to_fit"] is True
        assert max(d["width"], d["height"]) < 256       # the loop stepped it down
    finally:
        gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
                 args={"i": iid}, undo_group=False)


def test_get_bitmap_save_to_writes_file(gimp, grp, fx, tmp_path):
    """save_to writes the render to disk and returns the path, with NO inline base64.
    Uses a .jpg path (fmt arg left at the png default) to prove the reported fmt is
    derived from the save_to extension, not the fmt argument."""
    out = str(tmp_path / "gimp-mcp-bitmap-out.jpg")
    res = grp._get_bitmap(gimp, image=fx["id"], save_to=out, max_dim=32)
    assert res["ok"], res["error"]
    d = res["result"]
    assert d["inline"] is False
    assert d["saved_to"] == out
    assert d["fmt"] == "jpg"                          # from the .jpg extension, not "png"
    assert "base64" not in d
    assert os.path.exists(out) and os.path.getsize(out) > 0
    assert d["bytes"] == os.path.getsize(out)
    assert max(d["width"], d["height"]) <= 32


def test_histogram(gimp, grp, fx):
    r = grp._histogram(gimp, channel="value", image=fx["id"])
    assert r["ok"], r["error"]
    res = r["result"]
    # Normalized to the 6 GIMP stat values + a channel label.
    assert "mean" in res and isinstance(res["mean"], (int, float))
    assert "std_dev" in res and "median" in res
    assert res["channel"] == "VALUE"


# --- perceptual value-space (TTUpscale item 1) ------------------------------
_GRAY_CODE = """
img = Gimp.Image.new(16, 16, Gimp.ImageBaseType.RGB)
l = Gimp.Layer.new(img, "g", 16, 16, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l, None, 0)
u = int(args["u8"])
Gimp.context_push()
Gimp.context_set_foreground(compat.color((u, u, u)))
l.fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop()
_result = {"image": img.get_id()}
"""


def _gray(gimp, u8=148):
    r = gimp.run(_GRAY_CODE, args={"u8": u8}, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["image"]


def _del(gimp, iid):
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": iid}, undo_group=False)


def test_histogram_perceptual_matches_read_pixel(gimp, grp):
    """A solid perceptual-0.58 gray (u8 148): read_pixel/get_bitmap see ~148, so the
    default perceptual histogram must agree — NOT GIMP's gamma-re-encoded ~200."""
    iid = _gray(gimp, 148)
    try:
        res = grp._histogram(gimp, channel="value", image=iid)["result"]
        assert res["space"] == "perceptual"
        assert res["channel"] == "VALUE"
        assert abs(res["mean"] - 148) <= 2, res["mean"]
        assert res["mean"] < 170, res["mean"]        # decisively not the ~200 curve
        assert res["min"] == res["max"]              # uniform fill
        assert res["sampled"] is False               # small image: not downscaled
        assert abs(res["median"] - 148) <= 2, res["median"]
    finally:
        _del(gimp, iid)


def test_histogram_large_drawable_is_sampled(gimp, grp):
    """A >1M-px drawable takes the point-sampled downscale path (sampled=True) but
    still reports the correct perceptual value for a solid fill."""
    code = """
img = Gimp.Image.new(1200, 1000, Gimp.ImageBaseType.RGB)
l = Gimp.Layer.new(img, "l", 1200, 1000, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l, None, 0)
Gimp.context_push()
Gimp.context_set_foreground(compat.color((148, 148, 148)))
l.fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop()
_result = {"image": img.get_id()}
"""
    r = gimp.run(code, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    iid = r["result"]["image"]
    try:
        res = grp._histogram(gimp, channel="value", image=iid)["result"]
        assert res["sampled"] is True
        assert res["pixels"] <= 1000000              # sampled count is capped
        assert abs(res["mean"] - 148) <= 2, res["mean"]  # solid value survives sampling
        assert res["min"] == res["max"]              # uniform -> single value
    finally:
        _del(gimp, iid)


def test_histogram_gimp_space_opt_out_differs(gimp, grp):
    """space='gimp' returns GIMP's native PDB stats (gamma-re-encoded axis ~200 for
    this gray) — a decisively different number from the perceptual default (~148)."""
    iid = _gray(gimp, 148)
    try:
        perc = grp._histogram(gimp, channel="value", image=iid, space="perceptual")["result"]
        native = grp._histogram(gimp, channel="value", image=iid, space="gimp")["result"]
        assert native["space"] == "gimp"
        assert native["mean"] > 185, native["mean"]           # ~200, the old behaviour
        assert native["mean"] - perc["mean"] > 30, (native["mean"], perc["mean"])
    finally:
        _del(gimp, iid)


def test_histogram_channels_perceptual(gimp, grp, fx):
    """Per-channel perceptual means on the red fixture with a small blue rect."""
    red = grp._histogram(gimp, channel="red", image=fx["id"])["result"]
    blue = grp._histogram(gimp, channel="blue", image=fx["id"])["result"]
    assert red["channel"] == "RED" and blue["channel"] == "BLUE"
    assert red["mean"] > 245, red["mean"]        # mostly red -> R high
    assert blue["mean"] < 12, blue["mean"]       # little blue -> B near zero
    # both channels span the two extremes present in the fixture.
    assert red["min"] == 0 and red["max"] == 255


# --- introspection / enumeration tools (image-independent) -----------------

def test_list_gegl_ops(gimp, grp):
    r = grp._list_gegl_ops(gimp)
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["ops"], list)
    assert res["count"] == len(res["ops"]) and res["count"] > 0
    # gaussian-blur is a stable, always-present GEGL op (also used by describe_op).
    assert "gegl:gaussian-blur" in res["ops"]


def test_describe_op(gimp, grp):
    r = grp._describe_op(gimp, "gegl:gaussian-blur")
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["op"] == "gegl:gaussian-blur"
    assert isinstance(res["properties"], list) and len(res["properties"]) > 0
    # Each property carries a name + type.
    first = res["properties"][0]
    assert "name" in first and "type" in first


def test_list_procedures(gimp, grp):
    r = grp._list_procedures(gimp)
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["procedures"], list)
    assert res["count"] == len(res["procedures"]) and res["count"] > 0


@pytest.mark.parametrize("kind", ["fonts", "brushes", "patterns", "gradients", "palettes"])
def test_list_resource(gimp, grp, kind):
    r = grp._list_resource(gimp, kind)
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["names"], list)
    assert res["count"] == len(res["names"])
