"""Group J — Print / DTF helpers (Dave's primary use case).

THE HEADLINE GROUP. Most tools are COMPOSED orchestration (no single PDB proc):
selection algebra + new layers + GEGL + file I/O wired together for DTF
(direct-to-film) workflows. Tools build a fixed code template + pass params as a
data `args` payload (injection-safe) to the bridge's exec op, and read a
structured `_result`. Impl functions are module-level so they can be unit-tested
against a bridge directly.

Tools:
  - print_geometry        set resolution and/or scale to a physical size
  - trim_to_content       autocrop to the artwork's alpha bbox (+ padding)
  - white_underbase       core DTF: white ink layer below art, choked
  - edge_choke / edge_spread   shrink/grow the art's effective alpha edge
  - knockout_background   ADAPTIVE one-click shirt-colour knockout (auto-detect +
                          hard/subtract + defringe/clean); shirt= garment presets
  - list_shirt_presets    list the built-in garment-colour presets
  - clean_for_dtf         clear sub-threshold alpha + solidify partial alpha
  - despill               reduce edge color spill from a knockout
  - halftone_separation   PREVIEW newsprint screen (NOT registration film seps)
  - gang_sheet            shelf-pack many files onto a print sheet, export PNG
  - bleed_and_safe        add bleed margin + report the safe-area rect
  - export_dtf_png        transparent print-ready PNG (alpha preserved)
"""
from __future__ import annotations

import json
from pathlib import Path

from gimp_mcp.bridge.protocol import error_response, ok_response


# ---------------------------------------------------------------------------
# print_geometry — set DPI and/or scale to physical inches (folds set_dpi /
# size_to_inches / check_print_size).  Read-current + optional mutate.
# ---------------------------------------------------------------------------
_GEOMETRY_CODE = """
img = find_image(args.get("image"))
warnings = []
changed = False

# current resolution (get_resolution's (ok, xres, yres) quirk owned by compat)
cur_x, cur_y = compat.image_resolution(img)

dpi = args.get("dpi")
if dpi:
    img.set_resolution(float(dpi), float(dpi))
    cur_x = cur_y = float(dpi)
    changed = True

# effective dpi used for inch math (post-set if we set it)
eff_dpi = float(dpi) if dpi else (cur_x or 300.0)

w_px = img.get_width()
h_px = img.get_height()
width_in = args.get("width_in")
height_in = args.get("height_in")

if width_in or height_in:
    aspect = (w_px / h_px) if h_px else 1.0
    if width_in and not height_in:
        new_w = int(round(float(width_in) * eff_dpi))
        new_h = int(round(new_w / aspect)) if aspect else new_w
    elif height_in and not width_in:
        new_h = int(round(float(height_in) * eff_dpi))
        new_w = int(round(new_h * aspect))
    else:
        new_w = int(round(float(width_in) * eff_dpi))
        new_h = int(round(float(height_in) * eff_dpi))
    new_w = max(1, new_w)
    new_h = max(1, new_h)
    if new_w > w_px or new_h > h_px:
        warnings.append(
            "scaling UP from %dx%d to %dx%d px — upscaling loses print quality"
            % (w_px, h_px, new_w, new_h))
    img.scale(new_w, new_h)
    w_px, h_px = new_w, new_h
    changed = True

_result = {
    "image": img.get_id(),
    "width_px": w_px,
    "height_px": h_px,
    "dpi": [cur_x, cur_y],
    "width_in": round(w_px / eff_dpi, 4) if eff_dpi else None,
    "height_in": round(h_px / eff_dpi, 4) if eff_dpi else None,
    "changed": changed,
    "warnings": warnings,
}
"""


# ---------------------------------------------------------------------------
# trim_to_content — autocrop to the art's alpha bounding box (+ padding).
# ---------------------------------------------------------------------------
_TRIM_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
pad = int(args.get("padding") or 0)

# Select the drawable's alpha (non-transparent pixels). compat.selection_bbox
# owns the GIMP-3 Selection.bounds quirk and returns corner form (non_empty,
# x1, y1, x2, y2) — robust to a future tuple-shape shift (see gimp_compat.py).
img.select_item(Gimp.ChannelOps.REPLACE, drawable)
non_empty, x1, y1, x2, y2 = compat.selection_bbox(img)
if not non_empty:
    Gimp.Selection.none(img)
    _result = {"trimmed": False, "reason": "no non-transparent content",
               "image": img.get_id()}
else:
    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(img.get_width(), x2 + pad)
    y2 = min(img.get_height(), y2 + pad)
    w = x2 - x1
    h = y2 - y1
    img.crop(w, h, x1, y1)
    Gimp.Selection.none(img)
    _result = {"trimmed": True, "x": x1, "y": y1,
               "width": w, "height": h, "image": img.get_id()}
"""


# ---------------------------------------------------------------------------
# white_underbase — the core DTF op. White ink layer BELOW the art, choked.
# ---------------------------------------------------------------------------
_UNDERBASE_CODE = """
img = find_image(args.get("image"))
art = find_drawable(args.get("image"), args.get("layer"))
choke = int(args.get("choke") if args.get("choke") is not None else 2)
opacity = float(args.get("opacity") if args.get("opacity") is not None else 100.0)

# Select the art's alpha, then choke inward so white doesn't peek past the edge.
img.select_item(Gimp.ChannelOps.REPLACE, art)
if choke > 0:
    Gimp.Selection.shrink(img, choke)

# compat.selection_bbox -> (non_empty, x1, y1, x2, y2); [0] is the non-empty flag.
if not compat.selection_bbox(img)[0]:
    Gimp.Selection.none(img)
    _result = {"created": False, "reason": "empty selection after choke",
               "image": img.get_id()}
else:
    # New full-size transparent white-ink layer.
    base = Gimp.Layer.new(img, "white_underbase",
                          img.get_width(), img.get_height(),
                          Gimp.ImageType.RGBA_IMAGE, opacity,
                          Gimp.LayerMode.NORMAL)
    # Insert directly BELOW the art layer (same parent, position = art index + 1).
    parent = art.get_parent()
    siblings = parent.get_children() if parent is not None else img.get_layers()
    try:
        idx = [l.get_id() for l in siblings].index(art.get_id())
    except ValueError:
        idx = 0
    img.insert_layer(base, parent, idx + 1)
    base.fill(Gimp.FillType.TRANSPARENT)

    # Fill the (choked) selection with white on the new layer (guard context on error).
    Gimp.context_push()
    try:
        Gimp.context_set_foreground(compat.color("white"))
        base.edit_fill(Gimp.FillType.FOREGROUND)
    finally:
        Gimp.context_pop()
    Gimp.Selection.none(img)

    _result = {"created": True, "layer": base.get_id(), "image": img.get_id(),
               "name": base.get_name(), "choke": choke, "opacity": opacity}
"""


# ---------------------------------------------------------------------------
# edge_choke / edge_spread — shrink/grow the art's effective alpha edge.
# choke: shrink the kept alpha selection, invert, clear the outer ring -> the
#   art's opaque edge is pulled inward (trims fringing / underbase prep).
# spread: dilate the alpha outward via gegl:median-blur with alpha-percentile=100
#   (a true morphological dilate that also carries the edge colour into the grown
#   ring), so the spread genuinely fattens the art — not a selection-only no-op.
# Both verified to move the art's alpha bbox on GIMP 3.0.4.
# ---------------------------------------------------------------------------
_EDGE_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
px = int(args.get("pixels") or 0)
mode = args.get("mode")  # "choke" or "spread"
compat.ensure_alpha(drawable)

img.select_item(Gimp.ChannelOps.REPLACE, drawable)
# compat.selection_bbox -> (non_empty, x1, y1, x2, y2); [0] is the non-empty flag.
if not compat.selection_bbox(img)[0]:
    Gimp.Selection.none(img)
    _result = {"applied": False, "reason": "no alpha content",
               "layer": drawable.get_id(), "image": img.get_id()}
else:
    if mode == "choke":
        # Shrink the kept area, invert, clear the outer ring -> edge pulled in.
        if px > 0:
            Gimp.Selection.shrink(img, px)
        Gimp.Selection.invert(img)
        drawable.edit_clear()
        Gimp.Selection.none(img)
    else:  # spread: morphological alpha DILATE (grows the art outward)
        Gimp.Selection.none(img)
        if px > 0:
            filt = Gimp.DrawableFilter.new(drawable, "gegl:median-blur", "")
            cfg = filt.get_config()
            cfg.set_property("radius", int(px))
            cfg.set_property("percentile", 50.0)
            cfg.set_property("alpha-percentile", 100.0)  # 100 -> dilate (grow opaque)
            filt.update()
            drawable.merge_filter(filt)
    _result = {"applied": True, "mode": mode, "pixels": px,
               "layer": drawable.get_id(), "image": img.get_id()}
"""


# ---------------------------------------------------------------------------
# knockout_background — ADAPTIVE one-click shirt-colour knockout.
# Colour source: explicit > sample_xy eyedropper > auto-detect from edges.
# Technique: 'auto' picks subtract (color-to-alpha, for DARK garments where the
# colour = shirt-shows-through) vs hard (select+clear, for LIGHT/solid bg). Then
# optional defringe (1px halo trim) + clean (crisp/denoise alpha for film).
# ---------------------------------------------------------------------------
_KNOCKOUT_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
compat.ensure_alpha(drawable)

W = drawable.get_width(); H = drawable.get_height()
_ox, _oy = compat.layer_offsets(drawable)   # GIMP 3.x (success, x, y) quirk owned by compat
ox = int(_ox); oy = int(_oy)

req_mode = (args.get("mode") or "auto").lower()
tol = float(args.get("tolerance") if args.get("tolerance") is not None else 0.15)
contiguous = bool(args.get("contiguous"))
feather = float(args.get("feather") or 0.0)
_df = args.get("defringe"); do_defringe = True if _df is None else bool(_df)
_cl = args.get("clean");    do_clean = True if _cl is None else bool(_cl)

# --- resolve the target background colour ------------------------------------
explicit = args.get("color")
sample_xy = args.get("sample_xy")
auto = False; fail = None; seed = None; target = None
if explicit:
    target = list(compat.rgba(compat.color(explicit)))
elif sample_xy:
    sx = max(0, min(W - 1, int(sample_xy[0]))); sy = max(0, min(H - 1, int(sample_xy[1])))
    target = list(compat.read_pixel(drawable, sx, sy)); seed = (sx + ox, sy + oy)
else:
    auto = True
    pts = [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1),
           (W // 2, 0), (W // 2, H - 1), (0, H // 2), (W - 1, H // 2)]
    counts = {}; first = {}
    for (px, py) in pts:
        c = tuple(compat.read_pixel(drawable, px, py))
        if c[3] >= 8:                    # ignore already-transparent edges
            counts[c] = counts.get(c, 0) + 1
            if c not in first:
                first[c] = (px, py)
    if not counts:
        fail = "no opaque background found at the image edges (already transparent?)"
    else:
        best = None; bestn = -1
        for c in counts:
            if counts[c] > bestn:
                bestn = counts[c]; best = c
        target = list(best); seed = (first[best][0] + ox, first[best][1] + oy)

if fail:
    _result = {"knocked_out": False, "reason": fail,
               "layer": drawable.get_id(), "image": img.get_id()}
else:
    gcol = compat.color((target[0], target[1], target[2], 255))  # force 255-scale
    # Contiguous needs a seed. Auto-detect / sample_xy set one; for an explicit or
    # preset colour, find the closest-matching border pixel (else fall back global).
    if contiguous and seed is None:
        bestd = 10 ** 9; bp = None
        for (px, py) in [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1),
                         (W // 2, 0), (W // 2, H - 1), (0, H // 2), (W - 1, H // 2)]:
            c = compat.read_pixel(drawable, px, py)
            d = abs(c[0] - target[0]) + abs(c[1] - target[1]) + abs(c[2] - target[2])
            if d < bestd:
                bestd = d; bp = (px, py)
        if bp is not None and bestd <= 765.0 * tol + 12:
            seed = (bp[0] + ox, bp[1] + oy)
    # Auto technique pick. A pure luma split (dark->subtract / light->hard) is
    # saturation-blind: a saturated mid-tone (e.g. red ~luma 0.25) would route to
    # subtract and erode that colour from the artwork. So when 'auto', SNAP the
    # resolved colour to the NEAREST curated garment preset and inherit its
    # (hue-aware) mode -- domain knowledge a pure luma rule can't reproduce (red and
    # royal sit at the same luma yet want opposite techniques). Luma is only the
    # fallback when no preset table reached us.
    if req_mode == "auto":
        _pm = args.get("preset_modes") or []
        _best = None; _bd = 10 ** 12
        for _p in _pm:
            _d = ((_p[0] - target[0]) ** 2 + (_p[1] - target[1]) ** 2
                  + (_p[2] - target[2]) ** 2)
            if _d < _bd:
                _bd = _d; _best = _p
        if _best is not None:
            eff = _best[3]; mode_basis = "preset:" + str(_best[4])
        else:
            luma = (0.2126 * target[0] + 0.7152 * target[1] + 0.0722 * target[2]) / 255.0
            eff = "subtract" if luma < 0.28 else "hard"
            mode_basis = "luma"
    else:
        eff = req_mode; mode_basis = "requested"

    if eff == "subtract":
        compat.color_to_alpha(img, drawable, target=gcol, transparency_threshold=tol)
    else:                                # hard: select the colour, clear it
        Gimp.context_push()
        try:
            Gimp.context_set_sample_threshold(tol)
            try: Gimp.context_set_antialias(True)
            except Exception: pass
            try: Gimp.context_set_sample_merged(False)
            except Exception: pass
            if contiguous and seed is not None:
                img.select_contiguous_color(Gimp.ChannelOps.REPLACE, drawable,
                                            float(seed[0]), float(seed[1]))
            else:
                img.select_color(Gimp.ChannelOps.REPLACE, drawable, gcol)
            if feather > 0:
                try: Gimp.Selection.feather(img, feather)
                except Exception: pass
            if compat.selection_bbox(img)[0]:
                drawable.edit_clear()
            Gimp.Selection.none(img)
        finally:
            Gimp.context_pop()

    if do_defringe:                      # trim a 1px residual halo off the art
        img.select_item(Gimp.ChannelOps.REPLACE, drawable)
        if compat.selection_bbox(img)[0]:
            Gimp.Selection.shrink(img, 1)
            Gimp.Selection.invert(img)
            drawable.edit_clear()
        Gimp.Selection.none(img)

    if do_clean:                         # crisp (hard, no feather) / keep-gradient else
        N = 256
        if eff == "hard" and feather <= 0:
            curve = [(1.0 if (i / (N - 1)) >= 0.5 else 0.0) for i in range(N)]
        else:                            # subtract OR feathered hard: denoise, keep edge
            curve = [(0.0 if (i / (N - 1)) < 0.06 else (i / (N - 1))) for i in range(N)]
        drawable.curves_explicit(Gimp.HistogramChannel.ALPHA, curve)

    img.select_item(Gimp.ChannelOps.REPLACE, drawable)
    _bb = compat.selection_bbox(img)
    Gimp.Selection.none(img)
    _result = {"knocked_out": True, "mode": eff, "requested_mode": req_mode,
               "mode_basis": mode_basis,
               "color": [int(target[0]), int(target[1]), int(target[2])],
               "color_hex": "#%02x%02x%02x" % (int(target[0]), int(target[1]), int(target[2])),
               "auto_detected": auto, "tolerance": tol,
               "contiguous": bool(eff == "hard" and contiguous and seed is not None),
               "defringe": do_defringe, "clean": do_clean,
               "content_bbox": ([int(_bb[1]), int(_bb[2]), int(_bb[3]), int(_bb[4])]
                                if _bb[0] else None),
               "layer": drawable.get_id(), "image": img.get_id()}
"""


# ---------------------------------------------------------------------------
# clean_for_dtf — clear sub-threshold alpha + solidify remaining partial alpha.
# Composed: threshold the alpha channel so faint pixels vanish and kept pixels
# go fully opaque (crisp ink edges for film).
# ---------------------------------------------------------------------------
_CLEAN_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
thr = float(args.get("threshold") if args.get("threshold") is not None else 0.5)
compat.ensure_alpha(drawable)

# Remap the ALPHA channel through a binary step curve: pixels with alpha below
# thr -> fully transparent (drop faint noise), alpha >= thr -> fully opaque
# (crisp ink edges for film). gegl:threshold-alpha does NOT exist in 3.0.4, and
# Drawable.threshold(ALPHA) only thresholds value; curves_explicit on the ALPHA
# channel is the reliable primitive (verified on 3.0.4). RGB is untouched.
N = 256
curve = [(1.0 if (i / (N - 1)) >= thr else 0.0) for i in range(N)]
drawable.curves_explicit(Gimp.HistogramChannel.ALPHA, curve)

_result = {"cleaned": True, "threshold": thr, "method": "curves_explicit_alpha",
           "layer": drawable.get_id(), "image": img.get_id()}
"""


# ---------------------------------------------------------------------------
# despill — reduce edge colour spill from a knockout (best-effort).
# Approximate by a light color-to-alpha pass against the spill colour at low
# strength, then re-solidify. Flagged best-effort.
# ---------------------------------------------------------------------------
_DESPILL_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
spill = args.get("color") or "white"
amount = float(args.get("amount") if args.get("amount") is not None else 0.5)
compat.ensure_alpha(drawable)

# Best-effort: GEGL has no single 'despill'; use a mild saturation/levels nudge
# toward the spill complement via a soft color-to-alpha at low transparency
# threshold so only near-pure spill pixels are affected at the very edge.
try:
    filt = Gimp.DrawableFilter.new(drawable, "gegl:color-to-alpha", "")
    cfg = filt.get_config()
    cfg.set_property("color", compat.color(spill))
    try:
        cfg.set_property("transparency-threshold", 0.0)
        cfg.set_property("opacity-threshold", max(0.0, 1.0 - amount))
    except Exception:
        pass
    filt.update()
    drawable.merge_filter(filt)
    method = "color-to-alpha-soft"
    applied = True
except Exception as e:
    method = "noop:" + type(e).__name__
    applied = False

_result = {"despilled": applied, "color": spill, "amount": amount,
           "method": method, "note": "best-effort approximation",
           "layer": drawable.get_id(), "image": img.get_id()}
"""


# ---------------------------------------------------------------------------
# halftone_separation — PREVIEW newsprint screen (NOT film-accurate seps).
# ---------------------------------------------------------------------------
_HALFTONE_CODE = """
img = find_image(args.get("image"))
drawable = find_drawable(args.get("image"), args.get("layer"))
cell = float(args.get("cell_size") if args.get("cell_size") is not None else 8.0)
angle = float(args.get("angle") if args.get("angle") is not None else 45.0)

filt = Gimp.DrawableFilter.new(drawable, "gegl:newsprint", "")
cfg = filt.get_config()
# gegl:newsprint props: period (cell size in px), angle, pattern, color-model.
try:
    cfg.set_property("period", cell)
except Exception:
    pass
try:
    cfg.set_property("angle", angle)
except Exception:
    pass
filt.update()
drawable.merge_filter(filt)

_result = {
    "applied": True, "cell_size": cell, "angle": angle,
    "layer": drawable.get_id(), "image": img.get_id(),
    "warning": ("PREVIEW/APPROXIMATION only — gegl:newsprint is a visual "
                "halftone, NOT registration-accurate channel film separations."),
}
"""


# ---------------------------------------------------------------------------
# gang_sheet — the marquee tool. Shelf-pack many files onto one print sheet.
# Loads each file as a layer, packs left->right / top->bottom leaving a gutter,
# grows the canvas height as needed, exports a print-ready PNG at dpi.
# ---------------------------------------------------------------------------
_GANG_CODE = """
import math

files = list(args.get("files") or [])
out_path = args["out_path"]
sheet_w_in = float(args.get("sheet_width_in") or 22.0)
dpi = float(args.get("dpi") or 300.0)
gutter_in = float(args.get("gutter_in") if args.get("gutter_in") is not None else 0.125)
rotate = bool(args.get("rotate"))

sheet_w = max(1, int(round(sheet_w_in * dpi)))
gutter = max(0, int(round(gutter_in * dpi)))

# Start with a 1px-tall canvas; grow as the shelf packer needs more rows.
img = Gimp.Image.new(sheet_w, 1, Gimp.ImageBaseType.RGB)
img.set_resolution(dpi, dpi)

placed = 0
skipped = []
cur_x = gutter
cur_y = gutter
shelf_h = 0
max_used_h = gutter

for path in files:
    f = Gio.File.new_for_path(path)
    try:
        layer = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, img, f)
    except Exception as e:
        skipped.append({"path": path, "error": type(e).__name__ + ": " + str(e)})
        continue
    img.insert_layer(layer, None, 0)

    lw = layer.get_width()
    lh = layer.get_height()
    # Optional 90deg rotate to fit a too-wide item.
    if rotate and lw > (sheet_w - 2 * gutter) and lh <= (sheet_w - 2 * gutter):
        layer.transform_rotate_simple(Gimp.RotationType.DEGREES90, True, 0, 0)
        lw, lh = layer.get_width(), layer.get_height()

    # New shelf if this item won't fit on the current row.
    if cur_x + lw + gutter > sheet_w and cur_x > gutter:
        cur_x = gutter
        cur_y += shelf_h + gutter
        shelf_h = 0

    # Grow canvas height if needed before placing.
    need_h = cur_y + lh + gutter
    if need_h > img.get_height():
        img.resize(sheet_w, need_h, 0, 0)

    layer.set_offsets(cur_x, cur_y)
    cur_x += lw + gutter
    if lh > shelf_h:
        shelf_h = lh
    if cur_y + lh + gutter > max_used_h:
        max_used_h = cur_y + lh + gutter
    placed += 1

# Final canvas height = last used row + gutter.
final_h = max(1, max_used_h)
if final_h != img.get_height():
    img.resize(sheet_w, final_h, 0, 0)

# Export with alpha preserved: merge visible into one layer, save PNG.
merged = img.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
of = Gio.File.new_for_path(out_path)
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, of)

_result = {"placed": placed, "skipped": skipped,
           "sheet_px": [sheet_w, final_h], "out_path": out_path,
           "dpi": dpi, "image": img.get_id()}
"""


# ---------------------------------------------------------------------------
# bleed_and_safe — add a bleed margin (resize canvas) + report safe-area rect.
# ---------------------------------------------------------------------------
_BLEED_CODE = """
img = find_image(args.get("image"))
_xr, _yr = compat.image_resolution(img)
dpi = float((_xr or _yr) or 300.0)

bleed_in = float(args.get("bleed_in") if args.get("bleed_in") is not None else 0.125)
safe_in = float(args.get("safe_in") if args.get("safe_in") is not None else 0.125)
bleed = int(round(bleed_in * dpi))
safe = int(round(safe_in * dpi))

w0, h0 = img.get_width(), img.get_height()
# Grow canvas by bleed on every side; shift existing content to center.
new_w = w0 + 2 * bleed
new_h = h0 + 2 * bleed
if bleed > 0:
    img.resize(new_w, new_h, bleed, bleed)
    # Resize layers to the new image bounds so the bleed area is paintable.
    for l in img.get_layers():
        l.resize_to_image_size()

# Safe area: inset from the trimmed (pre-bleed) edge by safe px.
safe_x = bleed + safe
safe_y = bleed + safe
safe_w = max(0, new_w - 2 * (bleed + safe))
safe_h = max(0, new_h - 2 * (bleed + safe))

_result = {
    "image": img.get_id(),
    "bleed_px": bleed,
    "canvas_px": [new_w, new_h],
    "safe_rect": {"x": safe_x, "y": safe_y, "width": safe_w, "height": safe_h},
    "dpi": dpi,
}
"""


# ---------------------------------------------------------------------------
# export_dtf_png — transparent, print-ready PNG (PRESERVE ALPHA, do NOT flatten).
# ---------------------------------------------------------------------------
_EXPORT_CODE = """
img = find_image(args.get("image"))
path = args["path"]
dpi = args.get("dpi")

# Work on a duplicate so the user's open image is untouched.
dup = img.duplicate()
if dpi:
    dup.set_resolution(float(dpi), float(dpi))
# Merge visible layers WITHOUT flattening (flatten removes alpha -> ruins DTF).
try:
    dup.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
except Exception:
    pass
f = Gio.File.new_for_path(path)
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup, f)
_xr, _yr = compat.image_resolution(dup)
out_dpi = _xr or _yr
dup.delete()
_result = {"saved": path, "dpi": out_dpi, "alpha_preserved": True, "image": img.get_id()}
"""


# ---------------------------------------------------------------------------
# Impl functions (module-level, testable).  Destructive ops: image=image +
# "image" in args + undo_group=True.  Read-only / new-image: undo_group=False.
# ---------------------------------------------------------------------------
def _print_geometry(ctx, image=None, dpi=None, width_in=None, height_in=None):
    return ctx.run(_GEOMETRY_CODE,
                   args={"image": image, "dpi": dpi,
                         "width_in": width_in, "height_in": height_in},
                   image=image, undo_group=True).to_dict()


def _trim_to_content(ctx, image=None, layer=None, padding=0):
    return ctx.run(_TRIM_CODE,
                   args={"image": image, "layer": layer, "padding": padding},
                   image=image, undo_group=True).to_dict()


def _white_underbase(ctx, image=None, layer=None, choke=2, opacity=100.0):
    return ctx.run(_UNDERBASE_CODE,
                   args={"image": image, "layer": layer,
                         "choke": choke, "opacity": opacity},
                   image=image, undo_group=True).to_dict()


def _edge_choke(ctx, image=None, layer=None, pixels=2):
    return ctx.run(_EDGE_CODE,
                   args={"image": image, "layer": layer,
                         "pixels": pixels, "mode": "choke"},
                   image=image, undo_group=True).to_dict()


def _edge_spread(ctx, image=None, layer=None, pixels=2):
    return ctx.run(_EDGE_CODE,
                   args={"image": image, "layer": layer,
                         "pixels": pixels, "mode": "spread"},
                   image=image, undo_group=True).to_dict()


# --- Garment-colour presets (Workstream B) — data/garment_presets.json -------
_PRESETS_CACHE = None


def _load_garment_presets():
    global _PRESETS_CACHE
    if _PRESETS_CACHE is None:
        path = Path(__file__).resolve().parent.parent / "data" / "garment_presets.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _PRESETS_CACHE = data.get("presets", data)
        except Exception:
            _PRESETS_CACHE = {}
    return _PRESETS_CACHE


def _garment_preset(name):
    return _load_garment_presets().get((name or "").strip().lower())


def _preset_modes():
    """Compact ``[r, g, b, mode, name]`` rows for the garment presets — passed into
    ``_KNOCKOUT_CODE`` so auto-mode can snap a resolved colour to the nearest curated
    preset and inherit its (hue-aware) technique. Skips malformed rows."""
    rows = []
    for name, p in _load_garment_presets().items():
        hx = (p.get("hex") or "").lstrip("#")
        mode = p.get("mode")
        if len(hx) == 6 and mode in ("subtract", "hard"):
            try:
                rows.append([int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16),
                             mode, name])
            except ValueError:
                continue
    return rows


def _list_shirt_presets(ctx=None):
    presets = _load_garment_presets()
    return ok_response({"count": len(presets),
                        "presets": [dict(name=k, **v) for k, v in sorted(presets.items())]})


def _knockout_background(ctx, image=None, layer=None, color=None, shirt=None,
                         sample_xy=None, mode="auto", tolerance=None,
                         contiguous=False, feather=0.0, defringe=True, clean=True):
    mode = (mode or "auto").lower()
    if mode not in ("auto", "hard", "subtract"):
        return error_response(
            "ValueError",
            "mode must be 'auto', 'hard', or 'subtract' (got %r)" % (mode,))
    if sample_xy is not None and len(sample_xy) < 2:
        return error_response(
            "ValueError", "sample_xy must be [x, y] (got %r)" % (sample_xy,))
    if shirt:
        p = _garment_preset(shirt)
        if p is None:
            avail = ", ".join(sorted(_load_garment_presets()))
            return error_response(
                "ValueError",
                "unknown shirt preset %r; available: %s" % (shirt, avail))
        if color is None:
            color = p.get("hex")
        if mode == "auto" and p.get("mode"):
            mode = p["mode"]
        if tolerance is None and p.get("tolerance") is not None:
            tolerance = float(p["tolerance"])
    if tolerance is None:
        tolerance = 0.15                 # baseline when neither caller nor preset set it
    return ctx.run(_KNOCKOUT_CODE,
                   args={"image": image, "layer": layer, "color": color,
                         "sample_xy": list(sample_xy) if sample_xy else None,
                         "mode": mode, "tolerance": tolerance,
                         "contiguous": contiguous, "feather": feather,
                         "defringe": defringe, "clean": clean,
                         "preset_modes": _preset_modes()},
                   image=image, undo_group=True).to_dict()


def _clean_for_dtf(ctx, image=None, layer=None, threshold=0.5):
    return ctx.run(_CLEAN_CODE,
                   args={"image": image, "layer": layer, "threshold": threshold},
                   image=image, undo_group=True).to_dict()


def _despill(ctx, image=None, layer=None, color="white", amount=0.5):
    return ctx.run(_DESPILL_CODE,
                   args={"image": image, "layer": layer,
                         "color": color, "amount": amount},
                   image=image, undo_group=True).to_dict()


def _halftone_separation(ctx, image=None, layer=None, cell_size=8.0, angle=45.0):
    return ctx.run(_HALFTONE_CODE,
                   args={"image": image, "layer": layer,
                         "cell_size": cell_size, "angle": angle},
                   image=image, undo_group=True).to_dict()


def _gang_sheet(ctx, files, out_path, sheet_width_in=22.0, dpi=300.0,
                gutter_in=0.125, rotate=False):
    # New-image op (no existing image to group).
    return ctx.run(_GANG_CODE,
                   args={"files": files, "out_path": out_path,
                         "sheet_width_in": sheet_width_in, "dpi": dpi,
                         "gutter_in": gutter_in, "rotate": rotate},
                   undo_group=False).to_dict()


def _bleed_and_safe(ctx, image=None, bleed_in=0.125, safe_in=0.125):
    return ctx.run(_BLEED_CODE,
                   args={"image": image, "bleed_in": bleed_in, "safe_in": safe_in},
                   image=image, undo_group=True).to_dict()


def _export_dtf_png(ctx, path, image=None, dpi=None):
    return ctx.run(_EXPORT_CODE,
                   args={"image": image, "path": path, "dpi": dpi},
                   undo_group=False).to_dict()


# ---------------------------------------------------------------------------
def register(mcp, ctx) -> None:

    @mcp.tool(name="print_geometry")
    def print_geometry(image: int | str | None = None, dpi: float | None = None,
                       width_in: float | None = None,
                       height_in: float | None = None) -> dict:
        """Set print resolution (dpi) and/or scale to a physical size in inches.
        Pass width_in and/or height_in to scale (aspect preserved if one given).
        Reports current inches @ dpi and warns on upscaling."""
        return _print_geometry(ctx, image, dpi, width_in, height_in)

    @mcp.tool(name="trim_to_content")
    def trim_to_content(image: int | str | None = None,
                        layer: int | str | None = None,
                        padding: int = 0) -> dict:
        """Autocrop the image to the artwork's alpha bounding box, plus optional
        padding (px). Removes empty transparent margins."""
        return _trim_to_content(ctx, image, layer, padding)

    @mcp.tool(name="white_underbase")
    def white_underbase(image: int | str | None = None,
                        layer: int | str | None = None,
                        choke: int = 2, opacity: float = 100.0) -> dict:
        """Core DTF op: create a white-ink layer BELOW the art, filled to the
        art's alpha shrunk inward by `choke` px at `opacity`. Returns new layer id."""
        return _white_underbase(ctx, image, layer, choke, opacity)

    @mcp.tool(name="edge_choke")
    def edge_choke(image: int | str | None = None,
                   layer: int | str | None = None, pixels: int = 2) -> dict:
        """Pull the art's effective alpha edge inward by `pixels` (trims a thin
        rim — useful before underbase or to remove fringing)."""
        return _edge_choke(ctx, image, layer, pixels)

    @mcp.tool(name="edge_spread")
    def edge_spread(image: int | str | None = None,
                    layer: int | str | None = None, pixels: int = 2) -> dict:
        """Grow (dilate) the art's alpha edge outward by `pixels`, fattening the
        artwork and carrying the edge colour into the grown ring (morphological
        alpha dilate via gegl:median-blur)."""
        return _edge_spread(ctx, image, layer, pixels)

    @mcp.tool(name="knockout_background")
    def knockout_background(image: int | str | None = None,
                            layer: int | str | None = None,
                            color: str | None = None,
                            shirt: str | None = None,
                            sample_xy: list[int] | None = None,
                            mode: str = "auto",
                            tolerance: float | None = None,
                            contiguous: bool = False,
                            feather: float = 0.0,
                            defringe: bool = True,
                            clean: bool = True) -> dict:
        """One-click DTF background / shirt-colour knockout.

        Colour to remove, by priority: explicit `color` (name/#hex) > `shirt=`
        garment preset (see list_shirt_presets) > `sample_xy=[x,y]` eyedropper >
        AUTO-DETECT from the image edges. (A `shirt=` preset still supplies the
        technique + tolerance defaults even when an explicit `color` overrides
        its hue.)

        `mode='auto'` snaps the resolved colour to the nearest garment preset and
        inherits its technique (e.g. red -> hard, so reds in the art aren't eaten;
        black -> subtract, since on a black shirt black shows through); with no
        preset table it falls back to a luma rule (dark -> subtract / light ->
        hard). Override with 'hard'/'subtract' (case-insensitive). `tolerance` =
        match aggressiveness (default 0.15; a `shirt` preset supplies its own).
        Hard mode: `contiguous=True` removes only the edge-connected region (keeps
        design areas that reuse the bg colour); `feather` softens the cut.
        `defringe` trims a 1px halo; `clean` crisps/denoises alpha for film.

        Returns the colour used, effective `mode` + `mode_basis`
        (`preset:<name>` | `luma` | `requested`), `content_bbox` as
        `[x1, y1, x2, y2]` corner coords (or null if empty), and canonical
        `layer`/`image` ids."""
        return _knockout_background(ctx, image, layer, color, shirt, sample_xy,
                                    mode, tolerance, contiguous, feather,
                                    defringe, clean)

    @mcp.tool(name="list_shirt_presets")
    def list_shirt_presets() -> dict:
        """List the built-in garment / shirt-colour presets for
        knockout_background(shirt=...) — name, display, hex, technique, tolerance."""
        return _list_shirt_presets(ctx)

    @mcp.tool(name="clean_for_dtf")
    def clean_for_dtf(image: int | str | None = None,
                      layer: int | str | None = None,
                      threshold: float = 0.5) -> dict:
        """Clear sub-threshold (faint) alpha and solidify remaining partial alpha
        to opaque, giving crisp ink edges for film output."""
        return _clean_for_dtf(ctx, image, layer, threshold)

    @mcp.tool(name="despill")
    def despill(image: int | str | None = None,
                layer: int | str | None = None,
                color: str = "white", amount: float = 0.5) -> dict:
        """Reduce edge color spill left over from a knockout (best-effort
        approximation; flag if results are off)."""
        return _despill(ctx, image, layer, color, amount)

    @mcp.tool(name="halftone_separation")
    def halftone_separation(image: int | str | None = None,
                            layer: int | str | None = None,
                            cell_size: float = 8.0, angle: float = 45.0) -> dict:
        """Apply a newsprint halftone screen. PREVIEW/APPROXIMATION ONLY — this is
        a visual halftone, NOT registration-accurate channel film separations."""
        return _halftone_separation(ctx, image, layer, cell_size, angle)

    @mcp.tool(name="gang_sheet")
    def gang_sheet(files: list[str], out_path: str, sheet_width_in: float = 22.0,
                   dpi: float = 300.0, gutter_in: float = 0.125,
                   rotate: bool = False) -> dict:
        """Lay out multiple design files on a print sheet via a left-to-right /
        top-to-bottom shelf packer (gutter between items), growing height as
        needed, then export a transparent PNG. Returns {placed, sheet_px, out_path}."""
        return _gang_sheet(ctx, files, out_path, sheet_width_in, dpi,
                           gutter_in, rotate)

    @mcp.tool(name="bleed_and_safe")
    def bleed_and_safe(image: int | str | None = None, bleed_in: float = 0.125,
                       safe_in: float = 0.125) -> dict:
        """Add a bleed margin (resize canvas by bleed_in on each side) and report
        the inner safe-area rect (inset by safe_in)."""
        return _bleed_and_safe(ctx, image, bleed_in, safe_in)

    @mcp.tool(name="export_dtf_png")
    def export_dtf_png(path: str, image: int | str | None = None,
                       dpi: float | None = None) -> dict:
        """Export a transparent, print-ready PNG. PRESERVES ALPHA (merges visible
        layers without flattening). Optionally set output dpi. Source untouched."""
        return _export_dtf_png(ctx, path, image, dpi)
