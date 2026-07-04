"""Group B — Document / image.

Tools build a fixed code template + pass params as a data `args` payload
(injection-safe) to the bridge's exec op, and read structured `_result`. Impl
functions are module-level so they can be unit-tested against a bridge directly.
"""
from __future__ import annotations

_META_CODE = """
img = find_image(args.get("image"))
f = img.get_file()
layers = img.get_layers()
xres, yres = compat.image_resolution(img)
_result = {
    "image": img.get_id(),
    "name": f.get_basename() if f else None,
    "path": f.get_path() if f else None,
    "width": img.get_width(),
    "height": img.get_height(),
    "base_type": int(img.get_base_type()),
    "precision": int(img.get_precision()),
    "resolution": [xres, yres],
    "num_layers": len(layers),
    "layers": [
        {"id": l.get_id(), "name": l.get_name(),
         "width": l.get_width(), "height": l.get_height(),
         "opacity": l.get_opacity(), "visible": l.get_visible(),
         "has_alpha": l.has_alpha()}
        for l in layers
    ],
    "dirty": bool(img.is_dirty()),
}
"""

_NEW_CODE = """
w = args["width"]; h = args["height"]
img = Gimp.Image.new(w, h, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(img, args["name"], w, h, Gimp.ImageType.RGBA_IMAGE,
                       100.0, Gimp.LayerMode.NORMAL)
img.insert_layer(layer, None, 0)
_fill = {"white": Gimp.FillType.WHITE, "transparent": Gimp.FillType.TRANSPARENT,
         "background": Gimp.FillType.BACKGROUND, "foreground": Gimp.FillType.FOREGROUND}
layer.fill(_fill.get(args["fill"], Gimp.FillType.WHITE))
_result = {"image": img.get_id(), "layer": layer.get_id(), "width": w, "height": h, "name": args["name"]}
"""

_OPEN_CODE = """
f = Gio.File.new_for_path(args["path"])
img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, f)
_result = {"image": img.get_id(), "width": img.get_width(), "height": img.get_height()}
"""

# Export a FLATTENED COPY so the user's open image is never mutated.
_EXPORT_CODE = """
img = find_image(args.get("image"))
dup = img.duplicate()
dup.flatten()
f = Gio.File.new_for_path(args["path"])
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup, f)
dup.delete()
_result = {"saved": args["path"], "image": img.get_id()}
"""


def _get_metadata(ctx, image=None):
    return ctx.run(_META_CODE, args={"image": image}, undo_group=False).to_dict()


def _new_image(ctx, width, height, fill="white", name="Untitled"):
    return ctx.run(_NEW_CODE,
                   args={"width": width, "height": height, "fill": fill, "name": name},
                   undo_group=False).to_dict()


def _open_image(ctx, path):
    return ctx.run(_OPEN_CODE, args={"path": path}, undo_group=False).to_dict()


def _export_image(ctx, path, image=None):
    return ctx.run(_EXPORT_CODE, args={"path": path, "image": image}, undo_group=False).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="get_metadata")
    def get_metadata(image: int | str | None = None) -> dict:
        """Image + layer metadata (size, base type, precision, resolution, layers).
        `image` = id/basename, or omit for the active image."""
        return _get_metadata(ctx, image)

    @mcp.tool(name="new_image")
    def new_image(width: int, height: int, fill: str = "white",
                  name: str = "Untitled") -> dict:
        """Create a new RGBA image. fill: white|transparent|background|foreground.
        Returns the new image id + size."""
        return _new_image(ctx, width, height, fill, name)

    @mcp.tool(name="open_image")
    def open_image(path: str) -> dict:
        """Open an image file (any format GIMP loads). Returns new image id + size."""
        return _open_image(ctx, path)

    @mcp.tool(name="export_image")
    def export_image(path: str, image: int | str | None = None) -> dict:
        """Export a FLATTENED COPY of an image to a file (format by extension:
        png/jpg/tiff/...). The source image is not modified."""
        return _export_image(ctx, path, image)
