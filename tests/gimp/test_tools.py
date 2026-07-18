"""Tier-2 tests for the cross-cutting tool layer + the `document` and `session`
groups (translated from spike/test_tools.py).

`document` exposes module-level _impl fns (tested directly like the other groups).
`session` tools are closures registered inside register(), so they're exercised
via the registration report and the `info` round-trip — same as the spike.
"""
import os

import pytest

pytestmark = pytest.mark.gimp


def test_register_all_reports_every_group(gimp):
    # Registration is tolerant: implemented groups register, stubs would skip.
    from mcp.server.fastmcp import FastMCP
    from gimp_mcp import tools
    from gimp_mcp.server import build_server

    rep = tools.register_all(FastMCP("t"), gimp)
    assert "session" in rep["registered"]
    assert "document" in rep["registered"]
    assert not rep["skipped"], f"all groups should register, skipped={rep['skipped']!r}"
    # build_server must also construct cleanly with the same ctx.
    build_server(gimp)


def test_info_roundtrip(gimp):
    info = gimp.call("info").to_dict()
    assert info["ok"], info["error"]
    assert info["result"]["gimp_version"]
    assert "mode" in info["result"]


_ALPHA_BUILD = """
img = Gimp.Image.new(20, 20, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, "a", 20, 20, Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
layer.fill(Gimp.FillType.TRANSPARENT)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 5, 5, 10, 10)
Gimp.context_push(); Gimp.context_set_foreground(compat.color("red"))
layer.edit_fill(Gimp.FillType.FOREGROUND); Gimp.context_pop()
Gimp.Selection.none(img)
_result = {"image": img.get_id()}
"""

_PROBE_ACTIVE = """
img = find_image(args["image"]); drw = find_drawable(args["image"], None)
_result = {"px": [list(compat.read_pixel(drw, p[0], p[1])) for p in args["points"]]}
"""


def test_export_image_alpha_safe(gimp, load_group, tmp_path):
    # export_image must NOT silently flatten away transparency (the DTF footgun).
    doc = load_group("document")
    r = gimp.run(_ALPHA_BUILD, undo_group=False).to_dict()
    assert r["ok"], r["error"]
    iid = r["result"]["image"]
    ids = [iid]
    try:
        # PNG (auto): alpha preserved; transparent corner stays clear on reopen.
        png = str(tmp_path / "a.png")
        r = doc._export_image(gimp, png, iid)
        assert r["ok"], r["error"]
        assert r["result"]["flattened"] is False and r["result"]["alpha"] is True
        assert r["result"]["warning"] is None, r["result"]
        ro = doc._open_image(gimp, png)
        assert ro["ok"], ro["error"]
        oid = ro["result"]["image"]
        ids.append(oid)
        px = gimp.run(_PROBE_ACTIVE, args={"image": oid, "points": [[0, 0], [10, 10]]},
                      undo_group=False).to_dict()
        assert px["ok"], px["error"]
        corner, center = px["result"]["px"]
        assert corner[3] < 20, ("PNG export dropped alpha — corner not transparent", corner)
        assert center[3] > 200 and center[0] > 150, ("red block missing after export", center)

        # PNG flatten=True: forced opaque; warns that alpha was dropped.
        pf = str(tmp_path / "b.png")
        r = doc._export_image(gimp, pf, iid, flatten=True)
        assert r["ok"], r["error"]
        assert r["result"]["flattened"] is True and r["result"]["alpha"] is False
        assert r["result"]["warning"], r["result"]

        # JPG (auto): format can't hold alpha -> flattened + a helpful warning.
        jpg = str(tmp_path / "c.jpg")
        r = doc._export_image(gimp, jpg, iid)
        assert r["ok"], r["error"]
        assert r["result"]["flattened"] is True and r["result"]["alpha"] is False
        assert r["result"]["warning"] and "png" in r["result"]["warning"].lower()
    finally:
        gimp.run("for i in args['ids']:\n img = Gimp.Image.get_by_id(i)\n"
                 " (img.delete() if img else None)",
                 args={"ids": ids}, undo_group=False)


def test_document_new_metadata_export_open(gimp, load_group, tmp_path):
    doc = load_group("document")
    ids = []
    try:
        # new_image
        r = doc._new_image(gimp, 40, 30, "transparent", "Doc")
        assert r["ok"], r["error"]
        assert r["result"]["width"] == 40 and r["result"]["height"] == 30
        nid = r["result"]["image"]
        assert isinstance(nid, int)
        ids.append(nid)

        # get_metadata reflects the new image
        r = doc._get_metadata(gimp, nid)
        assert r["ok"], r["error"]
        assert r["result"]["width"] == 40
        assert r["result"]["height"] == 30
        assert r["result"]["num_layers"] == 1

        # export_image writes a real PNG to disk
        out = str(tmp_path / "doc_export.png")
        r = doc._export_image(gimp, out, nid)
        assert r["ok"], r["error"]
        assert os.path.exists(out) and os.path.getsize(out) > 0

        # open_image reopens it as a fresh image of the same size
        r = doc._open_image(gimp, out)
        assert r["ok"], r["error"]
        assert r["result"]["width"] == 40
        ids.append(r["result"]["image"])
    finally:
        # delete both images so the long-lived session doesn't leak.
        gimp.run(
            "for i in args['ids']:\n"
            " img = Gimp.Image.get_by_id(i)\n"
            " (img.delete() if img else None)",
            args={"ids": ids}, undo_group=False,
        )
