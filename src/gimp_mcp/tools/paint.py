"""Group F — Paint / fill / draw.

Tools build a fixed code template + pass params as a data `args` payload
(injection-safe) to the bridge's exec op, and read structured `_result`. Impl
functions are module-level so they can be unit-tested against a bridge directly.

Tools: set_fg, set_bg, fill, bucket_fill, gradient, stroke_selection,
pencil, paintbrush, set_brush, set_paint_opacity.
"""
from __future__ import annotations

# Context-only ops (no image/drawable target) — read/write paint state.
_SET_FG_CODE = """
Gimp.context_set_foreground(compat.color(args["color"]))
_result = {"foreground": list(compat.rgba(compat.color(args["color"])))}
"""

_SET_BG_CODE = """
Gimp.context_set_background(compat.color(args["color"]))
_result = {"background": list(compat.rgba(compat.color(args["color"])))}
"""

_SET_BRUSH_CODE = """
brush = Gimp.Brush.get_by_name(args["name"])
if brush is None:
    raise ValueError("no brush named %r" % args["name"])
Gimp.context_set_brush(brush)
_result = {"brush": brush.get_name()}
"""

_SET_OPACITY_CODE = """
Gimp.context_set_opacity(float(args["opacity"]))
_result = {"opacity": float(args["opacity"])}
"""

# Destructive ops — operate on a target drawable.
_FILL_CODE = """
drw = find_drawable(args.get("image"), args.get("layer"))
kind = str(args["fill_type"]).upper()
ft = getattr(Gimp.FillType, kind, Gimp.FillType.FOREGROUND)
drw.fill(ft)
Gimp.displays_flush()
_result = {"layer": drw.get_id(), "image": drw.get_image().get_id(), "fill_type": kind}
"""

_BUCKET_FILL_CODE = """
drw = find_drawable(args.get("image"), args.get("layer"))
# Guard context: a paint OP sets fg/opacity/mode to draw, but must not leak them into
# the session (set_fg / set_paint_opacity are the tools for persistent changes).
Gimp.context_push()
try:
    if args.get("color") is not None:
        Gimp.context_set_foreground(compat.color(args["color"]))
    if args.get("opacity") is not None:
        Gimp.context_set_opacity(float(args["opacity"]))
    mode = args.get("mode")
    if mode:
        m = getattr(Gimp.LayerMode, str(mode).upper().replace("-", "_"), Gimp.LayerMode.NORMAL)
        Gimp.context_set_paint_mode(m)
    # edit_bucket_fill(fill_type, x, y) — flood-fills the seed point with the fg color.
    drw.edit_bucket_fill(Gimp.FillType.FOREGROUND, float(args["x"]), float(args["y"]))
finally:
    Gimp.context_pop()
Gimp.displays_flush()
_result = {"layer": drw.get_id(), "image": drw.get_image().get_id(), "x": args["x"], "y": args["y"]}
"""

# Long signature flagged heavily — see api_risks.
_GRADIENT_CODE = """
drw = find_drawable(args.get("image"), args.get("layer"))
gtype = str(args.get("gradient_type", "linear")).upper().replace("-", "_")
gt = getattr(Gimp.GradientType, gtype, Gimp.GradientType.LINEAR)
Gimp.context_push()   # a paint OP: don't leak the chosen gradient into the session
try:
    if args.get("gradient"):
        grad = Gimp.Gradient.get_by_name(args["gradient"])
        if grad is None:
            raise ValueError("no gradient named %r" % args["gradient"])
        Gimp.context_set_gradient(grad)
    else:
        Gimp.context_set_gradient_fg_bg_rgb()
    # edit_gradient_fill(gradient_type, offset, supersample, supersample_max_depth,
    #                    supersample_threshold, dither, x1, y1, x2, y2)
    drw.edit_gradient_fill(gt, 0.0, False, 1, 0.0, True,
                           float(args["x1"]), float(args["y1"]),
                           float(args["x2"]), float(args["y2"]))
finally:
    Gimp.context_pop()
Gimp.displays_flush()
_result = {"layer": drw.get_id(), "image": drw.get_image().get_id(), "gradient_type": gtype}
"""

_STROKE_SELECTION_CODE = """
drw = find_drawable(args.get("image"), args.get("layer"))
Gimp.context_push()   # a paint OP: don't leak fg / line width into the session
try:
    if args.get("color") is not None:
        Gimp.context_set_foreground(compat.color(args["color"]))
    if args.get("line_width") is not None:
        Gimp.context_set_line_width(float(args["line_width"]))
    drw.edit_stroke_selection()
finally:
    Gimp.context_pop()
Gimp.displays_flush()
_result = {"layer": drw.get_id(), "image": drw.get_image().get_id(), "stroked": True}
"""

_PENCIL_CODE = """
drw = find_drawable(args.get("image"), args.get("layer"))
pts = [float(v) for v in args["points"]]
Gimp.context_push()   # a paint OP: don't leak fg into the session
try:
    if args.get("color") is not None:
        Gimp.context_set_foreground(compat.color(args["color"]))
    # pencil(drawable, strokes) — strokes is a flat [x1,y1,x2,y2,...] array.
    Gimp.pencil(drw, pts)
finally:
    Gimp.context_pop()
Gimp.displays_flush()
_result = {"layer": drw.get_id(), "image": drw.get_image().get_id(), "n_points": len(pts) // 2}
"""

_PAINTBRUSH_CODE = """
drw = find_drawable(args.get("image"), args.get("layer"))
pts = [float(v) for v in args["points"]]
Gimp.context_push()   # a paint OP: don't leak fg / brush / size into the session
try:
    if args.get("color") is not None:
        Gimp.context_set_foreground(compat.color(args["color"]))
    if args.get("brush"):
        b = Gimp.Brush.get_by_name(args["brush"])
        if b is not None:
            Gimp.context_set_brush(b)
    if args.get("size") is not None:
        Gimp.context_set_brush_size(float(args["size"]))
    # paintbrush_default(drawable, strokes) — uses current context brush/size.
    Gimp.paintbrush_default(drw, pts)
finally:
    Gimp.context_pop()
Gimp.displays_flush()
_result = {"layer": drw.get_id(), "image": drw.get_image().get_id(), "n_points": len(pts) // 2}
"""


def _set_fg(ctx, color):
    return ctx.run(_SET_FG_CODE, args={"color": color}, undo_group=False).to_dict()


def _set_bg(ctx, color):
    return ctx.run(_SET_BG_CODE, args={"color": color}, undo_group=False).to_dict()


def _set_brush(ctx, name):
    return ctx.run(_SET_BRUSH_CODE, args={"name": name}, undo_group=False).to_dict()


def _set_paint_opacity(ctx, opacity):
    return ctx.run(_SET_OPACITY_CODE, args={"opacity": opacity}, undo_group=False).to_dict()


def _fill(ctx, fill_type="foreground", layer=None, image=None):
    return ctx.run(_FILL_CODE,
                   args={"image": image, "layer": layer, "fill_type": fill_type},
                   image=image, undo_group=True).to_dict()


def _bucket_fill(ctx, x, y, color=None, opacity=None, mode=None,
                 layer=None, image=None):
    return ctx.run(_BUCKET_FILL_CODE,
                   args={"image": image, "layer": layer, "x": x, "y": y,
                         "color": color, "opacity": opacity, "mode": mode},
                   image=image, undo_group=True).to_dict()


def _gradient(ctx, x1, y1, x2, y2, gradient=None, gradient_type="linear",
              layer=None, image=None):
    return ctx.run(_GRADIENT_CODE,
                   args={"image": image, "layer": layer,
                         "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                         "gradient": gradient, "gradient_type": gradient_type},
                   image=image, undo_group=True).to_dict()


def _stroke_selection(ctx, line_width=None, color=None, layer=None, image=None):
    return ctx.run(_STROKE_SELECTION_CODE,
                   args={"image": image, "layer": layer,
                         "line_width": line_width, "color": color},
                   image=image, undo_group=True).to_dict()


def _pencil(ctx, points, color=None, layer=None, image=None):
    return ctx.run(_PENCIL_CODE,
                   args={"image": image, "layer": layer,
                         "points": points, "color": color},
                   image=image, undo_group=True).to_dict()


def _paintbrush(ctx, points, brush=None, size=None, color=None,
                layer=None, image=None):
    return ctx.run(_PAINTBRUSH_CODE,
                   args={"image": image, "layer": layer, "points": points,
                         "brush": brush, "size": size, "color": color},
                   image=image, undo_group=True).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="set_fg")
    def set_fg(color: str | list | tuple) -> dict:
        """Set the foreground color. color = name|'#rrggbb'|(r,g,b[,a])."""
        return _set_fg(ctx, color)

    @mcp.tool(name="set_bg")
    def set_bg(color: str | list | tuple) -> dict:
        """Set the background color. color = name|'#rrggbb'|(r,g,b[,a])."""
        return _set_bg(ctx, color)

    @mcp.tool(name="set_brush")
    def set_brush(name: str) -> dict:
        """Set the active paint brush by name (e.g. '2. Hardness 050')."""
        return _set_brush(ctx, name)

    @mcp.tool(name="set_paint_opacity")
    def set_paint_opacity(opacity: float) -> dict:
        """Set the paint/context opacity (0-100)."""
        return _set_paint_opacity(ctx, opacity)

    @mcp.tool(name="fill")
    def fill(fill_type: str = "foreground", layer: int | str | None = None,
             image: int | str | None = None) -> dict:
        """Fill the whole drawable. fill_type: foreground|background|white|
        transparent|pattern. `layer`/`image` optional (default active)."""
        return _fill(ctx, fill_type, layer, image)

    @mcp.tool(name="bucket_fill")
    def bucket_fill(x: float, y: float, color: str | list | tuple | None = None,
                    opacity: float | None = None, mode: str | None = None,
                    layer: int | str | None = None,
                    image: int | str | None = None) -> dict:
        """Flood-fill from seed point (x, y) with `color` (or current fg).
        mode = layer-mode name (e.g. 'normal', 'multiply')."""
        return _bucket_fill(ctx, x, y, color, opacity, mode, layer, image)

    @mcp.tool(name="gradient")
    def gradient(x1: float, y1: float, x2: float, y2: float,
                 gradient: str | None = None, gradient_type: str = "linear",
                 layer: int | str | None = None,
                 image: int | str | None = None) -> dict:
        """Draw a gradient from (x1,y1) to (x2,y2). `gradient` = named gradient
        (else fg->bg). gradient_type: linear|bilinear|radial|conical-symmetric|..."""
        return _gradient(ctx, x1, y1, x2, y2, gradient, gradient_type, layer, image)

    @mcp.tool(name="stroke_selection")
    def stroke_selection(line_width: float | None = None,
                         color: str | list | tuple | None = None,
                         layer: int | str | None = None,
                         image: int | str | None = None) -> dict:
        """Stroke the active selection's outline with `color`/`line_width`
        (current fg/line width if omitted)."""
        return _stroke_selection(ctx, line_width, color, layer, image)

    @mcp.tool(name="pencil")
    def pencil(points: list, color: str | list | tuple | None = None,
               layer: int | str | None = None,
               image: int | str | None = None) -> dict:
        """Hard-edged pencil stroke. points = flat [x1,y1,x2,y2,...]. Uses `color`
        or current fg."""
        return _pencil(ctx, points, color, layer, image)

    @mcp.tool(name="paintbrush")
    def paintbrush(points: list, brush: str | None = None,
                   size: float | None = None,
                   color: str | list | tuple | None = None,
                   layer: int | str | None = None,
                   image: int | str | None = None) -> dict:
        """Soft paintbrush stroke. points = flat [x1,y1,x2,y2,...]. Optional
        `brush` name + `size`; uses `color` or current fg."""
        return _paintbrush(ctx, points, brush, size, color, layer, image)
