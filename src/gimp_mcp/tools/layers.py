"""Group C — Layers.

Tools build a fixed code template + pass params as a data `args` payload
(injection-safe) to the bridge's exec op, and read structured `_result`. Impl
functions are module-level so they can be unit-tested against a bridge directly.

Tools:
  - create_layer, layer_from_file, duplicate_layer, delete_layer
  - reorder_layer, set_opacity, set_blend_mode, set_visible, rename_layer
  - merge_down, flatten                 [destructive -> auto-checkpoint]
  - group_create, group_add, group_ungroup
  - move_layer (absolute) / offset_layer (relative)   [reposition the layer]
  - offset_content (roll pixels, wrap) / seam_check    [tileable-texture seams]
"""
from __future__ import annotations

# Map a blend-mode string to a Gimp.LayerMode enum member, tolerant of case and
# '-'/' ' separators; falls back to NORMAL if the name is unknown.
_BLEND_HELPER = """
def _blend_mode(name):
    key = str(name).upper().replace("-", "_").replace(" ", "_")
    return getattr(Gimp.LayerMode, key, Gimp.LayerMode.NORMAL)
"""

# A new transparent RGBA layer sized to the image (or the given w/h), inserted at
# the top (position 0) under an optional parent group.
_CREATE_CODE = """
img = find_image(args.get("image"))
w = args.get("width") or img.get_width()
h = args.get("height") or img.get_height()
parent = None
if args.get("parent") is not None:
    parent = find_drawable(args.get("image"), args.get("parent"))
layer = Gimp.Layer.new(img, args["name"], w, h, Gimp.ImageType.RGBA_IMAGE,
                       float(args.get("opacity", 100.0)), Gimp.LayerMode.NORMAL)
layer.fill(Gimp.FillType.TRANSPARENT)
img.insert_layer(layer, parent, int(args.get("position", 0)))
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "name": layer.get_name(), "width": w, "height": h}
"""

# Load a file as a new layer and insert it (top of stack by default).
_FROM_FILE_CODE = """
img = find_image(args.get("image"))
f = Gio.File.new_for_path(args["path"])
layer = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, img, f)
if args.get("name"):
    layer.set_name(args["name"])
parent = None
if args.get("parent") is not None:
    parent = find_drawable(args.get("image"), args.get("parent"))
img.insert_layer(layer, parent, int(args.get("position", 0)))
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "name": layer.get_name(),
           "width": layer.get_width(), "height": layer.get_height()}
"""

# Duplicate a layer (Gimp.Layer.copy) and insert it just above the original.
_DUPLICATE_CODE = """
img = find_image(args.get("image"))
src = find_drawable(args.get("image"), args.get("layer"))
dup = src.copy()
if args.get("name"):
    dup.set_name(args["name"])
parent = src.get_parent()
# Insert above the source: same parent, position = source index.
siblings = parent.get_children() if parent is not None else img.get_layers()
pos = 0
for i, l in enumerate(siblings):
    if l.get_id() == src.get_id():
        pos = i
        break
img.insert_layer(dup, parent, pos)
_result = {"layer": dup.get_id(), "image": img.get_id(), "name": dup.get_name()}
"""

_DELETE_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
lid = layer.get_id()
img.remove_layer(layer)
_result = {"deleted": lid, "image": img.get_id()}
"""

# Reorder a layer to an absolute position within an (optionally new) parent.
_REORDER_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
parent = None
if args.get("parent") is not None:
    parent = find_drawable(args.get("image"), args.get("parent"))
else:
    parent = layer.get_parent()
img.reorder_item(layer, parent, int(args.get("position", 0)))
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "position": int(args.get("position", 0))}
"""

_OPACITY_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
layer.set_opacity(float(args["opacity"]))
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "opacity": layer.get_opacity()}
"""

_BLEND_CODE = _BLEND_HELPER + """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
mode = _blend_mode(args["mode"])
layer.set_mode(mode)
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "mode": str(args["mode"]).upper(),
           "mode_value": int(layer.get_mode())}
"""

_VISIBLE_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
layer.set_visible(bool(args["visible"]))
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "visible": layer.get_visible()}
"""

_RENAME_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
layer.set_name(args["name"])
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "name": layer.get_name()}
"""

_MERGE_DOWN_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
merged = img.merge_down(layer, Gimp.MergeType.EXPAND_AS_NECESSARY)
_result = {"layer": merged.get_id(), "image": img.get_id(),
           "name": merged.get_name()}
"""

_FLATTEN_CODE = """
img = find_image(args.get("image"))
layer = img.flatten()
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "name": layer.get_name(),
           "num_layers": len(img.get_layers())}
"""

# Create an empty layer group and insert it (top of stack by default).
_GROUP_CREATE_CODE = """
img = find_image(args.get("image"))
group = Gimp.GroupLayer.new(img)
if args.get("name"):
    group.set_name(args["name"])
parent = None
if args.get("parent") is not None:
    parent = find_drawable(args.get("image"), args.get("parent"))
img.insert_layer(group, parent, int(args.get("position", 0)))
_result = {"layer": group.get_id(), "image": img.get_id(),
           "name": group.get_name()}
"""

# Move an existing layer INTO a group via reorder_item (parent = the group).
_GROUP_ADD_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
group = find_drawable(args.get("image"), args.get("group"))
img.reorder_item(layer, group, int(args.get("position", 0)))
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "group": group.get_id()}
"""

# Ungroup a layer group: lift its children to the group's parent, then drop the
# now-empty group. Children are re-parented in order so the visual stack holds.
_GROUP_UNGROUP_CODE = """
img = find_image(args.get("image"))
group = find_drawable(args.get("image"), args.get("group"))
if not group.is_group():
    raise ValueError("layer %s is not a group" % group.get_id())
parent = group.get_parent()
siblings = parent.get_children() if parent is not None else img.get_layers()
base = 0
for i, l in enumerate(siblings):
    if l.get_id() == group.get_id():
        base = i
        break
children = list(group.get_children())
moved = []
for offset, child in enumerate(children):
    img.reorder_item(child, parent, base + offset)
    moved.append(child.get_id())
img.remove_layer(group)
_result = {"ungrouped": moved, "image": img.get_id()}
"""

# Set absolute canvas offsets (move) for a layer.
_MOVE_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
layer.set_offsets(int(args["x"]), int(args["y"]))
ox, oy = layer.get_offsets()[1], layer.get_offsets()[2]
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "x": ox, "y": oy}
"""

# Offset a layer by a relative delta from its current canvas position.
_OFFSET_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
off = layer.get_offsets()
cx, cy = (off[1], off[2]) if len(off) >= 3 else (off[0], off[1])
nx = cx + int(args.get("dx", 0))
ny = cy + int(args.get("dy", 0))
layer.set_offsets(nx, ny)
_result = {"layer": layer.get_id(), "image": img.get_id(),
           "x": nx, "y": ny}
"""

# Roll a layer's PIXEL CONTENT by (dx, dy). Distinct from offset_layer/move_layer,
# which reposition the layer on the canvas: this shifts the pixels WITHIN the layer.
# wrap=True wraps the edges around (seam-check tileable textures); wrap=False fills
# the vacated strip (transparent, or a solid `color`). GIMP 3.x offset() takes 5
# positional args: (wrap_around, fill_type, color, dx, dy).
_OFFSET_CONTENT_CODE = """
d = find_drawable(args.get("image"), args.get("layer"))
dx = int(args.get("dx", 0)); dy = int(args.get("dy", 0))
wrap = bool(args.get("wrap", True))
fill = (args.get("fill") or "transparent").upper()
if wrap:
    ftype = Gimp.OffsetType.WRAP_AROUND
elif fill == "COLOR":
    ftype = Gimp.OffsetType.COLOR
else:
    ftype = Gimp.OffsetType.TRANSPARENT
    compat.ensure_alpha(d)
col = compat.color(args.get("color") or (0, 0, 0, 0))
d.offset(wrap, ftype, col, dx, dy)
_result = {"layer": d.get_id(), "image": d.get_image().get_id(),
           "dx": dx, "dy": dy, "wrap": wrap}
"""

# Offset a layer's content by half its size (wrapping) so a tileable texture's seams
# land in the middle where they're easy to inspect. axis: both|x|y. Undo to revert —
# re-running the same axis only restores when that dimension is even (floor(w/2) twice
# lands 1px off for odd sizes).
_SEAM_CHECK_CODE = """
d = find_drawable(args.get("image"), args.get("layer"))
axis = (args.get("axis") or "both").lower()
w, h = d.get_width(), d.get_height()
dx = w // 2 if axis in ("both", "x", "horizontal") else 0
dy = h // 2 if axis in ("both", "y", "vertical") else 0
d.offset(True, Gimp.OffsetType.WRAP_AROUND, compat.color((0, 0, 0, 0)), dx, dy)
_result = {"layer": d.get_id(), "image": d.get_image().get_id(),
           "dx": dx, "dy": dy, "axis": axis}
"""


# --- impl functions (module-level for direct testing) ----------------------
def _create_layer(ctx, name, width=None, height=None, opacity=100.0,
                  parent=None, position=0, image=None):
    return ctx.run(_CREATE_CODE,
                   args={"name": name, "width": width, "height": height,
                         "opacity": opacity, "parent": parent,
                         "position": position, "image": image},
                   image=image, undo_group=True).to_dict()


def _layer_from_file(ctx, path, name=None, parent=None, position=0, image=None):
    return ctx.run(_FROM_FILE_CODE,
                   args={"path": path, "name": name, "parent": parent,
                         "position": position, "image": image},
                   image=image, undo_group=True).to_dict()


def _duplicate_layer(ctx, layer=None, name=None, image=None):
    return ctx.run(_DUPLICATE_CODE,
                   args={"layer": layer, "name": name, "image": image},
                   image=image, undo_group=True).to_dict()


def _delete_layer(ctx, layer=None, image=None):
    return ctx.run(_DELETE_CODE,
                   args={"layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _reorder_layer(ctx, layer=None, position=0, parent=None, image=None):
    return ctx.run(_REORDER_CODE,
                   args={"layer": layer, "position": position,
                         "parent": parent, "image": image},
                   image=image, undo_group=True).to_dict()


def _set_opacity(ctx, opacity, layer=None, image=None):
    return ctx.run(_OPACITY_CODE,
                   args={"opacity": opacity, "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _set_blend_mode(ctx, mode, layer=None, image=None):
    return ctx.run(_BLEND_CODE,
                   args={"mode": mode, "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _set_visible(ctx, visible, layer=None, image=None):
    return ctx.run(_VISIBLE_CODE,
                   args={"visible": visible, "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _rename_layer(ctx, name, layer=None, image=None):
    return ctx.run(_RENAME_CODE,
                   args={"name": name, "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _merge_down(ctx, layer=None, image=None):
    return ctx.run(_MERGE_DOWN_CODE,
                   args={"layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _flatten(ctx, image=None):
    return ctx.run(_FLATTEN_CODE, args={"image": image},
                   image=image, undo_group=True).to_dict()


def _group_create(ctx, name=None, parent=None, position=0, image=None):
    return ctx.run(_GROUP_CREATE_CODE,
                   args={"name": name, "parent": parent,
                         "position": position, "image": image},
                   image=image, undo_group=True).to_dict()


def _group_add(ctx, layer=None, group=None, position=0, image=None):
    return ctx.run(_GROUP_ADD_CODE,
                   args={"layer": layer, "group": group,
                         "position": position, "image": image},
                   image=image, undo_group=True).to_dict()


def _group_ungroup(ctx, group=None, image=None):
    return ctx.run(_GROUP_UNGROUP_CODE,
                   args={"group": group, "image": image},
                   image=image, undo_group=True).to_dict()


def _move_layer(ctx, x, y, layer=None, image=None):
    return ctx.run(_MOVE_CODE,
                   args={"x": x, "y": y, "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _offset_layer(ctx, dx=0, dy=0, layer=None, image=None):
    return ctx.run(_OFFSET_CODE,
                   args={"dx": dx, "dy": dy, "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _offset_content(ctx, dx=0, dy=0, wrap=True, fill="transparent", color=None,
                    layer=None, image=None):
    return ctx.run(_OFFSET_CONTENT_CODE,
                   args={"dx": dx, "dy": dy, "wrap": wrap, "fill": fill,
                         "color": color, "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def _seam_check(ctx, axis="both", layer=None, image=None):
    return ctx.run(_SEAM_CHECK_CODE,
                   args={"axis": axis, "layer": layer, "image": image},
                   image=image, undo_group=True).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="create_layer")
    def create_layer(name: str, width: int | None = None, height: int | None = None,
                     opacity: float = 100.0, parent: int | str | None = None,
                     position: int = 0, image: int | str | None = None) -> dict:
        """Create a new transparent RGBA layer (default sized to the image) and
        insert it at `position` (0=top) under optional `parent` group."""
        return _create_layer(ctx, name, width, height, opacity, parent, position, image)

    @mcp.tool(name="layer_from_file")
    def layer_from_file(path: str, name: str | None = None,
                        parent: int | str | None = None, position: int = 0,
                        image: int | str | None = None) -> dict:
        """Load an image file as a new layer in the image and insert it (top by
        default). Optional `name`/`parent`/`position`."""
        return _layer_from_file(ctx, path, name, parent, position, image)

    @mcp.tool(name="duplicate_layer")
    def duplicate_layer(layer: int | str | None = None, name: str | None = None,
                        image: int | str | None = None) -> dict:
        """Duplicate a layer; the copy is inserted just above the original.
        `layer` = id/name, or omit for the active layer."""
        return _duplicate_layer(ctx, layer, name, image)

    @mcp.tool(name="delete_layer")
    def delete_layer(layer: int | str | None = None,
                     image: int | str | None = None) -> dict:
        """Remove a layer from the image. `layer` = id/name, or omit for active."""
        return _delete_layer(ctx, layer, image)

    @mcp.tool(name="reorder_layer")
    def reorder_layer(layer: int | str | None = None, position: int = 0,
                      parent: int | str | None = None,
                      image: int | str | None = None) -> dict:
        """Move a layer to absolute `position` (0=top) within its parent, or into
        a different `parent` group when given."""
        return _reorder_layer(ctx, layer, position, parent, image)

    @mcp.tool(name="set_opacity")
    def set_opacity(opacity: float, layer: int | str | None = None,
                    image: int | str | None = None) -> dict:
        """Set a layer's opacity (0-100 float)."""
        return _set_opacity(ctx, opacity, layer, image)

    @mcp.tool(name="set_blend_mode")
    def set_blend_mode(mode: str, layer: int | str | None = None,
                       image: int | str | None = None) -> dict:
        """Set a layer's blend mode (NORMAL, MULTIPLY, SCREEN, OVERLAY, ...).
        Case/sep insensitive; unknown names fall back to NORMAL."""
        return _set_blend_mode(ctx, mode, layer, image)

    @mcp.tool(name="set_visible")
    def set_visible(visible: bool, layer: int | str | None = None,
                    image: int | str | None = None) -> dict:
        """Show/hide a layer."""
        return _set_visible(ctx, visible, layer, image)

    @mcp.tool(name="rename_layer")
    def rename_layer(name: str, layer: int | str | None = None,
                     image: int | str | None = None) -> dict:
        """Rename a layer."""
        return _rename_layer(ctx, name, layer, image)

    @mcp.tool(name="merge_down")
    def merge_down(layer: int | str | None = None,
                   image: int | str | None = None) -> dict:
        """Merge a layer down into the one below it (EXPAND_AS_NECESSARY).
        Destructive. Returns the resulting merged layer."""
        return _merge_down(ctx, layer, image)

    @mcp.tool(name="flatten")
    def flatten(image: int | str | None = None) -> dict:
        """Flatten the image to a single layer. Destructive."""
        return _flatten(ctx, image)

    @mcp.tool(name="group_create")
    def group_create(name: str | None = None, parent: int | str | None = None,
                     position: int = 0, image: int | str | None = None) -> dict:
        """Create an empty layer group, inserted at `position` (0=top) under
        optional `parent`."""
        return _group_create(ctx, name, parent, position, image)

    @mcp.tool(name="group_add")
    def group_add(layer: int | str | None = None, group: int | str | None = None,
                  position: int = 0, image: int | str | None = None) -> dict:
        """Move an existing layer into a layer group at `position` (0=top)."""
        return _group_add(ctx, layer, group, position, image)

    @mcp.tool(name="group_ungroup")
    def group_ungroup(group: int | str | None = None,
                      image: int | str | None = None) -> dict:
        """Dissolve a layer group: lift its children to the group's parent (in
        order) and remove the empty group. Destructive to the group structure."""
        return _group_ungroup(ctx, group, image)

    @mcp.tool(name="move_layer")
    def move_layer(x: int, y: int, layer: int | str | None = None,
                   image: int | str | None = None) -> dict:
        """Move a layer to absolute canvas offsets (x, y)."""
        return _move_layer(ctx, x, y, layer, image)

    @mcp.tool(name="offset_layer")
    def offset_layer(dx: int = 0, dy: int = 0, layer: int | str | None = None,
                     image: int | str | None = None) -> dict:
        """Offset a layer by a relative delta (dx, dy) from its current position."""
        return _offset_layer(ctx, dx, dy, layer, image)

    @mcp.tool(name="offset_content")
    def offset_content(dx: int = 0, dy: int = 0, wrap: bool = True,
                       fill: str = "transparent", color: str | None = None,
                       layer: int | str | None = None,
                       image: int | str | None = None) -> dict:
        """Roll a layer's PIXEL CONTENT by (dx, dy) — distinct from offset_layer, which
        repositions the layer on the canvas. wrap=True wraps edges around (use it to
        seam-check a tileable texture); wrap=False fills the vacated strip per
        fill: transparent|color (color uses the `color` name/hex, default clear)."""
        return _offset_content(ctx, dx, dy, wrap, fill, color, layer, image)

    @mcp.tool(name="seam_check")
    def seam_check(axis: str = "both", layer: int | str | None = None,
                   image: int | str | None = None) -> dict:
        """Wrap-offset a layer's content by half its size so a tileable texture's seams
        land in the middle for inspection (pair with get_bitmap). axis: both|x|y.
        Destructive — undo to revert (re-running the same axis restores only when that
        dimension is even; for odd sizes floor(w/2) applied twice lands 1px off)."""
        return _seam_check(ctx, axis, layer, image)
