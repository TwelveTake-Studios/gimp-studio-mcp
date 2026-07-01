"""Tier-1: get_bitmap envelope -> MCP content conversion (#1).

The vision tool's headline fix is returning a VIEWABLE image (an MCP image content
block) instead of a base64 string buried in a text field the model can't see.
``analysis._bitmap_content`` does that conversion in pure Python (no GIMP), so it is
covered in CI without a headless GIMP.
"""
from __future__ import annotations

import base64

from gimp_mcp.tools import analysis


def test_inline_returns_image_plus_metadata():
    from mcp.server.fastmcp import Image

    raw = b"\x89PNG\r\n\x1a\nhello-bitmap-bytes"
    env = {"ok": True, "result": {
        "inline": True, "fmt": "png", "width": 8, "height": 5, "bytes": len(raw),
        "base64": base64.b64encode(raw).decode("ascii"),
        "source_size": [1125, 750], "max_dim_requested": 1024, "max_dim_used": 8,
        "downscaled_to_fit": False}}
    out = analysis._bitmap_content(env)

    # A viewable image block AND a metadata block — not one big text blob.
    assert isinstance(out, list) and len(out) == 2
    img, meta = out
    assert isinstance(img, Image)
    assert isinstance(meta, dict)
    assert meta["width"] == 8 and meta["height"] == 5 and meta["inline"] is True
    assert "base64" not in meta                      # the bytes ride in the image block
    ic = img.to_image_content()
    assert ic.mimeType == "image/png"
    assert base64.b64decode(ic.data) == raw          # the image carries our exact bytes


def test_save_to_returns_path_not_inline_image():
    env = {"ok": True, "result": {
        "inline": False, "saved_to": "/tmp/x.png", "fmt": "png",
        "width": 1024, "height": 773, "bytes": 123456,
        "source_size": [3480, 2627], "max_dim_requested": 1024,
        "max_dim_used": 1024, "downscaled_to_fit": False}}
    out = analysis._bitmap_content(env)
    assert isinstance(out, dict)                      # no inline image for save_to
    assert out["saved_to"] == "/tmp/x.png"
    assert out["inline"] is False
    assert "base64" not in out


def test_error_envelope_passthrough():
    env = {"ok": False, "result": None,
           "error": {"type": "ValueError", "message": "boom"}}
    out = analysis._bitmap_content(env)
    assert out is env                                 # surfaced unchanged
