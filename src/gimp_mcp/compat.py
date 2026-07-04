"""Server-side compat references (pure Python, NO gi).

The gi-using GIMP-3.0 quirk HELPERS live in ``bridge/gimp_compat.py`` — they run
inside GIMP and are seeded into the bridge namespace as ``compat`` so tool code
(and ad-hoc exec) can call ``compat.color(...)``, ``compat.read_pixel(...)``,
``compat.color_to_alpha(...)`` etc.

This module stays importable in the external venv (no gi), so it only holds
pure-Python references/validation for tool authors.

Known GIMP-3.0 quirks (owned in bridge/gimp_compat.py):
  - Colors are Gegl.Color objects, not RGB tuples.
  - Drawable.get_pixel returns a Gegl.Color that doesn't unpack as a tuple.
  - plug-in-colortoalpha is unregistered in 3.0.4 → gegl:color-to-alpha filter.
  - Image resolution / Item offsets getters return a leading success flag
    (owned by compat.image_resolution / compat.layer_offsets).
  - Enum renames vs 2.10/early-3.0 docs (e.g. MaskApplyType -> MaskApplyMode).
"""
from __future__ import annotations

# For reference/validation in server-side tool schemas:
FILL_TYPES = ("white", "transparent", "background", "foreground")
BASE_TYPES = ("RGB", "GRAY", "INDEXED")

# Documented enum renames (2.10 / early-3.0 docs -> 3.0.4 reality).
ENUM_RENAMES = {
    "Gimp.MaskApplyType": "Gimp.MaskApplyMode",
}
