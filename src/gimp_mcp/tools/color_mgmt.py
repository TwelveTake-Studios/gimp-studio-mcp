"""Group K — Color management.

RGB / working-space ICC ops plus mode conversion. Soft-proof is a GIMP-3.0
VIEW-only setting (not pixel-scriptable), so it is reported as unsupported with a
clear note rather than faking proofed pixels. Profile enumeration is likewise not
exposed by the libgimp API → unsupported with a note.

Tools build a fixed code template + pass params as a data `args` payload
(injection-safe) to the bridge's exec op, and read structured `_result`.

Tools:
  - get_profile, list_profiles
  - assign_profile, convert_profile
  - soft_proof
  - to_grayscale, to_rgb
"""
from __future__ import annotations

# Read-only: report the embedded ICC profile label (or None for built-in sRGB).
_GET_PROFILE_CODE = """
img = find_image(args.get("image"))
prof = img.get_color_profile()
label = prof.get_label() if prof is not None else None
desc = None
if prof is not None:
    try:
        desc = prof.get_description()
    except Exception:
        desc = None
_result = {
    "image": img.get_id(),
    "has_profile": prof is not None,
    "label": label,
    "description": desc,
}
"""

# Destructive: tag the image with an ICC profile WITHOUT converting pixels.
_ASSIGN_CODE = """
img = find_image(args.get("image"))
f = Gio.File.new_for_path(args["icc_path"])
ok = img.set_color_profile_from_file(f)
prof = img.get_color_profile()
_result = {
    "image": img.get_id(),
    "assigned": bool(ok),
    "icc_path": args["icc_path"],
    "label": prof.get_label() if prof is not None else None,
}
"""

# Destructive: CONVERT pixels into the target ICC working space.
# RGB / working-space conversions only (CMYK/device-link not handled here).
_CONVERT_CODE = """
img = find_image(args.get("image"))
prof = Gimp.ColorProfile.new_from_file(Gio.File.new_for_path(args["icc_path"]))

_iname = (args.get("intent") or "perceptual").upper().replace("-", "_")
intent = getattr(Gimp.ColorRenderingIntent, _iname, Gimp.ColorRenderingIntent.PERCEPTUAL)
bpc = bool(args.get("bpc", True))

ok = img.convert_color_profile(prof, intent, bpc)
cur = img.get_color_profile()
_result = {
    "image": img.get_id(),
    "converted": bool(ok),
    "icc_path": args["icc_path"],
    "intent": _iname.lower(),
    "bpc": bpc,
    "label": cur.get_label() if cur is not None else None,
}
"""

# Soft-proof is a display/view setting in GIMP 3.0 — not pixel-scriptable.
_SOFT_PROOF_CODE = """
img = find_image(args.get("image"))
_result = {
    "image": img.get_id(),
    "supported": False,
    "note": ("Soft-proofing in GIMP 3.0 is a VIEW-only display simulation "
             "(View > Color Management), not a scriptable pixel operation. The "
             "libgimp API exposes no way to render proofed pixels. Use "
             "convert_profile to bake a conversion, or export+proof externally."),
    "requested_icc_path": args.get("icc_path"),
    "requested_intent": args.get("intent"),
}
"""

# Destructive: change image base type to GRAYSCALE.
_GRAYSCALE_CODE = """
img = find_image(args.get("image"))
already = int(img.get_base_type()) == int(Gimp.ImageBaseType.GRAY)
ok = True if already else bool(img.convert_grayscale())
_result = {"image": img.get_id(), "base_type": int(img.get_base_type()),
           "converted": ok, "already_grayscale": already}
"""

# Destructive: change image base type to RGB.
_RGB_CODE = """
img = find_image(args.get("image"))
already = int(img.get_base_type()) == int(Gimp.ImageBaseType.RGB)
ok = True if already else bool(img.convert_rgb())
_result = {"image": img.get_id(), "base_type": int(img.get_base_type()),
           "converted": ok, "already_rgb": already}
"""

# List-profiles is not enumerable through the libgimp API.
_LIST_PROFILES_CODE = """
_result = {
    "supported": False,
    "note": ("The libgimp 3.0 API does not enumerate installed ICC profiles. "
             "Pass an explicit .icc/.icm file path to assign_profile or "
             "convert_profile instead."),
}
"""


def _get_profile(ctx, image=None):
    return ctx.run(_GET_PROFILE_CODE, args={"image": image},
                   undo_group=False).to_dict()


def _assign_profile(ctx, icc_path, image=None):
    return ctx.run(_ASSIGN_CODE, args={"image": image, "icc_path": icc_path},
                   image=image, undo_group=True).to_dict()


def _convert_profile(ctx, icc_path, intent="perceptual", bpc=True, image=None):
    return ctx.run(_CONVERT_CODE,
                   args={"image": image, "icc_path": icc_path,
                         "intent": intent, "bpc": bpc},
                   image=image, undo_group=True).to_dict()


def _soft_proof(ctx, icc_path=None, intent="perceptual", image=None):
    return ctx.run(_SOFT_PROOF_CODE,
                   args={"image": image, "icc_path": icc_path, "intent": intent},
                   undo_group=False).to_dict()


def _to_grayscale(ctx, image=None):
    return ctx.run(_GRAYSCALE_CODE, args={"image": image},
                   image=image, undo_group=True).to_dict()


def _to_rgb(ctx, image=None):
    return ctx.run(_RGB_CODE, args={"image": image},
                   image=image, undo_group=True).to_dict()


def _list_profiles(ctx):
    return ctx.run(_LIST_PROFILES_CODE, args={}, undo_group=False).to_dict()


def register(mcp, ctx) -> None:

    @mcp.tool(name="get_profile")
    def get_profile(image: int | str | None = None) -> dict:
        """Read the image's embedded ICC profile label (None = built-in sRGB).
        `image` = id/basename, or omit for the active image."""
        return _get_profile(ctx, image)

    @mcp.tool(name="assign_profile")
    def assign_profile(icc_path: str, image: int | str | None = None) -> dict:
        """Tag the image with an ICC profile WITHOUT converting pixels (assign).
        `icc_path` = path to a .icc/.icm file. RGB/working-space use."""
        return _assign_profile(ctx, icc_path, image)

    @mcp.tool(name="convert_profile")
    def convert_profile(icc_path: str, intent: str = "perceptual",
                        bpc: bool = True, image: int | str | None = None) -> dict:
        """Convert pixels into the target ICC working space (RGB/working-space only).
        intent: perceptual|relative-colorimetric|saturation|absolute-colorimetric.
        bpc = black-point compensation."""
        return _convert_profile(ctx, icc_path, intent, bpc, image)

    @mcp.tool(name="soft_proof")
    def soft_proof(icc_path: str | None = None, intent: str = "perceptual",
                  image: int | str | None = None) -> dict:
        """NOT SUPPORTED as pixels: soft-proofing is a GIMP 3.0 VIEW-only display
        simulation, not scriptable. Returns {supported: false, note}. Use
        convert_profile to bake a conversion instead."""
        return _soft_proof(ctx, icc_path, intent, image)

    @mcp.tool(name="to_grayscale")
    def to_grayscale(image: int | str | None = None) -> dict:
        """Convert the image to GRAYSCALE mode (destructive)."""
        return _to_grayscale(ctx, image)

    @mcp.tool(name="to_rgb")
    def to_rgb(image: int | str | None = None) -> dict:
        """Convert the image to RGB mode (destructive)."""
        return _to_rgb(ctx, image)

    @mcp.tool(name="list_profiles")
    def list_profiles() -> dict:
        """NOT SUPPORTED: the libgimp API cannot enumerate installed ICC profiles.
        Returns {supported: false, note}. Pass explicit .icc paths instead."""
        return _list_profiles(ctx)
