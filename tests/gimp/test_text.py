"""Tier-2 tests for the text tool group (translated from spike/test_text.py).

TEXT TIER-3 RULE: text PIXELS are NOT goldened — FreeType/HarfBuzz
render drift across builds makes per-pixel compares flaky. Instead we assert text-layer
GEOMETRY/metadata (it is a text layer, width/height > 0, the returned font/text props)
plus a COARSE ink-coverage check (count non-transparent pixels via the ALPHA histogram
> 0). create_text_layer/set_text_props perform a font fallback (GIMP 3.2 dropped GIMP-2
aliases), so we assert the ACTUAL font returned, never that it equals the requested name.
"""
import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "text"

# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes on
# real GIMP 3.0.4 + 3.2.4). Font discovery (spike's _DISCOVER) + the 200x80 RGBA image
# (spike's fixture) are combined into one bridge run so `fx` yields both at once.
_FIXTURE_CODE = '''
# --- discover a real installed font (spike _DISCOVER) ---
fonts = Gimp.fonts_get_list("")
names = []
for f in fonts:
    try:
        names.append(f.get_name())
    except Exception:
        names.append(str(f))
ctx_font = Gimp.context_get_font()
real_font = names[0] if names else (ctx_font.get_name() if ctx_font else None)

# --- 200x80 RGBA image with a transparent bg layer (spike fixture) ---
img = Gimp.Image.new(200, 80, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "bg", 200, 80, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
layer.fill(Gimp.FillType.TRANSPARENT)
_result = {"image": img.get_id(), "bg": layer.get_id(),
           "font": real_font, "font_count": len(names)}
'''

# Read back a layer's structural state without transferring pixels: whether it is a text
# layer, its geometry/offsets, and a COARSE ink count (non-transparent ALPHA pixels). The
# histogram count index + 7->6 normalization mirror tools/analysis.py exactly (proven).
_READBACK = '''
layer = find_drawable(args.get("image"), args.get("layer"))
is_text = isinstance(layer, Gimp.TextLayer)
pred = getattr(layer, "is_text_layer", None)
if callable(pred):
    try:
        is_text = bool(pred()) or is_text
    except Exception:
        pass
off = layer.get_offsets()
ox, oy = (off[-2], off[-1]) if off else (None, None)
# ALPHA histogram over (~1/256 .. 1.0): count = non-transparent pixels (the ink).
res = layer.histogram(Gimp.HistogramChannel.ALPHA, 0.00390625, 1.0)
vals = list(res)
if len(vals) == 7:          # some bindings prepend a success bool
    vals = vals[1:]
ink = vals[4] if len(vals) > 4 else None   # index 4 = count
_result = {"is_text": is_text, "type": type(layer).__name__, "ink": ink,
           "w": layer.get_width(), "h": layer.get_height(), "x": ox, "y": oy}
'''


@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"image":.., "bg":.., "font":.., "font_count":..}
    # cleanup so the long-lived session doesn't leak images:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["image"]}, undo_group=False)


def _readback(gimp, image, layer):
    r = gimp.run(_READBACK, args={"image": image, "layer": layer}, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]


def test_create_text_layer(gimp, grp, fx):
    r = grp._create_text_layer(
        gimp, "Hello World", font=fx["font"], size=24.0, color="red",
        x=10, y=20, justify="center", line_spacing=2.0, letter_spacing=1.0,
        image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["layer"], int)
    # GEOMETRY (not pixels): the laid-out glyph box has real extent.
    assert res["width"] > 0 and res["height"] > 0, res
    # Position offsets were applied.
    assert res["x"] == 10 and res["y"] == 20, res
    # ACTUAL font used (may differ from requested due to the 3.2 fallback).
    assert isinstance(res["font"], str) and res["font"], res
    assert res["name"], res

    # EFFECT readback: it is a real text layer and it has ink (coarse, FreeType-safe).
    rb = _readback(gimp, fx["image"], res["layer"])
    assert rb["is_text"] or rb["type"] == "TextLayer", rb
    assert rb["ink"] and rb["ink"] > 0, "expected non-transparent text pixels"
    assert rb["x"] == 10 and rb["y"] == 20, rb


def test_set_text_props(gimp, grp, fx):
    # Prerequisite: a text layer to mutate (same create the spike runs first).
    c = grp._create_text_layer(
        gimp, "Hello World", font=fx["font"], size=24.0, color="red",
        x=10, y=20, image=fx["image"])
    assert c["ok"], c["error"]
    tid = c["result"]["layer"]

    r = grp._set_text_props(
        gimp, tid, text="Changed", size=18.0, color="blue",
        font=fx["font"], justify="left", line_spacing=1.5, letter_spacing=0.5,
        x=5, y=10, image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["layer"] == tid, res
    # Every mutated prop is reported in `changed`.
    expected = {"text", "size", "color", "justify",
                "line_spacing", "letter_spacing", "position"}
    assert expected <= set(res["changed"]), res["changed"]
    if fx["font"]:                       # font only "changes" if the name resolves
        assert "font" in res["changed"], res["changed"]

    # EFFECT readback: still a text layer, still inked, moved to the new offsets.
    rb = _readback(gimp, fx["image"], tid)
    assert rb["is_text"] or rb["type"] == "TextLayer", rb
    assert rb["ink"] and rb["ink"] > 0, rb
    assert rb["x"] == 5 and rb["y"] == 10, rb


def test_check_fonts(gimp, grp, fx):
    font = fx["font"]
    assert font, "no fonts installed in headless GIMP"
    r = grp._check_fonts(gimp, [font, "ZZZ_No_Such_Font_999"])
    assert r["ok"], r["error"]
    avail = r["result"]["available"]
    assert avail[font] is True, avail
    assert avail["ZZZ_No_Such_Font_999"] is False, avail


def test_substitute_font(gimp, grp, fx):
    font = fx["font"]
    assert font, "no fonts installed in headless GIMP"
    r = grp._substitute_font(gimp, "ZZZ_No_Such_Font_999", font)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["old"] == "ZZZ_No_Such_Font_999", res
    assert res["new"] == font, res
    assert res["old_present"] is False, res
    assert res["new_present"] is True, res
    assert res["substitution_recommended"] is True, res


def test_outline_text(gimp, grp, fx):
    # Prerequisite: a text layer to outline (spike outlines the created layer).
    c = grp._create_text_layer(
        gimp, "Hello World", font=fx["font"], size=24.0, color="red",
        x=10, y=20, image=fx["image"])
    assert c["ok"], c["error"]
    tid = c["result"]["layer"]

    r = grp._outline_text(gimp, tid, radius=3, color="black", image=fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert isinstance(res["layer"], int) and res["layer"] != tid, res
    assert res["radius"] == 3, res
    assert res["source"] == tid, res
    assert "outline" in res["name"], res

    # EFFECT readback: the outline is a full-image layer that actually has ink.
    rb = _readback(gimp, fx["image"], res["layer"])
    assert rb["w"] == 200 and rb["h"] == 80, rb
    assert rb["ink"] and rb["ink"] > 0, "expected a filled (grown) outline region"
