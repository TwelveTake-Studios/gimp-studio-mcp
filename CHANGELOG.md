# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/TwelveTake-Studios/gimp-studio-mcp/releases/tag/v0.1.0
