"""Tier-2 tests for the selections tool group (translated from spike/test_selections.py)."""
import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "selections"

# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes
# on real GIMP 3.0.4 + 3.2.4). 64x48 RGB image, one RGBA layer: transparent except
# a solid-red rectangle filled at (16,12) 32x24 (so by-color/fuzzy/alpha have real
# content), plus a rectangular path (10,10)->(40,30) for select_from_path.
_FIXTURE_CODE = """
w = 64; h = 48
img = Gimp.Image.new(w, h, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "fixture", w, h, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
layer.fill(Gimp.FillType.TRANSPARENT)
# Fill a colored rectangle region (16,12)-(48,36) with red, so by-color/fuzzy/
# alpha have real content to find.
img.select_rectangle(Gimp.ChannelOps.REPLACE, 16, 12, 32, 24)
Gimp.context_set_foreground(Gegl.Color.new("red"))
layer.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.Selection.none(img)
# Add a rectangular path so select_from_path can be verified for real.
p = Gimp.Path.new(img, "rect-path")
img.insert_path(p, None, 0)
p.stroke_new_from_points(Gimp.PathStrokeType.BEZIER,
    [10.0,10.0,10.0,10.0,10.0,10.0,
     40.0,10.0,40.0,10.0,40.0,10.0,
     40.0,30.0,40.0,30.0,40.0,30.0,
     10.0,30.0,10.0,30.0,10.0,30.0], True)
Gimp.displays_flush()
_result = {"id": img.get_id(), "layer_id": layer.get_id(), "path_id": p.get_id()}
"""

# A bare image with NO paths, to verify select_from_path degrades gracefully
# (spike's _NOPATH_FIXTURE, VERBATIM).
_NOPATH_FIXTURE = """
img = Gimp.Image.new(32, 32, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "bare", 32, 32, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
_result = {"id": img.get_id()}
"""

# Read the live selection bounds straight off the bridge (cheap EFFECT check).
# Mirrors the _BOUNDS_TAIL shape used inside the tool group.
_LIVE_BOUNDS = """
img = find_image(args.get("image"))
b = Gimp.Selection.bounds(img)
_non_empty = bool(b[-5])
_result = {
    "selection_empty": (not _non_empty),
    "bounds": (None if not _non_empty else [b[-4], b[-3], b[-2] - b[-4], b[-1] - b[-3]]),
}
"""


@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"id":.., "layer_id":.., "path_id":..}
    # cleanup so the long-lived session doesn't leak images:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["id"]}, undo_group=False)


@pytest.fixture
def bare_fx(gimp):
    r = gimp.run(_NOPATH_FIXTURE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"id":..}
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["id"]}, undo_group=False)


def _live_bounds(gimp, image):
    """Re-read the image's current selection bounds via the raw bridge."""
    r = gimp.run(_LIVE_BOUNDS, args={"image": image}, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]


# --- geometry --------------------------------------------------------------
def test_select_rect(gimp, grp, fx):
    img = fx["id"]
    r = grp._select_rect(gimp, 4, 4, 20, 16, "replace", img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["image"] == img
    assert res["selection_empty"] is False
    # Exact rect bounds [x, y, w, h] — also the spike's "leaked-flag shape" guard.
    assert res["bounds"] == [4, 4, 20, 16]


def test_select_ellipse(gimp, grp, fx):
    img = fx["id"]
    r = grp._select_ellipse(gimp, 4, 4, 20, 16, "replace", img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is False
    # The ellipse is inscribed in the x,y,w,h box, so it touches every edge and
    # its selection bounding box equals that box.
    assert res["bounds"] == [4, 4, 20, 16]


def test_select_all(gimp, grp, fx):
    img = fx["id"]
    r = grp._select_all(gimp, img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is False
    # Whole 64x48 canvas.
    assert res["bounds"] == [0, 0, 64, 48]


def test_select_invert(gimp, grp, fx):
    img = fx["id"]
    # Spike ordering: select_all then invert -> inverting a full selection empties it.
    a = grp._select_all(gimp, img)
    assert a["ok"], a["error"]
    r = grp._select_invert(gimp, img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is True
    assert res["bounds"] is None


# --- grow / shrink / feather / border (each needs a real selection first) ---
def test_select_grow(gimp, grp, fx):
    img = fx["id"]
    setup = grp._select_rect(gimp, 16, 12, 32, 24, "replace", img)
    assert setup["ok"], setup["error"]
    r = grp._select_grow(gimp, 2, img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is False
    # Growing by 2 px expands every edge: [16,12,32,24] -> [14,10,36,28].
    assert res["bounds"] == [14, 10, 36, 28]


def test_select_shrink(gimp, grp, fx):
    img = fx["id"]
    setup = grp._select_rect(gimp, 16, 12, 32, 24, "replace", img)
    assert setup["ok"], setup["error"]
    r = grp._select_shrink(gimp, 1, img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is False
    # Shrinking by 1 px contracts every edge: [16,12,32,24] -> [17,13,30,22].
    assert res["bounds"] == [17, 13, 30, 22]


def test_select_feather(gimp, grp, fx):
    img = fx["id"]
    setup = grp._select_rect(gimp, 16, 12, 32, 24, "replace", img)
    assert setup["ok"], setup["error"]
    r = grp._select_feather(gimp, 2.0, img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is False
    b = res["bounds"]
    assert isinstance(b, list) and len(b) == 4
    # Feathering spreads partial selection outward, so the bbox is at least as big
    # as the original rect.
    assert b[2] >= 32 and b[3] >= 24


def test_select_border(gimp, grp, fx):
    img = fx["id"]
    setup = grp._select_rect(gimp, 16, 12, 32, 24, "replace", img)
    assert setup["ok"], setup["error"]
    r = grp._select_border(gimp, 2, img)
    assert r["ok"], r["error"]
    res = r["result"]
    # Border replaces the selection with a band along the rect edge — non-empty.
    assert res["selection_empty"] is False
    b = res["bounds"]
    assert isinstance(b, list) and len(b) == 4


# --- color-based -----------------------------------------------------------
def test_select_by_color(gimp, grp, fx):
    img = fx["id"]
    r = grp._select_by_color(gimp, "red", 0.2, "replace", None, img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is False
    # Only the red rectangle matches -> exactly [16,12,32,24].
    assert res["bounds"] == [16, 12, 32, 24]


def test_fuzzy_select(gimp, grp, fx):
    img = fx["id"]
    # Seed (24,20) is inside the red rectangle; contiguous red -> the whole rect.
    r = grp._fuzzy_select(gimp, 24, 20, 0.2, "replace", None, img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is False
    assert res["bounds"] == [16, 12, 32, 24]


def _sample_threshold(gimp):
    r = gimp.run("_result = {'t': float(Gimp.context_get_sample_threshold())}",
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["t"]


def test_by_color_and_fuzzy_restore_context(gimp, grp, fx):
    """select_by_color / fuzzy_select set the sample threshold inside their own
    push/pop, so they must NOT leak it into the session context (#18 polish)."""
    gimp.run("Gimp.context_set_sample_threshold(0.99)", undo_group=False)
    grp._select_by_color(gimp, "red", 0.2, "replace", None, fx["id"])
    assert abs(_sample_threshold(gimp) - 0.99) < 0.005, "select_by_color leaked threshold"
    grp._fuzzy_select(gimp, 24, 20, 0.33, "replace", None, fx["id"])
    assert abs(_sample_threshold(gimp) - 0.99) < 0.005, "fuzzy_select leaked threshold"


# --- alpha -----------------------------------------------------------------
def test_select_from_alpha(gimp, grp, fx):
    img = fx["id"]
    r = grp._select_from_alpha(gimp, None, "replace", img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is False
    # The layer is transparent except the red rect, so its opaque (alpha) region
    # is exactly [16,12,32,24].
    assert res["bounds"] == [16, 12, 32, 24]


# --- channel save ----------------------------------------------------------
def test_selection_to_channel(gimp, grp, fx):
    img = fx["id"]
    setup = grp._select_rect(gimp, 16, 12, 32, 24, "replace", img)
    assert setup["ok"], setup["error"]
    r = grp._selection_to_channel(gimp, "saved-sel", img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["image"] == img
    assert isinstance(res["channel_id"], int)
    assert res["name"] == "saved-sel"
    # EFFECT: the saved channel really exists in the image and kept its name.
    chk = gimp.run(
        "ch = Gimp.Item.get_by_id(args['c'])\n"
        "_result = {'valid': bool(ch is not None and ch.is_valid()), "
        "'name': (ch.get_name() if ch else None)}",
        args={"c": res["channel_id"]}, undo_group=False).to_dict()
    assert chk["ok"], chk["error"]
    assert chk["result"]["valid"] is True
    assert chk["result"]["name"] == "saved-sel"


# --- from path -------------------------------------------------------------
def test_select_from_path(gimp, grp, fx):
    img = fx["id"]
    # Fixture has a real rectangular path, so this must produce a real selection.
    r = grp._select_from_path(gimp, None, "replace", img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res.get("selection_empty") is False, res
    b = res["bounds"]
    assert isinstance(b, list) and len(b) == 4
    # EFFECT: the live selection is non-empty too.
    assert _live_bounds(gimp, img)["selection_empty"] is False


def test_select_from_path_no_path(gimp, grp, bare_fx):
    img = bare_fx["id"]
    # No path exists and none named -> graceful degrade, NOT an error.
    r = grp._select_from_path(gimp, None, "replace", img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res.get("supported") is False, res
    assert "note" in res


# --- none ------------------------------------------------------------------
def test_select_none(gimp, grp, fx):
    img = fx["id"]
    setup = grp._select_rect(gimp, 16, 12, 32, 24, "replace", img)
    assert setup["ok"], setup["error"]
    assert setup["result"]["selection_empty"] is False
    r = grp._select_none(gimp, img)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["selection_empty"] is True
    assert res["bounds"] is None
    # EFFECT: the live selection is genuinely cleared.
    assert _live_bounds(gimp, img)["selection_empty"] is True


# --- #3 robustness contract: tuple-shape independence ----------------------
def test_selection_bounds_normalizer_dual_arity(gimp):
    """compat's tail-normalizer must read BOTH the current 6-tuple
    (success, non_empty, x1, y1, x2, y2) AND a future 5-tuple
    (non_empty, x1, y1, x2, y2) identically — head-indexing would not. This is the
    ONLY test that distinguishes the centralized fix from a head-indexed regression,
    since on a real 6-tuple both conventions read the same elements. We feed
    compat._normalize_bounds synthetic tuples of each arity (no live selection needed)."""
    code = """
six  = compat._normalize_bounds((True, True, 16, 12, 48, 36))  # success,non_empty,x1,y1,x2,y2
five = compat._normalize_bounds((True, 16, 12, 48, 36))        # non_empty,x1,y1,x2,y2 (future)
empty6 = compat._normalize_bounds((True, False, 0, 0, 0, 0))   # non_empty flag falsy on a 6-tuple
_result = {"six": list(six), "five": list(five), "empty6_flag": empty6[0]}
"""
    r = gimp.run(code, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    # Tail-indexing reads both shapes to the SAME (non_empty, x1, y1, x2, y2).
    assert r["result"]["six"] == [True, 16, 12, 48, 36]
    assert r["result"]["five"] == [True, 16, 12, 48, 36]
    assert r["result"]["empty6_flag"] is False
