"""Tier-2 tests for the filters tool group (translated from spike/test_filters.py).

Exercises every tool the spike proves — gaussian_blur, unsharp_mask, apply_filter
(generic gegl runner) and drop_shadow — with richer structural asserts on each
_impl's real return shape plus an EFFECT check (pixels actually changed) read back
through the bridge.

Tier-3: the four destructive pixel ops are deterministic on the forced CPU GEGL
path (conftest sets GEGL_USE_OPENCL=no), so each also has a @pytest.mark.golden
pixel-compare test. Goldens auto-create+SKIP on first run, then are eyeballed.
"""
import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "filters"

# --- PROVEN fixture-builder code (VERBATIM from spike/test_filters.py) --------
# 64x48 RGBA layer with a solid red square so blur/sharpen/pixelize have an edge.
_FIXTURE_CODE = '''
img = Gimp.Image.new(64, 48, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "fixture", 64, 48, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
layer.fill(Gimp.FillType.TRANSPARENT)
# Paint a solid red square in the middle so blur/sharpen have an edge to act on.
Gimp.context_set_foreground(compat.color((220, 30, 30)))
Gimp.Image.select_rectangle(img, Gimp.ChannelOps.REPLACE, 16, 12, 32, 24)
Gimp.Drawable.edit_fill(layer, Gimp.FillType.FOREGROUND)
Gimp.Selection.none(img)
img.flatten_disabled = True
_result = {"image": img.get_id(), "layer": layer.get_id()}
'''

# An OPAQUE RGB luminance-edge fixture (left dark / right bright, no alpha edge).
# Unsharp mask overshoot/undershoot is only provable on a real luminance gradient;
# on the RGBA red square the transparent<->opaque edge clamps and hides it.
_GRAD_FIXTURE = '''
img = Gimp.Image.new(64, 48, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "grad", 64, 48, Gimp.ImageType.RGB_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
Gimp.context_set_foreground(compat.color((40, 40, 40)))
Gimp.Image.select_rectangle(img, Gimp.ChannelOps.REPLACE, 0, 0, 32, 48)
Gimp.Drawable.edit_fill(layer, Gimp.FillType.FOREGROUND)
Gimp.context_set_foreground(compat.color((200, 200, 200)))
Gimp.Image.select_rectangle(img, Gimp.ChannelOps.REPLACE, 32, 0, 32, 48)
Gimp.Drawable.edit_fill(layer, Gimp.FillType.FOREGROUND)
Gimp.Selection.none(img)
_result = {"image": img.get_id(), "layer": layer.get_id()}
'''

# Read a pixel at (x,y) of a layer via compat — returns (r,g,b,a) ints.
_READPX = '''
d = find_drawable(args.get("image"), args.get("layer"))
_result = {"px": list(compat.read_pixel(d, args["x"], args["y"]))}
'''

# Hash a band of pixels across the square's left edge so we can detect ANY change
# from a filter without guessing the exact overshoot pixel.
_BANDHASH = '''
d = find_drawable(args.get("image"), args.get("layer"))
y = args["y"]
_result = {"band": [list(compat.read_pixel(d, x, y)) for x in range(args["x0"], args["x1"])]}
'''

# Flatten a duplicate onto white and read composited CANVAS pixels just past the
# square's bottom-right corner — where a drop shadow should land.
_SHADOW_PROBE = '''
img = find_image(args.get("image"))
dup = img.duplicate()
flat = dup.flatten()
samples = {}
for (sx, sy) in [(50, 38), (52, 40), (54, 42), (56, 44)]:
    samples["%d,%d" % (sx, sy)] = list(compat.read_pixel(flat, sx, sy))
dup.delete()
_result = {"samples": samples}
'''


# --- fixtures ----------------------------------------------------------------
@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


def _delete_image(gimp, image_id):
    """Tear down a fixture image so the long-lived session doesn't leak images."""
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": image_id}, undo_group=False)


@pytest.fixture
def fx(gimp):
    """Fresh 64x48 RGBA red-square image per test (order-independent)."""
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"image": .., "layer": ..}
    _delete_image(gimp, r["result"]["image"])


@pytest.fixture
def grad_fx(gimp):
    """Fresh opaque luminance-edge image per test (for unsharp_mask)."""
    r = gimp.run(_GRAD_FIXTURE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]
    _delete_image(gimp, r["result"]["image"])


# --- bridge read-back helpers (verbatim probe code from the spike) -----------
def _read_px(gimp, image, layer, x, y):
    r = gimp.run(_READPX, args={"image": image, "layer": layer, "x": x, "y": y},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return tuple(r["result"]["px"])


def _band(gimp, image, layer, x0, x1, y):
    r = gimp.run(_BANDHASH, args={"image": image, "layer": layer,
                                  "x0": x0, "x1": x1, "y": y},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return [tuple(p) for p in r["result"]["band"]]


# --- Tier-2 envelope/structure tests -----------------------------------------
def test_gaussian_blur(gimp, grp, fx):
    # Pixel just inside the sharp edge before blur.
    before = _read_px(gimp, fx["image"], fx["layer"], 16, 24)
    r = grp._gaussian_blur(gimp, 4.0, 4.0, layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["applied"] == "gegl:gaussian-blur"
    assert res["std_dev_x"] == 4.0
    assert res["std_dev_y"] == 4.0
    assert res["layer"] == fx["layer"] and res["image"] == fx["image"]
    # EFFECT: the sharp edge must have softened.
    after = _read_px(gimp, fx["image"], fx["layer"], 16, 24)
    assert before != after, "gaussian_blur did not change pixels"


def test_unsharp_mask(gimp, grp, grad_fx):
    # Use the OPAQUE luminance-edge fixture: unsharp's over/undershoot is provable
    # there (the RGBA square's alpha edge clamps it to a no-op).
    before = _band(gimp, grad_fx["image"], grad_fx["layer"], 28, 37, 24)
    r = grp._unsharp_mask(gimp, 3.0, 2.0, layer=grad_fx["layer"], image=grad_fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["applied"] == "gegl:unsharp-mask"
    assert res["std_dev"] == 3.0
    assert res["scale"] == 2.0
    assert res["layer"] == grad_fx["layer"] and res["image"] == grad_fx["image"]
    after = _band(gimp, grad_fx["image"], grad_fx["layer"], 28, 37, 24)
    assert before != after, "unsharp_mask did not change edge band"


def test_apply_filter_pixelize(gimp, grp, fx):
    # Sample a pixel near the square's edge so the 8px block straddles the
    # red/transparent boundary and the averaged block colour differs.
    before = _read_px(gimp, fx["image"], fx["layer"], 18, 14)
    r = grp._apply_filter(gimp, "gegl:pixelize",
                          params={"size_x": 8, "size_y": 8},
                          layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["applied"] == "gegl:pixelize"
    assert isinstance(res["layer"], int)
    # apply_filter converts underscores in param names to hyphens before setting.
    assert set(res["set_properties"]) == {"size-x", "size-y"}
    after = _read_px(gimp, fx["image"], fx["layer"], 18, 14)
    assert before != after, "pixelize did not change boundary pixels"


def test_apply_filter_gaussian_blur(gimp, grp, fx):
    # Generic runner driving a multi-property gegl op (the spike's "apply-blur" leg).
    r = grp._apply_filter(gimp, "gegl:gaussian-blur",
                          params={"std_dev_x": 3.0, "std_dev_y": 3.0},
                          layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["applied"] == "gegl:gaussian-blur"
    assert set(res["set_properties"]) == {"std-dev-x", "std-dev-y"}
    assert isinstance(res["layer"], int)


def test_apply_filter_unknown_op(gimp, grp, fx):
    # NULL-guard regression: an unknown / unwrappable GEGL op must come back as a
    # CLEAN error naming the op (ValueError), not the cryptic PyGObject
    # "TypeError: constructor returned NULL".
    r = grp._apply_filter(gimp, "gegl:nonexistent-xyz",
                          layer=fx["layer"], image=fx["image"])
    assert r["ok"] is False
    err = r["error"] or {}
    assert err.get("type") == "ValueError", err
    msg = err.get("message") or ""
    assert "gegl:nonexistent-xyz" in msg
    assert "constructor returned NULL" not in msg


def test_drop_shadow(gimp, grp, fx):
    # gegl:dropshadow GROWS the layer and SHIFTS its offsets, so drawable-relative
    # coords no longer equal canvas coords. Verify by flattening a duplicate onto
    # white and reading CANVAS pixels just past the square's bottom-right corner:
    # a soft grey shadow with falloff must appear where it was transparent before.
    r = grp._drop_shadow(gimp, x=6.0, y=6.0, blur=8.0, color="black",
                         opacity=0.6, grow=0.0, layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["applied"] == "gegl:dropshadow"
    assert res["x"] == 6.0
    assert res["y"] == 6.0
    assert res["blur"] == 8.0
    assert res["grow"] == 0.0
    assert res["opacity"] == 0.6
    assert isinstance(res["layer"], int)
    # EFFECT: a soft grey shadow must land in canvas space past the corner.
    shadow = gimp.run(_SHADOW_PROBE, args={"image": fx["image"]},
                      undo_group=False).to_dict()
    assert shadow["ok"], shadow["error"]
    samp = shadow["result"]["samples"]
    greys = [v for v in samp.values() if v[0] == v[1] == v[2] and 100 < v[0] < 250]
    assert greys, "drop_shadow produced no soft grey shadow in canvas space"


# --- Tier-3 golden pixel tests (deterministic destructive ops) ---------------
# Auto-created + SKIPped on first run / --regen-golden; eyeballed + committed later.
@pytest.mark.golden
def test_gaussian_blur_golden(gimp, grp, fx, assert_golden):
    r = grp._gaussian_blur(gimp, 4.0, 4.0, layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "filters_gaussian_blur")


@pytest.mark.golden
def test_unsharp_mask_golden(gimp, grp, grad_fx, assert_golden):
    r = grp._unsharp_mask(gimp, 3.0, 2.0, layer=grad_fx["layer"], image=grad_fx["image"])
    assert r["ok"], r["error"]
    assert_golden(grad_fx["image"], "filters_unsharp_mask")


@pytest.mark.golden
def test_apply_filter_pixelize_golden(gimp, grp, fx, assert_golden):
    r = grp._apply_filter(gimp, "gegl:pixelize",
                          params={"size_x": 8, "size_y": 8},
                          layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "filters_pixelize")


@pytest.mark.golden
def test_drop_shadow_golden(gimp, grp, fx, assert_golden):
    r = grp._drop_shadow(gimp, x=6.0, y=6.0, blur=8.0, color="black",
                         opacity=0.6, grow=0.0, layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert_golden(fx["image"], "filters_drop_shadow")
