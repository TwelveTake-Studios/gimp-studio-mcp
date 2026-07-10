"""Server-level AI orientation, surfaced to the MCP host as the server's
``instructions`` string (shown to the model on connect).

Deliberately principle-level: the mental model, the DTF/print golden path, and
the non-obvious gotchas that actually bite -- NOT a per-tool manual. Each tool's
own docstring documents its params/defaults; ``describe_op`` / ``list_*``
enumerate the surface at runtime. Kept concise because it loads into every
session's context.
"""
from __future__ import annotations

INSTRUCTIONS = """GIMP Studio MCP - reliable, structured control of GIMP 3.x for print/DTF production and general image editing. Works identically whether GIMP is live (visible canvas) or headless.

MENTAL MODEL
- One stateful GIMP session shared by all tools; operations mutate it and run sequentially - don't assume isolation. Reference images by numeric id or basename (list_images); most tools take image/layer and default to the active one.
- Every tool returns {ok, result, stdout, warnings, error} - check ok; output is captured even on error.
- Protect risky edits: checkpoint -> work -> restore. If you need the original after a destructive IN-PLACE edit (e.g. making a colour variant), duplicate the image/layer first - the session mutates, so an un-duplicated master is overwritten.

VERIFY, DON'T ASSUME
- After meaningful edits, confirm with your eyes AND the pixels: get_bitmap to view (region=/max_dim=/background= to flatten over a colour) and color_at / read_region for exact RGBA. In one shared session, unverified mistakes compound.
- To verify a DELIVERABLE, re-open the exported file and get_metadata - checking the live session image doesn't prove the file's pixels/DPI.
- get_bitmap save_to=<abs path> writes the preview to disk instead of inline - use when the client can't render inline images or the preview is large.

DTF / PRINT (headline)
- Typical path: checkpoint -> (background removal, if needed) -> clean/edge steps -> trim_to_content -> print_geometry -> export_dtf_png. The caller supplies the export path.
- Background removal is keyed on "is that colour ALSO inside the art?", not on garment: if the removal colour sits only around the art, a plain knockout is fine; if it also appears INSIDE the art (interior whites/snow, red elements on a red shirt) use a contiguous edge-seeded knockout so interior pixels survive. add_alpha first if the image is flat/no-alpha. (See knockout_background's docstring for contiguous/mode/tolerance/defringe/clean.)
- SKIP background removal when the input is already transparent, or when the "colour" is real ink you want to print (white on a dark garment). Garment framing: DARK shirt -> the art's own light inks print (often a transparent master already); LIGHT shirt -> the art needs its own dark ink - recolour a white-ink master rather than knocking out. Recolour: flat logo -> lock_alpha + set_fg(ink) + fill; tonal/multi-ink art -> a luminance-preserving remap (invert/curves). Default ink near-black unless a brand colour is given.
- Sizing: print_geometry RESAMPLES to a physical size (pass ONE of width_in/height_in to preserve aspect); export_dtf_png's dpi= only TAGS the file (no resample). Size with print_geometry AFTER trim, then export. Deliver transparent RGBA at 300 DPI; don't bake a white underbase unless asked (the shop RIP builds it from alpha). Placement (full-back, left-chest...) is a press setting, not part of the file - the file is just the sized art. gang_sheet nests copies.

GOTCHAS
- fill fills the WHOLE drawable and ignores the active selection - to fill only a selection use bucket_fill, or edit_fill via gimp_exec. lock_alpha to recolour without touching transparency.
- trim_to_content trims to the FULL alpha bbox (any alpha > 0) - remove faint stray/dust edge alpha FIRST (threshold_alpha/clean, or scan for the true box) or it inflates and mis-sizes the print.
- gimp_exec whose code deletes its OWN image should pass undo_group=False.
- histogram defaults to perceptual space (matches get_bitmap/levels); use space='gimp' for native PDB stats. Other GIMP-3 quirks are absorbed by the compat layer.

ESCAPE HATCH & DISCOVERY
- gimp_exec runs arbitrary GIMP-3 Python when no tool fits (disable via GIMP_MCP_NO_EXEC=1). Each tool's own docstring documents its params/defaults; describe_op / list_procedures / list_gegl_ops / list_* enumerate capabilities. status + list_images to orient at session start.
"""
