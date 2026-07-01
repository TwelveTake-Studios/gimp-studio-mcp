"""Group D — Masks & alpha (core to DTF/cutout work).

Tools build a fixed code template + pass params as a data `args` payload
(injection-safe) to the bridge's exec op, and read structured `_result`. Impl
functions are module-level so they can be unit-tested against a bridge directly.

Tools:
  - add_mask (white/black/alpha/alpha-transfer/selection/copy/channel)
  - apply_mask           [destructive]  (merge the layer mask into pixels)
  - remove_mask          [destructive]  (discard the layer mask)
  - add_alpha, lock_alpha
  - luminance_to_alpha   [destructive]  (luminance -> transparency, e.g. white-on-black)
  - color_to_alpha       [destructive]  (knockout; gegl:color-to-alpha, no plug-in)
  - threshold_alpha      [destructive]  (binarize alpha for a clean print edge)
"""
from __future__ import annotations

# add_mask: create a layer mask of the requested kind and attach it. Read-only of
# pixels, but it adds an item -> wrap as undo for clean rollback.
# Verified GIMP 3.0.4 (create_mask(self, mask_type) -> Gimp.LayerMask):
#   WHITE / BLACK / ALPHA / ALPHA_TRANSFER / SELECTION / COPY all work with the
#   single mask_type arg. CHANNEL is the lone exception: create_mask(CHANNEL)
#   returns None unless the image has a SELECTED channel to copy from (there is
#   no source-channel parameter on the 3.0.4 binding). We honour an explicit
#   `channel` arg (id/name) by selecting it first; otherwise we use whatever
#   channel is already selected; if neither exists CHANNEL degrades gracefully.
_ADD_MASK_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
kind = (args.get("mask_type") or "white").upper().replace("-", "_")
try:
    mask_type = getattr(Gimp.AddMaskType, kind)
except AttributeError:
    kind = "WHITE"
    mask_type = Gimp.AddMaskType.WHITE
# ALPHA-based masks require the layer to have an alpha channel.
if kind in ("ALPHA", "ALPHA_TRANSFER") and not layer.has_alpha():
    layer.add_alpha()
_note = None
if kind == "CHANNEL":
    # Resolve a source channel: explicit arg, else the already-selected channel.
    src = None
    chan_spec = args.get("channel")
    channels = img.get_channels()
    if chan_spec is not None:
        for ch in channels:
            if (str(chan_spec).isdigit() and ch.get_id() == int(chan_spec)) \\
                    or ch.get_name() == chan_spec:
                src = ch
                break
        if src is None:
            raise ValueError("no channel matching %r in image %s"
                             % (chan_spec, img.get_id()))
    else:
        sel = img.get_selected_channels()
        if sel:
            src = sel[0]
    if src is None:
        # Nothing to copy from -> can't build a CHANNEL mask via 3.0.4 scripting.
        _result = {"image": img.get_id(), "layer": layer.get_id(),
                   "mask": None, "mask_type": kind, "supported": False,
                   "note": ("CHANNEL mask needs a source channel: pass `channel` "
                            "(id/name) or select a channel first")}
    else:
        img.set_selected_channels([src])
        mask = layer.create_mask(mask_type)
        if mask is None:
            raise RuntimeError("create_mask(CHANNEL) returned None despite a "
                               "selected channel")
        layer.add_mask(mask)
        _result = {"image": img.get_id(), "layer": layer.get_id(),
                   "mask": mask.get_id(), "mask_type": kind,
                   "source_channel": src.get_id()}
else:
    mask = layer.create_mask(mask_type)
    if mask is None:
        raise RuntimeError("create_mask(%s) returned None" % kind)
    layer.add_mask(mask)
    _result = {"image": img.get_id(), "layer": layer.get_id(),
               "mask": mask.get_id(), "mask_type": kind}
"""

# apply_mask / remove_mask both call layer.remove_mask(mode); APPLY merges the
# mask into the layer's alpha, DISCARD throws it away.
_REMOVE_MASK_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
mode_name = "APPLY" if args.get("apply") else "DISCARD"
try:
    mode = getattr(Gimp.MaskApplyMode, mode_name)
except AttributeError:
    mode = Gimp.MaskApplyMode.APPLY if args.get("apply") else Gimp.MaskApplyMode.DISCARD
had_mask = layer.get_mask() is not None
if had_mask:
    layer.remove_mask(mode)
_result = {"image": img.get_id(), "layer": layer.get_id(),
           "mode": mode_name, "had_mask": bool(had_mask)}
"""

# add_alpha: ensure the drawable has an alpha channel (no-op if it already does).
_ADD_ALPHA_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
already = drawable.has_alpha()
compat.ensure_alpha(drawable)
_result = {"image": img.get_id(), "layer": drawable.get_id(),
           "had_alpha": bool(already), "has_alpha": bool(drawable.has_alpha())}
"""

# lock_alpha: protect/unprotect the alpha channel from painting.
_LOCK_ALPHA_CODE = """
img = find_image(args.get("image"))
layer = find_drawable(args.get("image"), args.get("layer"))
locked = bool(args.get("locked", True))
layer.set_lock_alpha(locked)
_result = {"image": img.get_id(), "layer": layer.get_id(),
           "lock_alpha": bool(layer.get_lock_alpha())}
"""

# luminance_to_alpha: a TRUE luminance -> transparency map (not a single-color
# knockout). For every pixel new_alpha = old_alpha * luminance (invert=False ->
# bright is opaque, dark is transparent: the white-art-on-black case) or
# old_alpha * (1 - luminance) (invert=True -> bright becomes transparent).
#
# Done over the layer's GEGL buffer in one bulk get/set using the gamma-encoded
# "R'G'B'A u8" format, so luminance is perceptual (matches GIMP's own result).
# GIMP 3.0.4 has no gegl:luminance-to-alpha / gegl:value-to-alpha op (both return
# NULL from DrawableFilter.new on this build), so we compute it directly — exact
# and dependency-free, fast enough for any reasonable layer.
_LUMINANCE_TO_ALPHA_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
compat.ensure_alpha(drawable)
invert = bool(args.get("invert"))
W = drawable.get_width(); H = drawable.get_height()
buf = drawable.get_buffer()
rect = Gegl.Rectangle.new(0, 0, W, H)
fmt = "R'G'B'A u8"
data = bytearray(buf.get(rect, 1.0, fmt, Gegl.AbyssPolicy.CLAMP))
# Rec.709 luma weights on the gamma-encoded channels (perceptual).
for i in range(0, len(data), 4):
    r = data[i]; g = data[i + 1]; b = data[i + 2]; a = data[i + 3]
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    factor = (1.0 - lum) if invert else lum
    na = int(round(a * factor))
    data[i + 3] = 0 if na < 0 else (255 if na > 255 else na)
buf.set(rect, fmt, list(data))
buf.flush()
drawable.update(0, 0, W, H)
_result = {"image": img.get_id(), "layer": drawable.get_id(),
           "method": "buffer-luma", "invert": invert,
           "knocked_out": ("bright" if invert else "dark")}
"""

# color_to_alpha: knock out a target color to transparency (compat owns the
# gegl:color-to-alpha quirk + alpha-channel guard).
_COLOR_TO_ALPHA_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
compat.color_to_alpha(
    img, drawable, target=args.get("color", "white"),
    transparency_threshold=float(args.get("transparency_threshold", 0.0)),
    opacity_threshold=float(args.get("opacity_threshold", 1.0)),
)
_result = {"image": img.get_id(), "layer": drawable.get_id(),
           "color": args.get("color", "white")}
"""

# threshold_alpha: binarize the alpha channel at a cutoff (alpha < value -> 0,
# else -> fully opaque) for a clean print edge.
#
# GIMP 3.0.4 has NO gegl:threshold-alpha op (DrawableFilter.new returns NULL ->
# TypeError "constructor returned NULL"). We binarize directly over the layer's
# GEGL buffer: exact, dependency-free, and identical to what the op would do.
# `value` is a 0..1 fraction of full opacity.
_THRESHOLD_ALPHA_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
compat.ensure_alpha(drawable)
value = float(args.get("value", 0.5))
cut = int(round(max(0.0, min(1.0, value)) * 255))
W = drawable.get_width(); H = drawable.get_height()
buf = drawable.get_buffer()
rect = Gegl.Rectangle.new(0, 0, W, H)
fmt = "R'G'B'A u8"
data = bytearray(buf.get(rect, 1.0, fmt, Gegl.AbyssPolicy.CLAMP))
for i in range(3, len(data), 4):
    data[i] = 0 if data[i] < cut else 255
buf.set(rect, fmt, list(data))
buf.flush()
drawable.update(0, 0, W, H)
_result = {"image": img.get_id(), "layer": drawable.get_id(),
           "value": value, "cut": cut, "applied": "buffer-threshold"}
"""


def _add_mask(ctx, layer=None, mask_type="white", image=None, channel=None):
    return ctx.run(_ADD_MASK_CODE,
                   args={"image": image, "layer": layer, "mask_type": mask_type,
                         "channel": channel},
                   image=image, undo_group=True).to_dict()


def _apply_mask(ctx, layer=None, image=None):
    return ctx.run(_REMOVE_MASK_CODE,
                   args={"image": image, "layer": layer, "apply": True},
                   image=image, undo_group=True).to_dict()


def _remove_mask(ctx, layer=None, image=None):
    return ctx.run(_REMOVE_MASK_CODE,
                   args={"image": image, "layer": layer, "apply": False},
                   image=image, undo_group=True).to_dict()


def _add_alpha(ctx, layer=None, image=None):
    return ctx.run(_ADD_ALPHA_CODE,
                   args={"image": image, "layer": layer},
                   image=image, undo_group=True).to_dict()


def _lock_alpha(ctx, layer=None, locked=True, image=None):
    return ctx.run(_LOCK_ALPHA_CODE,
                   args={"image": image, "layer": layer, "locked": locked},
                   image=image, undo_group=True).to_dict()


def _luminance_to_alpha(ctx, layer=None, invert=False, image=None):
    return ctx.run(_LUMINANCE_TO_ALPHA_CODE,
                   args={"image": image, "layer": layer, "invert": invert},
                   image=image, undo_group=True).to_dict()


def _color_to_alpha(ctx, color="white", layer=None,
                    transparency_threshold=0.0, opacity_threshold=1.0, image=None):
    return ctx.run(_COLOR_TO_ALPHA_CODE,
                   args={"image": image, "layer": layer, "color": color,
                         "transparency_threshold": transparency_threshold,
                         "opacity_threshold": opacity_threshold},
                   image=image, undo_group=True).to_dict()


def _threshold_alpha(ctx, value=0.5, layer=None, image=None):
    return ctx.run(_THRESHOLD_ALPHA_CODE,
                   args={"image": image, "layer": layer, "value": value},
                   image=image, undo_group=True).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="add_mask")
    def add_mask(layer: int | str | None = None, mask_type: str = "white",
                 image: int | str | None = None,
                 channel: int | str | None = None) -> dict:
        """Add a layer mask. mask_type: white|black|alpha|alpha-transfer|
        selection|copy|channel. Omit `layer` for the selected/top layer.
        For mask_type='channel' pass `channel` (id/name) as the source, or have a
        channel already selected; with neither, channel masks return
        supported=False (GIMP 3.0.4 create_mask(CHANNEL) needs a selected
        channel and exposes no source-channel parameter)."""
        return _add_mask(ctx, layer, mask_type, image, channel)

    @mcp.tool(name="apply_mask")
    def apply_mask(layer: int | str | None = None,
                   image: int | str | None = None) -> dict:
        """Merge the layer's mask into its alpha (destructive). No-op if no mask."""
        return _apply_mask(ctx, layer, image)

    @mcp.tool(name="remove_mask")
    def remove_mask(layer: int | str | None = None,
                    image: int | str | None = None) -> dict:
        """Discard the layer's mask without applying it (destructive)."""
        return _remove_mask(ctx, layer, image)

    @mcp.tool(name="add_alpha")
    def add_alpha(layer: int | str | None = None,
                  image: int | str | None = None) -> dict:
        """Add an alpha channel to the layer if it lacks one."""
        return _add_alpha(ctx, layer, image)

    @mcp.tool(name="lock_alpha")
    def lock_alpha(locked: bool = True, layer: int | str | None = None,
                   image: int | str | None = None) -> dict:
        """Lock (or unlock) the layer's alpha channel against painting."""
        return _lock_alpha(ctx, layer, locked, image)

    @mcp.tool(name="luminance_to_alpha")
    def luminance_to_alpha(invert: bool = False, layer: int | str | None = None,
                           image: int | str | None = None) -> dict:
        """Map luminance to transparency per-pixel (destructive): default makes
        dark pixels transparent and bright pixels opaque (white-art-on-black);
        invert=True makes bright pixels transparent (knock out white/highlights).
        Computed directly over the layer buffer (no dedicated GEGL op exists in
        GIMP 3.0.4)."""
        return _luminance_to_alpha(ctx, layer, invert, image)

    @mcp.tool(name="color_to_alpha")
    def color_to_alpha(color: str = "white", layer: int | str | None = None,
                       transparency_threshold: float = 0.0,
                       opacity_threshold: float = 1.0,
                       image: int | str | None = None) -> dict:
        """Knock out a color to transparency (destructive). color: name/#rrggbb."""
        return _color_to_alpha(ctx, color, layer,
                               transparency_threshold, opacity_threshold, image)

    @mcp.tool(name="threshold_alpha")
    def threshold_alpha(value: float = 0.5, layer: int | str | None = None,
                        image: int | str | None = None) -> dict:
        """Binarize the alpha channel at `value` (0..1) for a clean print edge
        (destructive)."""
        return _threshold_alpha(ctx, value, layer, image)
