"""Group I — Filters & effects (GEGL).

Per the v1 decision: a GENERIC apply_filter plus three named convenience filters.
(list_gegl_ops / describe_op live in the analysis group, not here.)

Tools build a fixed code template + pass params as a data `args` payload
(injection-safe) to the bridge's exec op, and read structured `_result`. All ops
here are DESTRUCTIVE: they merge a GEGL filter onto the target drawable, so they
pass image=image (+ "image" in args) and undo_group=True.

Planned tools:
  - apply_filter(op, params?, layer?)         generic gegl:<op> runner
  - gaussian_blur(layer?, std_dev_x?, std_dev_y?)
  - unsharp_mask(layer?, std_dev?, scale?)
  - drop_shadow(layer?, x?, y?, blur?, color?, opacity?, grow?)
"""
from __future__ import annotations

# Generic: apply any gegl:<op> with a free dict of property -> value. Scalars set
# directly; if set_property fails on a raw value AND the value looks color-like,
# retry via compat.color() (best-effort — color props are an api_risk).
_APPLY_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
op = args["op"]
params = args.get("params") or {}
# An unknown / unwrappable GEGL op makes DrawableFilter.new return NULL, which
# PyGObject surfaces as a cryptic "TypeError: constructor returned NULL". Guard it
# so the caller gets a clear, actionable error naming the offending op instead.
try:
    filt = Gimp.DrawableFilter.new(drawable, op, "")
except Exception:
    filt = None
if filt is None:
    raise ValueError(
        "apply_filter: %r is not an applicable GEGL operation (unknown op, or it "
        "cannot be wrapped as a DrawableFilter). Use list_gegl_ops to see valid ops." % op)
cfg = filt.get_config()
_set = []
for k, v in params.items():
    prop = k.replace("_", "-")
    try:
        cfg.set_property(prop, v)
        _set.append(prop)
    except Exception:
        # Maybe a color-typed property given as a name/'#rrggbb'/tuple.
        if isinstance(v, str) or (isinstance(v, (list, tuple)) and len(v) in (3, 4)):
            cfg.set_property(prop, compat.color(v))
            _set.append(prop)
        else:
            raise
filt.update()
drawable.merge_filter(filt)
_result = {"applied": op, "set_properties": _set, "layer": drawable.get_id(), "image": img.get_id()}
"""

_GAUSSIAN_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
filt = Gimp.DrawableFilter.new(drawable, "gegl:gaussian-blur", "")
cfg = filt.get_config()
cfg.set_property("std-dev-x", float(args["std_dev_x"]))
cfg.set_property("std-dev-y", float(args["std_dev_y"]))
filt.update()
drawable.merge_filter(filt)
_result = {"applied": "gegl:gaussian-blur",
           "std_dev_x": float(args["std_dev_x"]),
           "std_dev_y": float(args["std_dev_y"]),
           "layer": drawable.get_id(), "image": img.get_id()}
"""

_UNSHARP_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
filt = Gimp.DrawableFilter.new(drawable, "gegl:unsharp-mask", "")
cfg = filt.get_config()
cfg.set_property("std-dev", float(args["std_dev"]))
cfg.set_property("scale", float(args["scale"]))
filt.update()
drawable.merge_filter(filt)
_result = {"applied": "gegl:unsharp-mask",
           "std_dev": float(args["std_dev"]), "scale": float(args["scale"]),
           "layer": drawable.get_id(), "image": img.get_id()}
"""

# Drop shadow grows beyond the layer bounds -> ensure alpha so the soft edge can
# render. gegl:dropshadow may enlarge the layer (flagged as an api_risk).
_DROP_SHADOW_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
compat.ensure_alpha(drawable)
filt = Gimp.DrawableFilter.new(drawable, "gegl:dropshadow", "")
cfg = filt.get_config()
cfg.set_property("x", float(args["x"]))
cfg.set_property("y", float(args["y"]))
cfg.set_property("radius", float(args["blur"]))
cfg.set_property("grow-radius", float(args["grow"]))
cfg.set_property("opacity", float(args["opacity"]))
cfg.set_property("color", compat.color(args["color"]))
filt.update()
drawable.merge_filter(filt)
_result = {"applied": "gegl:dropshadow", "x": float(args["x"]), "y": float(args["y"]),
           "blur": float(args["blur"]), "grow": float(args["grow"]),
           "opacity": float(args["opacity"]), "layer": drawable.get_id(), "image": img.get_id()}
"""


def _apply_filter(ctx, op, params=None, layer=None, image=None):
    return ctx.run(_APPLY_CODE,
                   args={"op": op, "params": params, "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _gaussian_blur(ctx, std_dev_x=1.5, std_dev_y=1.5, layer=None, image=None):
    return ctx.run(_GAUSSIAN_CODE,
                   args={"std_dev_x": std_dev_x, "std_dev_y": std_dev_y,
                         "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _unsharp_mask(ctx, std_dev=3.0, scale=0.5, layer=None, image=None):
    return ctx.run(_UNSHARP_CODE,
                   args={"std_dev": std_dev, "scale": scale,
                         "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _drop_shadow(ctx, x=8.0, y=8.0, blur=10.0, color="black", opacity=0.5,
                 grow=0.0, layer=None, image=None):
    return ctx.run(_DROP_SHADOW_CODE,
                   args={"x": x, "y": y, "blur": blur, "color": color,
                         "opacity": opacity, "grow": grow,
                         "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="apply_filter")
    def apply_filter(op: str, params: dict | None = None,
                     layer: int | str | None = None,
                     image: int | str | None = None) -> dict:
        """Apply any GEGL operation to a layer (destructive merge).
        `op` = full op name e.g. 'gegl:pixelize'. `params` = {property: value}
        (underscores become hyphens; color-like values auto-coerced)."""
        return _apply_filter(ctx, op, params, layer, image)

    @mcp.tool(name="gaussian_blur")
    def gaussian_blur(std_dev_x: float = 1.5, std_dev_y: float = 1.5,
                      layer: int | str | None = None,
                      image: int | str | None = None) -> dict:
        """Gaussian blur a layer (gegl:gaussian-blur). std_dev_x/y in pixels."""
        return _gaussian_blur(ctx, std_dev_x, std_dev_y, layer, image)

    @mcp.tool(name="unsharp_mask")
    def unsharp_mask(std_dev: float = 3.0, scale: float = 0.5,
                     layer: int | str | None = None,
                     image: int | str | None = None) -> dict:
        """Sharpen a layer via unsharp mask (gegl:unsharp-mask). std_dev = blur
        radius, scale = sharpen strength."""
        return _unsharp_mask(ctx, std_dev, scale, layer, image)

    @mcp.tool(name="drop_shadow")
    def drop_shadow(x: float = 8.0, y: float = 8.0, blur: float = 10.0,
                    color: str = "black", opacity: float = 0.5, grow: float = 0.0,
                    layer: int | str | None = None,
                    image: int | str | None = None) -> dict:
        """Add a drop shadow to a layer (gegl:dropshadow). x/y = offset, blur =
        shadow radius, grow = shadow expansion, color = name/'#rrggbb'/(r,g,b).

        NOTE: gegl:dropshadow ENLARGES the target layer and shifts its offsets so
        the soft shadow has room to render (verified on GIMP 3.0.4: a 64x48 layer
        at (0,0) became 116x100 at (-20,-20)). The layer may therefore extend
        beyond the canvas; flatten or crop afterward if you need it clipped."""
        return _drop_shadow(ctx, x, y, blur, color, opacity, grow, layer, image)
