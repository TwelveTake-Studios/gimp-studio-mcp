import pytest

pytestmark = pytest.mark.gimp

_HEX_IMAGE = """
w = args["w"]; h = args["h"]
img = Gimp.Image.new(w, h, Gimp.ImageBaseType.RGB)
img.set_resolution(300.0, 300.0)
layer = Gimp.Layer.new(img, "art", w, h, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
Gimp.Selection.none(img)
Gimp.context_push()
Gimp.context_set_foreground(Gegl.Color.new(args["bg"]))
layer.edit_fill(Gimp.FillType.FOREGROUND)
if args.get("fg"):
    img.select_rectangle(Gimp.ChannelOps.REPLACE, w // 3, h // 3, w // 3, h // 3)
    Gimp.context_set_foreground(Gegl.Color.new(args["fg"]))
    layer.edit_fill(Gimp.FillType.FOREGROUND)
    Gimp.Selection.none(img)
Gimp.context_pop()
_result = {"id": img.get_id(), "layer": layer.get_id(), "cx": w // 2, "cy": h // 2}
"""

_FILL_WITH_FOREGROUND = """
img = Gimp.Image.get_by_id(args["i"])
img.get_layers()[0].edit_fill(Gimp.FillType.FOREGROUND)
_result = True
"""


def _hex_image(gimp, bg, fg=None, w=120, h=90):
    r = gimp.run(_HEX_IMAGE, args={"w": w, "h": h, "bg": bg, "fg": fg},
                 undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]


def _delete(gimp, iid):
    gimp.run("img = Gimp.Image.get_by_id(args['i'])\nimg.delete() if img else None",
             args={"i": iid}, undo_group=False)


def _image_count(gimp):
    r = gimp.run("_result = len(Gimp.get_images())", undo_group=False).to_dict()
    assert r["ok"], r["error"]
    return r["result"]


def _file_rgba(path, x, y):
    from PIL import Image
    return list(Image.open(path).convert("RGBA").getpixel((x, y)))


def test_color_at_reports_srgb(gimp, load_group):
    f = _hex_image(gimp, "#c62828")
    try:
        r = load_group("analysis")._color_at(gimp, 5, 5, image=f["id"])
        assert r["ok"], r["error"]
        assert r["result"]["rgba"] == [198, 40, 40, 255]
    finally:
        _delete(gimp, f["id"])


def test_read_region_reports_srgb(gimp, load_group):
    f = _hex_image(gimp, "#1f7a3d")
    try:
        r = load_group("analysis")._read_region(gimp, 0, 0, 2, 2, image=f["id"])
        assert r["ok"], r["error"]
        assert r["result"]["pixels"][1][1] == [31, 122, 61, 255]
    finally:
        _delete(gimp, f["id"])


def test_set_fg_tuple_paints_the_srgb_color(gimp, load_group, tmp_path):
    f = _hex_image(gimp, "#ffffff", w=16, h=16)
    out = tmp_path / "fg.png"
    try:
        r = load_group("paint")._set_fg(gimp, [198, 40, 40])
        assert r["ok"], r["error"]
        assert r["result"]["foreground"] == [198, 40, 40, 255]
        fill = gimp.run(_FILL_WITH_FOREGROUND, args={"i": f["id"]}, undo_group=False).to_dict()
        assert fill["ok"], fill["error"]
        e = load_group("document")._export_image(gimp, str(out), image=f["id"])
        assert e["ok"], e["error"]
        assert _file_rgba(out, 8, 8) == [198, 40, 40, 255]
    finally:
        _delete(gimp, f["id"])


def test_set_fg_hex_reports_srgb(gimp, load_group):
    r = load_group("paint")._set_fg(gimp, "#b21f35")
    assert r["ok"], r["error"]
    assert r["result"]["foreground"] == [178, 31, 53, 255]


@pytest.mark.parametrize("shirt_hex, preset, mode", [
    ("#b21f35", "red", "hard"),
    ("#1f7a3d", "kelly", "hard"),
    ("#1e3a8a", "royal", "subtract"),
    ("#b3b6b8", "heather_gray", "hard"),
])
def test_knockout_auto_snaps_a_real_shirt_color_to_its_preset(gimp, load_group,
                                                             shirt_hex, preset, mode):
    f = _hex_image(gimp, shirt_hex, fg="#ffe000")
    try:
        r = load_group("print_dtf")._knockout_background(gimp, image=f["id"])
        assert r["ok"], r["error"]
        res = r["result"]
        assert res["mode_basis"] == "preset:" + preset, res
        assert res["mode"] == mode, res
        assert res["color_hex"] == shirt_hex, res
        corner = load_group("analysis")._color_at(gimp, 1, 1, image=f["id"])["result"]["rgba"]
        assert corner[3] < 40, corner
        art = load_group("analysis")._color_at(gimp, f["cx"], f["cy"], image=f["id"])
        assert art["result"]["rgba"][3] > 150, art
    finally:
        _delete(gimp, f["id"])


def _assert_missing_folder_failure(r, target):
    assert r["ok"] is False, r
    assert "does not exist" in str(r.get("error")), r.get("error")
    assert not target.exists()


def test_export_image_to_a_missing_folder_fails(gimp, load_group, tmp_path):
    f = _hex_image(gimp, "#ffffff", w=16, h=16)
    target = tmp_path / "missing" / "out.png"
    try:
        before = _image_count(gimp)
        r = load_group("document")._export_image(gimp, str(target), image=f["id"])
        _assert_missing_folder_failure(r, target)
        assert _image_count(gimp) == before
    finally:
        _delete(gimp, f["id"])


def test_export_dtf_png_to_a_missing_folder_fails(gimp, load_group, tmp_path):
    f = _hex_image(gimp, "#ffffff", w=16, h=16)
    target = tmp_path / "missing" / "out.png"
    try:
        before = _image_count(gimp)
        r = load_group("print_dtf")._export_dtf_png(gimp, str(target), image=f["id"])
        _assert_missing_folder_failure(r, target)
        assert _image_count(gimp) == before
    finally:
        _delete(gimp, f["id"])


def test_gang_sheet_to_a_missing_folder_fails(gimp, load_group, tmp_path):
    f = _hex_image(gimp, "#c62828", w=40, h=40)
    art = tmp_path / "art.png"
    target = tmp_path / "missing" / "sheet.png"
    try:
        e = load_group("document")._export_image(gimp, str(art), image=f["id"])
        assert e["ok"], e["error"]
        before = _image_count(gimp)
        r = load_group("print_dtf")._gang_sheet(gimp, [str(art)], str(target),
                                                sheet_width_in=2.0)
        _assert_missing_folder_failure(r, target)
        assert _image_count(gimp) == before
    finally:
        _delete(gimp, f["id"])


def test_get_bitmap_save_to_a_missing_folder_fails(gimp, load_group, tmp_path):
    f = _hex_image(gimp, "#ffffff", w=16, h=16)
    target = tmp_path / "missing" / "preview.png"
    try:
        before = _image_count(gimp)
        r = load_group("analysis")._get_bitmap(gimp, image=f["id"], save_to=str(target))
        _assert_missing_folder_failure(r, target)
        assert _image_count(gimp) == before
    finally:
        _delete(gimp, f["id"])
