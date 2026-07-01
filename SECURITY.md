# Security Policy

## Trust model

GIMP Studio MCP is designed for a **single-user workstation**. It runs with the
same privileges as the user who launches it, and it gives any attached AI agent
the ability to drive GIMP on that machine. **It is not a sandbox and does not
attempt to be one.** Treat installing it the same way you would treat granting a
local automation script full access to your GIMP and your files.

If you need a stronger boundary (multi-tenant, untrusted agents, or untrusted
input), run the server inside an OS-level sandbox / container / dedicated user
account. This project does not provide that isolation itself.

## Threat model

### What it protects

- **Loopback only.** The GIMP-side bridge listens on `127.0.0.1` with an
  ephemeral port — it is never bound to a public interface.
- **Per-session token.** Clients must present a token published with the bridge
  endpoint, so an unrelated local process cannot trivially drive your GIMP by
  guessing the port.
- **Structured, validated tools.** The 100+ first-class tools validate their
  parameters and return a structured envelope, so normal agent use does not
  require handing GIMP raw code.

### What it does not protect against

- **`gimp_exec` is arbitrary host code execution, by design.** It is the raw
  escape hatch: it runs whatever Python it is given inside GIMP's process, which
  can read and write files, spawn processes, and reach anything the user can.
- **Prompt injection.** An AI agent can be manipulated by untrusted content —
  for example, instructions hidden in metadata, a filename, or the pixels of an
  image you ask it to open — into calling `gimp_exec` (or destructive tools)
  with attacker-chosen input. The same-user trust boundary means there is no
  privilege barrier between "what the agent was told to do" and "what your
  account can do."
- **Local processes.** Loopback + token raise the bar but are not a hard
  authentication boundary against a determined local attacker already running as
  your user.

## Reducing exposure

- **Disable raw exec.** Set `GIMP_MCP_NO_EXEC=1` to skip registering `gimp_exec`
  entirely. The rest of the structured tool surface continues to work. Do this
  if you do not need the escape hatch, or whenever you point the agent at content
  you do not fully trust.
- **Attach only trusted agents.** Only register this server with MCP clients /
  agents you control.
- **Be deliberate with untrusted images.** Opening an arbitrary downloaded image
  and then letting the agent act on it freely is the main prompt-injection path.
- **Keep it loopback.** Do not forward or proxy the bridge port off `localhost`.

## Supported versions

This project is pre-1.0; security fixes are applied to the latest release on
`main`. Pin a released version if you need stability.

## Reporting a vulnerability

Please report security issues privately to **contact@twelvetake.com** rather than
opening a public issue. Include a description, reproduction steps, and the impact
you observed. We will acknowledge the report and work with you on a fix and
coordinated disclosure.
