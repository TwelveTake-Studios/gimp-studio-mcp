"""MCP server entry point.

Wires every tool group onto a FastMCP server and connects to the GIMP bridge
(live-attach by default, headless-spawn as fallback or when forced). The same
tools work in both modes — they only ever talk to the BridgeClient.
"""
from __future__ import annotations

import os

from .bridge import launcher, protocol as P
from .bridge.client import BridgeClient, BridgeResponse


class GimpContext:
    """Lazily-connected bridge access shared by all tools.

    Live-attach if a bridge endpoint exists (a running GIMP), otherwise spawn a
    headless GIMP. Set GIMP_MCP_HEADLESS to force headless.
    """

    def __init__(self, prefer_headless: bool | None = None):
        self._client: BridgeClient | None = None
        self._headless = None
        if prefer_headless is None:
            prefer_headless = bool(os.environ.get("GIMP_MCP_HEADLESS"))
        self.prefer_headless = prefer_headless

    def client(self) -> BridgeClient:
        if self._client is None:
            if not self.prefer_headless and os.path.exists(P.default_endpoint_file()):
                self._client = launcher.attach_live()
            else:
                self._headless = launcher.launch_headless()
                self._client = self._headless.client
        return self._client

    def run(self, code: str, *, args=None, image=None, undo_group: bool = True,
            timeout: float | None = None) -> BridgeResponse:
        return self.client().exec(code, args=args, image=image,
                                  undo_group=undo_group, timeout=timeout)

    def call(self, method: str, *a, **k) -> BridgeResponse:
        """Invoke a BridgeClient convenience method (info, list_images, ...)."""
        return getattr(self.client(), method)(*a, **k)

    def close(self) -> None:
        if self._headless is not None:
            self._headless.shutdown()
            self._headless = None
            self._client = None
        elif self._client is not None:
            self._client.close()
            self._client = None


def build_server(ctx: GimpContext | None = None):
    """Create the FastMCP server, attach the bridge context, register tool groups."""
    from mcp.server.fastmcp import FastMCP

    if ctx is None:
        ctx = GimpContext()
    mcp = FastMCP("gimp-mcp")
    from . import tools
    tools.register_all(mcp, ctx)
    return mcp, ctx


def main() -> None:
    """Console entry point (`gimp-mcp serve`). Runs the MCP server over stdio."""
    mcp, ctx = build_server()
    try:
        mcp.run()
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
