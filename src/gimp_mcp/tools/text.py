"""Group G — Text & typography.

Tools build a fixed code template + pass params as a data `args` payload
(injection-safe) to the bridge's exec op, and read structured `_result`. Impl
functions are module-level so they can be unit-tested against a bridge directly.

Implemented tools:
  - create_text_layer (font/size/color/justify/line+letter spacing/position)
  - set_text_props (mutate any subset of an existing text layer's props)
  - outline_text (destructive — alpha->selection->grow->stroke a new layer)
  - check_fonts (availability map)
  - substitute_font (best-effort old-missing / new-present report)

Deferred to v1.1: text_along_path, distress_text.
"""
from __future__ import annotations

_CREATE_CODE = """
img = find_image(args.get("image"))
font = args.get("font")
font_obj = None
if font:
    font_obj = Gimp.Font.get_by_name(font)
    if font_obj is None:
        # GIMP 3.2 dropped GIMP-2 font aliases (e.g. "Sans" -> "Sans-serif"), so a
        # legacy name now resolves to None. Try plausible 3.2 aliases, then fall
        # back to the context font — NEVER pass None to TextLayer.new (3.2 raises
        # "Argument 2 does not allow None").
        for alias in (font + "-serif", font.replace(" ", "-"),
                      "Sans-serif", "Serif", "Monospace"):
            font_obj = Gimp.Font.get_by_name(alias)
            if font_obj is not None:
                break
if font_obj is None:
    font_obj = Gimp.context_get_font()
size = float(args.get("size", 18.0))
unit = Gimp.Unit.pixel()
layer = Gimp.TextLayer.new(img, args["text"], font_obj, size, unit)
img.insert_layer(layer, None, 0)

color = args.get("color")
if color is not None:
    try:
        layer.set_color(compat.color(color))
    except Exception:
        pass

j = args.get("justify")
if j:
    try:
        layer.set_justification(getattr(Gimp.TextJustification, j.upper()))
    except Exception:
        pass

ls = args.get("line_spacing")
if ls is not None:
    try:
        layer.set_line_spacing(float(ls))
    except Exception:
        pass
ks = args.get("letter_spacing")
if ks is not None:
    try:
        layer.set_letter_spacing(float(ks))
    except Exception:
        pass

x = args.get("x"); y = args.get("y")
if x is not None and y is not None:
    layer.set_offsets(int(x), int(y))

off = layer.get_offsets()
ox, oy = (off[-2], off[-1]) if off else (None, None)
_result = {
    "layer": layer.get_id(),
    "image": img.get_id(),
    "name": layer.get_name(),
    "width": layer.get_width(),
    "height": layer.get_height(),
    "x": ox, "y": oy,
    "font": (font_obj.get_name() if font_obj else None),  # actual font used (may differ from requested)
}
"""

# Mutate any subset of an existing text layer's props (None = leave unchanged).
_SET_PROPS_CODE = """
layer = find_drawable(args.get("image"), args.get("layer"))
changed = []

text = args.get("text")
if text is not None:
    layer.set_text(text); changed.append("text")

font = args.get("font")
if font is not None:
    fo = Gimp.Font.get_by_name(font)
    if fo is None:
        # 3.2 dropped GIMP-2 aliases (see create) — resolve before giving up.
        for alias in (font + "-serif", font.replace(" ", "-"),
                      "Sans-serif", "Serif", "Monospace"):
            fo = Gimp.Font.get_by_name(alias)
            if fo is not None:
                break
    if fo is not None:
        layer.set_font(fo); changed.append("font")

size = args.get("size")
if size is not None:
    layer.set_font_size(float(size), Gimp.Unit.pixel()); changed.append("size")

color = args.get("color")
if color is not None:
    layer.set_color(compat.color(color)); changed.append("color")

j = args.get("justify")
if j is not None:
    layer.set_justification(getattr(Gimp.TextJustification, j.upper()))
    changed.append("justify")

ls = args.get("line_spacing")
if ls is not None:
    layer.set_line_spacing(float(ls)); changed.append("line_spacing")

ks = args.get("letter_spacing")
if ks is not None:
    layer.set_letter_spacing(float(ks)); changed.append("letter_spacing")

x = args.get("x"); y = args.get("y")
if x is not None and y is not None:
    layer.set_offsets(int(x), int(y)); changed.append("position")

_result = {"layer": layer.get_id(), "image": layer.get_image().get_id(),
           "name": layer.get_name(), "changed": changed}
"""

# Outline: use the text layer's alpha as a selection, grow it, fill onto a new
# layer placed beneath the text so the outline frames the glyphs.
_OUTLINE_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
radius = int(args.get("radius", 3))
color = args.get("color", "black")

# Alpha -> selection, then grow.
img.select_item(Gimp.ChannelOps.REPLACE, layer)
if radius > 0:
    Gimp.Selection.grow(img, radius)

# New transparent full-image layer placed just below the text layer.
ow = img.get_width(); oh = img.get_height()
outline = Gimp.Layer.new(img, layer.get_name() + " outline", ow, oh,
                         Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
pos = img.get_item_position(layer)
img.insert_layer(outline, None, pos + 1)
outline.fill(Gimp.FillType.TRANSPARENT)

# Fill the grown selection with the outline color.
Gimp.context_set_foreground(compat.color(color))
outline.edit_fill(Gimp.FillType.FOREGROUND)

Gimp.Selection.none(img)
_result = {"layer": outline.get_id(), "image": img.get_id(),
           "name": outline.get_name(),
           "radius": radius, "source": layer.get_id()}
"""

_CHECK_FONTS_CODE = """
result = {}
for name in args.get("fonts", []):
    try:
        result[name] = Gimp.Font.get_by_name(name) is not None
    except Exception:
        result[name] = False
_result = {"available": result}
"""

# Best-effort substitution report — does NOT mutate global config.
_SUBSTITUTE_CODE = """
old = args.get("old"); new = args.get("new")
old_present = False; new_present = False
try:
    old_present = Gimp.Font.get_by_name(old) is not None
except Exception:
    pass
try:
    new_present = Gimp.Font.get_by_name(new) is not None
except Exception:
    pass
_result = {
    "old": old, "new": new,
    "old_present": old_present,
    "new_present": new_present,
    "substitution_recommended": (not old_present) and new_present,
}
"""


def _create_text_layer(ctx, text, font=None, size=18.0, color=None, x=None, y=None,
                       justify=None, line_spacing=None, letter_spacing=None, image=None):
    return ctx.run(_CREATE_CODE,
                   args={"text": text, "font": font, "size": size, "color": color,
                         "x": x, "y": y, "justify": justify,
                         "line_spacing": line_spacing, "letter_spacing": letter_spacing,
                         "image": image},
                   image=image, undo_group=True).to_dict()


def _set_text_props(ctx, layer, text=None, font=None, size=None, color=None,
                    justify=None, line_spacing=None, letter_spacing=None,
                    x=None, y=None, image=None):
    return ctx.run(_SET_PROPS_CODE,
                   args={"layer": layer, "text": text, "font": font, "size": size,
                         "color": color, "justify": justify,
                         "line_spacing": line_spacing, "letter_spacing": letter_spacing,
                         "x": x, "y": y, "image": image},
                   image=image, undo_group=True).to_dict()


def _outline_text(ctx, layer, radius=3, color="black", image=None):
    return ctx.run(_OUTLINE_CODE,
                   args={"layer": layer, "radius": radius, "color": color, "image": image},
                   image=image, undo_group=True).to_dict()


def _check_fonts(ctx, fonts):
    return ctx.run(_CHECK_FONTS_CODE, args={"fonts": fonts}, undo_group=False).to_dict()


def _substitute_font(ctx, old, new):
    return ctx.run(_SUBSTITUTE_CODE, args={"old": old, "new": new}, undo_group=False).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="create_text_layer")
    def create_text_layer(text: str, font: str | None = None, size: float = 18.0,
                          color: str | None = None, x: int | None = None,
                          y: int | None = None, justify: str | None = None,
                          line_spacing: float | None = None,
                          letter_spacing: float | None = None,
                          image: int | str | None = None) -> dict:
        """Create a text layer. font=name (else context font); size in px; color by
        name/#hex/(r,g,b); justify=left|right|center|fill; x/y = position offsets."""
        return _create_text_layer(ctx, text, font, size, color, x, y, justify,
                                   line_spacing, letter_spacing, image)

    @mcp.tool(name="set_text_props")
    def set_text_props(layer: int | str, text: str | None = None,
                       font: str | None = None, size: float | None = None,
                       color: str | None = None, justify: str | None = None,
                       line_spacing: float | None = None,
                       letter_spacing: float | None = None,
                       x: int | None = None, y: int | None = None,
                       image: int | str | None = None) -> dict:
        """Mutate any subset of an existing text layer's properties (omitted args are
        left unchanged). `layer` = id/name of a text layer."""
        return _set_text_props(ctx, layer, text, font, size, color, justify,
                               line_spacing, letter_spacing, x, y, image)

    @mcp.tool(name="outline_text")
    def outline_text(layer: int | str, radius: int = 3, color: str = "black",
                     image: int | str | None = None) -> dict:
        """Add an outline beneath a text layer: alpha->selection, grow by `radius`,
        fill onto a new layer in `color`. Destructive (adds a layer)."""
        return _outline_text(ctx, layer, radius, color, image)

    @mcp.tool(name="check_fonts")
    def check_fonts(fonts: list[str]) -> dict:
        """Report availability of each font name -> {name: bool}."""
        return _check_fonts(ctx, fonts)

    @mcp.tool(name="substitute_font")
    def substitute_font(old: str, new: str) -> dict:
        """Best-effort font-substitution report: whether `old` is missing and `new`
        is present (does not mutate global GIMP config)."""
        return _substitute_font(ctx, old, new)
