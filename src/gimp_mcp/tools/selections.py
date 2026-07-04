"""Group E — Selections.

Tools build a fixed code template + pass params as a data `args` payload
(injection-safe) to the bridge's exec op, and read structured `_result`. Impl
functions are module-level so they can be unit-tested against a bridge directly.

Tools:
  - select_rect, select_ellipse
  - select_by_color, fuzzy_select
  - select_all, select_none, select_invert
  - grow, shrink, feather, border
  - selection_to_channel
  - select_from_path, select_from_alpha
"""
from __future__ import annotations

# Map an op string -> Gimp.ChannelOps; default REPLACE. Shared by geometry/color ops.
_OP_HELPER = """
def _channel_op(name):
    return getattr(Gimp.ChannelOps, (name or "replace").upper().replace("-", "_"),
                   Gimp.ChannelOps.REPLACE)
"""

# Common tail: report the selection bounds (None if nothing selected).
# The GIMP-3 Selection.bounds 6-tuple quirk is owned by compat.selection_bounds()
# (returns (non_empty, x, y, w, h), robust to a future tuple-shape shift) — never
# hand-roll the indexing here again.
_BOUNDS_TAIL = """
_non_empty, _x, _y, _w, _h = compat.selection_bounds(img)
_result = {
    "image": img.get_id(),
    "selection_empty": (not _non_empty),
    "bounds": (None if not _non_empty else [_x, _y, _w, _h]),
}
"""

_RECT_CODE = _OP_HELPER + """
img = find_image(args.get("image"))
op = _channel_op(args.get("op"))
img.select_rectangle(op, args["x"], args["y"], args["w"], args["h"])
""" + _BOUNDS_TAIL

_ELLIPSE_CODE = _OP_HELPER + """
img = find_image(args.get("image"))
op = _channel_op(args.get("op"))
img.select_ellipse(op, args["x"], args["y"], args["w"], args["h"])
""" + _BOUNDS_TAIL

_BY_COLOR_CODE = _OP_HELPER + """
img = find_image(args.get("image"))
drw = find_drawable(args.get("image"), args.get("layer"))
op = _channel_op(args.get("op"))
Gimp.context_push()
try:
    Gimp.context_set_sample_threshold(float(args["threshold"]))
    img.select_color(op, drw, compat.color(args["color"]))
finally:
    Gimp.context_pop()
""" + _BOUNDS_TAIL

_FUZZY_CODE = _OP_HELPER + """
img = find_image(args.get("image"))
drw = find_drawable(args.get("image"), args.get("layer"))
op = _channel_op(args.get("op"))
Gimp.context_push()
try:
    Gimp.context_set_sample_threshold(float(args["threshold"]))
    img.select_contiguous_color(op, drw, float(args["x"]), float(args["y"]))
finally:
    Gimp.context_pop()
""" + _BOUNDS_TAIL

_ALL_CODE = """
img = find_image(args.get("image"))
Gimp.Selection.all(img)
""" + _BOUNDS_TAIL

_NONE_CODE = """
img = find_image(args.get("image"))
Gimp.Selection.none(img)
""" + _BOUNDS_TAIL

_INVERT_CODE = """
img = find_image(args.get("image"))
Gimp.Selection.invert(img)
""" + _BOUNDS_TAIL

_GROW_CODE = """
img = find_image(args.get("image"))
Gimp.Selection.grow(img, int(args["steps"]))
""" + _BOUNDS_TAIL

_SHRINK_CODE = """
img = find_image(args.get("image"))
Gimp.Selection.shrink(img, int(args["steps"]))
""" + _BOUNDS_TAIL

# feather(image, radius) takes a double; border defaults to edge-lock semantics.
_FEATHER_CODE = """
img = find_image(args.get("image"))
Gimp.Selection.feather(img, float(args["radius"]))
""" + _BOUNDS_TAIL

_BORDER_CODE = """
img = find_image(args.get("image"))
Gimp.Selection.border(img, int(args["radius"]))
""" + _BOUNDS_TAIL

# Save the current selection to a new channel; optionally name it.
_TO_CHANNEL_CODE = """
img = find_image(args.get("image"))
ch = Gimp.Selection.save(img)
name = args.get("name")
if name:
    ch.set_name(name)
_result = {"image": img.get_id(), "channel_id": ch.get_id(), "name": ch.get_name()}
"""

# Replace the selection with the target layer's alpha (opaque pixels).
_FROM_ALPHA_CODE = _OP_HELPER + """
img = find_image(args.get("image"))
drw = find_drawable(args.get("image"), args.get("layer"))
op = _channel_op(args.get("op"))
img.select_item(op, drw)
""" + _BOUNDS_TAIL

# Resolve a path by name/id or use the active path, then select from it.
# GIMP 3.0.4 names these get_paths/get_selected_paths (NOT get_vectors); a path
# is selected via image.select_item(op, path). If the image has NO paths and the
# caller did not name one, we degrade gracefully (supported=False) rather than
# erroring, since "select from path" is meaningless without a path.
_FROM_PATH_CODE = _OP_HELPER + """
img = find_image(args.get("image"))
op = _channel_op(args.get("op"))
spec = args.get("path")
target = None
paths = img.get_paths()
if spec is None:
    sel = img.get_selected_paths()
    target = (sel[0] if sel else (paths[0] if paths else None))
elif isinstance(spec, int) or (isinstance(spec, str) and str(spec).isdigit()):
    target = Gimp.Item.get_by_id(int(spec))
    if target is None or not target.is_valid():
        raise ValueError("StaleHandle: no valid path with id %r" % (spec,))
else:
    for p in paths:
        if p.get_name() == spec:
            target = p
            break
    if target is None:
        raise ValueError("no path named %r in image %s" % (spec, img.get_id()))
if target is None:
    # No path at all and none requested -> nothing to select from. Degrade.
    _result = {"image": img.get_id(), "supported": False,
               "note": "no paths exist in this image; create a path first"}
else:
    img.select_item(op, target)
    _non_empty, _x, _y, _w, _h = compat.selection_bounds(img)
    _result = {
        "image": img.get_id(),
        "selection_empty": (not _non_empty),
        "bounds": (None if not _non_empty else [_x, _y, _w, _h]),
    }
"""


def _select_rect(ctx, x, y, w, h, op="replace", image=None):
    return ctx.run(_RECT_CODE,
                   args={"x": x, "y": y, "w": w, "h": h, "op": op, "image": image},
                   image=image, undo_group=True).to_dict()


def _select_ellipse(ctx, x, y, w, h, op="replace", image=None):
    return ctx.run(_ELLIPSE_CODE,
                   args={"x": x, "y": y, "w": w, "h": h, "op": op, "image": image},
                   image=image, undo_group=True).to_dict()


def _select_by_color(ctx, color, threshold=0.15, op="replace", layer=None, image=None):
    return ctx.run(_BY_COLOR_CODE,
                   args={"color": color, "threshold": threshold, "op": op,
                         "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _fuzzy_select(ctx, x, y, threshold=0.15, op="replace", layer=None, image=None):
    return ctx.run(_FUZZY_CODE,
                   args={"x": x, "y": y, "threshold": threshold, "op": op,
                         "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _select_all(ctx, image=None):
    return ctx.run(_ALL_CODE, args={"image": image}, image=image,
                   undo_group=True).to_dict()


def _select_none(ctx, image=None):
    return ctx.run(_NONE_CODE, args={"image": image}, image=image,
                   undo_group=True).to_dict()


def _select_invert(ctx, image=None):
    return ctx.run(_INVERT_CODE, args={"image": image}, image=image,
                   undo_group=True).to_dict()


def _select_grow(ctx, steps, image=None):
    return ctx.run(_GROW_CODE, args={"steps": steps, "image": image}, image=image,
                   undo_group=True).to_dict()


def _select_shrink(ctx, steps, image=None):
    return ctx.run(_SHRINK_CODE, args={"steps": steps, "image": image}, image=image,
                   undo_group=True).to_dict()


def _select_feather(ctx, radius, image=None):
    return ctx.run(_FEATHER_CODE, args={"radius": radius, "image": image}, image=image,
                   undo_group=True).to_dict()


def _select_border(ctx, radius, image=None):
    return ctx.run(_BORDER_CODE, args={"radius": radius, "image": image}, image=image,
                   undo_group=True).to_dict()


def _selection_to_channel(ctx, name=None, image=None):
    return ctx.run(_TO_CHANNEL_CODE, args={"name": name, "image": image}, image=image,
                   undo_group=True).to_dict()


def _select_from_alpha(ctx, layer=None, op="replace", image=None):
    return ctx.run(_FROM_ALPHA_CODE,
                   args={"layer": layer, "op": op, "image": image},
                   image=image, undo_group=True).to_dict()


def _select_from_path(ctx, path=None, op="replace", image=None):
    return ctx.run(_FROM_PATH_CODE,
                   args={"path": path, "op": op, "image": image},
                   image=image, undo_group=True).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="select_rect")
    def select_rect(x: int, y: int, w: int, h: int, op: str = "replace",
                    image: int | str | None = None) -> dict:
        """Rectangular selection. op: replace|add|subtract|intersect.
        Returns selection bounds."""
        return _select_rect(ctx, x, y, w, h, op, image)

    @mcp.tool(name="select_ellipse")
    def select_ellipse(x: int, y: int, w: int, h: int, op: str = "replace",
                       image: int | str | None = None) -> dict:
        """Elliptical selection inscribed in the x,y,w,h box. op:
        replace|add|subtract|intersect."""
        return _select_ellipse(ctx, x, y, w, h, op, image)

    @mcp.tool(name="select_by_color")
    def select_by_color(color: str | list | tuple, threshold: float = 0.15,
                        op: str = "replace", layer: int | str | None = None,
                        image: int | str | None = None) -> dict:
        """Select all pixels matching a color (name/#rrggbb/(r,g,b[,a])) within
        threshold (0..1). op: replace|add|subtract|intersect."""
        return _select_by_color(ctx, color, threshold, op, layer, image)

    @mcp.tool(name="fuzzy_select")
    def fuzzy_select(x: int, y: int, threshold: float = 0.15, op: str = "replace",
                     layer: int | str | None = None,
                     image: int | str | None = None) -> dict:
        """Contiguous color (magic-wand) selection seeded at x,y within threshold
        (0..1). op: replace|add|subtract|intersect."""
        return _fuzzy_select(ctx, x, y, threshold, op, layer, image)

    @mcp.tool(name="select_all")
    def select_all(image: int | str | None = None) -> dict:
        """Select the entire image canvas."""
        return _select_all(ctx, image)

    @mcp.tool(name="select_none")
    def select_none(image: int | str | None = None) -> dict:
        """Clear the selection (select nothing)."""
        return _select_none(ctx, image)

    @mcp.tool(name="select_invert")
    def select_invert(image: int | str | None = None) -> dict:
        """Invert the current selection."""
        return _select_invert(ctx, image)

    @mcp.tool(name="select_grow")
    def select_grow(steps: int, image: int | str | None = None) -> dict:
        """Grow the selection outward by `steps` pixels."""
        return _select_grow(ctx, steps, image)

    @mcp.tool(name="select_shrink")
    def select_shrink(steps: int, image: int | str | None = None) -> dict:
        """Shrink the selection inward by `steps` pixels."""
        return _select_shrink(ctx, steps, image)

    @mcp.tool(name="select_feather")
    def select_feather(radius: float, image: int | str | None = None) -> dict:
        """Feather (soften) the selection edge by `radius` pixels."""
        return _select_feather(ctx, radius, image)

    @mcp.tool(name="select_border")
    def select_border(radius: int, image: int | str | None = None) -> dict:
        """Replace the selection with a border band `radius` pixels wide."""
        return _select_border(ctx, radius, image)

    @mcp.tool(name="selection_to_channel")
    def selection_to_channel(name: str | None = None,
                             image: int | str | None = None) -> dict:
        """Save the current selection to a new channel. Returns the channel id."""
        return _selection_to_channel(ctx, name, image)

    @mcp.tool(name="select_from_alpha")
    def select_from_alpha(layer: int | str | None = None, op: str = "replace",
                          image: int | str | None = None) -> dict:
        """Select from a layer's alpha (opaque pixels). layer = id/name or omit for
        the active drawable. op: replace|add|subtract|intersect."""
        return _select_from_alpha(ctx, layer, op, image)

    @mcp.tool(name="select_from_path")
    def select_from_path(path: int | str | None = None, op: str = "replace",
                         image: int | str | None = None) -> dict:
        """Select the region enclosed by a path. path = id/name or omit for the
        active path. op: replace|add|subtract|intersect. If the image has no
        paths and none is named, returns {"supported": false} (nothing to do)
        rather than erroring."""
        return _select_from_path(ctx, path, op, image)
