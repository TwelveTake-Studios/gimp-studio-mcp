"""Group A — Session / lifecycle.

Thin wrappers over bridge ops. (launch_headless/attach_live/shutdown are handled
by the server's GimpContext, not exposed as agent tools.)
"""
from __future__ import annotations

from gimp_mcp import install, settings


def exec_disabled() -> bool:
    """True when the raw `gimp_exec` host-code-exec tool is opted out.

    Set ``GIMP_MCP_NO_EXEC=1`` (also accepts ``true``/``yes``/``on``) to skip
    registering it — the rest of the structured tool surface is unaffected.
    See SECURITY.md and the README "Security model" section.
    """
    return settings.flag(settings.NO_EXEC)


def _status(ctx) -> dict:
    env = ctx.call("info").to_dict()
    if not env.get("ok"):
        return env
    result = env.get("result") or {}
    running = ctx.bridge_version() or result.get("bridge_version")
    warnings = list(env.get("warnings") or [])
    stale = install.older_bridge_warning(running)
    if stale:
        warnings.append(stale)
    warnings.extend(settings.problems())
    env["warnings"] = warnings
    return env


def register(mcp, ctx) -> None:

    @mcp.tool(name="status")
    def status() -> dict:
        """GIMP/bridge health: GIMP version, bridge version, mode, open-image count,
        and the names seeded into the persistent namespace. `warnings` reports a
        bridge in GIMP older than this package expects, and any timeout setting
        that is not a usable number of seconds."""
        return _status(ctx)

    @mcp.tool(name="list_images")
    def list_images() -> dict:
        """List open images: id, name (basename), width, height, dirty flag."""
        return ctx.call("list_images").to_dict()

    @mcp.tool(name="set_active_image")
    def set_active_image(image: int | str) -> dict:
        """Set the active image by integer id or basename string. Tools that take
        `image` act on the active image when it is omitted: the image last chosen
        here, opened or created, while it stays open; otherwise GIMP's first open
        image. A checkpoint copy is never the active image."""
        return ctx.set_active_image(image).to_dict()

    if not exec_disabled():
        @mcp.tool(name="gimp_exec")
        def gimp_exec(code: str, undo_group: bool = True) -> dict:
            """Raw escape hatch: run Python inside GIMP.

            Returns the structured envelope {ok, result, stdout, warnings, error}; stdout
            is preserved even on error. Set a top-level `_result` for structured output.
            The persistent namespace has Gimp/Gegl/Gio/GLib/GObject/Babl, `pdb`,
            `find_image(spec)`, and `compat` (color/read_pixel/color_to_alpha/...).

            Runs arbitrary host code by design (same trust boundary as the local user);
            set GIMP_MCP_NO_EXEC=1 to skip registering this tool. See SECURITY.md."""
            return ctx.run(code, undo_group=undo_group).to_dict()

    @mcp.tool(name="reset_namespace")
    def reset_namespace(scope: str = "soft") -> dict:
        """Reset the persistent namespace.

        scope='soft' (default): clear user-defined vars and re-seed the baseline;
        the canvas/open images are untouched.
        scope='scratch' (guarded HARD reset): soft + delete only bridge-created
        scratch images (checkpoint snapshots tagged in `_gimpmcp_scratch`); never
        touches user-opened or agent working images."""
        return ctx.call("reset_namespace", scope).to_dict()
