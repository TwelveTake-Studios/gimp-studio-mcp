#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GIMP-side compat / quirk helpers — run INSIDE GIMP (PyGObject).

Seeded into the bridge namespace as ``compat`` so tool code (and ad-hoc exec)
can call these instead of relearning GIMP-3.0 gotchas each session. Copied next
to the bridge by install-plugin; stdlib + gi.repository only.

Quirks owned here (extend as discovered):
  - Colors are Gegl.Color objects, not RGB tuples (Gegl.Color.new('black')).
  - Drawable.get_pixel returns a Gegl.Color that does NOT unpack as a tuple.
  - plug-in-colortoalpha is not registered in 3.0.4 → use gegl:color-to-alpha.
  - color_to_alpha needs an alpha channel present first.
"""
import gi
gi.require_version("Gimp", "3.0")
try:
    gi.require_version("Gegl", "0.4")
except Exception:
    pass
from gi.repository import Gimp, Gegl  # noqa: E402

__compat_version__ = "0.4.0"  # +layer_offsets / image_resolution (get_offsets/get_resolution 3-tuple owners)


def color(spec):
    """Return a Gegl.Color from a name ('black'), '#rrggbb', or an (r,g,b[,a]) tuple.

    Tuple components may be 0-255 ints or 0.0-1.0 floats (auto-detected).
    """
    if isinstance(spec, Gegl.Color):
        return spec
    if isinstance(spec, str):
        return Gegl.Color.new(spec)
    if isinstance(spec, (tuple, list)) and len(spec) in (3, 4):
        vals = list(spec)
        if len(vals) == 3:
            vals.append(255 if _looks_255(vals) else 1.0)
        if _looks_255(vals):
            r, g, b, a = (v / 255.0 for v in vals)
        else:
            r, g, b, a = vals
        c = Gegl.Color.new("black")
        c.set_rgba(float(r), float(g), float(b), float(a))
        return c
    raise ValueError("unrecognized color spec: %r" % (spec,))


def _looks_255(vals) -> bool:
    return any((isinstance(v, int) and v > 1) or (isinstance(v, float) and v > 1.0)
               for v in vals)


def rgba(gcolor) -> tuple:
    """(r, g, b, a) as 0-255 ints from a Gegl.Color."""
    r, g, b, a = gcolor.get_rgba()
    return (round(r * 255), round(g * 255), round(b * 255), round(a * 255))


def read_pixel(drawable, x: int, y: int) -> tuple:
    """(r, g, b, a) 0-255 at (x, y), normalizing get_pixel's Gegl.Color quirk."""
    px = drawable.get_pixel(x, y)
    # GIMP 3.0: get_pixel returns a Gegl.Color (not an unpackable tuple).
    if isinstance(px, Gegl.Color):
        return rgba(px)
    # Older/alt signatures returned (n_channels, [values]) — handle defensively.
    try:
        _n, vals = px
        vals = list(vals)
        while len(vals) < 4:
            vals.append(255)
        return tuple(int(v) for v in vals[:4])
    except Exception:
        return rgba(px)


def ensure_alpha(drawable):
    """Add an alpha channel if the drawable lacks one (no-op otherwise)."""
    if not drawable.has_alpha():
        drawable.add_alpha()


def layer_offsets(item) -> tuple:
    """(x, y) canvas offset of a layer/item, normalizing GIMP-3's 3-tuple return.

    ``Gimp.Item.get_offsets`` returns THREE values on 3.0.4 / 3.2.4 — (success, x, y) —
    not the two the 2.10 docs imply. Tail-index (x, y are always the last two) so a
    revert to head-indexing can't silently slide the coords by one. THE owner of this
    quirk; every tool site routes get_offsets through here (mirrors the selection-bounds
    fix — see ``_normalize_bounds``).
    """
    off = item.get_offsets()
    return (off[-2], off[-1])


def image_resolution(image) -> tuple:
    """(xres, yres) DPI of an image, normalizing GIMP-3's 3-tuple return.

    ``Gimp.Image.get_resolution`` returns THREE values on 3.0.4 / 3.2.4 — (success,
    xres, yres). Tail-index. THE owner of this quirk; every tool site routes
    get_resolution through here.
    """
    res = image.get_resolution()
    return (res[-2], res[-1])


def _normalize_bounds(b):
    """Tail-normalize a raw ``Gimp.Selection.bounds`` result to (non_empty, x1, y1, x2, y2).

    THE owner of the GIMP-3 selection-bounds quirk. ``Gimp.Selection.bounds`` returns
    SIX values on 3.0.4 / 3.2.4 — (success, non_empty, x1, y1, x2, y2) — not the five
    the 2.10 docs imply. We index off the TAIL (the bbox is always the last four ints;
    non_empty is the element just before them), which reads BOTH the current 6-tuple
    AND a future 5-tuple (non_empty, x1, y1, x2, y2) correctly. Naive head-indexing
    (b[1] as the flag, b[2:6] as coords) instead silently misreads a shape shift — the
    success flag becomes the bbox and every coordinate slides by one (wrong DTF crops /
    underbase). Kept separate from the API call so the dual-arity contract is unit-
    testable without a live selection. All tool sites go through compat; never
    hand-roll this indexing again.
    """
    return (bool(b[-5]), b[-4], b[-3], b[-2], b[-1])


def _bounds_tail(image):
    """(non_empty, x1, y1, x2, y2) for the image's current selection. See ``_normalize_bounds``."""
    return _normalize_bounds(Gimp.Selection.bounds(image))


def selection_bbox(image):
    """(non_empty, x1, y1, x2, y2) — corner form — for the image's selection.

    Use when you need the bounding-box corners (e.g. crop math). See ``_bounds_tail``.
    """
    return _bounds_tail(image)


def selection_bounds(image):
    """(non_empty, x, y, w, h) — offset+size form — for the image's selection.

    Use when you want the bbox as origin + dimensions. See ``_bounds_tail``.
    """
    non_empty, x1, y1, x2, y2 = _bounds_tail(image)
    return (non_empty, x1, y1, x2 - x1, y2 - y1)


def color_to_alpha(image, drawable, target="white", transparency_threshold=0.0,
                   opacity_threshold=1.0):
    """Knock out ``target`` colour to transparency via gegl:color-to-alpha.

    Replaces the missing plug-in-colortoalpha. Destructive (merged); ensure the
    caller has checkpointed if needed.
    """
    ensure_alpha(drawable)
    filt = Gimp.DrawableFilter.new(drawable, "gegl:color-to-alpha", "")
    cfg = filt.get_config()
    cfg.set_property("color", color(target))
    try:
        cfg.set_property("transparency-threshold", float(transparency_threshold))
        cfg.set_property("opacity-threshold", float(opacity_threshold))
    except Exception:
        pass  # property names vary across GEGL versions; color is the essential one
    filt.update()
    drawable.merge_filter(filt)
    return drawable
