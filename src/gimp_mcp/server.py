"""MCP server entry point.

Wires every tool group onto a FastMCP server and connects to the GIMP bridge
(live-attach by default, headless-spawn as fallback or when forced). The same
tools work in both modes — they only ever talk to the BridgeClient.
"""
from __future__ import annotations

import os

from . import __version__, settings
from .bridge import launcher, protocol as P
from .bridge.client import BridgeClient, BridgeError, BridgeResponse
from .tools._snippets import CHECKPOINT_TAG

DOCTOR_HINT = "Run `gimp-mcp doctor` to diagnose the connection to GIMP."

_PICK_IMAGE_CODE = """
def _gimpmcp_pick_image(want, horizon, tag):
    copies = set(globals().get("_gimpmcp_scratch") or ())
    for entry in (globals().get("_gimpmcp_checkpoints") or {}).values():
        copies.add(entry["snapshot"] if isinstance(entry, dict) else entry)
    ids = []
    for im in Gimp.get_images():
        try:
            if im.get_parasite(tag) is not None:
                copies.add(im.get_id())
        except Exception:
            pass
        ids.append(im.get_id())
    working = [i for i in ids if i not in copies]
    newer = [i for i in working if horizon is not None and i > horizon]
    if newer:
        image = max(newer)
    elif want in working:
        image = want
    else:
        image = working[0] if working else None
    return {"image": image, "open": len(ids), "newest": max(ids) if ids else 0,
            "checkpoint": want in copies}


_result = _gimpmcp_pick_image(args.get("want"), args.get("horizon"), args["tag"])
del _gimpmcp_pick_image
"""


def connection_failure(exc: BaseException) -> BridgeResponse:
    message = str(exc) or type(exc).__name__
    if "gimp-mcp doctor" not in message:
        message = "%s. %s" % (message.rstrip(". "), DOCTOR_HINT)
    return BridgeResponse(ok=False, error=P.BridgeError(type(exc).__name__, message))


class GimpContext:
    """Lazily-connected bridge access shared by all tools.

    Live-attach if a bridge endpoint exists (a running GIMP), otherwise spawn a
    headless GIMP. Set GIMP_MCP_HEADLESS to force headless.
    """

    def __init__(self, prefer_headless: bool | None = None):
        self._client: BridgeClient | None = None
        self._headless = None
        if prefer_headless is None:
            prefer_headless = settings.flag(settings.HEADLESS)
        self.prefer_headless = prefer_headless
        self.active_image: int | None = None
        self._horizon: int | None = None
        self._session: str | None = None

    def client(self) -> BridgeClient:
        if self._client is None:
            if not self.prefer_headless and os.path.exists(P.default_endpoint_file()):
                self._client = launcher.attach_live()
            else:
                self._headless = launcher.launch_headless()
                self._client = self._headless.client
        return self._client

    def _ready_client(self) -> BridgeClient:
        client = self.client()
        client.ensure_connected()
        if client.session != self._session:
            self._session = client.session
            self.active_image = None
            self._horizon = None
        return client

    def bridge_version(self) -> str | None:
        return self._client.bridge_version if self._client is not None else None

    def _pick_image(self, client: BridgeClient, want, horizon=None) -> BridgeResponse:
        return client.exec(_PICK_IMAGE_CODE,
                           args={"want": want, "horizon": horizon, "tag": CHECKPOINT_TAG},
                           undo_group=False)

    def _default_image(self, client: BridgeClient):
        picked = self._pick_image(client, self.active_image, self._horizon)
        if not picked.ok:
            return picked
        result = picked.result or {}
        if result.get("image") is None and result.get("open"):
            return BridgeResponse(ok=False, error=P.BridgeError(
                "NoWorkingImage",
                "every open image is a checkpoint copy; open or create an image, "
                "or pass image= to choose one"))
        return result.get("image")

    def run(self, code: str, *, args=None, image=None, undo_group: bool = True,
            timeout: float | None = None, default_image: bool = True) -> BridgeResponse:
        try:
            client = self._ready_client()
            if (default_image and image is None and isinstance(args, dict)
                    and "image" in args and args["image"] is None):
                target = self._default_image(client)
                if isinstance(target, BridgeResponse):
                    return target
                if target is not None:
                    args = dict(args, image=target)
                    image = target
            return client.exec(code, args=args, image=image,
                               undo_group=undo_group, timeout=timeout)
        except BridgeError as e:
            return connection_failure(e)

    def call(self, method: str, *a, **k) -> BridgeResponse:
        """Invoke a BridgeClient convenience method (info, list_images, ...)."""
        try:
            return getattr(self._ready_client(), method)(*a, **k)
        except BridgeError as e:
            return connection_failure(e)

    def set_active_image(self, image) -> BridgeResponse:
        chosen = self.call("set_active_image", image)
        if not chosen.ok:
            return chosen
        image_id = (chosen.result or {}).get("active_image_id")
        try:
            picked = self._pick_image(self._ready_client(), image_id)
        except BridgeError as e:
            return connection_failure(e)
        if not picked.ok:
            return picked
        result = picked.result or {}
        if result.get("checkpoint"):
            return BridgeResponse(ok=False, error=P.BridgeError(
                "CheckpointCopy",
                "image %s is a checkpoint copy and cannot be the active image; use restore() "
                "to roll back, or pass image=%s to a tool to read it" % (image_id, image_id)))
        self.active_image = image_id
        self._horizon = result.get("newest")
        return chosen

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

    from .instructions import INSTRUCTIONS

    if ctx is None:
        ctx = GimpContext()
    mcp = FastMCP("gimp-mcp", instructions=INSTRUCTIONS)
    mcp._mcp_server.version = __version__
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
