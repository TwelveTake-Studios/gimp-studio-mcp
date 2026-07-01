"""Group H — Color & tone.

All ops are DESTRUCTIVE (edit pixels in place): each passes image=image AND
"image" in args, with undo_group=True so the bridge wraps it as one undo step.
Targets resolve via find_drawable(args.get("image"), args.get("layer")).

In GIMP 3.0 these became Drawable METHODS (not PDB procs); levels/curves work in
0.0-1.0 float space (see api_risks). Impl functions are module-level for testing.

Tools:
  - levels, curves, brightness_contrast
  - hue_saturation, color_balance
  - desaturate (mode), invert, posterize, threshold
  - normalize (auto stretch-contrast; keep_colors preserves hue)
"""
from __future__ import annotations

# Channel enum resolver, shared by levels/curves/threshold.
_CHANNEL_HELPER = """
def _channel(name):
    n = (name or "value").upper().replace("-", "_")
    try:
        return getattr(Gimp.HistogramChannel, n)
    except Exception:
        return Gimp.HistogramChannel.VALUE
"""

_LEVELS_CODE = _CHANNEL_HELPER + """
d = find_drawable(args.get("image"), args.get("layer"))
ch = _channel(args.get("channel"))
d.levels(ch,
         float(args.get("low_in", 0.0)), float(args.get("high_in", 1.0)),
         True,
         float(args.get("gamma", 1.0)),
         float(args.get("low_out", 0.0)), float(args.get("high_out", 1.0)),
         True)
_result = {"layer": d.get_id(), "image": d.get_image().get_id(), "channel": int(ch)}
"""

# curves: points is a flat [x0,y0,x1,y1,...] control-point list (0.0-1.0).
_CURVES_CODE = _CHANNEL_HELPER + """
d = find_drawable(args.get("image"), args.get("layer"))
ch = _channel(args.get("channel"))
pts = [float(v) for v in (args.get("points") or [])]
d.curves_spline(ch, pts)
_result = {"layer": d.get_id(), "image": d.get_image().get_id(), "channel": int(ch), "num_points": len(pts) // 2}
"""

_BRIGHTNESS_CONTRAST_CODE = """
d = find_drawable(args.get("image"), args.get("layer"))
d.brightness_contrast(float(args.get("brightness", 0.0)),
                      float(args.get("contrast", 0.0)))
_result = {"layer": d.get_id(), "image": d.get_image().get_id()}
"""

_HUE_SATURATION_CODE = """
d = find_drawable(args.get("image"), args.get("layer"))
rname = (args.get("range") or "all").upper().replace("-", "_")
try:
    hr = getattr(Gimp.HueRange, rname)
except Exception:
    hr = Gimp.HueRange.ALL
d.hue_saturation(hr,
                 float(args.get("hue", 0.0)),
                 float(args.get("lightness", 0.0)),
                 float(args.get("saturation", 0.0)),
                 float(args.get("overlap", 0.0)))
_result = {"layer": d.get_id(), "image": d.get_image().get_id(), "range": int(hr)}
"""

_COLOR_BALANCE_CODE = """
d = find_drawable(args.get("image"), args.get("layer"))
rname = (args.get("range") or "midtones").upper().replace("-", "_")
try:
    tm = getattr(Gimp.TransferMode, rname)
except Exception:
    tm = Gimp.TransferMode.MIDTONES
d.color_balance(tm, bool(args.get("preserve_lum", True)),
                float(args.get("cyan_red", 0.0)),
                float(args.get("magenta_green", 0.0)),
                float(args.get("yellow_blue", 0.0)))
_result = {"layer": d.get_id(), "image": d.get_image().get_id(), "range": int(tm)}
"""

_DESATURATE_CODE = """
d = find_drawable(args.get("image"), args.get("layer"))
mname = (args.get("mode") or "luminance").upper().replace("-", "_")
try:
    dm = getattr(Gimp.DesaturateMode, mname)
except Exception:
    dm = Gimp.DesaturateMode.LUMINANCE
d.desaturate(dm)
_result = {"layer": d.get_id(), "image": d.get_image().get_id(), "mode": int(dm)}
"""

_INVERT_CODE = """
d = find_drawable(args.get("image"), args.get("layer"))
try:
    d.invert(bool(args.get("linear", False)))
except TypeError:
    d.invert()
_result = {"layer": d.get_id(), "image": d.get_image().get_id()}
"""

_POSTERIZE_CODE = """
d = find_drawable(args.get("image"), args.get("layer"))
d.posterize(int(args["levels"]))
_result = {"layer": d.get_id(), "image": d.get_image().get_id(), "levels": int(args["levels"])}
"""

_THRESHOLD_CODE = """
def _channel(name):
    n = (name or "value").upper().replace("-", "_")
    try:
        return getattr(Gimp.HistogramChannel, n)
    except Exception:
        return Gimp.HistogramChannel.VALUE
d = find_drawable(args.get("image"), args.get("layer"))
ch = _channel(args.get("channel"))
d.threshold(ch, float(args.get("low", 0.5)), float(args.get("high", 1.0)))
_result = {"layer": d.get_id(), "image": d.get_image().get_id(), "channel": int(ch)}
"""

# Auto-stretch tones to fill the full range (like Colors > Auto > Stretch Contrast).
# Both modes use gegl:stretch-contrast so a flat/solid channel is a NO-OP (Drawable.
# levels_stretch() instead maps a zero-range channel to 0 — it silently blacks out a
# solid fill / white underbase, which is a footgun in a DTF workflow). keep_colors=False
# stretches each channel independently (max contrast, may shift colour balance);
# keep_colors=True stretches all channels uniformly so hue is preserved.
_NORMALIZE_CODE = """
d = find_drawable(args.get("image"), args.get("layer"))
keep = bool(args.get("keep_colors", False))
filt = Gimp.DrawableFilter.new(d, "gegl:stretch-contrast", "")
cfg = filt.get_config()
cfg.set_property("keep-colors", keep)
filt.update()
d.merge_filter(filt)
_result = {"layer": d.get_id(), "image": d.get_image().get_id(),
           "keep_colors": keep, "method": "stretch-contrast"}
"""


def _levels(ctx, layer=None, channel="value", low_in=0.0, high_in=1.0,
            gamma=1.0, low_out=0.0, high_out=1.0, image=None):
    return ctx.run(_LEVELS_CODE,
                   args={"image": image, "layer": layer, "channel": channel,
                         "low_in": low_in, "high_in": high_in, "gamma": gamma,
                         "low_out": low_out, "high_out": high_out},
                   image=image, undo_group=True).to_dict()


def _curves(ctx, points, layer=None, channel="value", image=None):
    return ctx.run(_CURVES_CODE,
                   args={"image": image, "layer": layer, "channel": channel,
                         "points": points},
                   image=image, undo_group=True).to_dict()


def _brightness_contrast(ctx, brightness=0.0, contrast=0.0, layer=None, image=None):
    return ctx.run(_BRIGHTNESS_CONTRAST_CODE,
                   args={"image": image, "layer": layer,
                         "brightness": brightness, "contrast": contrast},
                   image=image, undo_group=True).to_dict()


def _hue_saturation(ctx, layer=None, hue=0.0, lightness=0.0, saturation=0.0,
                    overlap=0.0, range="all", image=None):
    return ctx.run(_HUE_SATURATION_CODE,
                   args={"image": image, "layer": layer, "range": range,
                         "hue": hue, "lightness": lightness,
                         "saturation": saturation, "overlap": overlap},
                   image=image, undo_group=True).to_dict()


def _color_balance(ctx, layer=None, range="midtones", cyan_red=0.0,
                   magenta_green=0.0, yellow_blue=0.0, preserve_lum=True, image=None):
    return ctx.run(_COLOR_BALANCE_CODE,
                   args={"image": image, "layer": layer, "range": range,
                         "cyan_red": cyan_red, "magenta_green": magenta_green,
                         "yellow_blue": yellow_blue, "preserve_lum": preserve_lum},
                   image=image, undo_group=True).to_dict()


def _desaturate(ctx, layer=None, mode="luminance", image=None):
    return ctx.run(_DESATURATE_CODE,
                   args={"image": image, "layer": layer, "mode": mode},
                   image=image, undo_group=True).to_dict()


def _invert(ctx, layer=None, linear=False, image=None):
    return ctx.run(_INVERT_CODE,
                   args={"image": image, "layer": layer, "linear": linear},
                   image=image, undo_group=True).to_dict()


def _posterize(ctx, levels, layer=None, image=None):
    return ctx.run(_POSTERIZE_CODE,
                   args={"image": image, "layer": layer, "levels": levels},
                   image=image, undo_group=True).to_dict()


def _threshold(ctx, low=0.5, high=1.0, layer=None, channel="value", image=None):
    return ctx.run(_THRESHOLD_CODE,
                   args={"image": image, "layer": layer, "channel": channel,
                         "low": low, "high": high},
                   image=image, undo_group=True).to_dict()


def _normalize(ctx, keep_colors=False, layer=None, image=None):
    return ctx.run(_NORMALIZE_CODE,
                   args={"image": image, "layer": layer, "keep_colors": keep_colors},
                   image=image, undo_group=True).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="levels")
    def levels(layer: int | str | None = None, channel: str = "value",
               low_in: float = 0.0, high_in: float = 1.0, gamma: float = 1.0,
               low_out: float = 0.0, high_out: float = 1.0,
               image: int | str | None = None) -> dict:
        """Adjust levels on a drawable. channel: value|red|green|blue|alpha.
        Inputs/outputs are 0.0-1.0 floats; gamma is the midtone exponent."""
        return _levels(ctx, layer, channel, low_in, high_in, gamma,
                       low_out, high_out, image)

    @mcp.tool(name="curves")
    def curves(points: list[float], layer: int | str | None = None,
               channel: str = "value", image: int | str | None = None) -> dict:
        """Apply a spline curve. `points` = flat [x0,y0,x1,y1,...] control points
        in 0.0-1.0. channel: value|red|green|blue|alpha."""
        return _curves(ctx, points, layer, channel, image)

    @mcp.tool(name="brightness_contrast")
    def brightness_contrast(brightness: float = 0.0, contrast: float = 0.0,
                            layer: int | str | None = None,
                            image: int | str | None = None) -> dict:
        """Adjust brightness and contrast. Each ranges -1.0..1.0 (0 = no change)."""
        return _brightness_contrast(ctx, brightness, contrast, layer, image)

    @mcp.tool(name="hue_saturation")
    def hue_saturation(layer: int | str | None = None, hue: float = 0.0,
                       lightness: float = 0.0, saturation: float = 0.0,
                       overlap: float = 0.0, range: str = "all",
                       image: int | str | None = None) -> dict:
        """Adjust hue/lightness/saturation. range: all|red|yellow|green|cyan|blue|
        magenta. hue -180..180, lightness/saturation -100..100, overlap 0..100."""
        return _hue_saturation(ctx, layer, hue, lightness, saturation,
                               overlap, range, image)

    @mcp.tool(name="color_balance")
    def color_balance(layer: int | str | None = None, range: str = "midtones",
                      cyan_red: float = 0.0, magenta_green: float = 0.0,
                      yellow_blue: float = 0.0, preserve_lum: bool = True,
                      image: int | str | None = None) -> dict:
        """Shift color balance for a tonal range: shadows|midtones|highlights.
        Each channel pair is -100..100; preserve_lum keeps luminosity."""
        return _color_balance(ctx, layer, range, cyan_red, magenta_green,
                              yellow_blue, preserve_lum, image)

    @mcp.tool(name="desaturate")
    def desaturate(layer: int | str | None = None, mode: str = "luminance",
                   image: int | str | None = None) -> dict:
        """Convert to grayscale values in place. mode: luminance|luma|lightness|
        average|value."""
        return _desaturate(ctx, layer, mode, image)

    @mcp.tool(name="invert")
    def invert(layer: int | str | None = None, linear: bool = False,
               image: int | str | None = None) -> dict:
        """Invert colors. linear=True inverts in linear light, else perceptual."""
        return _invert(ctx, layer, linear, image)

    @mcp.tool(name="posterize")
    def posterize(levels: int, layer: int | str | None = None,
                  image: int | str | None = None) -> dict:
        """Reduce each channel to `levels` discrete tones (2..255)."""
        return _posterize(ctx, levels, layer, image)

    @mcp.tool(name="threshold")
    def threshold(low: float = 0.5, high: float = 1.0,
                  layer: int | str | None = None, channel: str = "value",
                  image: int | str | None = None) -> dict:
        """Black/white threshold: pixels in [low, high] -> white, else black.
        low/high are 0.0-1.0. channel: value|red|green|blue|alpha."""
        return _threshold(ctx, low, high, layer, channel, image)

    @mcp.tool(name="normalize")
    def normalize(keep_colors: bool = False, layer: int | str | None = None,
                  image: int | str | None = None) -> dict:
        """Auto-stretch tones to fill the full 0-255 range (like Colors > Auto >
        Stretch Contrast). keep_colors=False (default) stretches each channel
        independently — maximum contrast, but may shift colour balance; keep_colors=True
        stretches all channels uniformly to preserve hue (gegl:stretch-contrast)."""
        return _normalize(ctx, keep_colors, layer, image)
