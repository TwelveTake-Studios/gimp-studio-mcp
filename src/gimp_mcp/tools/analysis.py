"""Group L — Analysis / introspection (read-only, vision-first).

All tools here are READ-ONLY (undo_group=False). They build a fixed code template
+ pass params as a data `args` payload (injection-safe) to the bridge's exec op,
and read structured `_result`. Impl functions are module-level for unit-testing.

Tools:
  - get_bitmap     THE vision tool — base64 of a scaled/cropped/composited copy
  - read_region    structured 2D pixel grid (size-capped)
  - color_at       single-pixel RGBA
  - histogram      channel statistics (perceptual space, matches read_pixel/levels)
  - list_gegl_ops, describe_op       GEGL op introspection
  - list_procedures                  PDB procedure enumeration
  - list_fonts/brushes/patterns/gradients/palettes   resource enumeration
"""
from __future__ import annotations

# Render a SCALED/CROPPED/COMPOSITED COPY of the image so the agent can SEE it.
# Works on a duplicate (the user's open image is never mutated). The server wraps
# the result as VIEWABLE MCP image content. Two ceilings keep it from overflowing
# the model's context: max_dim (longest side, clamped 1..4096) AND max_bytes (the
# encoded size) — if the render exceeds max_bytes we auto-step the longest side
# down and re-render. save_to=<path> writes the render to disk instead of inlining
# it (the escape hatch for big previews: returns the path, no base64).
_BITMAP_CODE = """
import os, tempfile, base64
img = find_image(args.get("image"))
src = img.duplicate()
try:
    region = args.get("region")
    if region:
        rx, ry, rw, rh = region
        src.crop(int(rw), int(rh), int(rx), int(ry))
    bg = args.get("background")
    if bg:
        # Flatten over an opaque background colour (drops alpha). Guard the push so a
        # failure can't leak an unbalanced push / changed bg into the live GIMP context.
        Gimp.context_push()
        try:
            Gimp.context_set_background(compat.color(bg))
            src.flatten()
        finally:
            Gimp.context_pop()
    base_w, base_h = src.get_width(), src.get_height()

    req_dim = int(args.get("max_dim") or 1024)
    max_dim = max(1, min(4096, req_dim))          # never upscale past 4096
    fmt = (args.get("fmt") or "png").lower()
    save_to = args.get("save_to")

    def _save_scaled(target_dim, out_path):
        # Always re-scale a FRESH copy of the (cropped/flattened) source so the
        # byte-budget retries never compound scaling artefacts.
        d = src.duplicate()
        try:
            cw, ch = d.get_width(), d.get_height()
            longest = max(cw, ch)
            if longest > target_dim:
                s = float(target_dim) / float(longest)
                d.scale(max(1, int(round(cw * s))), max(1, int(round(ch * s))))
            ow, oh = d.get_width(), d.get_height()
            Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, d,
                           Gio.File.new_for_path(out_path))
            return ow, oh
        finally:
            d.delete()

    if save_to:
        # GIMP picks the encoder from the path's extension; never inline base64.
        out_fmt = os.path.splitext(save_to)[1].lstrip(".").lower() or fmt
        ow, oh = _save_scaled(max_dim, save_to)
        _result = {
            "inline": False, "saved_to": save_to, "fmt": out_fmt,
            "width": ow, "height": oh, "bytes": os.path.getsize(save_to),
            "source_size": [base_w, base_h],
            "max_dim_requested": req_dim, "max_dim_used": max(ow, oh),
            "downscaled_to_fit": False,
        }
    else:
        max_bytes = max(1, int(args.get("max_bytes") or 4000000))
        target = max_dim
        ow = oh = 0
        data = b""
        downscaled = False
        attempts = 0
        while True:
            fd, path = tempfile.mkstemp(suffix="." + fmt, prefix="gimp-mcp-bitmap-")
            os.close(fd)
            try:
                ow, oh = _save_scaled(target, path)
                with open(path, "rb") as fh:
                    data = fh.read()
            finally:
                try:
                    os.remove(path)
                except Exception:
                    pass
            longest_out = max(ow, oh)
            if len(data) <= max_bytes or longest_out <= 64 or attempts >= 5:
                break
            # Step the longest side down ~proportional to sqrt(budget/size).
            ratio = (float(max_bytes) / float(len(data))) ** 0.5
            nxt = int(longest_out * ratio * 0.9)
            if nxt >= longest_out:
                nxt = int(longest_out * 0.8)
            target = max(64, nxt)
            downscaled = True
            attempts += 1
        _result = {
            "inline": True, "fmt": fmt,
            "width": ow, "height": oh, "bytes": len(data),
            "base64": base64.b64encode(data).decode("ascii"),
            "source_size": [base_w, base_h],
            "max_dim_requested": req_dim, "max_dim_used": max(ow, oh),
            "downscaled_to_fit": downscaled, "max_bytes": max_bytes,
        }
finally:
    src.delete()
"""

# Structured pixel grid — capped so we never blow the frame budget.
_REGION_CODE = """
draw = find_drawable(args.get("image"), args.get("layer"))
x = int(args["x"]); y = int(args["y"])
w = int(args["w"]); h = int(args["h"])
if w <= 0 or h <= 0:
    raise ValueError("w and h must be positive")
if w * h > 4096:
    raise ValueError("region too large: w*h=%d exceeds 4096 (use get_bitmap)" % (w * h))
rows = []
for py in range(y, y + h):
    row = []
    for px in range(x, x + w):
        row.append(list(compat.read_pixel(draw, px, py)))
    rows.append(row)
_result = {"width": w, "height": h, "pixels": rows}
"""

_COLOR_AT_CODE = """
draw = find_drawable(args.get("image"), args.get("layer"))
rgba = list(compat.read_pixel(draw, int(args["x"]), int(args["y"])))
_result = {"rgba": rgba}
"""

# Channel statistics. GIMP's native PDB histogram reports its value axis on a
# gamma-RE-ENCODED curve: a solid perceptual-0.58 gray reads mean ~0.78 (200/255),
# which does NOT match read_pixel / color_at / get_bitmap / levels (all perceptual
# 0-255). So by default we compute the stats ourselves from the drawable's own
# pixels in that SAME perceptual space — histogram numbers now line up with what
# those tools show and with what levels/curves consume (÷255 for their 0-1 inputs).
# space='gimp' returns the old native PDB behaviour as an opt-out.
_HISTOGRAM_CODE = """
import collections
draw = find_drawable(args.get("image"), args.get("layer"))
ch_name = (args.get("channel") or "value").upper().replace("-", "_")
space = (args.get("space") or "perceptual").lower()

if space == "gimp":
    # GIMP 3.0: returns (ok?, mean, std_dev, median, pixels, count, percentile) on
    # the gamma-re-encoded value axis (kept as an opt-out / parity with GIMP's dialog).
    try:
        ch = getattr(Gimp.HistogramChannel, ch_name)
    except AttributeError:
        ch = Gimp.HistogramChannel.VALUE
        ch_name = "VALUE"                    # label must match the data actually computed
    vals = list(draw.histogram(ch, 0.0, 1.0))
    if len(vals) == 7:                       # some bindings prepend a success bool
        vals = vals[1:]
    keys = ["mean", "std_dev", "median", "pixels", "count", "percentile"]
    stats = {k: vals[i] for i, k in enumerate(keys) if i < len(vals)}
    stats["channel"] = ch_name
    stats["space"] = "gimp"
    _result = stats
else:
    # Perceptual stats from the drawable's OWN pixels, same 0-255 space as read_pixel.
    # Pure-Python (GIMP's interpreter has no numpy); very large drawables are sampled
    # at reduced resolution so the bin loop stays fast + bounded in memory.
    src = draw
    scratch = None
    sampled = False
    w, h = draw.get_width(), draw.get_height()
    CAP = 1000000
    if w * h > CAP:
        # Downscale a copy so the pure-Python bin loop stays bounded. POINT-sample
        # (interpolation NONE) so every sampled value is a REAL source pixel — an
        # interpolated resample would invent min/max values that exist nowhere in the
        # source and disagree with color_at. Reported stats are from the sample, so
        # min/max may miss a lone isolated extreme (flagged via "sampled": True).
        s = (float(CAP) / float(w * h)) ** 0.5
        sw, sh = max(1, int(w * s)), max(1, int(h * s))
        gimg = draw.get_image()
        scratch = Gimp.Image.new_with_precision(w, h, gimg.get_base_type(),
                                                gimg.get_precision())
        cp = Gimp.Layer.new_from_drawable(draw, scratch)
        scratch.insert_layer(cp, None, 0)
        Gimp.context_push()
        try:
            Gimp.context_set_interpolation(Gimp.InterpolationType.NONE)
            scratch.scale(sw, sh)
        finally:
            Gimp.context_pop()
        src = scratch.get_layers()[0]
        w, h = src.get_width(), src.get_height()
        sampled = True
    try:
        n = w * h
        buf = src.get_buffer()
        rect = Gegl.Rectangle.new(0, 0, w, h)
        # Pick the babl format whose bytes match read_pixel's perceptual value. The
        # no-prime 'RGBA u8' matches on GIMP 3.x ('R'G'B'A u8' gives the re-encoded
        # curve); auto-select so a future babl/version flip can't silently mislabel.
        ref = list(compat.read_pixel(src, 0, 0))
        data = None
        for fmt in ("RGBA u8", "R'G'B'A u8"):
            d = bytes(buf.get(rect, 1.0, fmt, Gegl.AbyssPolicy.CLAMP))
            if len(d) >= 4 and all(abs(d[i] - ref[i]) <= 1 for i in range(3)):
                data = d
                break
        if data is None:                     # corner was atypical (abyss/edge): take one
            data = bytes(buf.get(rect, 1.0, "RGBA u8", Gegl.AbyssPolicy.CLAMP))
        if ch_name in ("RED", "R"):
            seq = data[0::4]
        elif ch_name in ("GREEN", "G"):
            seq = data[1::4]
        elif ch_name in ("BLUE", "B"):
            seq = data[2::4]
        elif ch_name == "ALPHA":
            seq = data[3::4]
        elif ch_name == "LUMINANCE":
            seq = bytes(int(0.2126 * data[i] + 0.7152 * data[i + 1]
                            + 0.0722 * data[i + 2] + 0.5) for i in range(0, 4 * n, 4))
        else:                                # VALUE = max(R,G,B) (GIMP HISTOGRAM_VALUE)
            ch_name = "VALUE"
            seq = bytes(max(data[i], data[i + 1], data[i + 2]) for i in range(0, 4 * n, 4))
        counts = collections.Counter(seq)
        s1 = sum(v * c for v, c in counts.items())
        mean = s1 / n if n else 0.0
        s2 = sum(c * (v - mean) ** 2 for v, c in counts.items())
        std = (s2 / n) ** 0.5 if n else 0.0
        cum, median, half = 0, 0, n / 2.0
        for v in range(256):
            cum += counts.get(v, 0)
            if cum >= half:
                median = v
                break
        _result = {
            "mean": round(mean, 4), "std_dev": round(std, 4), "median": float(median),
            "min": float(min(counts)) if counts else 0.0,
            "max": float(max(counts)) if counts else 0.0,
            "pixels": n, "count": n, "percentile": 1.0,
            "channel": ch_name, "space": "perceptual", "sampled": sampled,
        }
    finally:
        if scratch is not None:
            scratch.delete()
"""

_LIST_GEGL_CODE = """
Gegl.init(None)  # idempotent; GEGL's op registry is empty until initialized
ops = list(Gegl.list_operations())
flt = args.get("filter")
if flt:
    ops = [o for o in ops if flt in o]
ops.sort()
_result = {"count": len(ops), "ops": ops}
"""

_DESCRIBE_OP_CODE = """
Gegl.init(None)  # idempotent; needed before the op registry is queryable
op = args["op"]
info = {"op": op}
# Op metadata via Gegl.Operation.get_key(op, key) (GIMP 3.0.4 / GEGL 0.4).
for key in ("title", "categories", "description"):
    try:
        info[key] = Gegl.Operation.get_key(op, key)
    except Exception:
        info[key] = None
# Properties via Gegl.Operation.list_properties(op) -> list[GParamSpec].
props = []
try:
    for spec in Gegl.Operation.list_properties(op):
        props.append({
            "name": spec.get_name(),
            "type": GObject.type_name(spec.value_type) if spec.value_type else None,
            "blurb": spec.get_blurb(),
        })
except Exception as e:
    info["properties_error"] = str(e)
info["properties"] = props
_result = info
"""

_LIST_PROCEDURES_CODE = """
# GIMP 3.0.4: Gimp.PDB.query_procedures takes 8 string match-filters
# (name, blurb, help, authors, copyright, date, proc-type, return-types) and
# returns a flat list[str] of matching procedure names. (9 incl. bound self.)
names = list(pdb.query_procedures("", "", "", "", "", "", "", ""))
flt = args.get("filter")
if flt:
    names = [n for n in names if flt in n]
names = sorted(set(str(n) for n in names))
_result = {"count": len(names), "procedures": names}
"""


# Resource enumerators all share the same shape: Gimp.<kind>_get_list(filter).
_LIST_RESOURCE_CODE = """
kind = args["kind"]
flt = args.get("filter") or ""
fn = getattr(Gimp, kind + "_get_list")
res = fn(flt)
# GIMP 3.0 may return [resources] or (count, [resources]); normalize.
vals = list(res)
if vals and isinstance(vals[0], int) and len(vals) >= 2 and isinstance(vals[-1], (list, tuple)):
    vals = list(vals[-1])
elif len(vals) == 1 and isinstance(vals[0], (list, tuple)):
    vals = list(vals[0])
names = []
for v in vals:
    get = getattr(v, "get_name", None)
    names.append(get() if callable(get) else str(v))
names = sorted(set(names))
_result = {"count": len(names), "names": names}
"""


def _get_bitmap(ctx, image=None, max_dim=1024, region=None, background=None,
                fmt="png", save_to=None, max_bytes=4_000_000):
    return ctx.run(_BITMAP_CODE,
                   args={"image": image, "max_dim": max_dim, "region": region,
                         "background": background, "fmt": fmt,
                         "save_to": save_to, "max_bytes": max_bytes},
                   image=image, undo_group=False).to_dict()


# Metadata keys lifted from the bridge result onto the MCP response (both modes).
_BITMAP_META_KEYS = ("fmt", "width", "height", "bytes", "source_size",
                     "max_dim_requested", "max_dim_used", "downscaled_to_fit",
                     "inline", "saved_to", "max_bytes")


def _bitmap_content(envelope: dict):
    """Turn a get_bitmap bridge envelope into MCP tool output.

    The reason this tool exists is so the model can SEE the picture — so the inline
    path returns ``[Image(...), metadata]`` (a viewable image content block PLUS the
    size numbers), not a base64 string buried in a text field (the #1 bug). The
    ``save_to`` path and any error return a plain dict (no inline image).
    """
    if not envelope.get("ok"):
        return envelope  # surface the structured error envelope unchanged
    r = envelope.get("result") or {}
    meta = {k: r[k] for k in _BITMAP_META_KEYS if k in r}
    if r.get("inline") and r.get("base64"):
        import base64 as _b64

        from mcp.server.fastmcp import Image
        meta["note"] = ("downscaled to fit max_bytes" if r.get("downscaled_to_fit")
                        else "rendered at max_dim")
        return [Image(data=_b64.b64decode(r["base64"]), format=r.get("fmt", "png")),
                meta]
    return meta


def _read_region(ctx, x, y, w, h, image=None, layer=None):
    return ctx.run(_REGION_CODE,
                   args={"image": image, "layer": layer, "x": x, "y": y, "w": w, "h": h},
                   image=image, undo_group=False).to_dict()


def _color_at(ctx, x, y, image=None, layer=None):
    return ctx.run(_COLOR_AT_CODE,
                   args={"image": image, "layer": layer, "x": x, "y": y},
                   image=image, undo_group=False).to_dict()


def _histogram(ctx, channel="value", image=None, layer=None, space="perceptual"):
    return ctx.run(_HISTOGRAM_CODE,
                   args={"image": image, "layer": layer, "channel": channel,
                         "space": space},
                   image=image, undo_group=False).to_dict()


def _list_gegl_ops(ctx, filter=None):
    return ctx.run(_LIST_GEGL_CODE, args={"filter": filter}, undo_group=False).to_dict()


def _describe_op(ctx, op):
    return ctx.run(_DESCRIBE_OP_CODE, args={"op": op}, undo_group=False).to_dict()


def _list_procedures(ctx, filter=None):
    return ctx.run(_LIST_PROCEDURES_CODE, args={"filter": filter}, undo_group=False).to_dict()


def _list_resource(ctx, kind, filter=None):
    return ctx.run(_LIST_RESOURCE_CODE,
                   args={"kind": kind, "filter": filter}, undo_group=False).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="get_bitmap", structured_output=False)
    def get_bitmap(image: int | str | None = None, max_dim: int = 1024,
                   region: list[int] | None = None, background: str | None = None,
                   fmt: str = "png", save_to: str | None = None,
                   max_bytes: int = 4_000_000):
        """THE vision tool: render a scaled/cropped/composited COPY and return it as a
        VIEWABLE image (an MCP image block you can actually see) plus size metadata.
        region=[x,y,w,h] crops first; background (name/hex) flattens over an opaque
        colour (else alpha is kept); the longest side is scaled to <= max_dim (clamped
        1..4096). If the encoded image would exceed max_bytes (~4 MB default) it is
        auto-downscaled to fit your context. Pass save_to=<abs path> to write the
        render to disk and get the path back instead of an inline image — use this for
        previews too large to view inline."""
        return _bitmap_content(
            _get_bitmap(ctx, image, max_dim, region, background, fmt, save_to, max_bytes))

    @mcp.tool(name="read_region")
    def read_region(x: int, y: int, w: int, h: int,
                    image: int | str | None = None,
                    layer: int | str | None = None) -> dict:
        """Structured 2D grid of [r,g,b,a] pixels for a rectangle. Capped at w*h<=4096
        (raises if larger — use get_bitmap for big areas)."""
        return _read_region(ctx, x, y, w, h, image, layer)

    @mcp.tool(name="color_at")
    def color_at(x: int, y: int, image: int | str | None = None,
                 layer: int | str | None = None) -> dict:
        """Sample one pixel: returns {rgba:[r,g,b,a]} (0-255)."""
        return _color_at(ctx, x, y, image, layer)

    @mcp.tool(name="histogram")
    def histogram(channel: str = "value", image: int | str | None = None,
                  layer: int | str | None = None, space: str = "perceptual") -> dict:
        """Channel statistics (value|red|green|blue|alpha|luminance).

        space='perceptual' (default) computes the stats from the drawable's own pixels
        in the SAME 0-255 space as color_at/get_bitmap — and that levels/curves consume
        (divide by 255 for their 0.0-1.0 inputs) — returning mean, std_dev, median, min,
        max (all 0-255) plus pixels/count. space='gimp' returns GIMP's native PDB
        histogram (mean/std_dev/median/percentile, no min/max), whose value axis is
        gamma-re-encoded and does NOT line up with those tools (a perceptual-0.58 gray
        reads ~200/255 there, not 148). Very large drawables are point-sampled at reduced
        resolution (min/max are from the sample, flagged by `sampled`)."""
        return _histogram(ctx, channel, image, layer, space)

    @mcp.tool(name="list_gegl_ops")
    def list_gegl_ops(filter: str | None = None) -> dict:
        """List available GEGL operation names, optionally filtered by substring."""
        return _list_gegl_ops(ctx, filter)

    @mcp.tool(name="describe_op")
    def describe_op(op: str) -> dict:
        """Introspect a GEGL op: categories, description, and its properties."""
        return _describe_op(ctx, op)

    @mcp.tool(name="list_procedures")
    def list_procedures(filter: str | None = None) -> dict:
        """List PDB procedure names, optionally filtered by substring."""
        return _list_procedures(ctx, filter)

    @mcp.tool(name="list_fonts")
    def list_fonts(filter: str | None = None) -> dict:
        """List installed font names, optionally filtered by substring."""
        return _list_resource(ctx, "fonts", filter)

    @mcp.tool(name="list_brushes")
    def list_brushes(filter: str | None = None) -> dict:
        """List brush names, optionally filtered by substring."""
        return _list_resource(ctx, "brushes", filter)

    @mcp.tool(name="list_patterns")
    def list_patterns(filter: str | None = None) -> dict:
        """List pattern names, optionally filtered by substring."""
        return _list_resource(ctx, "patterns", filter)

    @mcp.tool(name="list_gradients")
    def list_gradients(filter: str | None = None) -> dict:
        """List gradient names, optionally filtered by substring."""
        return _list_resource(ctx, "gradients", filter)

    @mcp.tool(name="list_palettes")
    def list_palettes(filter: str | None = None) -> dict:
        """List palette names, optionally filtered by substring."""
        return _list_resource(ctx, "palettes", filter)
