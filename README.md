# GIMP Studio MCP

A [TwelveTake Studios](https://twelvetake.com) project.

[![Tools](https://img.shields.io/badge/tools-117-blue)](https://github.com/TwelveTake-Studios/gimp-studio-mcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow)](https://buymeacoffee.com/twelvetake)

A comprehensive **GIMP 3.x MCP server** that gives an AI agent full, reliable control of GIMP — with structured returns, real error capture, a GIMP-3 compatibility layer, safety checkpoints, and print/DTF-aware tooling.

**117 tools across 13 groups**, each behaviorally tested against a real (headless) GIMP. Built for a working print shop's DTF (direct-to-film) pipeline, not as a thin API wrapper.

**Status:** v1 — feature-complete and verified on GIMP **3.0.4** and **3.2.4** (headless and live).

## Why this exists

The existing thin GIMP MCP wrapper is intentionally minimal — one universal `call_api` console exec plus a few read-only inspectors. That's a deliberate design, not a gap to PR against. This is a **separate project** that makes a different bet: own the ergonomics layer the thin wrapper punts to the model at runtime, so the agent gets validated, structured, print-aware tools instead of having to write GIMP Python by hand every time.

### The design rules (every tool follows these)

1. **Structured returns** — `{ ok, result, stdout, warnings, error }`; output is never lost, even on error.
2. **Raw `exec` stays** — the universal escape hatch (`gimp_exec`) survives, with proper capture. (Opt out with `GIMP_MCP_NO_EXEC=1` — see [Security model](#security-model).)
3. **Compat / quirk layer** — one module owns GIMP-3 gotchas so nobody relearns them per session.
4. **Mode-agnostic tools** — work identically whether GIMP is live (visible canvas) or headless.
5. **Vision-first** — `get_bitmap` returns a viewable image the agent can actually see (with region/scale/byte-budget), for self-verification.
6. **Safety by default** — `checkpoint` / `restore` around destructive ops.
7. **Validated params** everywhere.
8. **Print-aware throughout** — DPI / inches are first-class; DTF output is a headline feature.

## Architecture (hybrid, day one)

Live and headless differ only in **who launches GIMP**, not how we talk to it — so it's one bridge:

- **Live:** a persistent GIMP extension (installed by `gimp-mcp install-plugin`) starts on launch and publishes a loopback endpoint; the MCP server auto-attaches to the running GIMP, canvas visible.
- **Headless:** if no live GIMP is found, the server spawns a long-running `gimp -i` and loads the same bridge.

Both speak one loopback-TCP, length-prefixed-JSON, token-authenticated protocol; pixel export works in both.

## Capability areas (117 tools)

| Group | Tools | What it covers |
|-------|:-----:|----------------|
| Session | 5 | health/status, open-image list, active-image switch, raw `gimp_exec`, namespace reset |
| Document | 4 | new/open image, flatten, export |
| Layers | 18 | create/duplicate/delete/reorder/move, opacity, blend mode, groups, merge, content-offset + seam-check (tileable textures) |
| Masks & Alpha | 8 | add/apply/remove masks, alpha add/lock, alpha↔selection, luminance→alpha |
| Selections | 14 | rect/ellipse/by-color/fuzzy/from-path/from-alpha, grow/shrink/feather/border, invert, to-channel |
| Paint | 10 | pencil/paintbrush, bucket fill, gradient, stroke, fg/bg + brush + opacity context |
| Text | 5 | create/edit text layers, props, outline, font check/substitute |
| Color & Tone | 10 | brightness/contrast, levels, curves, hue/sat, color balance, desaturate, posterize, invert, threshold, normalize (auto stretch-contrast) |
| Filters & Effects | 4 | gaussian blur, unsharp mask, drop shadow, generic GEGL `apply_filter` |
| Print / DTF | 13 | white underbase, edge choke/spread, trim-to-content, **knockout_background** (garment-aware), clean-for-DTF, halftone separation, gang sheet (22″@300), DTF PNG export, geometry/bleed |
| Color Management | 7 | assign/convert ICC profiles, soft-proof, grayscale/RGB conversion, profile inspect |
| Analysis | 12 | **`get_bitmap`** (viewable preview), histogram (perceptual, matches `levels`), color-at, region read, metadata, op describe, GEGL/procedure listing |
| Safety | 7 | checkpoint/restore, undo/redo, undo groups, list checkpoints |

Tools are self-describing through your MCP client; `describe_op` and `list_*` tools enumerate ops and resources at runtime.

## Requirements

- **GIMP 3.x** (tested on 3.0.4 and 3.2.4)
- **Python 3.10+** (for the MCP server)
- An MCP-compatible AI assistant (e.g. Claude Code / Claude Desktop)

## Installation

### 1. Install the server

GitHub-first (recommended while the PyPI release is a fast-follow):

```bash
pipx install git+https://github.com/TwelveTake-Studios/gimp-studio-mcp
```

Or from a clone:

```bash
git clone https://github.com/TwelveTake-Studios/gimp-studio-mcp
cd gimp-studio-mcp
pip install -e .
```

### 2. Install the GIMP-side bridge

```bash
gimp-mcp install-plugin     # copies the bridge into GIMP's plug-ins dir
gimp-mcp doctor             # verify install + GIMP exe + bridge reachability
```

Restart GIMP (or it loads on next launch). The bridge auto-starts as a persistent extension.

### 3. Register with your MCP client

Add to your client's MCP config (e.g. `.mcp.json`):

```json
{
  "mcpServers": {
    "gimp": {
      "command": "gimp-mcp",
      "args": ["serve"]
    }
  }
}
```

The server attaches to a running GIMP if one is available, and otherwise spawns a headless GIMP automatically. Set `GIMP_MCP_HEADLESS=1` to always run headless.

## Quick start

### General editing

```
"Open logo.png and tell me its size and layers"
"Add a 12px white outline to the text layer"
"Auto-crop the image to its content, then export a transparent PNG"
"Show me the current canvas"          → get_bitmap returns a viewable preview
```

### DTF (direct-to-film) — the headline workflow

```
"Knock out the black shirt color behind this artwork"     → knockout_background, garment-aware
"Add a white underbase choked 2px so it doesn't peek"      → white_underbase + edge_choke
"Clean this up for DTF and export a 300-DPI transparent PNG"
"Gang up 12 copies of this design onto a 22-inch sheet at 300 DPI"
```

`knockout_background` is garment-aware: pass a `shirt=` preset (black, navy, heather_gray, red, …) and it picks the right removal technique (color-to-alpha for dark garments where black is the shirt showing through, hard select-and-clear for light/saturated ones). `list_shirt_presets` shows the catalog.

## Security model

This server runs on the **same trust boundary as the local user**. Installing it grants any attached AI agent the ability to drive GIMP on your machine — and, through `gimp_exec`, to run arbitrary Python in GIMP's process. That is intended for a single-user workstation; it is **not** a sandbox.

- **Loopback only + token.** The bridge listens on `127.0.0.1` with an ephemeral port and a per-session token. It is not exposed to the network.
- **`gimp_exec` is arbitrary host code execution by design.** A malicious or prompt-injected instruction (for example, hidden in an image you ask the agent to open) could reach `gimp_exec`. Only attach trusted agents and trusted content.
- **Disable switch.** Set `GIMP_MCP_NO_EXEC=1` to skip registering `gimp_exec` entirely; the other 116 structured tools still work.

See [SECURITY.md](SECURITY.md) for the full threat model and reporting instructions.

## Environment variables

| Variable | Effect |
|----------|--------|
| `GIMP_MCP_HEADLESS` | Force headless GIMP (spawn `gimp -i`) instead of attaching to a running one. |
| `GIMP_MCP_NO_EXEC` | Skip registering the raw `gimp_exec` host-code-exec tool (`1`/`true`/`yes`/`on`). |
| `GIMP_MCP_DISABLED` | The in-GIMP bridge acks and does not serve. |
| `GIMP_MCP_ENDPOINT_FILE` | Explicit endpoint-file path (advanced / headless). |
| `GIMP_MCP_PORT` | Pin a bridge port instead of an ephemeral one. |

## Troubleshooting

Run `gimp-mcp doctor` first — it checks the install, locates the GIMP executable, and round-trips the bridge. Add `--headless` to also spawn a headless GIMP and verify a full round-trip.

- **"Bridge not reachable" with GIMP open:** make sure you ran `gimp-mcp install-plugin` and restarted GIMP so the bridge extension loaded.
- **Edited the server but tools look unchanged:** the MCP server does not hot-reload — restart your MCP client session.

## License

MIT — see [LICENSE](LICENSE).

---

**TwelveTake Studios LLC**
Website: [twelvetake.com](https://twelvetake.com)
Contact: contact@twelvetake.com
