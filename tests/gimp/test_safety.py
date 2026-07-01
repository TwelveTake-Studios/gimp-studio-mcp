"""Tier-2 tests for the safety tool group (translated from spike/test_safety.py)."""
import pytest

pytestmark = pytest.mark.gimp           # whole module needs --run-gimp

GROUP = "safety"

# Reuse the PROVEN fixture-builder code from the spike VERBATIM (it already passes
# on real GIMP 3.0.4 + 3.2.4): a 64x48 RGB image with two layers, "Top" over
# "Background". Do not invent new GIMP calls.
_FIXTURE_CODE = """
img = Gimp.Image.new(64, 48, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(img, "Background", 64, 48, Gimp.ImageType.RGBA_IMAGE,
                    100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(bg, None, 0)
bg.fill(Gimp.FillType.WHITE)
top = Gimp.Layer.new(img, "Top", 64, 48, Gimp.ImageType.RGBA_IMAGE,
                     100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(top, None, 0)
top.fill(Gimp.FillType.TRANSPARENT)
_result = {"image": img.get_id(),
           "layers": [l.get_name() for l in img.get_layers()]}
"""

# --- proven helper snippets (verbatim from the spike) ----------------------
# list current layer names + ids of an image
_LAYERS_CODE = """
img = find_image(args.get("image"))
_result = {"layers": [l.get_name() for l in img.get_layers()],
           "ids": [l.get_id() for l in img.get_layers()]}
"""

# add a throwaway layer
_ADD_LAYER_CODE = """
img = find_image(args.get("image"))
l = Gimp.Layer.new(img, args["name"], img.get_width(), img.get_height(),
                   Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(l, None, 0)
l.fill(Gimp.FillType.TRANSPARENT)
_result = {"layers": [x.get_name() for x in img.get_layers()]}
"""

# read the persistent globals to prove they survive across ctx.run calls
_GLOBALS_CODE = """
cps = globals().get("_gimpmcp_checkpoints")
scratch = globals().get("_gimpmcp_scratch")
ctr = globals().get("_gimpmcp_cp_counter")
_result = {
    "have_checkpoints": cps is not None,
    "checkpoint_keys": sorted(cps.keys()) if cps else [],
    "have_scratch": scratch is not None,
    "scratch_count": len(scratch) if scratch else 0,
    "counter": ctr,
}
"""


@pytest.fixture
def grp(load_group):
    return load_group(GROUP)


@pytest.fixture
def fx(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    assert r["result"]["layers"] == ["Top", "Background"], r["result"]
    yield r["result"]                   # {"image":.., "layers": ["Top","Background"]}
    # cleanup so the long-lived session doesn't leak the fixture image:
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": r["result"]["image"]}, undo_group=False)


def test_checkpoint(gimp, grp, fx):
    """checkpoint() duplicates the image and registers it in the persistent maps."""
    r = grp._checkpoint(gimp, fx["image"], label="before risky")
    assert r["ok"], r["error"]
    res = r["result"]
    cid = res["checkpoint_id"]
    assert isinstance(cid, int)
    assert res["image"] == fx["image"]
    assert res["label"] == "before risky"
    assert isinstance(res["snapshot_image_id"], int)
    # the snapshot is a real, independent duplicate (distinct image id)
    assert res["snapshot_image_id"] != fx["image"]

    # verify the EFFECT: the persistent _gimpmcp_* globals survived this call
    g = gimp.run(_GLOBALS_CODE, undo_group=False).to_dict()
    assert g["ok"], g["error"]
    gr = g["result"]
    assert gr["have_checkpoints"], gr
    assert cid in gr["checkpoint_keys"], gr
    assert gr["have_scratch"] and gr["scratch_count"] >= 1, gr
    # cid is the counter value handed out for this checkpoint; nothing else
    # incremented it between the checkpoint and this read.
    assert gr["counter"] == cid, gr


def test_list_checkpoints(gimp, grp, fx):
    """list_checkpoints() reports the just-made checkpoint as a valid snapshot."""
    cp = grp._checkpoint(gimp, fx["image"], label="cp")
    assert cp["ok"], cp["error"]
    cid = cp["result"]["checkpoint_id"]

    r = grp._list_checkpoints(gimp)
    assert r["ok"], r["error"]
    cps = r["result"]["checkpoints"]
    assert isinstance(cps, list)
    match = [c for c in cps if c["id"] == cid]
    assert match, cps
    entry = match[0]
    assert entry["valid"] is True
    assert isinstance(entry["image_id"], int)
    assert entry["image_id"] == cp["result"]["snapshot_image_id"]


def test_restore(gimp, grp, fx):
    """checkpoint -> mutate (add a layer) -> restore rebuilds the original stack.

    Ordered/stateful: kept in one test fn, preserving the spike's exact sequence
    and helper ctx.run code.
    """
    img_id = fx["image"]

    cp = grp._checkpoint(gimp, img_id, label="before risky")
    assert cp["ok"], cp["error"]
    cid = cp["result"]["checkpoint_id"]

    # MUTATE: add a layer so the layer set differs from the snapshot.
    m = gimp.run(_ADD_LAYER_CODE, args={"image": img_id, "name": "Scratch"},
                 undo_group=False).to_dict()
    assert m["ok"], m["error"]
    before = gimp.run(_LAYERS_CODE, args={"image": img_id},
                      undo_group=False).to_dict()
    assert before["ok"], before["error"]
    assert "Scratch" in before["result"]["layers"], before["result"]

    # RESTORE back to the checkpoint -> layer set should match the snapshot.
    r = grp._restore(gimp, cid, img_id)
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["restored"] is True
    assert res["checkpoint_id"] == cid
    assert res["image"] == img_id
    assert res["layers_restored"] == 2

    after = gimp.run(_LAYERS_CODE, args={"image": img_id},
                     undo_group=False).to_dict()
    assert after["ok"], after["error"]
    assert after["result"]["layers"] == ["Top", "Background"], after["result"]


def test_undo_group_begin_end(gimp, grp, fx):
    """undo_group_begin/_end wrap a couple ops into one undo step.

    The begin/end pair is inherently stateful, so it stays in one test fn,
    preserving the spike's exact ordering (two trivial ops inside the group).
    """
    img_id = fx["image"]

    b = grp._undo_group_begin(gimp, img_id)
    assert b["ok"], b["error"]
    assert b["result"]["image"] == img_id
    assert b["result"]["undo_group"] == "begin"

    # do a couple of trivial ops inside the group
    gimp.run(_ADD_LAYER_CODE, args={"image": img_id, "name": "Grouped1"},
             undo_group=False).to_dict()
    gimp.run(_ADD_LAYER_CODE, args={"image": img_id, "name": "Grouped2"},
             undo_group=False).to_dict()

    e = grp._undo_group_end(gimp, img_id)
    assert e["ok"], e["error"]
    assert e["result"]["image"] == img_id
    assert e["result"]["undo_group"] == "end"


def test_undo(gimp, grp, fx):
    """undo() never raises; it honestly reports scriptable-undo support."""
    r = grp._undo(gimp, fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["op"] == "undo"
    assert res["image"] == fx["image"]
    assert isinstance(res["supported"], bool)


def test_redo(gimp, grp, fx):
    """redo() never raises; it honestly reports scriptable-redo support."""
    r = grp._redo(gimp, fx["image"])
    assert r["ok"], r["error"]
    res = r["result"]
    assert res["op"] == "redo"
    assert res["image"] == fx["image"]
    assert isinstance(res["supported"], bool)


def _new_image(gimp):
    r = gimp.run(_FIXTURE_CODE, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["image"]


def _layers(gimp, image):
    r = gimp.run(_LAYERS_CODE, args={"image": image}, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]["layers"]


def _delete(gimp, *image_ids):
    for i in image_ids:
        gimp.run("im = Gimp.Image.get_by_id(args['i'])\nim.delete() if im else None",
                 args={"i": i}, undo_group=False)


def test_restore_defaults_to_origin_not_active(gimp, grp):
    """Regression (data-loss): restore() with NO image must rebuild the ORIGIN
    image the checkpoint was taken from — never whatever image is currently
    'active'. Checkpoint A, then create B (now active), then restore(cid): A is
    restored and B is left completely untouched."""
    a_id = _new_image(gimp)
    b_id = _new_image(gimp)  # created after A -> the active image
    try:
        cp = grp._checkpoint(gimp, a_id, label="A")
        assert cp["ok"], cp["error"]
        cid = cp["result"]["checkpoint_id"]
        # list_checkpoints records the origin now.
        lc = grp._list_checkpoints(gimp)
        assert lc["ok"], lc["error"]
        entry = next(e for e in lc["result"]["checkpoints"] if e["id"] == cid)
        assert entry["origin_image_id"] == a_id

        # Mutate A so restore has something to undo; snapshot B to prove it's safe.
        assert gimp.run(_ADD_LAYER_CODE, args={"image": a_id, "name": "scratch"},
                        undo_group=False).to_dict()["ok"]
        b_before = _layers(gimp, b_id)

        r = grp._restore(gimp, cid)  # NO image arg
        assert r["ok"], r["error"]
        assert r["result"]["image"] == a_id, "restore must target the origin, not the active image"
        assert r["result"]["warning"] is None
        assert _layers(gimp, a_id) == ["Top", "Background"]      # A rolled back
        assert _layers(gimp, b_id) == b_before, "restore clobbered the unrelated active image B"
    finally:
        _delete(gimp, a_id, b_id)


def test_restore_explicit_mismatch_warns(gimp, grp):
    """An explicit image= that differs from the checkpoint's origin is ALLOWED
    (deliberate cross-image restore) but must be flagged with a warning."""
    a_id = _new_image(gimp)
    b_id = _new_image(gimp)
    try:
        cid = grp._checkpoint(gimp, a_id)["result"]["checkpoint_id"]
        r = grp._restore(gimp, cid, b_id)  # explicit override into a different image
        assert r["ok"], r["error"]
        assert r["result"]["image"] == b_id
        assert r["result"]["warning"] and "taken from image" in r["result"]["warning"]
    finally:
        _delete(gimp, a_id, b_id)
