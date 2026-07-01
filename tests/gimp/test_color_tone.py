"""Tier-2 tests for the color_tone tool group (translated from spike/test_color_tone.py).

Tier-3 golden: every op in this group is a DESTRUCTIVE, deterministic pixel
transform, so each structural test has a sibling ``@pytest.mark.golden`` test that
applies the op to a small fixture and pins the exact pixel output. Goldens are
auto-created + SKIPped on first run (eyeball + commit later).
"""
import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "color_tone"

# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes
# on real GIMP 3.0.4 + 3.2.4): a 64x48 RGB image with eight 8px-wide colour bands
# so every tone op has structure to change. Only the final ``_result`` key is named
# "image" (instead of the spike's "id") so the fx fixture + cleanup can find it.
_FIXTURE_CODE = """
w = 64; h = 48
img = Gimp.Image.new(w, h, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "bg", w, h, Gimp.ImageType.RGB_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
# Paint a gradient by selecting columns and bucket filling via pixel writes.
# Use Gimp blend (gradient) if available, else manual pixel writes.
import math
# Manual per-pixel write is too slow; instead fill rectangles in bands.
Gimp.context_push()
for i in range(8):
    x0 = i * 8
    v = i / 7.0
    col = compat.color((v, 0.5, 1.0 - v))
    Gimp.context_set_foreground(col)
    img.select_rectangle(Gimp.ChannelOps.REPLACE, x0, 0, 8, h)
    layer.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.Selection.none(img)
Gimp.context_pop()
img.flatten()
_result = {"image": img.get_id(), "width": w, "height": h}
"""

# Read one pixel back (compat.read_pixel -> (r, g, b, a) as 0-255 ints).
_READPX_CODE = """
d = find_drawable(args.get("image"), None)
_result = {"px": list(compat.read_pixel(d, args["x"], args["y"]))}
"""


@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"image": .., "width": 64, "height": 48}
    # cleanup so the long-lived session doesn't leak images:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["image"]}, undo_group=False)


def _px(gimp, image, x=20, y=24):
    """Read a single (r, g, b, a) 0-255 pixel from the flattened fixture."""
    r = gimp.run(_READPX_CODE, args={"image": image, "x": x, "y": y},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return tuple(r["result"]["px"])


# --- Tier-2: envelope + structural result -----------------------------------

def test_brightness_contrast(gimp, grp, fx):
    r = grp._brightness_contrast(gimp, brightness=0.3, contrast=0.2, image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)


def test_levels(gimp, grp, fx):
    r = grp._levels(gimp, channel="value", low_in=0.1, high_in=0.9,
                    gamma=1.2, image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    assert isinstance(r["result"]["channel"], int)


def test_curves(gimp, grp, fx):
    r = grp._curves(gimp, points=[0.0, 0.0, 0.5, 0.7, 1.0, 1.0],
                    channel="value", image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    assert isinstance(r["result"]["channel"], int)
    # points is a flat [x0,y0,...]; 6 floats -> 3 control points.
    assert r["result"]["num_points"] == 3


def test_hue_saturation(gimp, grp, fx):
    r = grp._hue_saturation(gimp, hue=30.0, lightness=0.0,
                            saturation=20.0, range="all", image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    assert isinstance(r["result"]["range"], int)


def test_color_balance(gimp, grp, fx):
    r = grp._color_balance(gimp, range="midtones", cyan_red=30.0,
                           magenta_green=-10.0, yellow_blue=20.0, image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    assert isinstance(r["result"]["range"], int)


def test_desaturate(gimp, grp, fx):
    before = _px(gimp, fx["image"])
    assert before[0] != before[1] or before[1] != before[2]  # fixture pixel is coloured
    r = grp._desaturate(gimp, mode="luminance", image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    assert isinstance(r["result"]["mode"], int)
    # luminance desaturate collapses RGB to a single grey value.
    after = _px(gimp, fx["image"])
    assert after[0] == after[1] == after[2], after


def test_invert(gimp, grp, fx):
    before = _px(gimp, fx["image"])
    r = grp._invert(gimp, image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    # invert is a real pixel change for our (non mid-grey) fixture pixel.
    after = _px(gimp, fx["image"])
    assert after != before, (before, after)


def test_posterize(gimp, grp, fx):
    r = grp._posterize(gimp, levels=3, image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    assert r["result"]["levels"] == 3


def test_threshold(gimp, grp, fx):
    r = grp._threshold(gimp, low=0.5, high=1.0, channel="value", image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    assert isinstance(r["result"]["channel"], int)
    # threshold on the value channel yields pure black or white.
    after = _px(gimp, fx["image"])
    assert after[0] == after[1] == after[2], after
    assert after[0] in (0, 255), after


# A tiny low-contrast image (two greys 100 & 150) so normalize's stretch is visible.
_LOWCON_CODE = """
img = Gimp.Image.new(2, 1, Gimp.ImageBaseType.RGB)
l = Gimp.Layer.new(img, "l", 2, 1, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l, None, 0)
Gimp.context_push()
img.select_rectangle(Gimp.ChannelOps.REPLACE, 0, 0, 1, 1)
Gimp.context_set_foreground(compat.color(args["lo"])); l.edit_fill(Gimp.FillType.FOREGROUND)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 1, 0, 1, 1)
Gimp.context_set_foreground(compat.color(args["hi"])); l.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop()
Gimp.Selection.none(img)
_result = {"image": img.get_id()}
"""


def _lowcon(gimp, lo=(100, 100, 100), hi=(150, 150, 150)):
    r = gimp.run(_LOWCON_CODE, args={"lo": list(lo), "hi": list(hi)},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["image"]


def _del(gimp, iid):
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": iid}, undo_group=False)


def test_normalize(gimp, grp, fx):
    r = grp._normalize(gimp, image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    assert r["result"]["keep_colors"] is False
    assert r["result"]["method"] == "stretch-contrast"


def test_normalize_stretches_low_contrast(gimp, grp):
    iid = _lowcon(gimp)
    try:
        res = grp._normalize(gimp, image=iid)
        assert res["ok"], res["error"]
        lo = _px(gimp, iid, 0, 0)[0]
        hi = _px(gimp, iid, 1, 0)[0]
        # 100->~0, 150->~255: normalize pushed the pair to (near) the full range.
        assert lo <= 5 and hi >= 250, (lo, hi)
    finally:
        _del(gimp, iid)


def test_normalize_keep_colors_preserves_hue(gimp, grp):
    # keep_colors stretches all channels uniformly (gegl:stretch-contrast) so the
    # relative channel ordering (hue) is preserved, unlike the per-channel default.
    iid = _lowcon(gimp, lo=(100, 110, 120), hi=(150, 140, 130))
    try:
        res = grp._normalize(gimp, keep_colors=True, image=iid)
        assert res["ok"], res["error"]
        assert res["result"]["keep_colors"] is True
        assert res["result"]["method"] == "stretch-contrast"
        px0 = _px(gimp, iid, 0, 0)
        px1 = _px(gimp, iid, 1, 0)
        # pixel 0 stays blue-leaning (b>r), pixel 1 stays red-leaning (r>b).
        assert px0[2] > px0[0], px0
        assert px1[0] > px1[2], px1
    finally:
        _del(gimp, iid)


def test_normalize_solid_no_blackout(gimp, grp):
    """A flat/solid layer must be a NO-OP, not silently blacked out (the reason both
    modes use gegl:stretch-contrast instead of Drawable.levels_stretch())."""
    for keep in (False, True):
        iid = _lowcon(gimp, lo=(128, 128, 128), hi=(128, 128, 128))
        try:
            res = grp._normalize(gimp, keep_colors=keep, image=iid)
            assert res["ok"], res["error"]
            px = _px(gimp, iid, 0, 0)
            assert tuple(px[:3]) == (128, 128, 128), (keep, px)   # unchanged, NOT (0,0,0)
        finally:
            _del(gimp, iid)


# --- Tier-3: deterministic golden pixel output ------------------------------

@pytest.mark.golden
def test_golden_brightness_contrast(gimp, grp, fx, assert_golden):
    r = grp._brightness_contrast(gimp, brightness=0.3, contrast=0.2, image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "color_tone_brightness_contrast")


@pytest.mark.golden
def test_golden_levels(gimp, grp, fx, assert_golden):
    r = grp._levels(gimp, channel="value", low_in=0.1, high_in=0.9,
                    gamma=1.2, image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "color_tone_levels")


@pytest.mark.golden
def test_golden_curves(gimp, grp, fx, assert_golden):
    r = grp._curves(gimp, points=[0.0, 0.0, 0.5, 0.7, 1.0, 1.0],
                    channel="value", image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "color_tone_curves")


@pytest.mark.golden
def test_golden_hue_saturation(gimp, grp, fx, assert_golden):
    r = grp._hue_saturation(gimp, hue=30.0, lightness=0.0,
                            saturation=20.0, range="all", image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "color_tone_hue_saturation")


@pytest.mark.golden
def test_golden_color_balance(gimp, grp, fx, assert_golden):
    r = grp._color_balance(gimp, range="midtones", cyan_red=30.0,
                           magenta_green=-10.0, yellow_blue=20.0, image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "color_tone_color_balance")


@pytest.mark.golden
def test_golden_desaturate(gimp, grp, fx, assert_golden):
    r = grp._desaturate(gimp, mode="luminance", image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "color_tone_desaturate")


@pytest.mark.golden
def test_golden_invert(gimp, grp, fx, assert_golden):
    r = grp._invert(gimp, image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "color_tone_invert")


@pytest.mark.golden
def test_golden_posterize(gimp, grp, fx, assert_golden):
    r = grp._posterize(gimp, levels=3, image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "color_tone_posterize")


@pytest.mark.golden
def test_golden_threshold(gimp, grp, fx, assert_golden):
    r = grp._threshold(gimp, low=0.5, high=1.0, channel="value", image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "color_tone_threshold")
