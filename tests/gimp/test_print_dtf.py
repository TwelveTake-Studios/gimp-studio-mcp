"""Tier-2 tests for the print_dtf tool group (translated from spike/test_print_dtf.py)."""
import os

import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "print_dtf"


# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes
# on real GIMP 3.0.4 + 3.2.4). Do not invent new GIMP calls.
# Fixture: transparent 300x200 RGBA image @300dpi with an opaque red 120x80
# rectangle centered at x90-210,y60-140 (alpha bbox interior, empty margins).
_FIXTURE_CODE = """
img = Gimp.Image.new(300, 200, Gimp.ImageBaseType.RGB)
img.set_resolution(300.0, 300.0)
layer = Gimp.Layer.new(img, "art", 300, 200, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
layer.fill(Gimp.FillType.TRANSPARENT)
# Select a centered rectangle and fill it opaque red.
img.select_rectangle(Gimp.ChannelOps.REPLACE, 90, 60, 120, 80)
Gimp.context_push()
c = Gegl.Color.new("black")
c.set_rgba(0.85, 0.1, 0.1, 1.0)
Gimp.context_set_foreground(c)
layer.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop()
Gimp.Selection.none(img)
_result = {"id": img.get_id(), "layer_id": layer.get_id()}
"""


# Solid-background fixture for the adaptive knockout: a fully-opaque WxH layer
# filled with `bg`, with a centred `fg` rectangle (the "design"). Used to prove a
# background colour is removed while the design is preserved.
_BG_FIXTURE_CODE = """
w = args["w"]; h = args["h"]
img = Gimp.Image.new(w, h, Gimp.ImageBaseType.RGB)
img.set_resolution(300.0, 300.0)
layer = Gimp.Layer.new(img, "art", w, h, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
Gimp.Selection.none(img)
Gimp.context_push()
Gimp.context_set_foreground(compat.color(tuple(args["bg"])))
layer.edit_fill(Gimp.FillType.FOREGROUND)            # whole layer -> opaque bg
rw = w // 3; rh = h // 3; rx = (w - rw) // 2; ry = (h - rh) // 2
img.select_rectangle(Gimp.ChannelOps.REPLACE, rx, ry, rw, rh)
Gimp.context_set_foreground(compat.color(tuple(args["fg"])))
layer.edit_fill(Gimp.FillType.FOREGROUND)            # centred design
Gimp.context_pop()
Gimp.Selection.none(img)
_result = {"id": img.get_id(), "layer_id": layer.get_id(),
           "cx": w // 2, "cy": h // 2}
"""


# Graded-alpha fixture for clean_for_dtf: 4x1 RGBA with alpha 1.0 / 0.3 / 0.7 / clear.
_GRADED_CODE = """
img = Gimp.Image.new(4,1,Gimp.ImageBaseType.RGB)
d = Gimp.Layer.new(img,"l",4,1,Gimp.ImageType.RGBA_IMAGE,100.0,Gimp.LayerMode.NORMAL)
img.insert_layer(d,None,0); d.fill(Gimp.FillType.TRANSPARENT)
# Graded alpha via direct set_pixel. GIMP 3.2 set_pixel/get_pixel take & return a
# Gegl.Color (not a byte list). The old gegl:opacity-via-DrawableFilter trick was
# dropped: GIMP 3.2 refuses to wrap gegl:opacity in a Gimp.DrawableFilter
# (DrawableFilter.new returns NULL), though the op itself still exists.
def setpx(x,rgba):
    c=Gegl.Color.new("black");c.set_rgba(*rgba);d.set_pixel(x,0,c)
setpx(0,(1,0,0,1.0)); setpx(1,(0,1,0,0.3)); setpx(2,(0,0,1,0.7))
d.update(0,0,4,1)
_result={"id":img.get_id(),"lid":d.get_id()}
"""


# tiny PNGs for gang_sheet (transparent w/ opaque blob) — spike VERBATIM.
_TINY_CODE = """
w = args["w"]; h = args["h"]
img = Gimp.Image.new(w, h, Gimp.ImageBaseType.RGB)
img.set_resolution(150.0, 150.0)
layer = Gimp.Layer.new(img, "t", w, h, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
layer.fill(Gimp.FillType.TRANSPARENT)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 2, 2, w-4, h-4)
Gimp.context_push()
c = Gegl.Color.new(args["col"])
Gimp.context_set_foreground(c)
layer.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.context_pop()
Gimp.Selection.none(img)
dup = img.duplicate()
dup.flatten()
# keep alpha: do not flatten; merge visible instead
dup.delete()
img.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
f = Gio.File.new_for_path(args["path"])
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, f)
img.delete()
_result = {"path": args["path"]}
"""


# --- bridge helpers (spike ctx.run code, kept verbatim) --------------------
def _probe_pixel(gimp, layer_id, x, y):
    """Read RGBA at (x,y) on a specific layer id."""
    code = """
it = Gimp.Item.get_by_id(args["lid"])
_result = {"rgba": list(compat.read_pixel(it, args["x"], args["y"]))}
"""
    return gimp.run(code, args={"lid": layer_id, "x": x, "y": y},
                    undo_group=False).to_dict()


def _layer_order(gimp, iid):
    """Return list of layer ids top-to-bottom for an image."""
    code = """
img = find_image(args["i"])
_result = {"ids": [l.get_id() for l in img.get_layers()],
           "names": [l.get_name() for l in img.get_layers()]}
"""
    return gimp.run(code, args={"i": iid}, undo_group=False).to_dict()


def _alpha_bbox(gimp, iid, lid):
    """Return [x1, y1, x2, y2] of the layer's alpha selection bounds."""
    return gimp.run(
        'img=find_image(args["i"]);d=Gimp.Item.get_by_id(args["l"])\n'
        'img.select_item(Gimp.ChannelOps.REPLACE,d)\n'
        'b=Gimp.Selection.bounds(img);Gimp.Selection.none(img)\n'
        '_result={"bb":list(b)[2:]}',
        args={"i": iid, "l": lid}, undo_group=False).to_dict()["result"]["bb"]


def _alpha_check(gimp, path):
    """Load the PNG on disk and report whether alpha was preserved."""
    code = """
f = Gio.File.new_for_path(args["path"])
img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, f)
d = img.get_layers()[0]
ha = d.has_alpha()
res = {"width": img.get_width(), "height": img.get_height(), "has_alpha": ha}
img.delete()
_result = res
"""
    return gimp.run(code, args={"path": path}, undo_group=False).to_dict()


def _make_tiny(gimp, path, w, h, col):
    r = gimp.run(_TINY_CODE, args={"path": path, "w": w, "h": h, "col": col},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return path


def _mk_bg(gimp, w, h, bg, fg):
    r = gimp.run(_BG_FIXTURE_CODE,
                 args={"w": w, "h": h, "bg": list(bg), "fg": list(fg)},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]


def _del_img(gimp, iid):
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": iid}, undo_group=False)


# --- fixtures --------------------------------------------------------------
@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"id": .., "layer_id": ..}
    # cleanup so the long-lived session doesn't leak images:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["id"]}, undo_group=False)


# --- tests (one per tool) --------------------------------------------------
def test_print_geometry(gimp, grp, fx):
    r = grp._print_geometry(gimp, image=fx["id"], dpi=300)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["image"] == fx["id"]
    assert res["width_px"] == 300 and res["height_px"] == 200
    assert res["dpi"] == [300.0, 300.0]
    assert res["changed"] is True
    # 300px @ 300dpi == 1.0in wide; 200px @ 300dpi == 0.6667in tall.
    assert res["width_in"] == 1.0
    assert abs(res["height_in"] - 0.6667) < 1e-3
    assert res["warnings"] == []          # no upscale (no width_in/height_in scale)


def test_trim_to_content(gimp, grp, fx):
    # Art bbox = x90-210,y60-140; with padding 5 -> x85,y55,w130,h90.
    r = grp._trim_to_content(gimp, image=fx["id"], padding=5)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["trimmed"] is True
    assert res["x"] == 85 and res["y"] == 55
    assert res["width"] == 130 and res["height"] == 90
    # effect: the canvas was actually cropped to the trimmed size.
    meta = gimp.run('img=find_image(args["i"])\n'
                    '_result={"w":img.get_width(),"h":img.get_height()}',
                    args={"i": fx["id"]}, undo_group=False).to_dict()
    assert meta["ok"], meta["error"]
    assert meta["result"]["w"] == 130 and meta["result"]["h"] == 90


def test_white_underbase(gimp, grp, fx):
    r = grp._white_underbase(gimp, image=fx["id"], choke=2)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["created"] is True
    assert isinstance(res["layer"], int)
    assert res["choke"] == 2
    assert res["opacity"] == 100.0
    assert res["name"] == "white_underbase"
    ub_id = res["layer"]
    # underbase must be WHITE + opaque under the art center (150,100).
    pp = _probe_pixel(gimp, ub_id, 150, 100)
    assert pp["ok"], pp["error"]
    rr, gg, bb, aa = pp["result"]["rgba"]
    assert aa > 200 and rr > 200 and gg > 200 and bb > 200, pp["result"]
    # and it must sit BELOW the art in stacking order.
    lo = _layer_order(gimp, fx["id"])
    assert lo["ok"], lo["error"]
    ids = lo["result"]["ids"]
    assert ids.index(ub_id) > ids.index(fx["layer_id"]), lo["result"]


def test_edge_choke(gimp, grp, fx):
    r = grp._edge_choke(gimp, image=fx["id"], pixels=2)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["applied"] is True
    assert res["mode"] == "choke"
    assert res["pixels"] == 2


def test_edge_spread(gimp, grp, fx):
    # spread must actually GROW the art's alpha bbox outward.
    bb_before = _alpha_bbox(gimp, fx["id"], fx["layer_id"])
    r = grp._edge_spread(gimp, image=fx["id"], pixels=3)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["applied"] is True
    assert res["mode"] == "spread"
    assert res["pixels"] == 3
    bb_after = _alpha_bbox(gimp, fx["id"], fx["layer_id"])
    assert bb_after[0] < bb_before[0] and bb_after[2] > bb_before[2], \
        "edge_spread did not grow the art: %s -> %s" % (bb_before, bb_after)


def test_knockout_auto_black_bg(gimp, grp):
    # Black shirt (bg) + red design. Zero-arg auto-detect -> subtract: corners go
    # transparent, the red design survives (the black-shirt case).
    f = _mk_bg(gimp, 120, 90, (20, 20, 20), (210, 30, 30))
    try:
        r = grp._knockout_background(gimp, image=f["id"])   # one-click, no args
        assert r["ok"], r["error"]
        res = r["result"]
        assert res["knocked_out"] is True
        assert res["mode"] == "subtract", res          # dark bg -> color-to-alpha
        assert res["auto_detected"] is True
        assert res["layer"] == f["layer_id"] and res["image"] == f["id"]
        corner = _probe_pixel(gimp, f["layer_id"], 1, 1)["result"]["rgba"]
        assert corner[3] < 40, ("black bg not knocked out", corner)
        cen = _probe_pixel(gimp, f["layer_id"], f["cx"], f["cy"])["result"]["rgba"]
        assert cen[3] > 150 and cen[0] > cen[1] and cen[0] > cen[2], \
            ("red design not preserved", cen)
    finally:
        _del_img(gimp, f["id"])


def test_knockout_auto_red_bg_uses_hard_not_subtract(gimp, grp):
    # M1 regression: a saturated mid-tone bg (red #b21f35, luma ~0.25) must NOT
    # route to subtract (a pure-luma rule would, eroding reds from the artwork).
    # Auto snaps the detected colour to the nearest garment preset (`red` -> hard),
    # so a tolerant colour match clears the red bg while a non-red design survives.
    f = _mk_bg(gimp, 120, 90, (178, 31, 53), (30, 60, 210))   # red shirt + blue design
    try:
        r = grp._knockout_background(gimp, image=f["id"])      # zero-arg auto-detect
        assert r["ok"], r["error"]
        res = r["result"]
        assert res["mode"] == "hard", res                      # NOT subtract (the M1 bug)
        assert res["mode_basis"] == "preset:red", res          # snapped to curated preset
        assert res["auto_detected"] is True
        corner = _probe_pixel(gimp, f["layer_id"], 1, 1)["result"]["rgba"]
        assert corner[3] < 40, ("red bg not knocked out", corner)
        cen = _probe_pixel(gimp, f["layer_id"], f["cx"], f["cy"])["result"]["rgba"]
        assert cen[3] > 150 and cen[2] > cen[0], ("blue design not preserved", cen)
    finally:
        _del_img(gimp, f["id"])


def test_knockout_contiguous_preserves_interior(gimp, grp):
    # White bg, blue centre block, WHITE spot inside the blue. contiguous=True must
    # clear only the edge-connected outer white, preserving the interior white.
    code = """
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
_result = {"id": img.get_id(), "lid": layer.get_id()}
"""
    f = gimp.run(code, undo_group=False).to_dict()
    assert f["ok"], f["error"]
    iid, lid = f["result"]["id"], f["result"]["lid"]
    try:
        r = grp._knockout_background(gimp, image=iid, shirt="white",
                                     contiguous=True, defringe=False)
        assert r["ok"], r["error"]
        assert r["result"]["mode"] == "hard" and r["result"]["contiguous"] is True
        corner = _probe_pixel(gimp, lid, 1, 1)["result"]["rgba"]
        assert corner[3] < 40, ("outer white not cleared", corner)
        inner = _probe_pixel(gimp, lid, 50, 40)["result"]["rgba"]
        assert inner[3] > 200 and min(inner[:3]) > 200, \
            ("interior white not preserved by contiguous", inner)
    finally:
        _del_img(gimp, iid)


def test_knockout_feather_soft_edge(gimp, grp):
    # feather must survive `clean` (the binarize used to cancel it): a feathered
    # hard knockout should leave a soft (partial-alpha) band at the design edge.
    f = _mk_bg(gimp, 120, 90, (255, 255, 255), (30, 60, 210))
    try:
        r = grp._knockout_background(gimp, image=f["id"], shirt="white",
                                     mode="hard", feather=3.0)
        assert r["ok"], r["error"]
        partial = False
        for x in range(120):
            a = _probe_pixel(gimp, f["layer_id"], x, 45)["result"]["rgba"][3]
            if 13 < a < 242:
                partial = True
                break
        assert partial, "feather produced no soft (partial-alpha) edge"
    finally:
        _del_img(gimp, f["id"])


def test_knockout_transparent_noop(gimp, grp):
    # Fully transparent image -> nothing at the edges -> graceful no-op.
    code = """
img = Gimp.Image.new(40, 30, Gimp.ImageBaseType.RGB)
l = Gimp.Layer.new(img, "a", 40, 30, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l, None, 0); l.fill(Gimp.FillType.TRANSPARENT)
_result = {"id": img.get_id()}
"""
    f = gimp.run(code, undo_group=False).to_dict()
    assert f["ok"], f["error"]
    iid = f["result"]["id"]
    try:
        r = grp._knockout_background(gimp, image=iid)
        assert r["ok"], r["error"]
        assert r["result"]["knocked_out"] is False and "reason" in r["result"]
    finally:
        _del_img(gimp, iid)


def test_knockout_shirt_preset_white_hard(gimp, grp):
    # White shirt (bg) + blue design. shirt='white' preset -> hard select+clear:
    # white removed, blue design preserved.
    f = _mk_bg(gimp, 120, 90, (255, 255, 255), (30, 60, 210))
    try:
        r = grp._knockout_background(gimp, image=f["id"], shirt="white")
        assert r["ok"], r["error"]
        res = r["result"]
        assert res["mode"] == "hard", res
        assert res["color_hex"] == "#ffffff", res
        assert res["tolerance"] == 0.10, res          # white preset's curated tolerance
        corner = _probe_pixel(gimp, f["layer_id"], 1, 1)["result"]["rgba"]
        assert corner[3] < 40, ("white bg not cleared", corner)
        cen = _probe_pixel(gimp, f["layer_id"], f["cx"], f["cy"])["result"]["rgba"]
        assert cen[3] > 200 and cen[2] > 150, ("blue design not preserved", cen)
    finally:
        _del_img(gimp, f["id"])


def test_knockout_auto_light_bg_uses_hard(gimp, grp):
    # Auto on a light/near-white bg snaps to the `white` preset -> hard (the effective
    # light-bg outcome), clearing the bg and preserving the design.
    f = _mk_bg(gimp, 120, 90, (245, 245, 245), (30, 60, 210))
    try:
        r = grp._knockout_background(gimp, image=f["id"])      # zero-arg auto-detect
        assert r["ok"], r["error"]
        res = r["result"]
        assert res["mode"] == "hard", res
        assert res["mode_basis"] == "preset:white", res
        assert res["requested_mode"] == "auto" and res["auto_detected"] is True
        corner = _probe_pixel(gimp, f["layer_id"], 1, 1)["result"]["rgba"]
        assert corner[3] < 40, ("light bg not knocked out", corner)
        cen = _probe_pixel(gimp, f["layer_id"], f["cx"], f["cy"])["result"]["rgba"]
        assert cen[3] > 150 and cen[2] > cen[0], ("blue design not preserved", cen)
    finally:
        _del_img(gimp, f["id"])


def test_knockout_sample_xy_eyedropper(gimp, grp):
    # sample_xy picks the knockout colour from a pixel (a corner of the solid bg);
    # auto_detected is False and the sampled colour drives the removal.
    f = _mk_bg(gimp, 120, 90, (255, 255, 255), (30, 60, 210))
    try:
        r = grp._knockout_background(gimp, image=f["id"], sample_xy=[1, 1])
        assert r["ok"], r["error"]
        res = r["result"]
        assert res["auto_detected"] is False, res
        assert res["color_hex"] == "#ffffff", res              # sampled the white corner
        corner = _probe_pixel(gimp, f["layer_id"], 1, 1)["result"]["rgba"]
        assert corner[3] < 40, ("sampled bg not knocked out", corner)
        cen = _probe_pixel(gimp, f["layer_id"], f["cx"], f["cy"])["result"]["rgba"]
        assert cen[3] > 150 and cen[2] > cen[0], ("blue design not preserved", cen)
    finally:
        _del_img(gimp, f["id"])


def test_knockout_defringe_trims_one_px(gimp, grp):
    # defringe (default on) erodes a 1px ring off the kept art; defringe=False keeps
    # it. _BG_FIXTURE design rect for 120x90: rw=40, rx=(120-40)//2=40 -> the design's
    # left-edge column is x=40; probe it with defringe on vs off.
    rx, ey = 40, 45
    a = _mk_bg(gimp, 120, 90, (255, 255, 255), (30, 60, 210))
    b = _mk_bg(gimp, 120, 90, (255, 255, 255), (30, 60, 210))
    try:
        ra = grp._knockout_background(gimp, image=a["id"], shirt="white")
        rb = grp._knockout_background(gimp, image=b["id"], shirt="white", defringe=False)
        assert ra["ok"] and rb["ok"], (ra["error"], rb["error"])
        edge_on = _probe_pixel(gimp, a["layer_id"], rx, ey)["result"]["rgba"]
        edge_off = _probe_pixel(gimp, b["layer_id"], rx, ey)["result"]["rgba"]
        assert edge_off[3] > 150, ("design edge missing with defringe off", edge_off)
        assert edge_on[3] < edge_off[3], \
            ("defringe did not trim the edge", edge_on, edge_off)
    finally:
        _del_img(gimp, a["id"])
        _del_img(gimp, b["id"])


def test_clean_for_dtf(gimp, grp):
    # Own graded-alpha fixture (opaque / 30% / 70% / clear).
    gr = gimp.run(_GRADED_CODE, undo_group=False).to_dict()
    assert gr["ok"], gr["error"]
    giid, glid = gr["result"]["id"], gr["result"]["lid"]
    try:
        r = grp._clean_for_dtf(gimp, image=giid, threshold=0.5)
        assert r["ok"], r["error"]
        res = r["result"]
        assert res["cleaned"] is True
        assert res["threshold"] == 0.5
        assert res["method"] == "curves_explicit_alpha"
        assert res["layer"] == glid
        # alpha below 0.5 -> fully clear; >= 0.5 -> fully opaque.
        alphas = []
        for x in range(4):
            pp = _probe_pixel(gimp, glid, x, 0)
            assert pp["ok"], pp["error"]
            alphas.append(pp["result"]["rgba"][3])
        assert alphas == [255, 0, 255, 0], \
            "clean_for_dtf alpha step wrong: " + str(alphas)
    finally:
        gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
                 args={"i": giid}, undo_group=False)


def test_despill(gimp, grp, fx):
    # Point it at the red shape so it has something to act on.
    before = _probe_pixel(gimp, fx["layer_id"], 150, 100)["result"]["rgba"]
    r = grp._despill(gimp, image=fx["id"], color=(217, 26, 26), amount=0.8)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["despilled"] is True
    assert list(res["color"]) == [217, 26, 26]
    assert res["amount"] == 0.8
    assert "method" in res
    # When the GEGL path is taken (not a noop), the center pixel must change.
    if "noop" not in res.get("method", ""):
        after = _probe_pixel(gimp, fx["layer_id"], 150, 100)["result"]["rgba"]
        assert after != before, "despill made no change: %s == %s" % (before, after)


def test_halftone_separation(gimp, grp, fx):
    r = grp._halftone_separation(gimp, image=fx["id"], cell_size=8.0, angle=45.0)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["applied"] is True
    assert res["cell_size"] == 8.0
    assert res["angle"] == 45.0
    assert res["layer"] == fx["layer_id"]
    assert "warning" in res and "PREVIEW" in res["warning"]


def test_bleed_and_safe(gimp, grp, fx):
    # 300x200 @300dpi, default bleed/safe 0.125in -> 38px each side.
    r = grp._bleed_and_safe(gimp, image=fx["id"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["bleed_px"] == 38
    assert res["dpi"] == 300.0
    assert res["canvas_px"] == [376, 276]
    assert res["safe_rect"] == {"x": 76, "y": 76, "width": 224, "height": 124}
    # effect: canvas actually grew on disk.
    meta = gimp.run('img=find_image(args["i"])\n'
                    '_result={"w":img.get_width(),"h":img.get_height()}',
                    args={"i": fx["id"]}, undo_group=False).to_dict()
    assert meta["ok"], meta["error"]
    assert meta["result"]["w"] == 376 and meta["result"]["h"] == 276


def test_export_dtf_png(gimp, grp, fx, tmp_path):
    out_png = str(tmp_path / "gimp-mcp-dtf-export.png")
    r = grp._export_dtf_png(gimp, out_png, image=fx["id"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["saved"] == out_png
    assert res["alpha_preserved"] is True
    assert res["image"] == fx["id"]
    assert os.path.exists(out_png) and os.path.getsize(out_png) > 0
    # alpha must survive the round-trip to disk.
    ac = _alpha_check(gimp, out_png)
    assert ac["ok"], ac["error"]
    assert ac["result"]["has_alpha"] is True
    assert ac["result"]["width"] == 300 and ac["result"]["height"] == 200


def test_gang_sheet(gimp, grp, tmp_path):
    tinies = [str(tmp_path / ("gimp-mcp-dtf-tiny%d.png" % i)) for i in range(3)]
    _make_tiny(gimp, tinies[0], 80, 60, "blue")
    _make_tiny(gimp, tinies[1], 60, 90, "green")
    _make_tiny(gimp, tinies[2], 100, 50, "yellow")
    gang_out = str(tmp_path / "gimp-mcp-dtf-gang.png")
    r = grp._gang_sheet(gimp, tinies, gang_out, sheet_width_in=4.0,
                        dpi=150.0, gutter_in=0.0625, rotate=True)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["placed"] >= 2          # spike's proven floor (all 3 fit at 600px wide)
    assert res["skipped"] == []
    assert res["out_path"] == gang_out
    assert res["dpi"] == 150.0
    assert isinstance(res["image"], int)   # the built sheet image, referenceable by the caller
    # 4.0in * 150dpi == 600px sheet width.
    assert isinstance(res["sheet_px"], list) and len(res["sheet_px"]) == 2
    assert res["sheet_px"][0] == 600 and res["sheet_px"][1] > 0
    assert os.path.exists(gang_out) and os.path.getsize(gang_out) > 0
