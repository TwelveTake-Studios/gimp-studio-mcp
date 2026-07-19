"""Tier-2 tests for the color_mgmt tool group (translated from spike/test_color_mgmt.py)."""
import os

import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "color_mgmt"

# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes on
# real GIMP 3.0.4 + 3.2.4). Only the final `_result` key is renamed to "image" so it
# matches the `fx` fixture contract below. Do not invent new GIMP calls.
_FIXTURE_CODE = '''
img = Gimp.Image.new(args["w"], args["h"], Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "bg", args["w"], args["h"],
                       Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
layer.fill(Gimp.FillType.WHITE)
_result = {"image": img.get_id(), "base_type": int(img.get_base_type())}
'''

# Dump a DISTINCT, embeddable RGB profile to a real .icc on disk. NB: GIMP ELIDES a
# profile byte-identical to its OWN built-in sRGB (get_color_profile() -> None), so a
# dumped new_rgb_srgb() is invisible on read-back (has_profile stays False). Linear
# sRGB is a real, distinct built-in profile GIMP embeds, so the assign/convert effect
# is observable while staying a genuine on-disk ICC round-trip (not a fake).
_DUMP_SRGB_ICC = '''
import os
prof = Gimp.ColorProfile.new_rgb_srgb_linear()
prof.save_to_file(Gio.File.new_for_path(args["out"]))
size = os.path.getsize(args["out"]) if os.path.exists(args["out"]) else 0
_result = {"wrote": args["out"] if size else None,
           "bytes": size,
           "note": "dumped built-in linear sRGB (distinct from sRGB so GIMP embeds it)",
           "label": prof.get_label()}
'''

# Read the current image base type straight from GIMP (effect verification helper).
_READ_BASE_TYPE = (
    "img = Gimp.Image.get_by_id(args['i'])\n"
    "_result = {'bt': int(img.get_base_type())}"
)


@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, args={"w": 32, "h": 32}, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    yield r["result"]                   # {"image": <id>, "base_type": <int>}
    # cleanup so the long-lived session doesn't leak images:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["image"]}, undo_group=False)


@pytest.fixture
def srgb_icc(gimp, fx, tmp_path):
    """Dump GIMP's built-in sRGB profile to a real .icc on disk (proven spike probe)."""
    out = str(tmp_path / "srgb_dump.icc")
    r = gimp.run(_DUMP_SRGB_ICC, args={"image": fx["image"], "out": out},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    assert r["result"]["bytes"] > 0 and os.path.exists(out), "sRGB .icc dump failed"
    return out


def _base_type_int(gimp, name):
    """int value of Gimp.ImageBaseType.<name> (RGB / GRAY) read live from GIMP."""
    r = gimp.run(f"_result = {{'v': int(Gimp.ImageBaseType.{name})}}",
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["v"]


def test_get_profile(gimp, grp, fx):
    r = grp._get_profile(gimp, fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["image"] == fx["image"]
    # A freshly created Gimp.Image has no embedded ICC tag (implicit sRGB).
    assert res["has_profile"] is False
    assert res["label"] is None
    assert "description" in res


def test_assign_profile(gimp, grp, fx, srgb_icc):
    r = grp._assign_profile(gimp, srgb_icc, fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["image"] == fx["image"]
    assert res["assigned"] is True
    assert res["icc_path"] == srgb_icc
    # A valid ICC may carry no description tag, so label can legitimately be None.
    assert res["label"] is None or isinstance(res["label"], str)
    # Effect: the image now reports an embedded profile via get_profile.
    after = grp._get_profile(gimp, fx["image"])
    assert after["ok"], after["error"]
    assert after["result"]["has_profile"] is True
    assert after["result"]["label"] == res["label"]


def test_convert_profile(gimp, grp, fx, srgb_icc):
    # Proven spike call: intent="relative-colorimetric", bpc=True (positional).
    r = grp._convert_profile(gimp, srgb_icc, "relative-colorimetric", True, fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["image"] == fx["image"]
    assert res["icc_path"] == srgb_icc
    assert isinstance(res["converted"], bool)
    # The impl normalizes the intent name to UPPER/underscore then echoes .lower().
    assert res["intent"] == "relative_colorimetric"
    assert res["bpc"] is True
    # convert_color_profile embeds the target profile; label may be None if the ICC has no desc tag.
    assert res["label"] is None or isinstance(res["label"], str)
    # Effect: the image now carries the converted-into profile.
    after = grp._get_profile(gimp, fx["image"])
    assert after["ok"], after["error"]
    assert after["result"]["has_profile"] is True


def test_soft_proof(gimp, grp, fx, srgb_icc):
    # Soft-proof is a VIEW-only setting in GIMP 3.x → it can NEVER change pixels,
    # so the envelope must say ok=false; ok=true would read as a real proof.
    r = grp._soft_proof(gimp, srgb_icc, "perceptual", fx["image"])
    assert r["ok"] is False
    assert r["error"]["type"] == "UnsupportedOperation"
    assert "convert_profile" in r["error"]["message"]
    res = r["result"]
    assert res["image"] == fx["image"]
    assert res["supported"] is False
    assert isinstance(res["note"], str) and res["note"]
    assert res["requested_intent"] == "perceptual"
    assert res["requested_icc_path"] == srgb_icc


def test_list_profiles(gimp, grp):
    # The libgimp API cannot enumerate installed profiles → never works, so the
    # envelope must say ok=false and point at the path-based alternative.
    r = grp._list_profiles(gimp)
    assert r["ok"] is False
    assert r["error"]["type"] == "UnsupportedOperation"
    assert "assign_profile" in r["error"]["message"]
    res = r["result"]
    assert res["supported"] is False
    assert isinstance(res["note"], str) and res["note"]


def test_to_grayscale(gimp, grp, fx):
    gray_int = _base_type_int(gimp, "GRAY")
    r = grp._to_grayscale(gimp, fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["image"] == fx["image"]
    assert res["base_type"] == gray_int
    assert res["converted"] is True
    assert res["already_grayscale"] is False
    # Effect: read the base type straight back off the image.
    back = gimp.run(_READ_BASE_TYPE, args={"i": fx["image"]}, undo_group=False).to_dict()
    assert back["ok"], back["error"]
    assert back["result"]["bt"] == gray_int


def test_to_rgb(gimp, grp, fx):
    # Stateful round-trip (spike ordering): grayscale FIRST, then to_rgb converts back,
    # which exercises the real conversion path (not the already-RGB no-op).
    rgb_int = _base_type_int(gimp, "RGB")
    g = grp._to_grayscale(gimp, fx["image"])
    assert g["ok"], g["error"]

    r = grp._to_rgb(gimp, fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["image"] == fx["image"]
    assert res["base_type"] == rgb_int
    assert res["converted"] is True
    assert res["already_rgb"] is False
    # Effect: image is RGB again.
    back = gimp.run(_READ_BASE_TYPE, args={"i": fx["image"]}, undo_group=False).to_dict()
    assert back["ok"], back["error"]
    assert back["result"]["bt"] == rgb_int
