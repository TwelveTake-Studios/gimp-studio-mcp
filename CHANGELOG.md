# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.2] - 2026-07-19

Fixes a silent-data-loss path in `knockout_background` on dark garments, and makes
the tool report what it removed instead of guessing. No tool added or removed.

### Fixed
- **`contiguous=True` was silently ignored in `subtract` mode.** The six dark
  garment presets (black, navy, charcoal, royal, maroon, forest) resolve to
  `subtract` -> `color_to_alpha`, which is global, and the subtract path never
  looked at the flag. A caller asking for interior preservation did not get it and
  got no error. `subtract` now restricts `color_to_alpha` to the border-connected
  background when `contiguous=True`, so artwork in the garment colour that is
  ENCLOSED by other art survives. The selection is grown 1px first so the
  anti-aliased transition ring is still thinned -- the soft edge subtract mode
  exists to produce.
- **The border-seed ballot no longer picks a transparent pixel.** Transparent
  pixels store RGB `(0,0,0)`, so for a dark removal colour an already-transparent
  border pixel scored a perfect match and won — and a contiguous select seeded on
  transparency yields an empty selection, i.e. a knockout that removed nothing
  while reporting success. Realistic input: a file whose corner was cleared by an
  earlier pass. The ballot now skips transparent pixels, the same guard the
  auto-detect path already applied.
- **`contiguous` no longer reports a removal that did not happen.** In hard mode
  the flag was recorded right after building the selection rather than after the
  clear, so an empty selection reported `contiguous: true` having cleared nothing.
- **`contiguous=True` that cannot be applied now warns.** When no usable border
  seed exists the run falls back to a GLOBAL removal — precisely what `contiguous`
  was asked to prevent — so it now says so and suggests `sample_xy=[x,y]`.
- **Interior removals are no longer silent.** A non-contiguous run now reports
  `enclosed_bbox` and a `warning` when it cleared regions of the removal colour
  that are not connected to the border. This is *correct* for letter counters (the
  hole in an O) and donut holes -- background that must knock out so the garment
  shows through -- and *wrong* when those regions were interior artwork. Colour
  cannot distinguish the two, so the tool measures, reports, and names the escape
  hatch rather than guessing.

### Added
- Garment presets may carry a `contiguous` hint, honoured unless the caller passes
  an explicit value. **No shipped preset sets it** -- enclosed regions of the
  garment colour are usually letter counters, and defaulting it on would fill the
  hole in every O, A, B, D, P, R and 0. Opt-in only, pinned by a test.
- `knockout_background` result gains `contiguous` (what was actually applied),
  `contiguous_requested`, `enclosed_bbox` and `warning`.

### Notes
- A *global* subtract from a dark key also thins the alpha of all remaining art
  toward each pixel's own brightest channel (an orange logo lands near 87% alpha).
  That is inherent to color-to-alpha and unchanged here -- but `contiguous=True`
  avoids it entirely, keeping art at full opacity.

## [0.3.1] - 2026-07-19

Honesty release: tools that can never do what they advertise now say so in `ok`,
and the docs stop selling a capability GIMP 3.x does not expose. No tool added or
removed — the surface stays at 119.

### Fixed
- **`undo` / `redo` no longer report success when nothing was rolled back.** GIMP
  3.x has no `gimp-image-undo` / `gimp-image-redo` in its PDB — undo is driven by
  the interactive GUI stack — so both tools always took their unsupported branch
  and still returned `ok: true`. A caller that checks only `ok` read that as a
  real rollback. They now return `ok: false` with an
  `error.type` of `UnsupportedOperation` pointing at `checkpoint()` / `restore()`,
  which are the working rollback primitives. The `{supported, note}` result is
  preserved, and if a future GIMP ever exposes the procs the tools succeed normally.
- **`soft_proof` / `list_profiles` likewise return `ok: false`.** Both are permanent
  capability gaps (soft-proofing is a VIEW-only display simulation in GIMP 3.x;
  libgimp cannot enumerate installed ICC profiles), and both reported `ok: true`
  while doing nothing. Their errors name the alternative that does work —
  `convert_profile` to bake a conversion, explicit `.icc` paths for profile
  assignment, `get_profile` to read an image's own profile.

### Changed
- Docstrings for the four tools above now state plainly that the operation always
  fails on GIMP 3.x and what to use instead, rather than hedging with "it may not".

### Documentation
- README no longer advertises **soft-proof** under Color Management, nor
  **undo/redo** under Safety — neither can be scripted in GIMP 3.x, so listing them
  as capabilities was misleading. The Safety row now names `checkpoint`/`restore`
  as the scriptable rollback path.
- README tool-count fixes: the shields badge said 117 (now 119, matching the prose),
  and the `GIMP_MCP_NO_EXEC=1` note said "116 structured tools" (now 118 — stale
  117-era arithmetic).

## [0.3.0] - 2026-07-18

Cutout-quality + export-safety release. Tool surface 117 → 119.

### Added
- **`cutout_color`** (masks/alpha) — a crisp HARD colour knockout: add alpha →
  select-by-colour → delete. The WYSIWYG counterpart to `color_to_alpha`'s soft subtract
  (no auto-mode, no defringe, no alpha-clean) — i.e. the by-hand "select by colour →
  delete" as one call. Takes an explicit `color` or a `sample_xy` eyedropper, plus
  `threshold` / `contiguous` / `antialias` / `feather`.
- **`foreground_select`** (selections) — edge-aware SUBJECT selection via
  `gimp-drawable-foreground-extract` (SIOX / matting-global, headless). Give a rough hint
  (a bounding box, or the current selection); it builds a trimap
  (shrink→foreground / grow→background / the ring between→unknown), runs matting, and
  replaces the selection with the refined matte — for backgrounds that AREN'T a flat
  colour, where colour-select fails. See Known issues.

### Changed
- **`knockout_background`: `defringe` and `clean` now default OFF.** The always-on
  `defringe` eroded a 1px ring off the artwork (destroying thin detail) and `clean`
  hardened soft/anti-aliased edges — both degraded output. Both remain available as
  opt-in (`defringe=True` / `clean=True`).
- **`color_to_alpha`: default `transparency_threshold` 0.0 → 0.15**, and its docstring now
  presents it as the recommended SOFT black-knockout path (e.g. `#000000` at ~0.15–0.20).
- **`export_image` is now ALPHA-SAFE.** It previously always flattened, silently dropping
  transparency (ruining a transparent DTF cutout). It is now format-aware: alpha is
  preserved for formats that support it (png/tiff/webp/tga/gif) and flattened only for
  formats that can't (jpg/bmp) or on explicit `flatten=True`; a `warning` is returned
  whenever alpha had to be dropped. New `flatten: bool | None` parameter.

### Known issues
- **`foreground_select`'s bbox hint requires the subject to roughly FILL its box.** The
  shrunk-box interior is treated as definite foreground, so a subject that doesn't fill
  its box (an irregular shape, or several subjects with background between them) keeps
  that interior background — only the edge band is refined. Use a rough OUTLINE *selection*
  for irregular subjects; cutting a subject out of a busy scene (people in a crowd, etc.)
  needs an external model (rembg / u²-net) — on the roadmap, not built in.

## [0.2.0] - 2026-07-10

### Added
- **Server-level AI `instructions`.** The MCP server now ships a concise orientation
  string (surfaced to the model on connect via FastMCP's `instructions`): the session
  mental model, the DTF/print golden path, and the load-bearing gotchas — e.g.
  `print_geometry` *resamples* vs `export_dtf_png` `dpi=` only *tags*, `fill` ignores the
  active selection, and `trim_to_content` uses the full alpha bbox — pointing to each
  tool's own docstring for params. Lives in `gimp_mcp/instructions.py`; a Tier-1 test
  pins its presence and wiring.

### Changed
- **Sharper agent-facing DTF/metadata docstrings** (behaviour-verified): `export_dtf_png`
  documents that an omitted `dpi` inherits the image's current resolution and that it
  only *tags* DPI (never resamples — use `print_geometry` for physical size);
  `clean_for_dtf` documents that it *binarizes* alpha (hardens soft/AA edges, drops
  low-alpha interior detail); `knockout_background` notes it auto-adds alpha on flat
  inputs; `get_metadata` enumerates its returned fields (incl. `resolution`) and how to
  inspect a file on disk.

## [0.1.1] - 2026-07-03

Bug-fix / hardening release.

### Fixed
- **Context no longer leaks into the session.** Paint operations (`bucket_fill`, `gradient`,
  `stroke_selection`, `pencil`, `paintbrush`), the colour selections (`select_by_color`,
  `fuzzy_select`), and `outline_text` now set their foreground / opacity / brush / sample-threshold
  only for their own operation and restore it afterward — they no longer overwrite the persistent
  paint context (`set_fg` / `set_bg` / `set_brush` / `set_paint_opacity` remain the tools for
  intentional, persistent changes).
- **`despill`** reports `despilled: false` on the no-op fallback path (was always `true`).
- Centralized the GIMP-3 `get_offsets` / `get_resolution` 3-tuple quirk into single compat owners,
  fixing an unguarded offset read in `move_layer` and making every call site robust.

### Internal
- Root-caused and fixed a GIMP projection tile-validation race that intermittently mis-rendered a
  Tier-3 golden (via a `displays_flush` in the export path + test reruns); no product-code impact.

## [0.1.0] - 2026-07-01

First public release. A full v1 tool surface over a hybrid live/headless GIMP
bridge, verified against real GIMP **3.0.4** and **3.2.4**.

### Added
- **117 tools across 13 groups**, each behaviorally tested against a real
  (headless) GIMP: session, document, layers, masks/alpha, selections, paint,
  text, color/tone, filters/effects, print/DTF, color management, analysis,
  safety. Every tool returns the structured envelope `{ ok, result, stdout,
  warnings, error }`; the acted-on drawable is keyed canonically as
  `layer` + `image` across the whole surface.
- **`normalize`** (color/tone) — auto stretch-contrast to the full tonal range via
  `gegl:stretch-contrast` (a NO-OP on a flat/solid channel, so a solid fill / white
  underbase is never silently blacked out); `keep_colors` toggles per-channel vs
  hue-preserving.
- **`offset_content` + `seam_check`** (layers) — roll a layer's pixel content with
  optional wrap-around, for seam-checking tileable textures (distinct from
  `offset_layer`, which repositions the layer on the canvas).
- **Perceptual `histogram`.** Stats (`mean`/`std_dev`/`median`/`min`/`max`, 0-255) are
  computed from the drawable's own pixels in the same space as `color_at`/`get_bitmap`
  and that `levels`/`curves` consume; `space='gimp'` opts into GIMP's native
  gamma-encoded value axis.
- **Hybrid bridge.** A persistent GIMP extension auto-starts on launch and
  publishes a loopback endpoint; the server attaches to a running GIMP by
  default and otherwise spawns a headless `gimp -i` (`GIMP_MCP_HEADLESS` forces
  headless). Transport is loopback TCP + length-prefixed JSON + a per-session
  token. The same tools work identically in both modes.
- **Print / DTF tooling (the headline):** `white_underbase`, `edge_choke` /
  `edge_spread`, `trim_to_content`, `clean_for_dtf`, `halftone_separation`,
  `gang_sheet` (22″ @ 300 DPI default), `export_dtf_png`, `print_geometry`,
  `bleed_and_safe`, and a garment-aware `knockout_background` with
  `list_shirt_presets` (12 garment presets; picks color-to-alpha vs.
  select-and-clear from the garment).
- **Vision-first `get_bitmap`** — returns a viewable image the agent can
  actually see (region / scale), with a byte-budget auto-downscale, a `max_dim`
  clamp (≤4096), and a `save_to=<path>` escape hatch for large exports.
- **Safety:** `checkpoint` / `restore` (origin-aware — restore targets the image
  the checkpoint was taken from, so it cannot clobber a different active image),
  `undo` / `redo`, and undo-group control.
- **Compat layer** owning GIMP-3 quirks (e.g. the `Selection.bounds` 6-tuple,
  font-alias fallback) with version-aware endpoint discovery across GIMP 3.0/3.2.
- **Raw `gimp_exec` escape hatch** with full stdout capture even on error.
- **CLI** (`gimp-mcp`): `serve`, `install-plugin`, `uninstall-plugin`,
  `doctor`, `status`, `version`.
- **3-tier test suite** — Tier-1 (no GIMP: registration, protocol, envelope,
  compat, bridge purity) plus Tier-2/3 golden-image tests under `--run-gimp` —
  and a GitHub Actions CI workflow (ruff + pytest on Python 3.10–3.13).
- Packaging as `twelvetake-gimp-studio-mcp` (import package `gimp_mcp`, console
  script `gimp-mcp`); `LICENSE` (MIT) and `SECURITY.md`.

### Security
- `gimp_exec` runs arbitrary host code by design (same trust boundary as the
  local user). Set `GIMP_MCP_NO_EXEC=1` to skip registering it entirely; the
  other 116 structured tools are unaffected. The threat model — same-user trust
  boundary, loopback + token, and the prompt-injection-via-image vector — is
  documented in `SECURITY.md` and the README "Security model" section.

### Fixed
- Launcher spawns headless GIMP with `stdin=DEVNULL` (console GIMP otherwise
  blocks reading a batch script from an inherited pipe stdin, which broke `serve`
  over stdio) and scrubs `PYTHONPATH` / `PYTHONHOME` so the external venv never
  leaks into GIMP's Python.

[Unreleased]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/releases/tag/v0.1.0
