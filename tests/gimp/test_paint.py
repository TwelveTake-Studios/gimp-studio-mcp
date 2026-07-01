"""Tier-2 tests for the paint tool group (translated from spike/test_paint.py)."""
import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "paint"

# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes
# on real GIMP 3.0.4 + 3.2.4): a 64x48 RGBA white layer + a rectangular selection
# at 8,8 -> 40,32. Do not invent new GIMP calls.
_FIXTURE_CODE = """
img = Gimp.Image.new(64, 48, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "paint", 64, 48, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
layer.fill(Gimp.FillType.WHITE)
# Rectangular selection 8,8 -> 40,32 for stroke_selection / fill-in-selection.
Gimp.Selection.none(img)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 8, 8, 32, 24)
Gimp.displays_flush()
_result = {"image": img.get_id(), "layer": layer.get_id()}
"""

# Read one pixel back as a [r, g, b, a] 0-255 list (proven spike helper).
_READPX_CODE = """
drw = find_drawable(args.get("image"), args.get("layer"))
_result = {"px": list(compat.read_pixel(drw, int(args["x"]), int(args["y"])))}
"""

# Re-apply / clear the selection (verbatim spike inline snippets).
_SELECT_RECT_CODE = ("img = find_image(args.get('image'));"
                     "img.select_rectangle(Gimp.ChannelOps.REPLACE, 8, 8, 32, 24);"
                     "Gimp.displays_flush();_result={'ok':True}")
_SELECT_NONE_CODE = ("Gimp.Selection.none(find_image(args.get('image')));"
                     "_result={'ok':True}")


@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"image": .., "layer": ..}
    # cleanup so the long-lived session doesn't leak images:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["image"]}, undo_group=False)


def _px(gimp, image, layer, x, y):
    """Read back the [r, g, b, a] pixel at (x, y) to verify a paint EFFECT."""
    r = gimp.run(_READPX_CODE, args={"image": image, "layer": layer, "x": x, "y": y},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["px"]


# ----------------------------------------------------------------------------
# context-only state ops
# ----------------------------------------------------------------------------
def test_set_fg(gimp, grp):
    r = grp._set_fg(gimp, "red")
    assert r["ok"], r["error"]
    # _impl returns {"foreground": [r, g, b, a]} as 0-255 ints.
    fg = r["result"]["foreground"]
    assert fg[0] >= 250 and fg[1] <= 5 and fg[2] <= 5, fg
    # EFFECT: the live context foreground reflects the set color.
    chk = gimp.run("_result = {'fg': list(compat.rgba(Gimp.context_get_foreground()))}",
                   undo_group=False).to_dict()
    assert chk["ok"], chk["error"]
    cfg = chk["result"]["fg"]
    assert cfg[0] >= 250 and cfg[1] <= 5 and cfg[2] <= 5, cfg


def test_set_bg(gimp, grp):
    r = grp._set_bg(gimp, [0, 0, 255])
    assert r["ok"], r["error"]
    bg = r["result"]["background"]
    assert bg[0] <= 5 and bg[1] <= 5 and bg[2] >= 250, bg
    # EFFECT: the live context background reflects the set color.
    chk = gimp.run("_result = {'bg': list(compat.rgba(Gimp.context_get_background()))}",
                   undo_group=False).to_dict()
    assert chk["ok"], chk["error"]
    cbg = chk["result"]["bg"]
    assert cbg[0] <= 5 and cbg[1] <= 5 and cbg[2] >= 250, cbg


def test_set_paint_opacity(gimp, grp):
    r = grp._set_paint_opacity(gimp, 100)
    assert r["ok"], r["error"]
    assert r["result"]["opacity"] == 100.0
    # EFFECT: the live context opacity reflects the set value.
    chk = gimp.run("_result = {'o': Gimp.context_get_opacity()}", undo_group=False).to_dict()
    assert chk["ok"], chk["error"]
    assert abs(chk["result"]["o"] - 100.0) < 1e-6, chk["result"]["o"]


def test_set_brush(gimp, grp):
    r = grp._set_brush(gimp, "2. Hardness 050")
    assert r["ok"], r["error"]
    assert r["result"]["brush"] == "2. Hardness 050"
    # EFFECT: the live context brush is the one we just set.
    chk = gimp.run("_result = {'b': Gimp.context_get_brush().get_name()}",
                   undo_group=False).to_dict()
    assert chk["ok"], chk["error"]
    assert chk["result"]["b"] == "2. Hardness 050"


# ----------------------------------------------------------------------------
# destructive paint ops (fresh fixture per test)
# ----------------------------------------------------------------------------
def test_fill(gimp, grp, fx):
    # Set fg = red, then fill the whole drawable with the foreground.
    assert grp._set_fg(gimp, "red")["ok"]
    r = grp._fill(gimp, "foreground", layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["layer"]
    assert r["result"]["fill_type"] == "FOREGROUND"
    # EFFECT: drawable.fill ignores the selection -> (1,1) (outside it) is red.
    px = _px(gimp, fx["image"], fx["layer"], 1, 1)
    assert px[0] >= 250 and px[1] <= 5 and px[2] <= 5, px


def test_bucket_fill(gimp, grp, fx):
    # Seed INSIDE the active selection (8,8..40,32); bucket honors the mask.
    r = grp._bucket_fill(gimp, 20, 20, color=[0, 255, 0],
                         layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["layer"]
    assert r["result"]["x"] == 20 and r["result"]["y"] == 20
    # EFFECT: (20,20) flooded green; (5,5) outside the selection stays white.
    inside = _px(gimp, fx["image"], fx["layer"], 20, 20)
    outside = _px(gimp, fx["image"], fx["layer"], 5, 5)
    assert inside[0] <= 5 and inside[1] >= 250, inside
    assert outside[0] >= 250 and outside[1] >= 250 and outside[2] >= 250, outside


def test_gradient(gimp, grp, fx):
    # fg=red -> bg=blue linear gradient along x across the selection.
    assert grp._set_fg(gimp, [255, 0, 0])["ok"]
    assert grp._set_bg(gimp, [0, 0, 255])["ok"]
    r = grp._gradient(gimp, 8, 8, 39, 8, gradient_type="linear",
                      layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["layer"]
    assert r["result"]["gradient_type"] == "LINEAR"
    # EFFECT: left end is red-dominant, right end is blue-dominant.
    left = _px(gimp, fx["image"], fx["layer"], 9, 20)
    right = _px(gimp, fx["image"], fx["layer"], 38, 20)
    assert left[0] > left[2], left
    assert right[2] > right[0], right


def test_stroke_selection(gimp, grp, fx):
    # Re-assert the selection (proven spike snippet), then stroke its outline.
    gimp.run(_SELECT_RECT_CODE, args={"image": fx["image"]}, undo_group=False)
    r = grp._stroke_selection(gimp, line_width=3, color="black",
                              layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["layer"]
    assert r["result"]["stroked"] is True
    # EFFECT: the selection edge at (8,20) is darkened by the black stroke.
    px = _px(gimp, fx["image"], fx["layer"], 8, 20)
    assert max(px[0], px[1], px[2]) < 128, px


def test_pencil(gimp, grp, fx):
    # Clear the selection first so ink isn't masked out (spike ordering).
    gimp.run(_SELECT_NONE_CODE, args={"image": fx["image"]}, undo_group=False)
    assert grp._set_brush(gimp, "2. Hardness 050")["ok"]
    r = grp._pencil(gimp, [10, 10, 50, 40], color=[255, 0, 255],
                    layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["layer"]
    assert r["result"]["n_points"] == 2
    # EFFECT: hard-edged magenta ink at the (10,10) endpoint (green ~0).
    px = _px(gimp, fx["image"], fx["layer"], 10, 10)
    assert px[0] >= 250 and px[1] <= 5 and px[2] >= 250, px


def test_paintbrush(gimp, grp, fx):
    # Clear the selection so the full stroke is unmasked (spike ordering).
    gimp.run(_SELECT_NONE_CODE, args={"image": fx["image"]}, undo_group=False)
    r = grp._paintbrush(gimp, [12, 12, 48, 36], brush="2. Hardness 050", size=5,
                        color=[0, 0, 0], layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["layer"] == fx["layer"]
    assert r["result"]["n_points"] == 2
    # EFFECT: the (12,12) endpoint is darkened from white by the black brush.
    px = _px(gimp, fx["image"], fx["layer"], 12, 12)
    assert max(px[0], px[1], px[2]) < 230, px
