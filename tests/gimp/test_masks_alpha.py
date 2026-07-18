"""Tier-2 tests for the masks_alpha tool group (translated from spike/test_masks_alpha.py)."""
import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "masks_alpha"

# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes on
# real GIMP 3.0.4 + 3.2.4). 64x48 RGBA layer: red rect, blue rect, black rect, then
# a CLEARED rectangle so alpha varies (transparent hole). Do not invent new GIMP calls.
_FIXTURE_CODE = r"""
w, h = 64, 48
img = Gimp.Image.new(w, h, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "art", w, h, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
# Start fully white & opaque.
layer.fill(Gimp.FillType.WHITE)
Gimp.context_set_opacity(100.0)
Gimp.context_set_paint_mode(Gimp.LayerMode.NORMAL)


def _fill_rect(x, y, ww, hh, col):
    img.select_rectangle(Gimp.ChannelOps.REPLACE, x, y, ww, hh)
    Gimp.context_set_foreground(compat.color(col))
    layer.edit_fill(Gimp.FillType.FOREGROUND)


_fill_rect(2, 2, 24, 20, "red")
_fill_rect(34, 2, 24, 20, (0, 0, 255))
_fill_rect(2, 26, 24, 18, "black")
# Clear a region so alpha varies (transparent hole).
img.select_rectangle(Gimp.ChannelOps.REPLACE, 36, 26, 22, 16)
layer.edit_clear()
Gimp.Selection.none(img)
img.flatten() if False else None
_result = {"image": img.get_id(), "layer": layer.get_id(),
           "has_alpha": layer.has_alpha()}
"""

# Sample pixel helper (proven in the spike) to verify alpha actually changed.
_SAMPLE_CODE = r"""
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
out = []
for (x, y) in args.get("points"):
    out.append(list(compat.read_pixel(drawable, x, y)))
_result = {"pixels": out, "has_alpha": drawable.has_alpha()}
"""

# Create a source channel (proven in the spike) so CHANNEL masks have something to copy.
_MKCHAN_CODE = (
    'img = find_image(args.get("image"))\n'
    'ch = Gimp.Channel.new(img, "srcA", img.get_width(), img.get_height(), '
    '50.0, Gegl.Color.new("white"))\n'
    'img.insert_channel(ch, None, 0)\n'
    '_result = {"channel": ch.get_id()}'
)


def _sample(gimp, image, layer, points):
    """Return the list of per-point [r,g,b,a] pixels read off the drawable."""
    r = gimp.run(_SAMPLE_CODE,
                 args={"image": image, "layer": layer, "points": points},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["pixels"]


@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"image":.., "layer":.., "has_alpha":..}
    # cleanup so the long-lived session doesn't leak images:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["image"]}, undo_group=False)


def test_add_alpha(gimp, grp, fx):
    # Fixture layer is RGBA, so the layer already has alpha (had_alpha True).
    r = grp._add_alpha(gimp, layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert isinstance(r["result"]["layer"], int)
    assert r["result"]["had_alpha"] is True   # fixture layer is RGBA
    assert r["result"]["has_alpha"] is True


def test_lock_alpha(gimp, grp, fx):
    # Lock, then unlock — verify the reported state flips both ways.
    r = grp._lock_alpha(gimp, layer=fx["layer"], locked=True, image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["lock_alpha"] is True
    assert isinstance(r["result"]["layer"], int)

    r = grp._lock_alpha(gimp, layer=fx["layer"], locked=False, image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["lock_alpha"] is False


def test_add_mask(gimp, grp, fx):
    # Stateful by nature: add each mask kind, then remove so only one mask exists at
    # a time. Keep the spike's exact ordering inside one test fn.
    img, layer = fx["image"], fx["layer"]

    for kind in ("white", "black", "alpha", "selection", "copy", "alpha-transfer"):
        r = grp._add_mask(gimp, layer=layer, mask_type=kind, image=img)
        assert r["ok"], "add_mask kind=%s must succeed: %s" % (kind, r["error"])
        assert r["result"]["mask"] is not None
        assert isinstance(r["result"]["mask"], int)
        assert r["result"]["mask_type"] == kind.upper().replace("-", "_")
        # remove it so the next add has a clean slate
        rr = grp._remove_mask(gimp, layer=layer, image=img)
        assert rr["ok"], "cleanup remove_mask failed: %s" % rr["error"]

    # CHANNEL with no source -> graceful supported=False
    rc = grp._add_mask(gimp, layer=layer, mask_type="channel", image=img)
    assert rc["ok"], rc["error"]
    assert rc["result"]["supported"] is False
    assert rc["result"]["mask"] is None
    assert rc["result"]["mask_type"] == "CHANNEL"
    assert rc["result"].get("note")

    # CHANNEL with a real source channel -> works
    mk = gimp.run(_MKCHAN_CODE, args={"image": img}, undo_group=False).to_dict()
    assert mk["ok"], mk["error"]
    chid = mk["result"]["channel"]
    rc2 = grp._add_mask(gimp, layer=layer, mask_type="channel", image=img, channel=chid)
    assert rc2["ok"], rc2["error"]
    assert rc2["result"]["mask"] is not None
    assert isinstance(rc2["result"]["mask"], int)
    assert rc2["result"]["source_channel"] == chid
    grp._remove_mask(gimp, layer=layer, image=img)

    # final functional check: a plain white mask attaches cleanly
    r = grp._add_mask(gimp, layer=layer, mask_type="white", image=img)
    assert r["ok"], "add_mask(white) must work: %s" % r["error"]
    assert isinstance(r["result"]["mask"], int)


def test_apply_mask(gimp, grp, fx):
    # Needs a mask present first; apply merges it into the layer alpha.
    ra = grp._add_mask(gimp, layer=fx["layer"], mask_type="white", image=fx["image"])
    assert ra["ok"], ra["error"]

    r = grp._apply_mask(gimp, layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["had_mask"] is True
    assert r["result"]["mode"] == "APPLY"
    # the mask is gone after applying
    rb = grp._apply_mask(gimp, layer=fx["layer"], image=fx["image"])
    assert rb["ok"], rb["error"]
    assert rb["result"]["had_mask"] is False


def test_remove_mask(gimp, grp, fx):
    # Add a mask, then discard it (without applying).
    ra = grp._add_mask(gimp, layer=fx["layer"], mask_type="white", image=fx["image"])
    assert ra["ok"], ra["error"]

    r = grp._remove_mask(gimp, layer=fx["layer"], image=fx["image"])
    assert r["ok"], r["error"]
    assert r["result"]["had_mask"] is True
    assert r["result"]["mode"] == "DISCARD"
    # nothing left to remove
    rb = grp._remove_mask(gimp, layer=fx["layer"], image=fx["image"])
    assert rb["ok"], rb["error"]
    assert rb["result"]["had_mask"] is False


def test_color_to_alpha(gimp, grp, fx):
    img, layer = fx["image"], fx["layer"]
    before = _sample(gimp, img, layer, [[10, 10]])  # inside the red rect
    r = grp._color_to_alpha(gimp, color="red", layer=layer, image=img)
    assert r["ok"], r["error"]
    assert r["result"]["color"] == "red"
    assert isinstance(r["result"]["layer"], int)
    after = _sample(gimp, img, layer, [[10, 10]])
    # knocking out red must reduce alpha where the red was painted.
    assert after[0][3] < before[0][3], \
        "color_to_alpha should reduce alpha where red was (before=%s after=%s)" \
        % (before[0][3], after[0][3])


def test_cutout_color(gimp, grp, fx):
    # HARD crisp knockout: select the red and DELETE it -> red goes fully transparent,
    # the blue rect is untouched. No defringe/clean -> WYSIWYG.
    img, layer = fx["image"], fx["layer"]
    r = grp._cutout_color(gimp, color="red", layer=layer, image=img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["cutout"] is True and res["cleared"] is True
    assert res["color_hex"] == "#ff0000", res
    assert isinstance(res["layer"], int) and isinstance(res["image"], int)
    smp = _sample(gimp, img, layer, [[10, 10], [45, 10]])   # red rect, blue rect
    assert smp[0][3] < 40, ("red not cut out (should be crisp/transparent)", smp[0])
    assert smp[1][3] > 200, ("blue rect must survive an unrelated-colour cutout", smp[1])


def test_cutout_color_sample_xy(gimp, grp, fx):
    # Eyedropper form: sample the colour from a red pixel, cut it out.
    img, layer = fx["image"], fx["layer"]
    r = grp._cutout_color(gimp, sample_xy=[10, 10], layer=layer, image=img)
    assert r["ok"], r["error"]
    assert r["result"]["color_hex"] == "#ff0000", r["result"]
    smp = _sample(gimp, img, layer, [[10, 10], [45, 10]])
    assert smp[0][3] < 40, ("sampled red not cut out", smp[0])
    assert smp[1][3] > 200, ("blue must survive", smp[1])


def test_cutout_color_needs_target(gimp, grp, fx):
    # With neither `color` nor `sample_xy`, cutout_color must fail loudly (not guess).
    r = grp._cutout_color(gimp, layer=fx["layer"], image=fx["image"])
    assert r["ok"] is False, r
    assert "cutout_color needs" in str(r.get("error")), r.get("error")


# Dedicated fixtures for the contiguous paths (the shared fx has a transparent hole,
# not a clean edge-connected bg).
_CONTIG_INTERIOR_CODE = """
img = Gimp.Image.new(100, 80, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "a", 100, 80, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
Gimp.Selection.none(img)
Gimp.context_push()
Gimp.context_set_foreground(compat.color((255, 255, 255))); layer.edit_fill(Gimp.FillType.FOREGROUND)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 30, 20, 40, 40)
Gimp.context_set_foreground(compat.color((30, 60, 210))); layer.edit_fill(Gimp.FillType.FOREGROUND)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 45, 35, 10, 10)
Gimp.context_set_foreground(compat.color((255, 255, 255))); layer.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop(); Gimp.Selection.none(img)
_result = {"image": img.get_id(), "layer": layer.get_id()}
"""

_CONTIG_CENTRE_CODE = """
img = Gimp.Image.new(100, 80, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "a", 100, 80, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
Gimp.Selection.none(img)
Gimp.context_push()
Gimp.context_set_foreground(compat.color((255, 255, 255))); layer.edit_fill(Gimp.FillType.FOREGROUND)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 40, 30, 20, 20)
Gimp.context_set_foreground(compat.color((220, 20, 20))); layer.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop(); Gimp.Selection.none(img)
_result = {"image": img.get_id(), "layer": layer.get_id()}
"""


def _del(gimp, iid):
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": iid}, undo_group=False)


def test_cutout_color_contiguous_preserves_interior(gimp, grp):
    # White bg, blue centre block, WHITE spot inside the blue. contiguous=True clears
    # only the edge-connected outer white; the interior white survives.
    f = gimp.run(_CONTIG_INTERIOR_CODE, undo_group=False).to_dict()
    assert f["ok"], f["error"]
    iid, lid = f["result"]["image"], f["result"]["layer"]
    try:
        r = grp._cutout_color(gimp, color="white", contiguous=True, image=iid, layer=lid)
        assert r["ok"], r["error"]
        assert r["result"]["contiguous"] is True, r["result"]
        smp = _sample(gimp, iid, lid, [[1, 1], [50, 40]])   # outer white, interior white
        assert smp[0][3] < 40, ("outer white not cleared", smp[0])
        assert smp[1][3] > 200 and min(smp[1][:3]) > 200, \
            ("interior white not preserved by contiguous", smp[1])
    finally:
        _del(gimp, iid)


def test_cutout_color_contiguous_color_not_at_corner_falls_back(gimp, grp):
    # Regression guard for the missing distance check: contiguous=True but the target
    # colour is NOT at any corner must NOT flood the corner's colour. It falls back to a
    # GLOBAL colour select of the target and honestly reports contiguous=False.
    f = gimp.run(_CONTIG_CENTRE_CODE, undo_group=False).to_dict()
    assert f["ok"], f["error"]
    iid, lid = f["result"]["image"], f["result"]["layer"]
    try:
        r = grp._cutout_color(gimp, color=(220, 20, 20), contiguous=True, image=iid, layer=lid)
        assert r["ok"], r["error"]
        assert r["result"]["contiguous"] is False, \
            ("no red corner -> must fall back to global, not flood the white corner", r["result"])
        smp = _sample(gimp, iid, lid, [[50, 40], [1, 1]])   # red centre, white corner
        assert smp[0][3] < 40, ("red centre not cleared (global fallback failed)", smp[0])
        assert smp[1][3] > 200, ("white corner must NOT be cleared", smp[1])
    finally:
        _del(gimp, iid)


def test_luminance_to_alpha(gimp, grp, fx):
    # default: bright -> opaque, dark -> transparent (white-art-on-black).
    img, layer = fx["image"], fx["layer"]
    gap_before = _sample(gimp, img, layer, [[30, 10]])[0][3]  # white bg
    blk_before = _sample(gimp, img, layer, [[10, 30]])[0][3]  # black rect
    assert gap_before == 255 and blk_before == 255            # both opaque to start

    r = grp._luminance_to_alpha(gimp, layer=layer, image=img)
    assert r["ok"], r["error"]
    assert r["result"]["method"] == "buffer-luma"
    assert r["result"]["invert"] is False
    assert r["result"]["knocked_out"] == "dark"

    gap_after = _sample(gimp, img, layer, [[30, 10]])[0][3]
    blk_after = _sample(gimp, img, layer, [[10, 30]])[0][3]
    assert blk_after < gap_after, \
        "luminance_to_alpha(default): dark should become more transparent than bright"
    assert blk_after == 0, "black should go fully transparent"
    assert gap_after == 255, "white should stay fully opaque"


def test_luminance_to_alpha_invert(gimp, grp, fx):
    # invert=True: bright -> transparent (knock out white/highlights).
    img, layer = fx["image"], fx["layer"]
    r = grp._luminance_to_alpha(gimp, layer=layer, invert=True, image=img)
    assert r["ok"], r["error"]
    assert r["result"]["invert"] is True
    assert r["result"]["knocked_out"] == "bright"

    w_alpha = _sample(gimp, img, layer, [[30, 10]])[0][3]  # white bg
    b_alpha = _sample(gimp, img, layer, [[10, 30]])[0][3]  # black rect
    assert w_alpha < b_alpha, \
        "luminance_to_alpha(invert): bright should be more transparent than dark"


def test_threshold_alpha(gimp, grp, fx):
    img, layer = fx["image"], fx["layer"]
    r = grp._threshold_alpha(gimp, value=0.5, layer=layer, image=img)
    assert r["ok"], r["error"]
    assert r["result"]["value"] == 0.5
    assert r["result"]["applied"] == "buffer-threshold"
    assert r["result"]["cut"] == 128          # round(0.5 * 255)

    # verify alpha is binarized to 0/255 across several sample points
    smp = _sample(gimp, img, layer, [[10, 10], [30, 10], [10, 30], [45, 34], [50, 5]])
    alphas = [p[3] for p in smp]
    assert all(a in (0, 255) for a in alphas), \
        "threshold_alpha must binarize alpha to 0/255, got %s" % alphas
