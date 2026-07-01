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
